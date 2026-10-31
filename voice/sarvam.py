"""Sarvam speech adapters (raw HTTPS).

Speech to text: POST https://api.sarvam.ai/speech-to-text  (multipart: file, model, mode)
  saaras:v4 (default, recommended) or saaras:v3, mode "codemix" for Hinglish, REST limit 30 seconds per clip.
  Browser MediaRecorder output (audio/webm) is an accepted format.
Text to speech: POST https://api.sarvam.ai/text-to-speech  (JSON: text, language_code, model, speaker)
  bulbul:v4-flash, default persona shubh_enhi_ads. Response carries base64 audio.
"""
from __future__ import annotations

import os

import httpx

BASE = os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai")
STT_MODEL = os.getenv("SARVAM_STT_MODEL", "saaras:v4")
KEYTERMS = '["Paytm","Soundbox","UPI","refund","saamaan"]'
TTS_MODEL = os.getenv("SARVAM_TTS_MODEL", "bulbul:v4-flash")
TTS_SPEAKER = os.getenv("SARVAM_TTS_SPEAKER", "shubh_enhi_ads")
TTS_LANG = os.getenv("SARVAM_TTS_LANG", "hi-IN")


def _key() -> str:
    k = os.getenv("SARVAM_API_KEY")
    if not k:
        raise RuntimeError("SARVAM_API_KEY not set: use the text box instead")
    return k


def duration_seconds(audio: bytes) -> float | None:
    """Real duration of a clip via ffprobe when it is installed; None if it cannot be measured
    (the 2 MB cap and the browser's own recording limit still apply)."""
    import shutil
    import subprocess
    import tempfile
    if not shutil.which("ffprobe"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".webm") as f:
        f.write(audio)
        f.flush()
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f.name],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            if out and out != "N/A":
                return float(out)
            # browser webm often has no duration header: decode and read the last timestamp
            err = subprocess.run(["ffmpeg", "-v", "info", "-i", f.name, "-f", "null", "-"],
                                 capture_output=True, text=True, timeout=20).stderr
            import re
            ts = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", err)
            if not ts:
                return None
            h, m, sec = ts[-1]
            return int(h) * 3600 + int(m) * 60 + float(sec)
        except (subprocess.SubprocessError, ValueError):
            return None


def transcribe(audio: bytes, filename: str = "clip.webm", mime: str = "audio/webm", mode: str = "codemix") -> dict:
    r = httpx.post(f"{BASE}/speech-to-text", headers={"api-subscription-key": _key()},
                   files={"file": (filename, audio, mime)},
                   data={"model": STT_MODEL, "mode": mode, **({"keyterms": KEYTERMS} if STT_MODEL.endswith("v4") else {})},
                   timeout=30)
    r.raise_for_status()
    j = r.json()
    return {"transcript": j.get("transcript", ""), "language_code": j.get("language_code")}


def speak(text: str) -> str:
    """Returns base64 encoded audio (wav) ready for an <audio> data URL."""
    r = httpx.post(f"{BASE}/text-to-speech", headers={"api-subscription-key": _key(), "Content-Type": "application/json"},
                   json={"text": text[:2400], "language_code": TTS_LANG, "model": TTS_MODEL, "speaker": TTS_SPEAKER},
                   timeout=30)
    r.raise_for_status()
    j = r.json()
    audios = j.get("audios") or ([j["audio"]] if j.get("audio") else [])
    if not audios:
        raise RuntimeError("TTS returned no audio")
    return audios[0]


# ---------------------------------------------------------------- fast speech for the demo
# A full message took 4 to 10 s to come back as one WAV. Three changes make it feel instant:
#   1. split into sentences and synthesise them in parallel; the first sentence plays while the rest arrive
#   2. MP3 at 22.05 kHz instead of WAV (about a third of the bytes)
#   3. prefetch: every new Tatkal message is synthesised in the background the moment it is created,
#      so by the time anyone presses play (or auto speak fires) the audio is usually already cached
import re as _re
import threading as _threading
from collections import OrderedDict as _OD
from concurrent.futures import Future as _Future, ThreadPoolExecutor as _Pool

_POOL = _Pool(max_workers=6, thread_name_prefix="tts")
_CACHE: "_OD[str, _Future]" = _OD()
_LOCK = _threading.Lock()
_CACHE_MAX = 400


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _re.split(r"(?<=[.?!।])\s+", text.strip()) if p.strip()]
    out: list[str] = []
    for p in parts:                       # glue very short pieces to their neighbour, it sounds better
        if out and (len(p) < 18 or len(out[-1]) < 18):
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out or [text]


def _synth(text: str) -> str:
    r = httpx.post(f"{BASE}/text-to-speech", headers={"api-subscription-key": _key(), "Content-Type": "application/json"},
                   json={"text": text[:2400], "language_code": TTS_LANG, "model": TTS_MODEL, "speaker": TTS_SPEAKER,
                         "output_audio_codec": "mp3", "speech_sample_rate": 22050},
                   timeout=30)
    r.raise_for_status()
    audios = r.json().get("audios") or []
    if not audios:
        raise RuntimeError("TTS returned no audio")
    return audios[0]


def _future(sentence: str) -> _Future:
    with _LOCK:
        f = _CACHE.get(sentence)
        if f is not None and not (f.done() and f.exception() is not None):
            _CACHE.move_to_end(sentence)
            return f
        f = _POOL.submit(_synth, sentence)
        _CACHE[sentence] = f
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
        return f


def prefetch(text: str) -> int:
    """Start synthesising every sentence of a message in the background. Returns the part count."""
    parts = split_sentences(text)
    for p in parts:
        _future(p)
    return len(parts)


def speak_part(text: str, part: int = 0) -> dict:
    """Audio for one sentence of the message (all sentences are started in parallel)."""
    parts = split_sentences(text)
    for p in parts:
        _future(p)
    part = max(0, min(part, len(parts) - 1))
    return {"audio_base64": _future(parts[part]).result(timeout=30), "mime": "audio/mpeg",
            "part": part, "parts": len(parts)}
