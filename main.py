"""命令行入口：导入论文 -> 入术语库 -> 生成发音 -> 打包 Anki。

用法：
    python main.py import 论文.pdf 或 目录
    python main.py build
    python main.py stats
    python main.py fix connectome "连接组：神经元之间的完整连接图谱"
    python main.py remove kringelbach
    python main.py web            # 网页版背单词（浏览器打开 http://127.0.0.1:8000）

领域新词、错译、论文没展开的缩写，直接补到 data/domain_glossary.tsv
（key<TAB>释义，一行一条，不区分大小写），import/build 自动套用。
"""
import argparse
import glob
import os
import re
import sys
from collections import defaultdict

from src import anki, db, extract, manual, phonetics, tts
from src.config import OUTPUT_DIR
from src.dictionary import clean_translation, lemmatize, lookup

CATEGORY_ORDER = [
    "缩写", "短语搭配", "领域术语", "计算机术语", "医学术语",
    "学术词汇 AWL", "学术词汇 NAWL", "进阶词汇",
]


# ---------------------------------------------------------------- 释义补全

def _first_sense(text: str) -> str:
    """取释义的第一个义项，用于拼接短语。"""
    text = clean_translation(text, max_items=1)
    text = re.sub(r"^[a-z]+\.\s*", "", text)
    text = re.split(r"[；;，,]", text)[0]
    return text.strip(" 。.")


def _phrase_gloss(key: str) -> str:
    """短语查不到词典时，用成分词义拼一个可读的中文提示。

    某个成分查不到释义（多半是领域新词）就保留英文原词，避免出现半截释义。
    """
    parts = []
    for word in key.split():
        info = lookup(word) or lookup(lemmatize(word))
        sense = _first_sense(info["translation"]) if info else ""
        parts.append(sense or word)
    return " · ".join(parts)


def _abbr_gloss(cand) -> str:
    """缩写优先用论文里给出的全称，避免 ECDICT 把它解释成别的领域（FC=消防）。"""
    full = (cand.tags or {}).get("abbr", "") or ""
    info = lookup(cand.key.lower())
    base = clean_translation(info["translation"]) if info else ""

    if not full:
        return base

    first = full.split()[0].lower()
    first_info = lookup(first) or lookup(lemmatize(first))
    first_zh = _first_sense(first_info["translation"]) if first_info else ""

    # 词典释义的第一个义项里出现了全称首词的中文，说明领域对得上，保留词典释义。
    # 只查第一个义项：FC 的 ECDICT 释义里恰好有"功能码"，全文匹配会误判。
    base_first = base.split("；")[0] if base else ""
    if base and first_zh and first_zh[:2] in base_first:
        return f"{base}（{full}）"

    gloss = _phrase_gloss(full)
    return f"{gloss}（{full}）" if gloss else f"（{full}）"


def _translation_of(cand) -> str:
    hit = manual.get(cand.key)
    if hit:
        return hit["translation"]
    if cand.kind == "phrase":
        info = lookup(cand.key)
        if info:
            return clean_translation(info["translation"])
        return _phrase_gloss(cand.key)
    if cand.kind == "abbr":
        return _abbr_gloss(cand)
    info = lookup(cand.key) or lookup(lemmatize(cand.key))
    return clean_translation(info["translation"]) if info else ""


def _phonetic_of(cand) -> str:
    hit = manual.get(cand.key)
    if hit and hit["phonetic"]:
        return hit["phonetic"]
    if cand.kind != "phrase":
        info = lookup(cand.key)
        if info and info["phonetic"]:
            return info["phonetic"]
    return phonetics.compute(cand.key, cand.kind)


def _category_of(cand) -> str:
    tags = cand.tags or {}
    if cand.kind == "abbr":
        return "缩写"
    if cand.kind == "phrase":
        return "短语搭配"
    if tags.get("discipline") == "CS":
        return "计算机术语"
    if tags.get("discipline") == "MED":
        return "医学术语"
    if tags.get("academic"):
        return f"学术词汇 {tags['academic']}" if tags["academic"] != "NAWL" else "学术词汇 NAWL"
    if tags.get("band") is None:
        return "领域术语"
    return "进阶词汇"


def _source_of(cand) -> str:
    if cand.kind == "phrase" and not lookup(cand.key):
        return "成分释义"
    return "ECDICT" if lookup(cand.key) else "待补充"


