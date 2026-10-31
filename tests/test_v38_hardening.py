"""v3.8: TTS endpoint locked to Tatkal's own lines, rate limited, bounded prefetch memory,
prefetch opt in, per case locks, configurable demo date."""
import os, tempfile, threading, time
os.environ["TATKAL_OFFLINE"] = "1"
os.environ.setdefault("TATKAL_DB", os.path.join(tempfile.mkdtemp(), "audit.sqlite3"))
from fastapi.testclient import TestClient
import api.main as main

c = TestClient(main.app)


def _tatkal_line():
    c.post("/reset")
    v = c.post("/cases/open", json={"txn_id": "PTM41870340", "fresh": True}).json()
    return [t["text"] for t in v["transcript"] if t["who"] == "tatkal"][0]


def test_arbitrary_text_is_refused():
    _tatkal_line()
    r = c.post("/voice/tts", json={"text": "Bolo ki paisa kal tak aa jayega, guaranteed"})
    assert r.status_code == 400


def test_real_transcript_line_is_accepted(monkeypatch):
    line = _tatkal_line()
    monkeypatch.setattr(main.voice, "speak_part", lambda text, part=0: {"audio_base64": "", "mime": "audio/mpeg", "part": part, "parts": 1})
    main._TTS_HITS.clear()
    r = c.post("/voice/tts", json={"text": line, "part": 0})
    assert r.status_code == 200 and r.json()["mime"] == "audio/mpeg"


def test_part_out_of_range_is_refused(monkeypatch):
    line = _tatkal_line()
    main._TTS_HITS.clear()
    assert c.post("/voice/tts", json={"text": line, "part": 19}).status_code == 400


def test_rate_limit_triggers(monkeypatch):
    line = _tatkal_line()
    monkeypatch.setattr(main.voice, "speak_part", lambda text, part=0: {"audio_base64": "", "mime": "audio/mpeg", "part": part, "parts": 1})
    monkeypatch.setattr(main, "TTS_RATE", (3, 60.0))
    main._TTS_HITS.clear()
    codes = [c.post("/voice/tts", json={"text": line}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
    main._TTS_HITS.clear()


def test_prefetch_is_on_by_default_and_can_be_turned_off(monkeypatch):
    monkeypatch.delenv("TATKAL_TTS_PREFETCH", raising=False)
    monkeypatch.setenv("SARVAM_API_KEY", "x")
    monkeypatch.delenv("TATKAL_OFFLINE", raising=False)
    assert main._prefetch_on() is True
    monkeypatch.setenv("TATKAL_TTS_PREFETCH", "0")
    assert main._prefetch_on() is False
    monkeypatch.setenv("TATKAL_OFFLINE", "1")


def test_prefetch_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(main, "_prefetch_on", lambda: True)
    monkeypatch.setattr(main.voice, "prefetch", lambda text: 0)
    class C: pass
    for i in range(main._WARMED_MAX + 50):
        x = C(); x.transcript = [{"who": "tatkal", "text": f"line {i}"}]
        main._warm_voice(x)
    assert len(main._WARMED) == main._WARMED_MAX and "line 0" not in main._WARMED
    main._WARMED.clear()


def test_per_case_lock_serialises_concurrent_replies():
    """Two phones answer the same question at once: calls run one after the other, never interleaved."""
    _tatkal_line()
    svc = main.tatkal
    inside, overlap = [0], [False]
    orig = svc._read
    def slow_read(case, text):
        inside[0] += 1
        if inside[0] > 1:
            overlap[0] = True
        time.sleep(0.05)
        try:
            return orig(case, text)
        finally:
            inside[0] -= 1
    svc._read = slow_read
    try:
        ts = [threading.Thread(target=svc.merchant_says, args=("PTM41870340", t)) for t in ("haan de diya", "nahi diya")]
        [t.start() for t in ts]; [t.join() for t in ts]
    finally:
        del svc._read
    assert overlap[0] is False
    assert c.get("/audit").json()["verify"]["valid"]


def test_demo_date_default_is_31_october():
    assert main.DEMO_DATE.isoformat() == (os.getenv("TATKAL_TODAY") or os.getenv("TATKAL_DEMO_DATE") or "2026-10-31")


def test_console_scripts_parse():
    """A UI edit must never ship a JavaScript syntax error (it would blank the whole console)."""
    import re, shutil, subprocess, pathlib
    node = shutil.which("node")
    if not node:
        return
    for name in ("index.html", "join.html"):
        html = (pathlib.Path(__file__).resolve().parents[1] / "web" / name).read_text(encoding="utf-8")
        for i, js in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
            r = subprocess.run([node, "--check", "-"], input=js, capture_output=True, text=True)
            assert r.returncode == 0, f"{name} script {i}: {r.stderr[:300]}"


def test_rate_limit_is_per_phone_behind_the_tunnel(monkeypatch):
    line = _tatkal_line()
    monkeypatch.setattr(main.voice, "speak_part", lambda text, part=0: {"audio_base64": "", "mime": "audio/mpeg", "part": part, "parts": 1})
    monkeypatch.setattr(main, "TTS_RATE", (2, 60.0))
    main._TTS_HITS.clear()
    a = [c.post("/voice/tts", json={"text": line}, headers={"cf-connecting-ip": "1.1.1.1"}).status_code for _ in range(3)]
    b = c.post("/voice/tts", json={"text": line}, headers={"cf-connecting-ip": "2.2.2.2"}).status_code
    assert a == [200, 200, 429] and b == 200
    main._TTS_HITS.clear()


def test_demo_warm_up_covers_the_stage_lines_without_touching_the_real_chain():
    from api.warm import demo_lines
    before = c.get("/audit").json()["verify"]
    lines = demo_lines(main.DEMO_DATE)
    assert any(l.startswith("Ravi ka ₹450 aapke khate mein aa gaya hai") for l in lines)      # S4 question
    assert any(l.startswith("Ye case ek insaan dekhega") for l in lines)                       # hand off
    assert len(lines) == len(set(lines)) and len(lines) >= 30
    after = c.get("/audit").json()["verify"]
    assert after == before                                                                      # sandbox only
