# 人机共读阅读器（Co-Reading Agent）系统架构与交互协议

## 一、核心设计哲学：人机同库、身份隔离、先读后写

本系统打破传统「把文章贴进对话框」的割裂体验，通过 **阅读器前端 + 关系型数据库 + MCP（Model Context Protocol）工具链** 实现人与 AI 在同一本书上的异步与实时共读：

- **浏览器前端操作（`actor_type = "user"`）**：默认识别为人类读者本人，拥有最高权限（增删改查自己的进度、划线、批注、书签）。
- **可信 MCP 链路操作（`actor_type = "ai"`）**：通过独立鉴权 Token / OAuth 路径接入，后台强制标记操作者为 AI。

**硬性权限边界**：AI 仅拥有全库读取权（Read）与「AI 身份批注 / 书签追加权（Append）」，严禁修改或删除任何 `actor_type = "user"` 的数据。

---

## 二、核心数据库表设计（Database Schema）

根据阅读习惯与共读需求，设计以下 5 张核心表（兼容 SQLite / PostgreSQL / Supabase）：

### 1. `books`（书籍主表）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID / PK | 书籍唯一标识 |
| `title` | TEXT | 书名 / 文档名 |
| `author` | TEXT | 作者 |
| `format` | VARCHAR | 原始格式（`txt` / `md` / `pdf` / `epub`） |
| `full_content` | LONGTEXT / JSONB | 结构化分段后的全文内容（按 `paragraph_index` 索引存储，确保前后端与 MCP 定位坐标完全一致） |
| `total_paragraphs` | INT | 总段落数 |
| `created_at` | TIMESTAMP | 导入时间 |

### 2. `reading_progress`（阅读进度表）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID / PK | — |
| `book_id` | FK → books.id | 关联书籍 |
| `actor_type` | VARCHAR | `user`（真实进度）或 `ai`（AI 伴读扫描进度） |
| `current_paragraph_index` | INT | 当前阅读到的段落序号 |
| `percentage` | FLOAT | 阅读百分比（0.00 – 100.00） |
| `updated_at` | TIMESTAMP | 最后阅读时间 |

### 3. `highlights`（重点划线表）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID / PK | — |
| `book_id` | FK → books.id | 关联书籍 |
| `actor_type` | VARCHAR | `user` 或 `ai` |
| `paragraph_index` | INT | 所在段落序号 |
| `start_offset` | INT | 字符起始偏移量 |
| `end_offset` | INT | 字符结束偏移量 |
| `selected_text` | TEXT | 划线的原文片段 |
| `color` | VARCHAR | 高亮颜色（用户默认黄，AI 推荐批注默认浅蓝） |
| `created_at` | TIMESTAMP | — |

### 4. `annotations`（批注与共读笔记表）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID / PK | — |
| `book_id` | FK → books.id | 关联书籍 |
| `highlight_id` | FK → highlights.id | 可空，支持整段或全书级批注 |
| `paragraph_index` | INT | 关联段落序号 |
| `actor_type` | VARCHAR | `user`（人类笔记）或 `ai`（AI 伴读批注） |
| `content` | TEXT | 批注正文 |
| `quote_context` | TEXT | 批注所针对的原文引用 |
| `created_at` | TIMESTAMP | — |

### 5. `bookmarks`（书签表）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID / PK | — |
| `book_id` | FK → books.id | 关联书籍 |
| `actor_type` | VARCHAR | `user` 或 `ai` |
| `paragraph_index` | INT | 书签定位段落 |
| `label` | TEXT | 书签备注（如「核心转折点」「待回看」） |
| `created_at` | TIMESTAMP | — |

---

## 三、MCP 工具链分层规范（先读后写）

### 第一阶段：只读工具（Read Tools — 优先跑通）

1. `get_book_metadata(book_id)`：获取书名、总段落数及当前用户的阅读进度（`current_paragraph_index` 与百分比）。
2. `read_context_window(book_id, target_paragraph, window_before=5, window_after=0)`：根据目标段落精准切片读取原文。默认 `window_after=0`（防剧透），仅当明确需要后文逻辑时才传入正数。
3. `search_book_text(book_id, keyword, max_paragraph=None)`：在全书或「已读范围（`<= current_paragraph_index`）」内检索概念首次出现的位置。
4. `list_annotations_and_highlights(book_id, actor_filter="all", paragraph_range=None)`：读取用户在某章节留下的所有重点与批注，让 AI 了解用户的思考轨迹。

### 第二阶段：受控写入工具（Write Tools — 强制绑定 `actor_type="ai"`）

5. `create_ai_annotation(book_id, paragraph_index, selected_text, comment)`：在指定段落生成一条带引用原文的 AI 伴读批注（后端强制写入 `actor_type="ai"`）。
6. `create_ai_bookmark(book_id, paragraph_index, label)`：当用户在讨论中发现关键伏笔或核心论点时，经用户同意后为其添加 AI 标记书签。
