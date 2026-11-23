r"""Локальный сервер озвучки на Qwen3-TTS (движок qwentts.cpp через qwen.dll).

POST /v1/audio/speech  {"text": "...", "voice": "serena"} → WAV 24 кГц моно.
Всё считается на видеокарте, без интернета и без студии.

Запуск: tools\qwen-tts-server.cmd
"""

from __future__ import annotations

import ctypes
import http.server
import io
import json
import os
import sys
import threading
import time
import wave
from ctypes import POINTER, c_char_p, c_float, c_int, c_int32, c_int64, c_void_p
from pathlib import Path

# Без консоли (pythonw) стандартные потоки могут быть закрыты, а движок пишет
# в stdout на уровне ОС и падает. Подставляем пустые валидные потоки.
import os as _os
for _fd in (1, 2):
    try:
        _os.fstat(_fd)
    except OSError:
        _null = _os.open(_os.devnull, _os.O_WRONLY)
        _os.dup2(_null, _fd)

LOG = Path(os.environ.get("QWEN_LOG", "qwen-server.log"))
LOG.parent.mkdir(parents=True, exist_ok=True)
_log = open(LOG, "a", encoding="utf-8", buffering=1)
sys.stdout = _log
sys.stderr = _log
# 23.11: ловим падения нативной части движка, иначе они уходят в никуда.
import faulthandler as _faulthandler

# numpy грузим ДО движка: после загрузки qwen.dll его импорт падает с access violation.
import numpy as _np

_faulthandler.enable(_log)

LIB_DIR = Path(os.environ.get("QWEN_LIB", r"C:\work\workspace\qwen-tts"))
MODELS = Path(os.environ.get("QWEN_MODELS", r"C:\Users\ipdnv\AppData\Local\Speech Studio\models\qwen3-tts"))
PORT = int(os.environ.get("QWEN_PORT", "8896"))
ABI = 6
DEFAULT_VOICE = os.environ.get("QWEN_VOICE", "serena")
LANG = os.environ.get("QWEN_LANG", "russian")
# Клонирование: путь к образцу (24 кГц моно) и его текст. Если задано — движок
# поднимается на base-модели и говорит голосом из образца.
REF_WAV = os.environ.get("QWEN_REF_WAV", "")
REF_TEXT = os.environ.get("QWEN_REF_TEXT", "")
TALKER = os.environ.get("QWEN_TALKER", "qwen-talker-1.7b-customvoice-Q8_0.gguf")
STRESS = os.environ.get("QWEN_STRESS", "1") == "1"
NORMALIZE = os.environ.get("QWEN_NORMALIZE", "1") == "1"


def speech_normalize(text: str) -> str:
    """Готовит текст к озвучке: тире и двоеточия становятся паузами.

    Синтез читает «—» как чёрточку без паузы, поэтому меняем её на запятую-паузу.
    Точки и запятые не трогаем, они и так дают паузу.
    """
    import re

    text = text.replace("\u2014", ", ").replace("\u2013", ", ")  # тире и среднее тире
    text = text.replace(";", ", ")
    # Дефис внутри слова («какой-нибудь», «чем-то») читается с паузой — убираем его.
    text = re.sub(r"(?<=[А-Яа-яЁёA-Za-z])-(?=[А-Яа-яЁёA-Za-z])", "", text)
    text = re.sub(r":\s+", ". ", text)
    text = text.replace("\u2026", "...")
    text = re.sub(r"\.{3,}", ", ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", ". ", text)
    text = re.sub(r",\s*,", ",", text)
    return text.strip(" ,")


class InitParams(ctypes.Structure):
    _fields_ = [
        ("abi_version", c_int), ("talker_path", c_char_p), ("codec_path", c_char_p),
        ("use_fa", ctypes.c_bool), ("clamp_fp16", ctypes.c_bool), ("max_batch", c_int),
        ("codec_chunk_sec", c_float), ("device", c_char_p),
    ]


class TtsParams(ctypes.Structure):
    _fields_ = [
        ("abi_version", c_int), ("text", c_char_p), ("lang", c_char_p),
        ("instruct", c_char_p), ("speaker", c_char_p),
        ("ref_audio_24k", POINTER(c_float)), ("ref_n_samples", c_int), ("ref_text", c_char_p),
        ("seed", c_int64), ("max_new_tokens", c_int), ("temperature", c_float),
        ("top_k", c_int), ("top_p", c_float), ("repetition_penalty", c_float),
        ("subtalker_temperature", c_float), ("subtalker_top_k", c_int), ("subtalker_top_p", c_float),
        ("dump_dir", c_char_p), ("cancel", c_void_p), ("cancel_user_data", c_void_p),
        ("on_chunk", c_void_p), ("on_chunk_user_data", c_void_p),
        ("ref_spk_emb", POINTER(c_float)), ("ref_spk_dim", c_int),
        ("ref_codes", POINTER(c_int32)), ("ref_T", c_int),
    ]


class Audio(ctypes.Structure):
    _fields_ = [("samples", POINTER(c_float)), ("n_samples", c_int),
                ("sample_rate", c_int), ("channels", c_int)]


