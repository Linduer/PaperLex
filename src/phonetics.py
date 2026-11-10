"""国际音标补全：领域词典音标 > ECDICT > 复合词成分拼接 > 缩写字母名。

零依赖：不引入 CMUdict/g2p。词典查不到的领域新词，音标手工写进
domain_glossary.tsv 第三列；短语、连字符复合词、缩写全部自动拼接。
"""
from src import manual
from src.dictionary import lemmatize, lookup

# 26 个字母名的 IPA（英式）
LETTER_IPA = {
    "a": "eɪ", "b": "biː", "c": "siː", "d": "diː", "e": "iː", "f": "ef",
    "g": "dʒiː", "h": "eɪtʃ", "i": "aɪ", "j": "dʒeɪ", "k": "keɪ", "l": "el",
    "m": "em", "n": "en", "o": "əʊ", "p": "piː", "q": "kjuː", "r": "ɑː",
    "s": "es", "t": "tiː", "u": "juː", "v": "viː", "w": "ˈdʌbljuː",
    "x": "eks", "y": "waɪ", "z": "zed",
}


def _abbr_ipa(key: str) -> str:
    """FC -> ef siː ；PV-SST -> piː viː es es tiː；fMRI -> ef em ɑː aɪ。"""
    out = []
    for ch in key:
        if ch == "-":
            continue
        ipa = LETTER_IPA.get(ch.lower())
        if not ipa:
            return ""
        out.append(ipa)
    return " ".join(out)


def _word_ipa(word: str) -> str:
    """取词的音标；ECDICT 的屈折形式词条常有空音标，回落到原形再取。"""
    for cand in dict.fromkeys([word, lemmatize(word)]):
        info = lookup(cand)
        if info and info["phonetic"]:
            return info["phonetic"]
    return ""


def compute(key: str, kind: str) -> str:
    """词典没给音标时的自动补全；成分拼不齐则返回空串。"""
    if kind == "abbr":
        return _abbr_ipa(key)
    if "-" in key:
        parts = key.split("-")
    elif kind == "phrase":
        parts = key.split()
    else:
        return ""  # 单个未收录词只能靠领域词典音标列
    out = []
    for p in parts:
        if not p:
            return ""
        hit = manual.get(p) or {}
        ipa = (hit.get("phonetic") or _word_ipa(p)
               or (_abbr_ipa(p) if p.isupper() else ""))
        if not ipa:
            return ""
        out.append(ipa)
    return " ".join(out)


def apply_phonetics(terms):
    """给缺音标的词条补音标；已有音标（含人工 fix）一律不动。"""
    for t in terms:
        if t.get("phonetic"):
            continue
        hit = manual.get(t["key"]) or {}
        ipa = hit.get("phonetic") or compute(t["key"], t["kind"])
        if ipa:
            t["phonetic"] = ipa
    return terms
