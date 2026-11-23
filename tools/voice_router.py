r"""Переключатель голоса для Насти.

Слушает 127.0.0.1:8895 (тот адрес, что прописан в приложении) и пересылает
запросы на выбранный движок:

    local   → локальный клон Леры (Qwen3-TTS, порт 8896, занимает видеопамять)
    yandex  → голос Леры из Яндекса (мост, порт 8899, видеопамять свободна)

Текущий выбор лежит в C:\work\workspace\.tmp\voice-target.txt (одно слово).
Менять можно на ходу, приложение перезапускать не нужно:

    echo yandex > C:\work\workspace\.tmp\voice-target.txt
    echo local  > C:\work\workspace\.tmp\voice-target.txt
"""

from __future__ import annotations

import http.server
import os
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("VOICE_ROUTER_PORT", "8895"))
TARGET_FILE = Path(os.environ.get("VOICE_TARGET", "voice-target.txt"))
BACKENDS = {
    "local": "http://127.0.0.1:8897/v1/audio/speech",
    "yandex": "http://127.0.0.1:8899/v1/audio/speech",
}
DEFAULT = "local"

LOG = Path(os.environ.get("VOICE_ROUTER_LOG", "voice-router.log"))
LOG.parent.mkdir(parents=True, exist_ok=True)
_log = open(LOG, "a", encoding="utf-8", buffering=1)
sys.stdout = _log
sys.stderr = _log


def current_target() -> str:
    try:
        value = TARGET_FILE.read_text(encoding="ascii").strip().lower()
    except OSError:
        return DEFAULT
    return value if value in BACKENDS else DEFAULT


def forward(payload: bytes, target: str) -> tuple[int, bytes, str]:
    url = BACKENDS[target]
    if target == "yandex":
        # Мост Яндекса ждёт свои имя голоса и пустую модель.
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
        text = data.get("input") or data.get("text") or ""
        payload = json.dumps({"model": "", "input": text, "voice": "lera"}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, response.read(), (response.headers.get("Content-Type") or "audio/wav")
    except urllib.error.HTTPError as error:
        return error.code, error.read()[:200], "text/plain"
    except Exception as error:  # noqa: BLE001
        print(f"ошибка пересылки на {target}: {error}", flush=True)
        return 502, str(error).encode("utf-8"), "text/plain"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        # Удобно проверять состояние: /who
        if self.path.split("?")[0] in ("/who", "/health"):
            body = json.dumps({"target": current_target()}).encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if self.path.split("?")[0] not in ("/v1/audio/speech", "/audio/speech"):
            self.send_response(404)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length) if length else b"{}"
        target = current_target()
        status, data, content_type = forward(payload, target)
        print(f"-> {target}: {status}, {len(data)} байт", flush=True)
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    if not TARGET_FILE.exists():
        TARGET_FILE.write_text(DEFAULT, encoding="ascii")
    try:
        Path(r"C:\work\workspace\.tmp\voice-router.pid").write_text(str(os.getpid()), encoding="ascii")
    except Exception:  # noqa: BLE001
        pass
    print(f"переключатель голоса на {PORT}, цель по умолчанию {current_target()}", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
