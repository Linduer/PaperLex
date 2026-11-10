"""把 ECDICT 的 CSV 与词形表导入 SQLite，供后续高速查询。

用法：python -m src.build_dict
"""
import csv
import sqlite3
import sys
import time

from src.config import ECDICT_CSV, ECDICT_DB, LEMMA_TXT

csv.field_size_limit(10 * 1024 * 1024)


def build() -> None:
    if not ECDICT_CSV.exists():
        sys.exit(f"找不到词典文件：{ECDICT_CSV}")
    if ECDICT_DB.exists():
        ECDICT_DB.unlink()

    conn = sqlite3.connect(ECDICT_DB)
    conn.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        CREATE TABLE ecdict (
            word        TEXT PRIMARY KEY,
            phonetic    TEXT,
            translation TEXT,
            pos         TEXT,
            tag         TEXT,
            bnc         INTEGER,
            frq         INTEGER,
            exchange    TEXT
        );
        CREATE TABLE lemma (
            variant TEXT PRIMARY KEY,
            lemma   TEXT NOT NULL
        );
        """
    )

    # ---- 1. 词形还原表 ----
    t0 = time.time()
    pairs = []
    with open(LEMMA_TXT, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            if "->" not in line:
                continue
            head, variants = line.split("->", 1)
            lemma = head.split("/", 1)[0].strip().lower()
            pairs.append((lemma, lemma))
            for v in variants.split(","):
                v = v.strip().lower()
                if v and v != lemma:
                    pairs.append((v, lemma))
    conn.executemany("INSERT OR IGNORE INTO lemma VALUES (?, ?)", pairs)
    print(f"[1/3] 词形表 {len(pairs)} 条，用时 {time.time() - t0:.1f}s")

    # ---- 2. ECDICT 主表 ----
    t0 = time.time()
    rows = []
    n = 0
    with open(ECDICT_CSV, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            word = (r.get("word") or "").strip().lower()
            if not word:
                continue
            try:
                bnc = int(r.get("bnc") or 0)
            except ValueError:
                bnc = 0
            try:
                frq = int(r.get("frq") or 0)
            except ValueError:
                frq = 0
            rows.append(
                (
                    word,
                    r.get("phonetic") or "",
                    r.get("translation") or "",
                    r.get("pos") or "",
                    r.get("tag") or "",
                    bnc,
                    frq,
                    r.get("exchange") or "",
                )
            )
            n += 1
            if len(rows) >= 20000:
                conn.executemany("INSERT OR IGNORE INTO ecdict VALUES (?,?,?,?,?,?,?,?)", rows)
                rows.clear()
    if rows:
        conn.executemany("INSERT OR IGNORE INTO ecdict VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    print(f"[2/3] 词典 {n} 条，用时 {time.time() - t0:.1f}s")

    # ---- 3. 用 exchange 字段补充词形（标记 0 表示原形） ----
    t0 = time.time()
    extra = []
    for word, exchange in conn.execute("SELECT word, exchange FROM ecdict WHERE exchange != ''"):
        for part in exchange.split("/"):
            if ":" not in part:
                continue
            flag, form = part.split(":", 1)
            form = form.strip().lower()
            if flag == "0" and form:
                extra.append((word, form))  # word 是 form 的变形
    conn.executemany("INSERT OR IGNORE INTO lemma VALUES (?, ?)", extra)
    conn.execute("CREATE INDEX idx_lemma ON lemma(lemma)")
    conn.commit()
    print(f"[3/3] exchange 补充 {len(extra)} 条，用时 {time.time() - t0:.1f}s")
    conn.close()
    print(f"完成：{ECDICT_DB}")


if __name__ == "__main__":
    build()
