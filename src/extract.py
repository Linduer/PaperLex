"""PDF -> 候选词条（单词 / 缩写 / 短语）。"""
import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from src.config import MAX_TERMS_PER_PAPER
from src.dictionary import lemmatize, lookup
from src.wordlists import academic_words, discipline_words, known_words, tags_for

# 功能词：不参与短语抽取，也不作为候选
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "there", "here", "it", "its", "they", "them", "their",
    "we", "our", "us", "you", "your", "he", "she", "his", "her", "i", "my",
    "is", "are", "was", "were", "be", "been", "being", "am", "do", "does",
    "did", "done", "have", "has", "had", "will", "would", "can", "could",
    "may", "might", "must", "shall", "should", "to", "of", "in", "on", "at",
    "by", "for", "with", "from", "as", "into", "onto", "over", "under",
    "between", "among", "through", "during", "before", "after", "above",
    "below", "up", "down", "out", "off", "about", "against", "along",
    "across", "around", "upon", "via", "per", "both", "each", "all", "any",
    "some", "no", "not", "nor", "so", "such", "very", "too", "also", "only",
    "just", "even", "more", "most", "less", "least", "many", "much", "few",
    "several", "other", "another", "same", "own", "which", "who", "whom",
    "whose", "what", "when", "where", "why", "how", "whether", "while",
    "because", "although", "though", "since", "unless", "until", "whereas",
    "however", "therefore", "thus", "hence", "moreover", "furthermore",
    "nevertheless", "nonetheless", "consequently", "additionally", "finally",
    "first", "second", "third", "et", "al", "etc", "e", "g", "i", "ie",
    "eg", "vs", "fig", "figs", "table", "tables", "eq", "eqs", "supp",
    "supplementary", "https", "http", "doi", "www", "org", "com", "edu",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "hundred", "thousand", "million", "billion", "percent", "mol",
    "without", "within", "toward", "towards", "versus", "via", "well",
    "upon", "across", "along", "among", "throughout", "whilst", "onto",
    # 期刊页眉/版权行套话（Nature Neuroscience | Volume 29 | April 2026）
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
    "volume", "vol", "issue", "article", "pages", "pp",
    # 图注里的固定句式，切块时直接断开
    "box", "boxes", "line", "lines", "arrow", "arrows", "dashed", "solid",
    "shading", "indicate", "indicates", "indicated", "represent", "represents",
    "represented", "show", "shows", "shown", "denote", "denotes", "error",
    "bars", "whiskers", "asterisk", "asterisks", "panel", "panels",
}

# 缩写白名单外的常见噪声
ABBR_BLACKLIST = {
    "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII", "XIII",
    "FIG", "FIGS", "TABLE", "EQ", "REF", "REFS", "DOI", "HTTP", "HTTPS",
    "WWW", "PDF", "USA", "UK", "EU", "SI", "SD", "SEM", "CI", "ANOVA",
}

# 常见"假缩写"（其实是正文大写词）
ROMAN = re.compile(r"^[IVX]+$")

# 期刊栏目名之类的固定短语，学不到东西
PHRASE_BLACKLIST = {
    "source data", "extended data", "data availability", "code availability",
    "peer review", "author contributions", "competing interests",
    "supplementary information", "reporting summary", "open access",
    "nature neuroscience", "source data file", "full statistical reporting",
    "data point", "data points", "human individuals", "human macaque",
    "individuals macaque", "macaque data", "empirical simulated",
}

# PDF 残渣与工具名/期刊名，直接丢弃
NOISE = {
    "modele", "specie", "ref", "refs", "coop", "compet", "neuroimage",
    "neurosynth", "et", "al", "fig", "table", "github", "matlab",
    "eneuro", "zenodo", "figshare",
    "simil",  # PDF 断词残渣（similar 被切）
    # 本领域高频作者人名，会被当成生词混进来
    "kringelbach", "deco",
}

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
ABBR_RE = re.compile(r"\b[A-Z][A-Za-z]{1,5}s?\b")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
POSSESSIVE = re.compile(r"['’]s?$")
MIXED_ABBR = re.compile(r"^[a-z]{0,2}[A-Z]{2,}[A-Za-z]{0,3}$")

