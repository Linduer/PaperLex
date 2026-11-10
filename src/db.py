"""术语库：保证同一单词/短语在不同论文里翻译一致。

设计要点：
- terms.key 归一化唯一，第二次遇到同一个词直接复用已有释义
- locked=1 表示已经人工或 AI 精修过，后续不再被词典覆盖
"""
import sqlite3
from datetime import datetime, timezone

from src.config import GLOSSARY_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    path     TEXT UNIQUE NOT NULL,
    title    TEXT,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS terms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT UNIQUE NOT NULL,
    display     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    phonetic    TEXT,
    translation TEXT,
    category    TEXT,
    source      TEXT,
    locked      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS occurrences (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id  INTEGER NOT NULL,
    paper_id INTEGER NOT NULL,
    sentence TEXT,
    freq     INTEGER NOT NULL DEFAULT 1,
    UNIQUE (term_id, paper_id)
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(GLOSSARY_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def add_paper(conn, path, title=""):
    """登记论文，返回 paper_id。已存在则复用。"""
    row = conn.execute("SELECT id FROM papers WHERE path = ?", (path,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO papers (path, title, added_at) VALUES (?, ?, ?)",
        (path, title, _now()),
    )
    conn.commit()
    return cur.lastrowid


def get_term(conn, key):
    row = conn.execute("SELECT * FROM terms WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def upsert_term(conn, key, display, kind, phonetic="", translation="",
                category="", source=""):
    """写入或更新术语。已有且未锁定时，只在原释义为空时补新释义。"""
    old = get_term(conn, key)
    if old:
        # 已锁定：完全不动；未锁定：只补空缺字段，绝不改写已有释义
        if not old["locked"]:
            new_phonetic = old["phonetic"] or phonetic
            new_translation = old["translation"] or translation
            new_category = old["category"] or category
            if (new_phonetic, new_translation, new_category) != (
                old["phonetic"], old["translation"], old["category"]
            ):
                conn.execute(
                    """UPDATE terms SET phonetic = ?, translation = ?,
                       category = ?, updated_at = ? WHERE id = ?""",
                    (new_phonetic, new_translation, new_category,
                     _now(), old["id"]),
                )
                conn.commit()
                old = get_term(conn, key)
        return old

    conn.execute(
        """INSERT INTO terms (key, display, kind, phonetic, translation,
           category, source, locked, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (key, display, kind, phonetic, translation, category, source,
         _now(), _now()),
    )
    conn.commit()
    return get_term(conn, key)


def link_occurrence(conn, term_id, paper_id, sentence="", freq=1):
    """登记某术语在某篇论文中的出现次数与例句。"""
    conn.execute(
        """INSERT INTO occurrences (term_id, paper_id, sentence, freq)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(term_id, paper_id) DO UPDATE SET
             freq = excluded.freq,
             sentence = CASE WHEN excluded.sentence != ''
                             THEN excluded.sentence ELSE sentence END""",
        (term_id, paper_id, sentence, freq),
    )
    conn.commit()


def terms_of_paper(conn, paper_id):
    """按论文取术语，连同出现次数和例句。"""
    rows = conn.execute(
        """SELECT t.*, o.freq, o.sentence
           FROM occurrences o JOIN terms t ON t.id = o.term_id
           WHERE o.paper_id = ?""",
        (paper_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_translation(conn, key, translation, category="", phonetic="", lock=True):
    """人工/AI 精修术语：写入并加锁，保证以后不被自动流程覆盖。"""
    old = get_term(conn, key)
    if not old:
        return None
    conn.execute(
        """UPDATE terms SET translation = ?, category = ?, phonetic = ?,
           locked = ?, updated_at = ? WHERE id = ?""",
        (translation, category or old["category"], phonetic or old["phonetic"],
         1 if lock else 0, _now(), old["id"]),
    )
    conn.commit()
    return get_term(conn, key)


def all_terms(conn):
    rows = conn.execute("SELECT * FROM terms ORDER BY id").fetchall()
    return [dict(r) for r in rows]
