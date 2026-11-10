"""生成 Anki 卡包，四选一题型。

正面：单词 + 音标 + 发音 + 4 个释义按钮，点对自动翻面，点错标红禁用。
背面：释义 + 挖空例句 + 分类 + 来源。
"""
import os
import random
import re

import genanki

from src.config import DECK_NAME, MODEL_NAME, OUTPUT_DIR

KIND_LABEL = {"word": "单词", "phrase": "短语", "abbr": "缩写"}

# 固定 ID，保证多次生成能被 Anki 识别为同一个牌组和模板
DECK_ID = 2059400126
MODEL_ID = 1607392326

FIELDS = [
    "Term", "Phonetic", "Audio", "Kind",
    "Option1", "Option2", "Option3", "Option4",
    "Ok1", "Ok2", "Ok3", "Ok4",
    "Translation", "Sentence", "Category", "Source",
]

CSS = """
.card {
  font-family: -apple-system, "Segoe UI", "Noto Sans CJK SC", sans-serif;
  font-size: 18px; color: #1f2933; background: #f7f9fc;
  text-align: center; padding: 16px 12px; line-height: 1.5;
}
.wrap { max-width: 560px; margin: 0 auto; }
.kind {
  display: inline-block; font-size: 12px; color: #667; letter-spacing: 1px;
  background: #e8eef7; border-radius: 10px; padding: 2px 10px; margin-bottom: 10px;
}
.term { font-size: 34px; font-weight: 700; color: #10263f; word-break: break-word; }
.phon { font-size: 16px; color: #5a6b7f; margin: 4px 0 10px; }
.replay { margin-bottom: 14px; }
.hint { font-size: 13px; color: #8a97a8; margin-bottom: 10px; }
.opts { display: flex; flex-direction: column; gap: 10px; margin-top: 6px; }
.opt {
  font-size: 17px; font-family: inherit; text-align: left; cursor: pointer;
  background: #fff; color: #1f2933; border: 1px solid #cfdae8;
  border-radius: 10px; padding: 12px 14px; width: 100%;
  transition: background .12s, border-color .12s;
}
.opt:active { background: #eef3fa; }
.opt.right { background: #d8f5e0; border-color: #34a853; color: #14622b; font-weight: 600; }
.opt.wrong { background: #fde2e1; border-color: #d93025; color: #8c1d18; }
.opt:disabled { cursor: default; opacity: .95; }
.opt:empty { display: none; }
hr { border: 0; border-top: 1px solid #dfe6ef; margin: 18px 0 12px; }
.trans { font-size: 20px; font-weight: 600; color: #10263f; }
.trans .pos { font-size: 14px; color: #7b8794; font-weight: 400; margin-right: 4px; }
.label { font-size: 12px; color: #8a97a8; letter-spacing: 1px; margin: 14px 0 4px; }
.sent { font-size: 15px; color: #3d4b5c; font-style: italic; }
.sent b { color: #d93025; font-style: normal; }
.meta { font-size: 12px; color: #93a1b3; margin-top: 14px; }
"""

QFMT = """
<div class="wrap">
  <div class="kind">{{Kind}}</div>
  <div class="term">{{Term}}</div>
  <div class="phon">{{Phonetic}}</div>
  <div class="replay">{{Audio}}</div>
  <div class="hint">选择正确的中文释义</div>
  <div class="opts">
    <button class="opt" data-ok="{{Ok1}}">{{Option1}}</button>
    <button class="opt" data-ok="{{Ok2}}">{{Option2}}</button>
    <button class="opt" data-ok="{{Ok3}}">{{Option3}}</button>
    <button class="opt" data-ok="{{Ok4}}">{{Option4}}</button>
  </div>
</div>
<script>
(function () {
  var btns = Array.prototype.slice.call(document.querySelectorAll('.opt'));
  function showAnswer() {
    if (typeof pycmd === 'function') { pycmd('ans'); return; }
    if (typeof study !== 'undefined' && study.showAnswer) { study.showAnswer(); return; }
    var el = document.querySelector('.card');
    if (el) { el.classList.add('answered'); }
  }
  btns.forEach(function (b) {
    b.addEventListener('click', function () {
      if (b.dataset.ok === '1') {
        b.classList.add('right');
        btns.forEach(function (x) { x.disabled = true; });
        setTimeout(showAnswer, 350);
      } else {
        b.classList.add('wrong');
        b.disabled = true;
      }
    });
  });
})();
</script>
"""