# 词形还原会把这些词还原成另一副面孔，这里强制保留原形
LEMMA_OVERRIDE = {
    "specie": "species",
    "species": "species",
    "series": "series",
    "means": "mean",
}

# 正文内引用 "Froudist-Walsh et al."：姓氏收集成噪声，防止人名混进生词
CITE_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:-[A-Z][a-z]{2,})?)\s+et\s+al\b")


def _author_names(text: str) -> set:
    names = set()
    for m in CITE_NAME_RE.finditer(text):
        for part in m.group(1).split("-"):
            names.add(part.lower())
    return names


def _is_abbr(token: str) -> bool:
    """纯大写缩写（LTP）或混合大小写缩写（fMRI、mRNA）。"""
    if token.isupper() and 2 <= len(token) <= 6:
        return token not in ABBR_BLACKLIST and not ROMAN.match(token)
    return bool(MIXED_ABBR.match(token))


def normalize_token(token: str, noise: set | None = None) -> str | None:
    """清洗单个 token：去所有格、还原词形、修复 PDF 断词残渣。"""
    noise = NOISE if noise is None else noise
    t = token.lower().replace("’", "'")
    t = POSSESSIVE.sub("", t).strip("'-")
    t = LEMMA_OVERRIDE.get(t, t)
    if len(t) < 3 or t in STOPWORDS or t in noise or not t.isalpha():
        return None

    lemma = LEMMA_OVERRIDE.get(lemmatize(t), lemmatize(t))
    if lemma in noise:
        return None
    if lookup(lemma):
        return lemma
    # PDF 断词常把词尾吃掉，这里做有限修复
    for cand in (lemma + "e", lemma + "s", lemma + "y",
                 lemma[:-1] if lemma.endswith("s") else ""):
        if cand and len(cand) >= 3 and cand not in noise and lookup(cand):
            return cand
    return lemma


@dataclass
class Candidate:
    key: str
    kind: str  # word / phrase / abbr
    freq: int = 0
    surfaces: Counter = field(default_factory=Counter)
    contexts: list[str] = field(default_factory=list)
    score: float = 0.0
    tags: dict = field(default_factory=dict)

    @property
    def surface(self) -> str:
        return self.surfaces.most_common(1)[0][0] if self.surfaces else self.key


# ---------------------------------------------------------------- PDF 读取

def _norm_header(line: str) -> str:
    """把数字统一替换掉，便于识别每页重复的页眉页脚（页码会变）。"""
    return re.sub(r"\d+", "#", line.strip())


def _strip_boilerplate(pages: list[str]) -> list[str]:
    """删掉每页重复出现的页眉页脚与纯页码行。"""
    n = len(pages)
    if n < 3:
        return pages
    counter: Counter = Counter()
    for page in pages:
        lines = [l.strip() for l in page.splitlines() if l.strip()]
        for l in set(lines[:3] + lines[-3:]):
            if len(l) < 120:
                counter[_norm_header(l)] += 1
    boiler = {l for l, c in counter.items() if c >= n * 0.5}

    cleaned = []
    for page in pages:
        lines = []
        for line in page.splitlines():
            s = line.strip()
            if not s:
                continue
            if _norm_header(s) in boiler:
                continue
            if re.fullmatch(r"\d{1,4}", s):
                continue
            lines.append(line)
        cleaned.append("\n".join(lines))
    return cleaned


def _cut_references(text: str) -> str:
    """切掉参考文献区。取第一个位于正文后 40% 之后的标题（双语版会中英各出现一次，
    取最后一个会把中间整段翻译版参考文献留在正文里）。"""
    pattern = re.compile(r"\n\s*(?:\d+\.?\s*)?(References|REFERENCES|Bibliography|BIBLIOGRAPHY|参考文献)\s*:?\s*\n")
    hits = [h for h in pattern.finditer(text) if h.start() > len(text) * 0.40]
    if hits:
        return text[: hits[0].start()]
    return text


