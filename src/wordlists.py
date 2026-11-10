"""加载学术词表，构建"已认识 / 学术词 / 领域词"三类集合。"""
import json
from functools import lru_cache

from src.config import NGSL_KNOWN_LEVELS, WORDLIST_DIR


def _load(name: str) -> dict:
    path = WORDLIST_DIR / f"{name}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def academic_words() -> dict[str, str]:
    """学术词 -> 来源标签（AWL sublist / NAWL）。"""
    result: dict[str, str] = {}

    # NAWL：{headword: [变体]}
    for head, variants in _load("NAWL").items():
        key = head.lower()
        result.setdefault(key, "NAWL")
        for v in variants:
            result.setdefault(v.lower(), "NAWL")

    # AWL：{sublist_N: {headword: {subwords: [...]}}}
    for sublist, heads in _load("AWL").items():
        tag = "AWL-" + sublist.replace("sublist_", "S")
        for head, info in heads.items():
            key = head.lower()
            result.setdefault(key, tag)
            for v in info.get("subwords") or []:
                result.setdefault(v.lower(), tag)

    return result


@lru_cache(maxsize=1)
def known_words() -> set[str]:
    """NGSL 高频段（默认前 3000）视为已认识。"""
    result: set[str] = set()
    ngsl = _load("NGSL")
    for level, heads in ngsl.items():
        if level not in NGSL_KNOWN_LEVELS:
            continue
        for head, variants in heads.items():
            result.add(head.lower())
            for v in variants:
                result.add(v.lower())
    return result


@lru_cache(maxsize=1)
def discipline_words() -> dict[str, str]:
    """学科词表：计算机 / 医学。"""
    result: dict[str, str] = {}
    cswl = _load("CSWL")
    for w in cswl.get("headwords", []):
        result.setdefault(w.lower(), "CS")
    for w in cswl.get("multi-words", []):
        result.setdefault(w.lower(), "CS")

    mawl = _load("MAWL")
    for key, val in mawl.items():
        result.setdefault(key.lower(), "MED")
        if isinstance(val, list):
            for w in val:
                result.setdefault(w.lower(), "MED")
    return result


@lru_cache(maxsize=1)
def frequency_bands() -> dict[str, int]:
    """BNC/COCA 词频档位，数字越小越常见。"""
    result: dict[str, int] = {}
    for level, words in _load("BNC_COCA").items():
        try:
            n = int(level)
        except ValueError:
            continue
        for w in words:
            result.setdefault(w.lower(), n)
    return result


def tags_for(lemma: str) -> dict[str, str | int]:
    """返回一个词的标签信息，供打分与分类使用。"""
    acad = academic_words()
    disc = discipline_words()
    freq = frequency_bands()
    tags: dict[str, str | int] = {}
    if lemma in acad:
        tags["academic"] = acad[lemma]
    if lemma in disc:
        tags["discipline"] = disc[lemma]
    if lemma in freq:
        tags["band"] = freq[lemma]
    return tags
