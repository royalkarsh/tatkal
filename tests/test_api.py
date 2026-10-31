import os, tempfile
os.environ["TATKAL_OFFLINE"] = "1"          # hermetic: never call Sarvam from unit tests
os.environ.pop("TATKAL_SINGLE_KEY_DEMO", None)
os.environ["TATKAL_DB"] = os.path.join(tempfile.mkdtemp(), "audit.sqlite3")
from fastapi.testclient import TestClient
from api.main import app

c = TestClient(app)

def test_end_to_end_api():
    assert c.post("/reset").status_code == 200
    s = c.get("/scenarios").json(); assert len(s) >= 7
    # default, no second reader: a clear yes still goes to a person (fail closed)
    r = c.post("/cases/open", json={"txn_id": "PTM41870340", "fresh": True}).json(); assert r["state"] == "MERCHANT_ASKED"
    r = c.post("/cases/PTM41870340/merchant", json={"text": "haan saamaan de diya"}).json()
    assert r["state"] == "ESCALATED" and r["response"] is None and not r["last_gate"]["two_key_verified"]
    # labelled single key demo switch: the same yes goes through, with a warrant
    os.environ["TATKAL_SINGLE_KEY_DEMO"] = "1"
    r = c.post("/cases/open", json={"txn_id": "PTM41870340", "fresh": True}).json(); assert r["state"] == "MERCHANT_ASKED"
    r = c.post("/cases/PTM41870340/merchant", json={"text": "haan saamaan de diya"}).json(); assert r["state"] == "RESOLVED"
    assert r["response"]["warrant"] == r["last_gate"]["warrant"]["wid"]
    os.environ.pop("TATKAL_SINGLE_KEY_DEMO")
    d = c.post("/cases/PTM41870340/draft", json={"text": "24 ghante mein paisa aa jayega"}).json(); assert not d["verdict"]["ok"]
    assert c.get("/audit").json()["verify"]["valid"]
    t = c.post("/audit/tamper").json()
    assert not t["verify"]["valid"] and t["real_log"]["valid"]
    assert c.get("/audit/anchor").json()["signature"]
    assert c.post("/cases/PTM41870340/recheck").status_code == 200
    assert c.post("/voice/tts", json={"text": "namaste"}).status_code == 400   # v3.8: only Tatkal lines are voiced


def test_audio_longer_than_30_seconds_is_refused_by_duration_not_size():
    import shutil, subprocess, tempfile
    if not shutil.which("ffmpeg"):
        return
    p = os.path.join(tempfile.mkdtemp(), "long.webm")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=35", "-c:a", "libopus",
                    "-b:a", "8k", p], check=True)
    assert os.path.getsize(p) < 2_000_000
    r = c.post("/voice/stt", files={"file": ("long.webm", open(p, "rb"), "audio/webm")})
    assert r.status_code == 413 and "30 s" in r.json()["detail"]


def test_anchor_checkpoints_detect_a_rewritten_chain():
    from api import main as m
    c.post("/cases/open", json={"txn_id": "PTM41870301", "fresh": True})
    a = c.get("/audit/anchor").json()
    assert c.get("/audit/anchors/verify").json()["bad"] == []
    m.audit.db.execute("UPDATE events SET hash = 'f00d' WHERE seq = ?", (a["count"],)); m.audit.db.commit()
    try:
        v = c.get("/audit/anchors/verify").json()
        assert any(b["count"] == a["count"] for b in v["bad"])
    finally:                                                     # put the shared test chain back
        m.audit.db.execute("UPDATE events SET hash = ? WHERE seq = ?", (a["head"], a["count"])); m.audit.db.commit()
