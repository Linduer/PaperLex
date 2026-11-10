"""全局路径与参数配置。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
CACHE_DIR = ROOT / "cache"
AUDIO_DIR = CACHE_DIR / "audio"

# 原始资源目录（词典 CSV、词形表、词表 JSON），可用环境变量覆盖：
#   setx PAPERLEX_RESOURCES "你的资源目录"
RESOURCES = Path(os.environ.get("PAPERLEX_RESOURCES", str(ROOT / "resources")))
ECDICT_CSV = RESOURCES / "ecdict.csv"
LEMMA_TXT = RESOURCES / "lemma.en.txt"
WORDLIST_DIR = RESOURCES / "wordlists"

# 生成的索引数据库
ECDICT_DB = DATA_DIR / "ecdict.db"
# 术语库（唯一真源）
GLOSSARY_DB = DATA_DIR / "glossary.db"

for _d in (DATA_DIR, OUTPUT_DIR, CACHE_DIR, AUDIO_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---- 过滤参数 ----
# NGSL 前 N 级视为"已认识"，直接丢弃（1000/2000/3000...）
NGSL_KNOWN_LEVELS = {"1000", "2000", "3000"}
MIN_WORD_LEN = 4
MIN_ABBR_LEN = 2
MIN_FREQ_WORD = 2
MIN_FREQ_PHRASE = 3
MIN_FREQ_ABBR = 2

# 每篇论文最多导出多少个词条
MAX_TERMS_PER_PAPER = 120

# ---- 发音 ----
TTS_VOICE = "en-US-AriaNeural"

# ---- Anki ----
DECK_NAME = "论文生词"
MODEL_NAME = "论文生词-四选一"
