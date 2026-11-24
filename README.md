# 阅伴 · 阅读器 + AI 伴读

> **一句话**:一个「阅读器 + AI 伴读」——你导入一本书(或 PDF),AI 能看到全文、知道你的阅读进度;你选中书里任意一句话,它就结合**上文和下文**帮你读懂它,而不是孤立地望文生义。

## 是什么 / 谁做的
- **为谁**:正在读一本书 / 论文 / 文献的读者(研究生、本科生、爱读书的人)。
- **解决什么**:读书时「看不懂某句话、理不清前后文、记不住读到哪」的卡点——选中一句话,直接问,AI 结合上下文回答,并自动记住你的进度。
- **作者**:[你的名字] · 北京师范大学心理学部 · 人本 AI 设计与创新(PBL 项目)

## 怎么用
1. 打开前端页面(上线后是 GitHub Pages 的 URL;本地跑法见下文)。
2. 左侧「导入书籍」上传 PDF / TXT / MD → 右侧显示全文,可滚动阅读。
3. **选中书里任意一句话** → 右下角弹出「问阅伴」框 → 输入你的问题(如「这里的『它』指什么?」)。
4. AI 拿到这句话 + 它前面的两段 + 后面的两段作为上下文,结合前后文回答。
5. 阅读进度在滚动时**自动保存**,下次打开自动回到上次读到的位置。

## 核心功能
| 功能 | 说明 |
|------|------|
| 导入书籍 | 上传 PDF(自动解析文字)/ TXT / MD,按段落切分入库 |
| 全文阅读 | 阅读器式排版,滚动流畅,可选中文字 |
| 选中即问 | 选中句子 → 弹 AI 问答框,带**前后文上下文** |
| 阅读进度 | 滚动自动保存到数据库,重开自动恢复 |
| 批注 | 选中文字可存为批注(接口已备好,前端可扩展) |
| 安全护栏 | 危机语句一票拦截转介真人;前端无 key |

## 架构(三层,和老师示例一致)
```
手机/浏览器 → 前端 index.html(纯静态·无 key,GitHub Pages)
                  │  POST /api/ask {selected, before, after, question}
                  │  POST /api/books/upload  · /api/books/{id}/progress
                  ▼
         后端 FastAPI(main.py + db.py)
           · SQLite:books / paragraphs / progress / annotations
           · PDF 解析(pymupdf)
           · 选中句子 + 前后文 → 拼成上下文 → 调模型
           · is_crisis() 确定性危机拦截(入口+出口)
           · 子 agent 安全复核
                  ▼
         DeepSeek(key 只在后端 env)· 部署:Railway
```

## 本地跑起来
```bash
# 后端
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # 填你的 DEEPSEEK_API_KEY
.venv/bin/uvicorn main:app --reload --port 8000

# 前端(另开一个终端)
cd frontend
python3 -m http.server 5500   # 打开 http://localhost:5500
```
> 不填 key 也能验证**危机拦截**与**优雅降级**(选中/输入"撑不下去了"应立即转介热线;后端关掉时前端给兜底话术)。

## 上线
- 后端 → Railway:Deploy from GitHub → Root Directory 设 `backend` → Variables 填 `DEEPSEEK_API_KEY` → 启动命令 `uvicorn main:app --host 0.0.0.0 --port $PORT`。
- 前端 → GitHub Pages:把 `frontend/index.html` 里的 `BACKEND` 改成你的 Railway 域名,后端 `CORS_ORIGINS` 填 Pages 域名。
- 数据:SQLite 文件 `backend/reader.db` 会自动创建;Railway 上用持久化卷(Volume)挂载 `backend/` 以保留数据。

## 三条红线(老师的一票否决项)
1. **key 只在后端**——前端源码 `grep -rniE "sk-[a-z0-9]{16}"` 必须为零。
2. **危机由确定性代码兜底**——`is_crisis()` 入口+出口各拦一次,不只靠模型自觉。
3. **不上线 = 没有 impact**。