# ---------------------------------------------------------------- 导入

def collect_pdfs(paths):
    result = []
    for p in paths:
        if os.path.isdir(p):
            result.extend(glob.glob(os.path.join(p, "**", "*.pdf"), recursive=True))
        elif p.lower().endswith(".pdf"):
            result.append(p)
    return sorted(dict.fromkeys(result))


def cmd_import(args):
    conn = db.connect()
    pdfs = collect_pdfs(args.paths)
    if not pdfs:
        print("没有找到 PDF 文件。")
        return 1

    total_new = 0
    for path in pdfs:
        print(f"\n[导入] {os.path.basename(path)}")
        try:
            result = extract.extract(path)
        except Exception as exc:
            print(f"  解析失败：{exc}")
            continue

        paper_id = db.add_paper(conn, os.path.abspath(path), result["title"])
        print(f"  {result['page_count']} 页 / {result['char_count']} 字符 / "
              f"{len(result['candidates'])} 个候选")

        new_count = 0
        empty = 0
        for cand in result["candidates"]:
            translation = _translation_of(cand)
            if not translation:
                empty += 1
            existing = db.get_term(conn, cand.key)
            source = "领域词典" if manual.get(cand.key) else _source_of(cand)
            term = db.upsert_term(
                conn, cand.key, cand.key, cand.kind,
                _phonetic_of(cand), translation,
                _category_of(cand), source,
            )
            if existing is None:
                new_count += 1
            sentence = cand.contexts[0] if cand.contexts else ""
            db.link_occurrence(conn, term["id"], paper_id, sentence, cand.freq)

        total_new += new_count
        print(f"  新增术语 {new_count} 个，复用 {len(result['candidates']) - new_count} 个，"
              f"暂无释义 {empty} 个")

    print(f"\n完成：共新增 {total_new} 条术语。")
    return 0


# ---------------------------------------------------------------- 生成卡包

def fetch_terms(conn, paper_id=None):
    sql = """SELECT t.*, o.sentence, o.freq FROM terms t
             LEFT JOIN occurrences o ON o.term_id = t.id"""
    params = ()
    if paper_id:
        sql += " JOIN papers p ON p.id = o.paper_id WHERE p.id = ?"
        params = (paper_id,)
    rows = conn.execute(sql, params).fetchall()

    merged = {}
    for r in rows:
        item = merged.setdefault(r["id"], dict(r))
        if r["sentence"] and len(r["sentence"]) > len(item.get("sentence") or ""):
            item["sentence"] = r["sentence"]
        if r["freq"]:
            item["freq"] = max(item.get("freq") or 0, r["freq"])
    return list(merged.values())


def apply_glossary(terms):
    """领域词典覆盖（人工 fix 锁定的除外）。import/build/网页版共用。"""
    for t in terms:
        hit = manual.get(t["key"])
        if hit and not t.get("locked"):
            t["translation"] = hit["translation"]
            t["source"] = "领域词典"
    return terms


def write_summary(terms, out_path):
    """按类别汇总成 Markdown，方便快速复习和查漏。"""
    groups = defaultdict(list)
    for t in terms:
        groups[t["category"] or "未分类"].append(t)

    def sort_key(name):
        for i, prefix in enumerate(CATEGORY_ORDER):
            if name.startswith(prefix):
                return (i, name)
        return (len(CATEGORY_ORDER), name)

    lines = ["# 论文生词汇总", "", f"共 {len(terms)} 条", ""]
    for name in sorted(groups, key=sort_key):
        items = sorted(groups[name], key=lambda x: x["display"].lower())
        lines.append(f"## {name}（{len(items)}）")
        lines.append("")
        lines.append("| 词条 | 音标 | 释义 |")
        lines.append("| --- | --- | --- |")
        for t in items:
            trans = (t["translation"] or "待补充").replace("|", "/")
            lines.append(f"| {t['display']} | {t['phonetic'] or ''} | {trans} |")
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path