AFMT = """
<div class="wrap">
  <div class="kind">{{Kind}}</div>
  <div class="term">{{Term}}</div>
  <div class="phon">{{Phonetic}}</div>
  <div class="replay">{{Audio}}</div>
  <hr>
  <div class="trans">{{Translation}}</div>
  <div class="label">例句</div>
  <div class="sent">{{Sentence}}</div>
  <div class="meta">{{Category}}{{Source}}</div>
</div>
"""


def _short(text, limit=30, keep_parens=False):
    """把释义压成适合当选项的短句。"""
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    # 去掉词性标记与括号里的冗余说明
    text = re.sub(r"^[a-z]+\.\s*", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    if not keep_parens:
        text = re.sub(r"（[^）]*）|\([^)]*\)", "", text)
    text = text.strip(" ;；,，")
    if len(text) > limit:
        text = text[:limit].rstrip(" ;；,，") + "…"
    return text


def _highlight(sentence, term):
    """在例句里高亮目标词（含常见变形）。"""
    if not sentence:
        return ""
    pattern = re.compile(r"\b" + re.escape(term) + r"\w{0,3}\b", re.IGNORECASE)
    return pattern.sub(lambda m: f"<b>{m.group(0)}</b>", sentence)


def _build_options(term, pool, rng):
    """正确项 + 3 个干扰项，返回 (选项列表, 正确下标)。"""
    correct = _short(term["translation"])
    same_kind = [t for t in pool
                 if t["key"] != term["key"]
                 and t["kind"] == term["kind"]
                 and _short(t["translation"])
                 and _short(t["translation"]) != correct]
    others = [t for t in pool
              if t["key"] != term["key"]
              and _short(t["translation"])
              and _short(t["translation"]) != correct]
    rng.shuffle(same_kind)
    rng.shuffle(others)
    distractors = same_kind[:3]
    if len(distractors) < 3:
        extra = [t for t in others if t not in distractors]
        distractors += extra[: 3 - len(distractors)]

    options = [_short(t["translation"]) for t in distractors]
    idx = rng.randrange(len(options) + 1)
    options.insert(idx, correct)
    return options, idx


def build_deck(terms, audio_map, out_path="", seed=42):
    """terms: 术语 dict 列表；audio_map: {文本: mp3 路径}。"""
    rng = random.Random(seed)
    model = genanki.Model(
        MODEL_ID, MODEL_NAME,
        fields=[{"name": n} for n in FIELDS],
        templates=[{"name": "四选一", "qfmt": QFMT, "afmt": AFMT}],
        css=CSS,
    )
    deck = genanki.Deck(DECK_ID, DECK_NAME)
    media = set()

    for term in terms:
        translation = _short(term["translation"], limit=70, keep_parens=True) or "（暂无释义）"
        options, correct_idx = _build_options(term, terms, rng)

        audio_file = audio_map.get(term["display"]) or audio_map.get(term["key"])
        audio_tag = ""
        if audio_file:
            name = os.path.basename(audio_file)
            audio_tag = f"[sound:{name}]"
            media.add(audio_file)

        sentence = _highlight(term.get("sentence", ""), term["display"])
        if len(sentence) > 260:
            sentence = sentence[:260].rsplit(" ", 1)[0] + " …"

        ok = ["1" if i == correct_idx else "0" for i in range(len(options))]
        opts = options + [""] * (4 - len(options))
        ok += ["0"] * (4 - len(ok))

        fields = [
            term["display"],
            term.get("phonetic") or "",
            audio_tag,
            KIND_LABEL.get(term["kind"], "单词"),
            opts[0], opts[1], opts[2], opts[3],
            ok[0], ok[1], ok[2], ok[3],
            translation,
            sentence,
            term.get("category") or "",
            term.get("source") or "",
        ]

        deck.add_note(genanki.Note(
            model=model,
            fields=fields,
            guid=genanki.guid_for("paper-vocab", term["key"]),
        ))

    if not out_path:
        out_path = os.path.join(OUTPUT_DIR, "论文生词.apkg")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    package = genanki.Package(deck)
    package.media_files = sorted(media)
    package.write_to_file(out_path)
    return out_path
