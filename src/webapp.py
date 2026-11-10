"""网页版背单词：Python 标准库 HTTP 服务，不引入新依赖。

    python main.py web [--host 127.0.0.1] [--port 8000] [--no-browser]

- 页面：web/index.html（四选一 + 列表复习，进度存浏览器 localStorage）
- 词表：glossary.db，套用 domain_glossary.tsv（改 TSV 后刷新页面即生效）
- 发音：cache/audio 现有 mp3；缺的走 /api/synth 用 edge-tts 现场合成
"""
import asyncio
import json
import os
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src import db, manual, phonetics, tts
from src.config import AUDIO_DIR, ROOT

INDEX_HTML = ROOT / "web" / "index.html"
WEB_DIR = ROOT / "web"
AUDIO_RE = re.compile(r"^/[0-9a-f]{16}\.mp3$")
STATIC_RE = re.compile(r"^/(sw\.js|manifest\.webmanifest|icons/icon-(192|512)\.png)$")
_TSV_MTIME = 0.0

_MIME = {
    ".js": "application/javascript; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".png": "image/png",
    ".html": "text/html; charset=utf-8",
}


def _reload_glossary_if_changed():
    """domain_glossary.tsv 改动后无需重启服务。"""
    global _TSV_MTIME
    m = manual.PATH.stat().st_mtime if manual.PATH.exists() else 0.0
    if m != _TSV_MTIME:
        manual.load.cache_clear()
        _TSV_MTIME = m


def load_terms() -> list[dict]:
    from main import apply_glossary, fetch_terms  # 延迟导入，避免与 main 循环

    _reload_glossary_if_changed()
    conn = db.connect()
    try:
        terms = phonetics.apply_phonetics(apply_glossary(fetch_terms(conn)))
    finally:
        conn.close()

    out = []
    for t in terms:
        if not t.get("translation"):
            continue
        path = tts._cache_path(t["display"])
        out.append({
            "key": t["key"],
            "display": t["display"],
            "kind": t["kind"],
            "phonetic": t["phonetic"] or "",
            "translation": t["translation"],
            "category": t["category"] or "",
            "source": t["source"] or "",
            "sentence": t.get("sentence") or "",
            "audio": f"/{os.path.basename(path)}" if os.path.exists(path) else "",
        })
    out.sort(key=lambda x: x["display"].lower())
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                body = INDEX_HTML.read_bytes()
            except OSError:
                self._send(500, "index.html 缺失".encode("utf-8"), "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")
            return

        if path == "/api/terms":
            try:
                self._json({"terms": load_terms()})
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
            return

        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return

        # PWA 静态资源（sw.js / manifest / 图标），只允许 web/ 目录白名单
        if STATIC_RE.match(path):
            full = WEB_DIR / path.lstrip("/")
            if full.exists() and WEB_DIR.resolve() in full.resolve().parents:
                self._send(200, full.read_bytes(), _MIME[full.suffix])
            else:
                self._send(404, b"", "text/plain")
            return

        if AUDIO_RE.match(path):
            name = path.lstrip("/")
            full = AUDIO_DIR / name
            if full.exists() and full.resolve().parent == AUDIO_DIR.resolve():
                self._send(200, full.read_bytes(), "audio/mpeg")
            else:
                self._send(404, b"", "text/plain")
            return

        self._send(404, "not found".encode("utf-8"), "text/plain; charset=utf-8")

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/synth":
            self._send(404, b"", "text/plain")
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            key = json.loads(self.rfile.read(n).decode("utf-8")).get("key", "")
        except Exception:
            self._json({"error": "bad request"}, 400)
            return

        from src import db as _db
        conn = _db.connect()
        try:
            term = conn.execute("SELECT * FROM terms WHERE key = ?", (key,)).fetchone()
            if not term:
                self._json({"error": "unknown key"}, 404)
                return
            path = tts.ensure_audio(term["display"])
            self._json({"audio": f"/{os.path.basename(path)}" if path else ""})
        except Exception as exc:
            self._json({"error": str(exc)}, 500)
        finally:
            conn.close()

    def log_message(self, *args):  # 安静模式
        pass


def run(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"端口被占用（{exc}），换一个：python main.py web --port {port + 1}")
        return 1
    print(f"网页版背单词：{url}   （Ctrl+C 退出）")
    print(f"共 {len(load_terms())} 个词可练习")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        httpd.server_close()
    return 0
