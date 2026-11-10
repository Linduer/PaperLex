"""ECDICT 查询封装：词形还原 + 释义清洗。"""
import re
import sqlite3
from functools import lru_cache

from src.config import ECDICT_DB

_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(ECDICT_DB, check_same_thread=False)
    return _conn


@lru_cache(maxsize=100_000)
def lemmatize(word: str) -> str:
    """把任意变形还原为原形；查不到则原样返回。"""
    w = word.strip().lower()
    row = _db().execute("SELECT lemma FROM lemma WHERE variant = ?", (w,)).fetchone()
    return row[0] if row else w


@lru_cache(maxsize=100_000)
def lookup(word: str) -> dict | None:
    """查词，返回音标/释义/考试标签/词频。"""
    w = word.strip().lower()
    row = _db().execute(
        "SELECT word, phonetic, translation, pos, tag, bnc, frq FROM ecdict WHERE word = ?",
        (w,),
    ).fetchone()
    if not row:
        return None
    return {
        "word": row[0],
        "phonetic": row[1] or "",
        "translation": row[2] or "",
        "pos": row[3] or "",
        "tag": row[4] or "",
        "bnc": row[5] or 0,
        "frq": row[6] or 0,
    }


def clean_translation(text: str, max_items: int = 3) -> str:
    """把 ECDICT 的长释义压缩成适合卡片显示的一行。

    规则：丢掉 [网络] 来源的行，保留词性开头的义项，最多 max_items 条。
    """
    if not text:
        return ""
    raw = text.replace("\\n", "\n").split("\n")
    items = []
    for line in raw:
        line = line.strip()
        if not line or line.startswith("[网络]"):
            continue
        # 一条里可能塞了多个分号义项，只保留前两段避免过长
        items.append(line)
        if len(items) >= max_items:
            break
    result = "；".join(items)
    result = re.sub(r"\s+", " ", result)
    if len(result) > 90:
        result = result[:90].rstrip("；;，, ") + "…"
    return result


def is_known_word(word: str) -> bool:
    """词典里完全查不到 → 可能是专有名词/拼写错误/生造词。"""
    return lookup(lemmatize(word)) is not None
