"""领域词典加载：data/domain_glossary.tsv，优先级最高。

key 不区分大小写，与术语库 key 一致；TAB 分列：key<TAB>释义[<TAB>音标]。
用户随时补行，import 与 build 都会自动套用，不需要动数据库。
"""
from functools import lru_cache

from src.config import DATA_DIR

PATH = DATA_DIR / "domain_glossary.tsv"


@lru_cache(maxsize=1)
def load() -> dict:
    out = {}
    if not PATH.exists():
        return out
    with open(PATH, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) >= 2 and parts[1]:
                out[parts[0].lower()] = {
                    "translation": parts[1],
                    "phonetic": parts[2] if len(parts) >= 3 else "",
                }
    return out


def get(key: str) -> dict | None:
    """查领域词典，返回 {translation, phonetic}，查不到返回 None。"""
    return load().get((key or "").strip().lower())