class VoiceRef(ctypes.Structure):
    _fields_ = [
        ("ref_spk_emb", POINTER(c_float)), ("ref_spk_dim", c_int),
        ("ref_codes", POINTER(c_int32)), ("ref_T", c_int), ("num_codebooks", c_int),
    ]


os.add_dll_directory(str(LIB_DIR))
os.environ["PATH"] = str(LIB_DIR) + ";" + os.environ.get("PATH", "")
_lib = ctypes.CDLL(str(LIB_DIR / "qwen.dll"))
_lib.qt_init.argtypes = [POINTER(InitParams)]
_lib.qt_init.restype = c_void_p
_lib.qt_init_default_params.argtypes = [POINTER(InitParams)]
_lib.qt_tts_default_params.argtypes = [POINTER(TtsParams)]
_lib.qt_synthesize.argtypes = [c_void_p, POINTER(TtsParams), POINTER(Audio)]
_lib.qt_extract_voice_ref.argtypes = [c_void_p, POINTER(c_float), c_int, POINTER(VoiceRef)]
_lib.qt_last_error.restype = c_char_p

_handle = None
_lock = threading.Lock()
_ref: VoiceRef | None = None
_REF_TEXT_VALUE = ""
CHUNK_CB = ctypes.CFUNCTYPE(ctypes.c_bool, POINTER(c_float), c_int, c_void_p)


def _prepare_text(text: str) -> str:
    if NORMALIZE:
        text = speech_normalize(text)
    if STRESS:
        try:
            from stress_ru import add_stress

            text = add_stress(text)
        except Exception as error:  # noqa: BLE001
            print("ударения не расставлены:", error, flush=True)
    return text


def _fill_params(tts: TtsParams, text: str, voice: str) -> None:
    _lib.qt_tts_default_params(ctypes.byref(tts))
    tts.abi_version = ABI
    tts.text = text.encode("utf-8")
    tts.lang = LANG.encode()
    if _ref is not None:
        tts.ref_spk_emb = _ref.ref_spk_emb
        tts.ref_spk_dim = _ref.ref_spk_dim
        tts.ref_codes = _ref.ref_codes
        tts.ref_T = _ref.ref_T
        if _REF_TEXT_VALUE:
            tts.ref_text = _REF_TEXT_VALUE.encode("utf-8")
    else:
        tts.speaker = voice.encode()


def synthesize_stream(text: str, voice: str, write_chunk) -> float:
    """Потоковый синтез: отдаёт куски PCM 16 бит 24 кГц по мере готовности."""
    handle = engine()
    text = _prepare_text(text)
    tts = TtsParams()
    _fill_params(tts, text, voice)
    started = time.monotonic()
    first_at: list[float] = []

    def on_chunk(samples, n_samples, _user):
        now = time.monotonic()
        if not first_at:
            first_at.append(now - started)
        data = _np.ctypeslib.as_array(samples, shape=(n_samples,)).copy()
        pcm = (_np.clip(data, -1, 1) * 32767).astype("<i2").tobytes()
        write_chunk(pcm)
        return True

    callback = CHUNK_CB(on_chunk)
    tts.on_chunk = ctypes.cast(callback, c_void_p)
    audio = Audio()
    with _lock:
        rc = _lib.qt_synthesize(handle, ctypes.byref(tts), ctypes.byref(audio))
    if rc != 0:
        raise RuntimeError((_lib.qt_last_error() or b"").decode(errors="replace"))
    print(f"поток: первый звук {first_at[0] if first_at else -1:.2f} с, "
          f"всего {time.monotonic()-started:.2f} с", flush=True)
    return time.monotonic() - started


def engine():
    global _handle, _ref
    if _handle is None:
        params = InitParams()
        _lib.qt_init_default_params(ctypes.byref(params))
        params.abi_version = ABI
        params.talker_path = str(MODELS / TALKER).encode()
        params.codec_path = str(MODELS / "qwen-tokenizer-12hz-Q8_0.gguf").encode()
        params.device = os.environ.get("QWEN_DEVICE", "CUDA0").encode()
        print("загружаю движок Qwen3-TTS…", flush=True)
        _handle = _lib.qt_init(ctypes.byref(params))
        if not _handle:
            raise RuntimeError((_lib.qt_last_error() or b"").decode(errors="replace"))
        if REF_WAV:
            global REF_TEXT, _REF_TEXT_VALUE
            if not REF_TEXT:
                # Текст образца лежит рядом с записью, такой же файл .txt
                sidecar = Path(REF_WAV).with_suffix(".txt")
                if sidecar.exists():
                    REF_TEXT = sidecar.read_text(encoding="utf-8").strip()
            _REF_TEXT_VALUE = REF_TEXT
            import wave as _wave

            with _wave.open(REF_WAV, "rb") as handle_wav:
                rate = handle_wav.getframerate()
                frames = handle_wav.readframes(handle_wav.getnframes())
            audio = _np.frombuffer(frames, dtype=_np.int16).astype(_np.float32) / 32768.0
            if rate != 24000:
                idx = _np.linspace(0, len(audio) - 1, int(len(audio) * 24000 / rate)).astype(_np.int32)
                audio = audio[idx]
            buf = audio.ctypes.data_as(POINTER(c_float))
            _ref = VoiceRef()
            rc = _lib.qt_extract_voice_ref(_handle, buf, len(audio), ctypes.byref(_ref))
            if rc != 0:
                raise RuntimeError((_lib.qt_last_error() or b"").decode(errors="replace"))
            print(f"отпечаток голоса готов: ref_T={_ref.ref_T}", flush=True)
        print("движок готов", flush=True)
    return _handle