def cmd_build(args):
    conn = db.connect()
    terms = fetch_terms(conn)
    if args.kind:
        terms = [t for t in terms if t["kind"] == args.kind]
    if args.category:
        terms = [t for t in terms if (t["category"] or "").startswith(args.category)]
    if not terms:
        print("术语库为空，先运行 import。")
        return 1

    if args.limit:
        terms = terms[: args.limit]

    # 领域词典是用户维护的真源：build 时套用（人工 fix 锁定的除外），
    # 这样往 tsv 里补行后不需要重导论文。
    apply_glossary(terms)
    phonetics.apply_phonetics(terms)

    missing = [t["id"] for t in terms
               if not t["translation"] or t["translation"] == "待补充"]
    terms = [t for t in terms if t["id"] not in missing]
    if missing:
        print(f"跳过无释义术语 {len(missing)} 个（可补 domain_glossary.tsv 后重跑）")

    print(f"准备生成 {len(terms)} 张卡片…")
    audio_map = tts.ensure_audio_batch([t["display"] for t in terms])
    print(f"发音就绪 {len(audio_map)} 个")

    out_path = args.out or os.path.join(OUTPUT_DIR, "论文生词.apkg")
    anki.build_deck(terms, audio_map, out_path)
    print(f"卡包已生成：{out_path}")

    summary = write_summary(terms, os.path.join(OUTPUT_DIR, "术语汇总.md"))
    print(f"分类汇总：{summary}")
    return 0


def cmd_stats(args):
    conn = db.connect()
    papers = conn.execute("SELECT * FROM papers ORDER BY id").fetchall()
    terms = db.all_terms(conn)
    print(f"论文 {len(papers)} 篇，术语 {len(terms)} 条\n")
    for p in papers:
        print(f"  #{p['id']} {p['title'][:60]}")
    by_kind = defaultdict(int)
    by_cat = defaultdict(int)
    no_trans = 0
    for t in terms:
        by_kind[t["kind"]] += 1
        by_cat[t["category"] or "未分类"] += 1
        if not t["translation"]:
            no_trans += 1
    print("\n按类型：", dict(by_kind))
    print("按分类：")
    for k, v in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print(f"\n缺释义：{no_trans} 条")
    return 0


def cmd_fix(args):
    conn = db.connect()
    term = db.get_term(conn, args.key.lower())
    if not term:
        print(f"术语库里没有 {args.key}，先 import 对应论文。")
        return 1
    db.set_translation(conn, args.key.lower(), args.translation,
                       category=args.category or "", lock=True)
    print(f"已更新并锁定：{term['display']} -> {args.translation}")
    return 0


def cmd_remove(args):
    conn = db.connect()
    term = db.get_term(conn, args.key.lower())
    if not term:
        print(f"术语库里没有 {args.key}。")
        return 1
    if term["locked"] and not args.force:
        print(f"{term['display']} 已锁定，确认删除请加 --force。")
        return 1
    conn.execute("DELETE FROM occurrences WHERE term_id = ?", (term["id"],))
    conn.execute("DELETE FROM terms WHERE id = ?", (term["id"],))
    conn.commit()
    print(f"已删除：{term['display']}（若重导论文会再次进入候选，可先加入抽取噪声表）")
    return 0


def cmd_web(args):
    from src import webapp
    return webapp.run(args.host, args.port, open_browser=not args.no_browser)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="PaperLex")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_import = sub.add_parser("import", help="导入 PDF 并抽取术语")
    p_import.add_argument("paths", nargs="+")
    p_import.set_defaults(func=cmd_import)

    p_build = sub.add_parser("build", help="生成 Anki 卡包")
    p_build.add_argument("--out", default="")
    p_build.add_argument("--limit", type=int, default=0)
    p_build.add_argument("--kind", choices=["word", "phrase", "abbr"], default="")
    p_build.add_argument("--category", default="")
    p_build.set_defaults(func=cmd_build)

    p_stats = sub.add_parser("stats", help="查看术语库概况")
    p_stats.set_defaults(func=cmd_stats)

    p_fix = sub.add_parser("fix", help="人工修正释义并锁定")
    p_fix.add_argument("key")
    p_fix.add_argument("translation")
    p_fix.add_argument("--category", default="")
    p_fix.set_defaults(func=cmd_fix)

    p_remove = sub.add_parser("remove", help="从术语库删除词条")
    p_remove.add_argument("key")
    p_remove.add_argument("--force", action="store_true")
    p_remove.set_defaults(func=cmd_remove)

    p_web = sub.add_parser("web", help="启动网页版背单词")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=8000)
    p_web.add_argument("--no-browser", action="store_true")
    p_web.set_defaults(func=cmd_web)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
