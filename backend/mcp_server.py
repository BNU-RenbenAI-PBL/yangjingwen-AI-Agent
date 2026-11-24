# -*- coding: utf-8 -*-
"""阅伴 MCP Server（读取类工具 + 写入类工具 + 用户/AI 身份区分）。

设计理念（从阅读需求反推工具）：
1. 身份识别：
   - 浏览器前端请求默认识别为 actor_type='user'（user_id='local'）
   - 通过可信 OAuth Bearer Token 进入的 MCP 请求识别为 actor_type='ai'（user_id='mcp_oauth'）
   后台数据库明确区分「谁读、谁写、谁批注」。
2. 读取类工具（先跑通数据链路）：
   - list_books
   - get_book_outline
   - read_book_context
   - get_reading_progress
   - list_annotations
3. 写入类工具（数据稳定后扩展）：
   - add_ai_annotation
   - update_reading_progress
   - delete_annotation
"""
import os
import db
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

MCP_OAUTH_TOKEN = os.getenv("MCP_OAUTH_TOKEN", "")

router = APIRouter(prefix="/mcp", tags=["MCP"])

TOOLS_SCHEMA = [
    {
        "name": "list_books",
        "description": "列出书库中所有已导入的书籍、段落总数及最近阅读进度。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_book_outline",
        "description": "读取指定书籍的目录大纲（所有章节标题及段落序号）。",
        "inputSchema": {
            "type": "object",
            "properties": {"book_id": {"type": "string", "description": "书籍 ID"}},
            "required": ["book_id"],
        },
    },
    {
        "name": "read_book_context",
        "description": "读取指定书籍从 start_seq 到 end_seq 的段落正文，用于结合前后文伴读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string"},
                "start_seq": {"type": "integer", "default": 0},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["book_id"],
        },
    },
    {
        "name": "get_reading_progress",
        "description": "获取用户当前在指定书籍中的阅读进度。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string"},
                "user_id": {"type": "string", "default": "local"},
            },
            "required": ["book_id"],
        },
    },
    {
        "name": "list_annotations",
        "description": "读取指定书籍的重点划线和批注（含用户批注与 AI 伴读批注）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string"},
                "user_id": {"type": "string", "default": "local"},
            },
            "required": ["book_id"],
        },
    },
    {
        "name": "add_ai_annotation",
        "description": "以 AI 伴读身份（actor_type=ai）为指定段落新增一条批注。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string"},
                "para_seq": {"type": "integer"},
                "selected": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["book_id", "para_seq", "selected", "note"],
        },
    },
    {
        "name": "update_reading_progress",
        "description": "更新指定书籍的阅读进度。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string"},
                "para_seq": {"type": "integer"},
                "user_id": {"type": "string", "default": "local"},
            },
            "required": ["book_id", "para_seq"],
        },
    },
    {
        "name": "delete_annotation",
        "description": "删除指定 ID 的批注。",
        "inputSchema": {
            "type": "object",
            "properties": {"annotation_id": {"type": "integer"}},
            "required": ["annotation_id"],
        },
    },
]


def _resolve_mcp_actor(authorization: str | None) -> tuple[str, str]:
    """若配置了 MCP_OAUTH_TOKEN，则强制校验 Bearer Token；识别为 AI 操作。"""
    if MCP_OAUTH_TOKEN:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing MCP OAuth Bearer token")
        token = authorization.removeprefix("Bearer ").strip()
        if token != MCP_OAUTH_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid MCP OAuth token")
    return ("mcp_oauth", "ai")


def execute_tool(name: str, args: dict, actor_user_id: str, actor_type: str) -> dict:
    if name == "list_books":
        return {"books": db.list_books()}

    if name == "get_book_outline":
        book = db.get_book(args["book_id"])
        if not book:
            return {"error": "book not found"}
        outline = [
            {"seq": p["seq"], "kind": p["kind"], "title": p["text"]}
            for p in book["paragraphs"]
            if p["kind"] in ("h1", "h2", "h3")
        ]
        return {"book_id": book["id"], "title": book["title"], "outline": outline}

    if name == "read_book_context":
        book = db.get_book(args["book_id"])
        if not book:
            return {"error": "book not found"}
        start = max(0, int(args.get("start_seq", 0)))
        limit = min(40, max(1, int(args.get("limit", 10))))
        slice_paras = book["paragraphs"][start : start + limit]
        return {"book_id": book["id"], "title": book["title"], "paragraphs": slice_paras}

    if name == "get_reading_progress":
        uid = args.get("user_id", "local")
        return {"progress": db.get_progress(uid, args["book_id"])}

    if name == "list_annotations":
        uid = args.get("user_id", "local")
        return {"annotations": db.get_annotations(uid, args["book_id"], include_ai=True)}

    if name == "add_ai_annotation":
        ann_id = db.add_annotation(
            user_id=actor_user_id,
            book_id=args["book_id"],
            para_seq=int(args["para_seq"]),
            selected=args["selected"],
            note=args["note"],
            actor_type=actor_type,
        )
        return {"ok": True, "annotation_id": ann_id, "actor_type": actor_type}

    if name == "update_reading_progress":
        db.save_progress(
            user_id=args.get("user_id", "local"),
            book_id=args["book_id"],
            para_seq=int(args["para_seq"]),
            actor_type=actor_type,
        )
        return {"ok": True, "actor_type": actor_type}

    if name == "delete_annotation":
        ok = db.delete_annotation(int(args["annotation_id"]))
        return {"ok": ok}

    return {"error": f"unknown tool: {name}"}


class JsonRpcReq(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict = {}


@router.get("/tools")
def list_mcp_tools():
    return {"tools": TOOLS_SCHEMA}


@router.post("")
def mcp_jsonrpc(req: JsonRpcReq, authorization: str | None = Header(default=None)):
    actor_uid, actor_type = _resolve_mcp_actor(authorization)

    if req.method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req.id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "yueban-reader-mcp", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
        }
    if req.method == "tools/list":
        return {"jsonrpc": "2.0", "id": req.id, "result": {"tools": TOOLS_SCHEMA}}

    if req.method == "tools/call":
        t_name = req.params.get("name", "")
        t_args = req.params.get("arguments", {}) or {}
        res = execute_tool(t_name, t_args, actor_uid, actor_type)
        return {
            "jsonrpc": "2.0",
            "id": req.id,
            "result": {"content": [{"type": "text", "text": str(res)}], "structuredContent": res},
        }

    return {"jsonrpc": "2.0", "id": req.id, "error": {"code": -32601, "message": "Method not found"}}
