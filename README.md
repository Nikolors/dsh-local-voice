# dsh-local-voice — локальная озвучка для DeepSeek Harness

![Platform](https://img.shields.io/badge/platform-Windows-0078d4)
![License](https://img.shields.io/badge/license-MIT-green)
![Voice](https://img.shields.io/badge/voice-Qwen3--TTS-orange)


Локальный голос для DSH: клонирование голоса по образцу, русская речь, без облака
и без оплаты за символы. Работает на видеокарте через движок
[qwentts.cpp](https://github.com/timoncool/qwentts.cpp) (Qwen3-TTS, MIT).

Проект вырос из личной сборки: сначала голос был только облачный, потом захотелось
свой, локальный — и чтобы он ещё и переключался обратно на облако без перезапуска
приложения.

## Что внутри

- `tools/qwen_tts_server.py` — HTTP-сервер озвучки на `127.0.0.1:8897`
  (OpenAI-совместимый `POST /v1/audio/speech`), поддерживает потоковую отдачу
  (`?stream=1`) и клонирование голоса по образцу 24 кГц.
- `tools/voice_router.py` — переключатель на порту `8895`: пересылает запросы либо
  на локальный сервер, либо на облачный мост. Выбор лежит в файле и меняется на
  ходу — приложение перезапускать не нужно.
- `tools/stress_ru.py` — словарь ударений RUAccent (необязательно).
- `plugin/` — плагин DSH: поднимает голосовой стек вместе с приложением и гасит
  его при выходе.

## Что нужно

1. Windows, NVIDIA с драйвером под CUDA (проверялось на RTX 5070 Laptop, 8 ГБ).
2. Python 3.11+ с `numpy`, `onnxruntime`, `winsdk` (для OCR не нужно, для голоса
   не требуется) — проще взять любое venv.
3. Движок `qwentts.cpp` версии `engine-0.2.2`: `qwen.dll`, `ggml-*.dll` и рядом
   CUDA-библиотеки (`cudart`, `cublas`, `cublasLt`, `cudnn`, `cufft`) и MSVC
   рантайм — без них `ggml-cuda.dll` не загрузится (ошибка 126).
4. Модели Qwen3-TTS в GGUF: `qwen-talker-1.7b-base-Q8_0.gguf`,
   `qwen-tokenizer-12hz-Q8_0.gguf`. Плюс образец голоса 24 кГц моно и его текст.

## Установка

```bat
git clone https://github.com/Nikolors/dsh-local-voice
cd dsh-local-voice

set QWEN_LIB=C:\path\to\qwentts-engine
set QWEN_MODELS=C:\path\to\models
set QWEN_REF_WAV=C:\path\to\voice-sample.wav
set QWEN_PORT=8897

python tools\qwen_tts_server.py
```

Сервер ответит по адресу `http://127.0.0.1:8897/v1/audio/speech`:

```json
{"input": "Привет, это проверка голоса.", "voice": "serena"}
```

Для клонирования достаточно задать `QWEN_REF_WAV` (и положить рядом `.txt` с
текстом образца) — сервер сам извлечёт отпечаток голоса.

## Переключатель локальный ↔ облако

```bat
set QWEN_PORT=8897
python tools\qwen_tts_server.py            :: локальный сервер

set VOICE_TARGET=C:\path\to\voice-target.txt
python tools\voice_router.py               :: переключатель на 8895
```

В файле цели одно слово: `local` или `yandex`. В настройках DSH укажите
`customBaseUrl: http://127.0.0.1:8895/v1` — и голос будет переключаться без
перезапуска приложения.

## Плагин DSH

`plugin/` — обычный bundle: ставится через **Plugins → Add plugin** (указать папку).
Он запускает голосовой стек при старте приложения и останавливает при выходе,
поэтому сервер не остаётся висеть после закрытия.

## Ограничения

- Занимает около 5 ГБ видеопамяти — с тяжёлыми играми не уживётся.
- Ударения в редких словах движок ставит сам и иногда ошибается; словарь RUAccent
  помогает не всегда, а сплошная разметка ударений без дообученной LoRA портит речь.
- Клон наследует тембр из образца, но не идеальную дикцию: качество зависит от
  чистоты записи.

## Лицензия

Код — MIT. Движок `qwentts.cpp` — MIT, модели Qwen3-TTS — по лицензии их авторов
(см. карточку модели). Словарь ударений RUAccent — по лицензии авторов словаря.

## Потоковая озвучка

`patches/dsh-tts-streaming/` — правка штатного плагина `@goodandready/dsh-tts`:
он начинает говорить по мере поступления текста, а не после всего ответа.
Инструкция и файл — внутри папки.
**Ключевые слова / keywords:** DeepSeek Harness, DSH плагин, локальная озвучка,
русский текст в речь, TTS без интернета, клонирование голоса по образцу, Qwen3-TTS,
qwentts.cpp, OpenAI-совместимый TTS сервер, переключение локальный/облачный голос,
потоковая озвучка, Windows, CUDA, NVIDIA.

> **English:** a local Russian text-to-speech plugin for DeepSeek Harness (DSH) —
> Qwen3-TTS voice cloning from a 24 kHz sample, an OpenAI-compatible TTS server,
> chunked streaming, a runtime local↔cloud switcher and a lifecycle plugin.