def read_pdf(path: str) -> tuple[str, int]:
    doc = pymupdf.open(path)
    pages = [p.get_text("text") for p in doc]
    pages = _strip_boilerplate(pages)
    text = "\n".join(pages)
    # en/em dash 统一成连字符：local–global 否则会被拆成两个词
    text = text.replace("–", "-").replace("—", "-")
    # 合并 PDF 断词：connec-\ntivity -> connectivity
    text = re.sub(r"([A-Za-z])-\n([a-z])", r"\1\2", text)
    text = _cut_references(text)
    return text, len(doc)


def guess_title(text: str) -> str:
    for line in text.splitlines()[:15]:
        line = line.strip()
        if (20 < len(line) < 200 and "|" not in line
                and "nature neuroscience" not in line.lower()
                and not line.lower().startswith(("www", "http", "doi"))):
            return line
    return "未命名论文"


# ---------------------------------------------------------------- 抽取

def _sentences(text: str) -> list[str]:
    parts = SENT_SPLIT.split(text)
    return [re.sub(r"\s+", " ", p).strip() for p in parts if len(p) > 20]


def _has_subseq(needle: str, hay: str) -> bool:
    """needle 的字母是否按序出现在 hay 里（不必连续）。"""
    it = iter(hay)
    return all(ch in it for ch in needle)


def _find_full_form(words: list[str], abbr: str) -> str:
    """在缩写左侧的词里，按首字母序列反查全称。允许跳词，取最短跨度。"""
    target = abbr.lower()
    best: list[str] | None = None
    start_min = max(0, len(words) - 16)
    for start in range(start_min, len(words)):
        j = 0
        end = None
        for i in range(start, len(words)):
            if i - start > 10:
                break
            if j < len(target) and words[i][0].lower() == target[j]:
                j += 1
                if j == len(target):
                    end = i
                    break
        if end is None:
            continue
        span = words[start : end + 1]
        if best is None or len(span) < len(best):
            best = span
    if best:
        return " ".join(best)

    # 首字母匹配不到时，退一步：单个长词内含缩写全部字母
    # somatostatin (SST)、parvalbumin (PV) 这类单词式全称
    for w in reversed(words):
        lw = w.lower()
        if len(lw) >= 5 and lw.isalpha() and _has_subseq(target, lw):
            if lookup(lw) or len(lw) >= 7:
                return w
    return ""


def _abbr_full_forms(text: str) -> dict[str, str]:
    """从 "long-term potentiation (LTP)" 这类写法里提取缩写全称。"""
    mapping: dict[str, str] = {}
    pattern = re.compile(r"([A-Za-z][A-Za-z\s\-]{4,160}?)\s*\(([A-Za-z]{2,8})s?\)")
    for m in pattern.finditer(text):
        full, abbr = m.group(1).strip(), m.group(2)
        if not abbr.isupper() and not MIXED_ABBR.match(abbr):
            continue
        words = [w for w in re.split(r"[\s\-]+", full) if w and w.isalpha()]
        if not words:
            continue
        found = _find_full_form(words, abbr)
        if found:
            mapping.setdefault(abbr, found)
    return mapping


