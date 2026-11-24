# -*- coding: utf-8 -*-
"""数据库层：SQLite 存书籍、段落、阅读进度、批注、伴读对话。

表设计（对齐「阅读器 + MCP」架构）：
- books:        书籍（标题、作者、全文段落数、创建时间）
- paragraphs:   段落（book_id 外键、序号、正文、类型 kind）
- progress:     阅读进度（user_id + actor_type + book_id + 段落序号 + 更新时间）
- annotations:  批注/重点（user_id + actor_type + book_id + 段落序号 + 选中文字 + 笔记）
- chat_history: 伴读对话记录（按书留存，区分 user/assistant）
"""
import sqlite3
import pathlib
import uuid
import datetime

HERE = pathlib.Path(__file__).parent
DB_PATH = HERE / "reader.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn, table: str, col: str, ddl: str):
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS books (
        id          TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        author      TEXT DEFAULT '',
        para_count  INTEGER DEFAULT 0,
        created_at  TEXT
    );
    CREATE TABLE IF NOT EXISTS paragraphs (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id  TEXT NOT NULL,
        seq      INTEGER NOT NULL,
        text     TEXT NOT NULL,
        kind     TEXT DEFAULT 'p'
    );
    CREATE INDEX IF NOT EXISTS idx_para_book ON paragraphs(book_id, seq);

    CREATE TABLE IF NOT EXISTS progress (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    TEXT NOT NULL DEFAULT 'local',
        actor_type TEXT NOT NULL DEFAULT 'user',
        book_id    TEXT NOT NULL,
        para_seq   INTEGER NOT NULL,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS annotations (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    TEXT NOT NULL DEFAULT 'local',
        actor_type TEXT NOT NULL DEFAULT 'user',
        book_id    TEXT NOT NULL,
        para_seq   INTEGER NOT NULL,
        selected   TEXT NOT NULL,
        note       TEXT DEFAULT '',
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS chat_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    TEXT NOT NULL DEFAULT 'local',
        book_id    TEXT NOT NULL,
        role       TEXT NOT NULL,
        selected   TEXT DEFAULT '',
        content    TEXT NOT NULL,
        created_at TEXT
    );
    """)
    # 兼容旧库迁移
    _ensure_column(conn, "progress", "actor_type", "TEXT NOT NULL DEFAULT 'user'")
    _ensure_column(conn, "annotations", "actor_type", "TEXT NOT NULL DEFAULT 'user'")
    conn.commit()
    conn.close()


init_db()


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def create_book(title: str, paragraphs: list, author: str = "") -> str:
    book_id = uuid.uuid4().hex[:12]
    conn = get_conn()
    conn.execute(
        "INSERT INTO books(id, title, author, para_count, created_at) VALUES(?,?,?,?,?)",
        (book_id, title, author, len(paragraphs), now()),
    )
    for i, item in enumerate(paragraphs):
        if isinstance(item, str):
            text, kind = item, "p"
        else:
            text = item.get("text", "")
            kind = item.get("kind", "p")
        conn.execute(
            "INSERT INTO paragraphs(book_id, seq, text, kind) VALUES(?,?,?,?)",
            (book_id, i, text, kind),
        )
    conn.commit()
    conn.close()
    return book_id


def get_book(book_id: str):
    conn = get_conn()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    paras = conn.execute(
        "SELECT seq, text, kind FROM paragraphs WHERE book_id=? ORDER BY seq",
        (book_id,),
    ).fetchall()
    conn.close()
    if not book:
        return None
    return {
        "id": book["id"],
        "title": book["title"],
        "author": book["author"],
        "para_count": book["para_count"],
        "created_at": book["created_at"],
        "paragraphs": [{"seq": p["seq"], "text": p["text"], "kind": p["kind"] or "p"} for p in paras],
    }


def list_books():
    conn = get_conn()
    rows = conn.execute(
        """SELECT b.id, b.title, b.author, b.para_count, b.created_at,
                  (SELECT p.para_seq FROM progress p
                   WHERE p.book_id = b.id ORDER BY p.id DESC LIMIT 1) AS last_seq
           FROM books b ORDER BY b.created_at DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_book(book_id: str) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM books WHERE id=?", (book_id,))
    conn.execute("DELETE FROM paragraphs WHERE book_id=?", (book_id,))
    conn.execute("DELETE FROM progress WHERE book_id=?", (book_id,))
    conn.execute("DELETE FROM annotations WHERE book_id=?", (book_id,))
    conn.execute("DELETE FROM chat_history WHERE book_id=?", (book_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def save_progress(user_id: str, book_id: str, para_seq: int, actor_type: str = "user"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO progress(user_id, actor_type, book_id, para_seq, updated_at) VALUES(?,?,?,?,?)",
        (user_id, actor_type, book_id, para_seq, now()),
    )
    conn.execute(
        """DELETE FROM progress WHERE user_id=? AND book_id=? AND id NOT IN
           (SELECT id FROM progress WHERE user_id=? AND book_id=? ORDER BY id DESC LIMIT 1)""",
        (user_id, book_id, user_id, book_id),
    )
    conn.commit()
    conn.close()


def get_progress(user_id: str, book_id: str):
    conn = get_conn()
    r = conn.execute(
        "SELECT para_seq, actor_type, updated_at FROM progress WHERE user_id=? AND book_id=? ORDER BY id DESC LIMIT 1",
        (user_id, book_id),
    ).fetchone()
    conn.close()
    return {"para_seq": r["para_seq"], "actor_type": r["actor_type"], "updated_at": r["updated_at"]} if r else None


def add_annotation(
    user_id: str, book_id: str, para_seq: int, selected: str, note: str = "", actor_type: str = "user"
) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO annotations(user_id, actor_type, book_id, para_seq, selected, note, created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (user_id, actor_type, book_id, para_seq, selected, note, now()),
    )
    ann_id = cur.lastrowid
    conn.commit()
    conn.close()
    return ann_id


def get_annotations(user_id: str, book_id: str, include_ai: bool = True):
    conn = get_conn()
    if include_ai:
        rows = conn.execute(
            """SELECT id, user_id, actor_type, para_seq, selected, note, created_at
               FROM annotations WHERE book_id=? AND (user_id=? OR actor_type='ai') ORDER BY para_seq, id""",
            (book_id, user_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, user_id, actor_type, para_seq, selected, note, created_at
               FROM annotations WHERE book_id=? AND user_id=? ORDER BY para_seq, id""",
            (book_id, user_id),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_annotation(ann_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM annotations WHERE id=?", (ann_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def add_chat_message(user_id: str, book_id: str, role: str, content: str, selected: str = ""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO chat_history(user_id, book_id, role, selected, content, created_at) VALUES(?,?,?,?,?,?)",
        (user_id, book_id, role, selected, content, now()),
    )
    conn.commit()
    conn.close()


def get_chat_history(user_id: str, book_id: str, limit: int = 30):
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, role, selected, content, created_at FROM chat_history
           WHERE user_id=? AND book_id=? ORDER BY id DESC LIMIT ?""",
        (user_id, book_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]