def synthesize(text: str, voice: str) -> bytes:
    handle = engine()
    if NORMALIZE:
        text = speech_normalize(text)
    if STRESS:
        try:
            from stress_ru import add_stress

            text = add_stress(text)
        except Exception as error:  # noqa: BLE001
            print("ударения не расставлены:", error, flush=True)
    tts = TtsParams()
    _lib.qt_tts_default_params(ctypes.byref(tts))
    tts.abi_version = ABI
    tts.text = text.encode("utf-8")
    tts.lang = LANG.encode()
    if _ref is not None:
        # Режим клонирования: тембр из образца плюс его речевые коды и текст.
        tts.ref_spk_emb = _ref.ref_spk_emb
        tts.ref_spk_dim = _ref.ref_spk_dim
        tts.ref_codes = _ref.ref_codes
        tts.ref_T = _ref.ref_T
        if REF_TEXT:
            tts.ref_text = REF_TEXT.encode("utf-8")
    else:
        tts.speaker = voice.encode()
    audio = Audio()
    with _lock:
        rc = _lib.qt_synthesize(handle, ctypes.byref(tts), ctypes.byref(audio))
    if rc != 0:
        raise RuntimeError((_lib.qt_last_error() or b"").decode(errors="replace"))
    samples = ctypes.cast(audio.samples, POINTER(c_float))
    pcm = bytearray()
    for i in range(audio.n_samples):
        value = max(-1.0, min(1.0, samples[i]))
        pcm += int(value * 32767).to_bytes(2, "little", signed=True)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle_wav:
        handle_wav.setnchannels(1)
        handle_wav.setsampwidth(2)
        handle_wav.setframerate(audio.sample_rate)
        handle_wav.writeframes(bytes(pcm))
    return buffer.getvalue()


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

    def do_POST(self):
        if self.path.split("?")[0] not in ("/v1/audio/speech", "/audio/speech"):
            self.send_response(404)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            payload = {}
        text = str(payload.get("input") or payload.get("text") or "").strip()
        voice = str(payload.get("voice") or DEFAULT_VOICE)
        if not text:
            self.send_response(400)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        started = time.monotonic()
        streaming = "stream=1" in self.path or self.headers.get("X-Stream") == "1"
        if streaming:
            # Поток: заголовок WAV с «бесконечной» длиной и куски PCM по мере готовности.
            try:
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                header = b"RIFF" + (0xFFFFFFFF).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
                header += (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
                header += (24000).to_bytes(4, "little") + (48000).to_bytes(4, "little")
                header += (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
                header += b"data" + (0xFFFFFFFF).to_bytes(4, "little")
                self.wfile.write(("%X\r\n" % len(header)).encode() + header + b"\r\n")

                def write_chunk(pcm: bytes) -> None:
                    self.wfile.write(("%X\r\n" % len(pcm)).encode() + pcm + b"\r\n")

                synthesize_stream(text, voice, write_chunk)
                self.wfile.write(b"0\r\n\r\n")
                print(f"поток отдан за {time.monotonic()-started:.2f} с, букв {len(text)}", flush=True)
            except Exception as error:  # noqa: BLE001
                print("ошибка потока:", error, flush=True)
            return
        try:
            data = synthesize(text, voice)
        except Exception as error:  # noqa: BLE001
            print("ошибка синтеза:", error, flush=True)
            self.send_response(500)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        print(f"{time.monotonic()-started:.2f} с, букв {len(text)}, голос {voice}, {len(data)} байт", flush=True)
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _watch_parent(pid_file: Path) -> None:
    """Следит за процессом приложения: когда оно закрылось — сервер выключается."""
    import ctypes as _ctypes
    import threading as _threading

    def alive(pid: int) -> bool:
        kernel32 = _ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True

    def loop() -> None:
        target = None
        while True:
            try:
                if target is None:
                    target = int(pid_file.read_text(encoding="ascii").strip())
                    print(f"слежу за процессом приложения {target}", flush=True)
                if not alive(target):
                    print("приложение закрылось — выключаюсь", flush=True)
                    _os._exit(0)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)

    _threading.Thread(target=loop, daemon=True).start()


def main() -> None:
    engine()
    pid_file = Path(os.environ.get("QWEN_PID", "qwen-server.pid"))
    try:
        pid_file.write_text(str(os.getpid()), encoding="ascii")
    except Exception:  # noqa: BLE001
        pass
    watch = Path(os.environ.get("QWEN_WATCH", "qwen-watch.pid"))
    if watch.exists():
        _watch_parent(watch)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Qwen3-TTS на http://127.0.0.1:{PORT}/v1/audio/speech, голос {DEFAULT_VOICE}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