def extract(pdf_path: str) -> dict:
    text, page_count = read_pdf(pdf_path)
    sentences = _sentences(text)
    known = known_words()
    academic = academic_words()
    discipline = discipline_words()
    abbr_forms = _abbr_full_forms(text)
    noise = NOISE | _author_names(text)

    words: dict[str, Candidate] = {}
    phrases: dict[str, Candidate] = {}
    abbrs: dict[str, Candidate] = {}

    for sent in sentences:
        tokens = TOKEN_RE.findall(sent)
        if not tokens:
            continue
        ctx = sent[:240]

        # ---- 单词与缩写 ----
        for raw in tokens:
            if _is_abbr(raw):
                if raw.lower() in noise:
                    continue
                c = abbrs.setdefault(raw, Candidate(key=raw, kind="abbr"))
                c.freq += 1
                c.surfaces[raw] += 1
                if len(c.contexts) < 2:
                    c.contexts.append(ctx)
                continue

            if "-" in raw:
                parts = [p for p in raw.split("-") if p]
                low_parts = [p.lower() for p in parts]
                # 复合词里夹停用词或噪声（cooperative-only）直接不收
                if (len(parts) >= 2 and all(len(p) >= 3 for p in parts)
                        and not any(p in STOPWORDS or p in noise for p in low_parts)):
                    key = "-".join(low_parts)
                    c = words.setdefault(key, Candidate(key=key, kind="word"))
                    c.freq += 1
                    c.surfaces[raw.lower()] += 1
                    if len(c.contexts) < 2:
                        c.contexts.append(ctx)
                for p in parts:
                    lemma = normalize_token(p, noise)
                    if not lemma:
                        continue
                    c = words.setdefault(lemma, Candidate(key=lemma, kind="word"))
                    c.freq += 1
                    c.surfaces[p.lower()] += 1
                    if len(c.contexts) < 2:
                        c.contexts.append(ctx)
                continue

            lemma = normalize_token(raw, noise)
            if not lemma:
                continue
            c = words.setdefault(lemma, Candidate(key=lemma, kind="word"))
            c.freq += 1
            c.surfaces[raw.lower()] += 1
            if len(c.contexts) < 2:
                c.contexts.append(ctx)

        # ---- 短语（2~3 gram，用停用词切块）----
        chunk: list[str] = []
        for raw in tokens + ["."]:
            low = raw.lower().strip("'-")
            # 缩写不参与短语成分（否则会出现 sst sst 这类碎片）
            if _is_abbr(raw) or low in STOPWORDS or not low.isalpha():
                if 2 <= len(chunk) <= 3:
                    key = " ".join(chunk)
                    c = phrases.setdefault(key, Candidate(key=key, kind="phrase"))
                    c.freq += 1
                    c.surfaces[key] += 1
                    if len(c.contexts) < 2:
                        c.contexts.append(ctx)
                # 超过 3 个词的块，取所有 2~3 连续组合
                elif len(chunk) > 3:
                    for i in range(len(chunk) - 1):
                        for n in (2, 3):
                            if i + n <= len(chunk):
                                key = " ".join(chunk[i : i + n])
                                c = phrases.setdefault(key, Candidate(key=key, kind="phrase"))
                                c.freq += 1
                                c.surfaces[key] += 1
                                if len(c.contexts) < 2:
                                    c.contexts.append(ctx)
                chunk = []
            elif len(low) >= 3:
                chunk.append(low)

    # ---- 复数归并：connectomes -> connectome ----
    for k in list(words):
        if k.endswith("s") and len(k) > 4 and k[:-1] in words:
            base, cur = words[k[:-1]], words[k]
            base.freq += cur.freq
            base.surfaces.update(cur.surfaces)
            for c_ in cur.contexts:
                if len(base.contexts) < 2 and c_ not in base.contexts:
                    base.contexts.append(c_)
            del words[k]

    # ---- 打分筛选 ----
    selected: list[Candidate] = []
    word_pool: list[Candidate] = []
    abbr_pool: list[Candidate] = []
    phrase_pool: list[Candidate] = []

    for c in words.values():
        if c.freq < 2:
            continue
        if c.key in known:
            # NGSL 前 3000 视为已认识，直接淘汰
            continue
        t = tags_for(c.key)
        c.tags = t
        band = t.get("band")
        score = 0.0
        # 1) 论文内高频（开方压缩，避免超高频词通吃）
        score += min(c.freq, 40) ** 0.5
        # 2) 罕见度：BNC/COCA 档位越高越生僻，未收录则视为领域词
        if band is None:
            score += 4.0
        else:
            b = int(band)
            if b >= 7000:
                score += 3.5
            elif b >= 6000:
                score += 3.0
            elif b >= 5000:
                score += 2.5
            elif b >= 4000:
                score += 1.5
            elif b >= 3000:
                score += 0.5
        # 3) 学术/学科标记（只作轻量加分，不做主导）
        if "academic" in t:
            score += 0.8
        if "discipline" in t:
            score += 1.0
        # 4) 长词通常更专业
        if len(c.key) >= 8:
            score += 0.8
        # 5) 词典未收录 -> 多半是本领域的专门概念
        if lookup(c.key) is None:
            score += 1.5
        c.score = score
        if score >= 4.0:
            word_pool.append(c)

    for c in abbrs.values():
        if c.freq < 2:
            continue
        c.tags = {"abbr": abbr_forms.get(c.key.upper(), "")}
        c.score = 5.0 + min(c.freq, 10) * 0.3
        abbr_pool.append(c)

    for c in phrases.values():
        n_words = len(c.key.split())
        min_freq = 3 if n_words == 2 else 8
        if c.freq < min_freq:
            continue
        parts = c.key.split()
        if any(p in STOPWORDS or p in noise for p in parts):
            continue
        # 副词开头的组合（significantly higher）是统计套话，不是术语
        first_info = lookup(parts[0])
        if first_info and (first_info["pos"].lower().startswith("adv")
                           or first_info["translation"].lstrip().lower().startswith("adv")):
            continue
        if all(p in known and p not in academic and p not in discipline for p in parts):
            continue
        if len(c.key) > 45:
            continue
        if c.key in PHRASE_BLACKLIST:
            continue
        c.tags = {"phrase": True}
        c.score = 4.0 + min(c.freq, 30) ** 0.5 * 0.7
        phrase_pool.append(c)

    # 重叠 n-gram 去重，长优先：local global 会被更具体的
    # local global hierarchy 替换；单复数也算重叠（competitive interaction/interactions）
    phrase_pool.sort(key=lambda x: (-x.freq, -x.score, x.key))
    kept: dict[str, Candidate] = {}
    for c in phrase_pool:
        padded = f" {c.key} "
        if any(padded in f" {k} " for k in kept):
            continue
        alt = c.key[:-1] if c.key.endswith("s") else c.key + "s"
        if alt in kept:
            continue
        for k in [k for k in kept if f" {k} " in padded]:
            del kept[k]
        kept[c.key] = c
    phrase_pool = list(kept.values())

    for pool in (word_pool, abbr_pool, phrase_pool):
        pool.sort(key=lambda x: (-x.score, -x.freq, x.key))

    # 配额：保证短语和缩写都有位置，不被单词通吃
    total = MAX_TERMS_PER_PAPER
    n_abbr = min(len(abbr_pool), max(3, int(total * 0.12)))
    n_phrase = min(len(phrase_pool), max(5, int(total * 0.30)))
    n_word = max(0, total - n_abbr - n_phrase)
    selected = word_pool[:n_word] + abbr_pool[:n_abbr] + phrase_pool[:n_phrase]
    selected.sort(key=lambda x: (-x.score, -x.freq, x.key))
    return {
        "title": guess_title(text),
        "page_count": page_count,
        "char_count": len(text),
        "sentences": len(sentences),
        "candidates": selected,
    }


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not path:
        print("用法：python -m src.extract 论文.pdf")
        sys.exit(1)
    r = extract(path)
    print(f"标题: {r['title']}")
    print(f"页数: {r['page_count']} 字符: {r['char_count']} 候选: {len(r['candidates'])}")
    for c in r["candidates"][:60]:
        print(f"  {c.score:5.2f} {c.freq:4d} [{c.kind}] {c.key}  {c.tags}")
