# -*- coding: utf-8 -*-
"""阅伴 · 后端 —— 阅读器 + AI 伴读 + MCP

三层架构（前端 → 后端 → 模型）：
- 前端：阅读器（导入 PDF/MD/TXT/DOCX/EPUB → 排版阅读 → 划线批注/选中问 AI → 自动记录进度）
- 后端：FastAPI + SQLite（书籍/段落/进度/批注/对话）+ 多格式解析 + MCP 读写接口 + AI 伴读
- 模型：DeepSeek / OpenRouter（API Key 仅保存在后端环境变量）

安全红线（一票否决项）：
1. key 只在后端环境变量，绝不进前端。
2. 危机由确定性代码 is_crisis() 兜底（入口 + 出口各一次）。
3. 优雅降级：模型调不通不 500、不白屏。
"""
import os
import pathlib
import httpx
import db
import parsers
import mcp_server
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
MODEL = os.getenv("MODEL", "deepseek-chat")
BASE_URL = os.getenv("API_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
HERE = pathlib.Path(__file__).parent

# ==================== AI 伴读核心 ====================

def _load_skill(name: str) -> str:
    p = HERE / "skills" / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def load_system_prompt() -> str:
    parts = [(HERE / "agent.md").read_text(encoding="utf-8")]
    # 危机转介 skill 永远排在最前(最高优先级)
    crisis = _load_skill("skill_安全红线与危机转介.md")
    if crisis:
        parts.append(crisis)
    for f in sorted((HERE / "skills").glob("skill_*.md")):
        if f.name != "skill_安全红线与危机转介.md":
            parts.append(f.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


SYSTEM_PROMPT = load_system_prompt()

CRISIS_PATTERNS = [
    "自杀", "想死", "不想活", "活着没意思", "活不下去", "撑不下去", "撑不住了",
    "结束生命", "轻生", "伤害自己", "自残", "自伤", "不想醒", "解脱",
]
CRISIS_REPLY = (
    "我听到你现在非常痛苦，谢谢你愿意说出来。这件事很重要，而我只是一个 AI，"
    "没办法替代真正能帮到你的人。请**现在**联系专业援助：\n"
    "· 全国心理援助热线：**12356**\n"
    "· 北京心理危机研究与干预中心：**010-82951332**（24 小时）\n"
    "如果你有立刻的危险，请拨打 **120** 或就近急诊。你不是一个人，愿意的话，"
    "也请告诉你信任的老师、家人或朋友。我会在这里陪着你，但请一定先联系上面的真人。"
)


def is_crisis(text: str) -> bool:
    return any(p in text for p in CRISIS_PATTERNS)


FALLBACK = "抱歉，我这会儿有点忙不过来（可能是网络或额度问题）。你可以先歇一歇，过一会儿再回来，我继续陪你读。"


def call_model(messages: list) -> str | None:
    if not API_KEY:
        return None
    try:
        r = httpx.post(
            BASE_URL,
            timeout=45,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": messages},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None


def _build_context_from_db(book_id: str, para_seq: int, radius: int = 3) -> tuple[str, str, str, str]:
    """根据 book_id 和段落序号自动补全书名与前后文窗口。"""
    if not book_id:
        return ("", "", "", "")
    book = db.get_book(book_id)
    if not book or not book["paragraphs"]:
        return ("", "", "", "")
    paras = book["paragraphs"]
    seq = max(0, min(para_seq, len(paras) - 1))
    before = "\n".join(p["text"] for p in paras[max(0, seq - radius) : seq])
    current = paras[seq]["text"]
    after = "\n".join(p["text"] for p in paras[seq + 1 : seq + 1 + radius])
    return (book["title"], before, current, after)


def ask_about_selection(
    selected: str,
    before: str,
    after: str,
    question: str,
    book_id: str = "",
    para_seq: int = 0,
    history: list[dict] | None = None,
) -> str:
    """结合选中句子 + 前后文 + 阅读进度 + 多轮对话历史解答。"""
    if is_crisis(selected + question):
        return CRISIS_REPLY

    book_title = ""
    if book_id and (not before.strip() and not after.strip()):
        book_title, db_before, db_cur, db_after = _build_context_from_db(book_id, para_seq)
        before = db_before
        after = db_after
        if not selected.strip():
            selected = db_cur
    elif book_id:
        b = db.get_book(book_id)
        if b:
            book_title = b["title"]

    context_parts = []
    if book_title:
        context_parts.append(f"【当前书目】《{book_title}》（当前定位：第 {para_seq + 1} 段）")
    if before.strip():
        context_parts.append(f"【上文】\n{before.strip()}")
    if selected.strip():
        context_parts.append(f"【选中/当前段落】\n{selected.strip()}")
    if after.strip():
        context_parts.append(f"【下文】\n{after.strip()}")
    context = "\n\n".join(context_parts)

    user_msg = (
        f"{context}\n\n"
        f"读者问题：{question or '请结合上下文帮我解释这段话的意思和它在文中的作用。'}\n\n"
        f"请结合上文和下文来解答，先直接回答，再简短说明推理依据（如指代关系、论证逻辑）。"
    )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        for h in history[-6:]:
            role = h.get("role")
            content = h.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_msg})

    raw = call_model(messages)
    if raw is None:
        return FALLBACK
    return raw.strip()


def safety_review(user_text: str, draft: str) -> str:
    if is_crisis(user_text):
        return CRISIS_REPLY
    if not API_KEY or draft == FALLBACK:
        return draft
    verdict = call_model([
        {
            "role": "system",
            "content": (
                "你是安全审核员。判断下面这条回复是否安全、是否越界"
                "（胡编文献结论/代写整篇作业/漏掉危机信号）。只回一个词：SAFE 或 UNSAFE。"
            ),
        },
        {"role": "user", "content": f"用户说：{user_text}\n回复：{draft}"},
    ])
    if verdict and "UNSAFE" in verdict.upper():
        return "我可能不适合回答这个问题——它超出了我能安全支持的范围。如果你正经历困扰，和信任的老师或专业人员聊聊会更好。"
    return draft


# ==================== FastAPI ====================

app = FastAPI(title="阅伴 · 阅读器 + AI 伴读 + MCP")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(mcp_server.router)


class AskIn(BaseModel):
    selected: str = ""
    before: str = ""
    after: str = ""
    question: str = ""
    book_id: str = ""
    para_seq: int = 0
    user_id: str = "local"
    history: list[dict] = []


class ProgressIn(BaseModel):
    user_id: str = "local"
    book_id: str
    para_seq: int


class AnnotationIn(BaseModel):
    user_id: str = "local"
    book_id: str
    para_seq: int
    selected: str
    note: str = ""


class PairAnnotationIn(BaseModel):
    user_id: str = "local"
    book_id: str
    para_seq: int
    selected: str
    user_note: str = ""
    ai_reply: str = ""


@app.get("/api/health")
def health():
    return {"status": "ok", "model": MODEL, "has_key": bool(API_KEY)}


# ---- 书籍 ----
@app.get("/api/books")
def list_books():
    return {"books": db.list_books()}


@app.get("/api/books/{book_id}")
def get_book(book_id: str):
    book = db.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")
    return book


@app.delete("/api/books/{book_id}")
def delete_book(book_id: str):
    ok = db.delete_book(book_id)
    if not ok:
        raise HTTPException(status_code=404, detail="book not found")
    return {"ok": True}


@app.post("/api/books/upload")
async def upload_book(file: UploadFile = File(...), title: str = Form(""), author: str = Form("")):
    data = await file.read()
    name = file.filename or "untitled.txt"
    paragraphs = parsers.parse_document(name, data)
    if not title:
        title = name.rsplit(".", 1)[0] or "未命名书籍"
    book_id = db.create_book(title, paragraphs, author)
    return {"book_id": book_id, "title": title, "para_count": len(paragraphs)}


# ---- 阅读进度（前端默认识别为 actor_type='user'） ----
@app.post("/api/books/{book_id}/progress")
def save_progress(book_id: str, body: ProgressIn):
    db.save_progress(body.user_id, book_id, body.para_seq, actor_type="user")
    return {"ok": True}


@app.get("/api/books/{book_id}/progress")
def get_progress(book_id: str, user_id: str = "local"):
    return {"progress": db.get_progress(user_id, book_id)}


# ---- 批注 ----
@app.post("/api/books/{book_id}/annotations")
def add_annotation(book_id: str, body: AnnotationIn):
    ann_id = db.add_annotation(
        body.user_id, book_id, body.para_seq, body.selected, body.note, actor_type="user"
    )
    return {"ok": True, "id": ann_id}


@app.post("/api/books/{book_id}/annotations/pair")
def add_pair_annotation(book_id: str, body: PairAnnotationIn):
    ids = []
    if body.user_note.strip():
        ids.append(db.add_annotation(
            body.user_id, book_id, body.para_seq, body.selected, body.user_note.strip(), actor_type="user"
        ))
    if body.ai_reply.strip():
        ids.append(db.add_annotation(
            "companion_ai", book_id, body.para_seq, body.selected, body.ai_reply.strip(), actor_type="ai"
        ))
    return {"ok": True, "ids": ids}


@app.get("/api/books/{book_id}/annotations")
def get_annotations(book_id: str, user_id: str = "local"):
    return {"annotations": db.get_annotations(user_id, book_id, include_ai=True)}


@app.delete("/api/annotations/{ann_id}")
def remove_annotation(ann_id: int):
    ok = db.delete_annotation(ann_id)
    return {"ok": ok}


# ---- 伴读聊天记录 ----
@app.get("/api/books/{book_id}/chat")
def get_chat(book_id: str, user_id: str = "local"):
    return {"messages": db.get_chat_history(user_id, book_id)}


# ---- 选中句子问 AI（核心） ----
@app.post("/api/ask")
def ask(body: AskIn):
    user_input = body.selected + " " + body.question
    if is_crisis(user_input):
        return {"reply": CRISIS_REPLY, "meta": {"crisis": True}}
    draft = ask_about_selection(
        body.selected,
        body.before,
        body.after,
        body.question,
        book_id=body.book_id,
        para_seq=body.para_seq,
        history=body.history,
    )
    final = safety_review(user_input, draft)
    is_c = final is CRISIS_REPLY
    if body.book_id and not is_c:
        db.add_chat_message(
            body.user_id, body.book_id, "user", body.question or "请解释这段内容", body.selected[:200]
        )
        db.add_chat_message(body.user_id, body.book_id, "assistant", final)
    return {"reply": final, "meta": {"crisis": is_c}}
