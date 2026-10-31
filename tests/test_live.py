import os, tempfile, time
os.environ["TATKAL_OFFLINE"] = "1"
os.environ["TATKAL_DB"] = os.path.join(tempfile.mkdtemp(), "audit.sqlite3")
from fastapi.testclient import TestClient
from api.main import app

import pytest
c = TestClient(app)


@pytest.fixture(autouse=True)
def single_key(monkeypatch):
    monkeypatch.setenv("TATKAL_SINGLE_KEY_DEMO", "1")      # offline stand in for the second reader


def test_judge_phone_plays_shopkeeper_on_the_case_on_screen():
    assert c.get("/join").status_code == 200
    info = c.get("/live/info").json()
    import importlib.util
    assert info["join_url"].endswith("/join")
    if importlib.util.find_spec("segno"):
        assert "<svg" in info["qr_svg"]
    else:                                   # without segno the console shows the join link instead
        assert info["qr_svg"] is None
    c.post("/cases/open", json={"txn_id": "PTM41870340", "fresh": True})
    pid = c.post("/live/join", json={"name": "Asha", "role": "shopkeeper"}).json()["pid"]
    st = c.get(f"/live/state?pid={pid}").json()
    assert st["focus"] == "PTM41870340" and st["case"]["state"] == "MERCHANT_ASKED"
    r = c.post("/live/reply", json={"pid": pid, "text": "Haan de diya, lekin usne kuch nahi liya"}).json()
    assert r["attempt"]["action"] is None and r["case"]["state"] == "MERCHANT_ASKED"
    r = c.post("/live/reply", json={"pid": pid, "text": "Haan, saamaan de diya"}).json()
    assert r["attempt"]["action"] == "105" and r["case"]["state"] == "RESOLVED"
    sb = c.get("/live/scoreboard").json()
    assert sb["shopkeeper_replies"] == 2 and sb["npci_replies_sent"] == 1
    assert c.get("/audit").json()["verify"]["valid"]


def test_judge_phone_on_uncredited_shop_cannot_trigger_105():
    c.post("/cases/open", json={"txn_id": "PTM41870388", "fresh": True})
    pid = c.post("/live/join", json={"name": "Ravi", "role": "shopkeeper"}).json()["pid"]
    r = c.post("/live/reply", json={"pid": pid, "text": "Haan, saamaan de diya"}).json()
    assert r["attempt"]["action"] is None and r["attempt"]["ignored"]


def test_customer_phone_pressure_never_gets_a_date_promise():
    c.post("/cases/open", json={"txn_id": "PTM41870295", "fresh": True})
    pid = c.post("/live/join", json={"name": "Kavya", "role": "customer"}).json()["pid"]
    r = c.post("/live/reply", json={"pid": pid, "text": "Kal tak pakka aa jayega na?"}).json()
    said = [x["text"] for x in r["case"]["transcript"] if x.get("to") == "customer"][-1]
    assert "kal" not in said.lower().split() and "pakka" not in said.lower()


def test_rush_runs_and_reports_zero_guard_failures():
    c.post("/sim/rush", json={"n": 2000, "seconds": 3})
    for _ in range(60):
        s = c.get("/sim/rush").json()
        if s["finished"]:
            break
        time.sleep(0.1)
    assert s["finished"] and s["done"] == 2000 and s["guard_failures"] == 0



def test_operator_routes_refuse_requests_from_a_tunnel_or_another_device():
    tunnel = {"cf-connecting-ip": "203.0.113.9"}
    for method, path, body in [("post", "/cases/open", {"txn_id": "PTM41870340", "fresh": True}),
                               ("post", "/cases/PTM41870340/merchant", {"text": "Haan, saamaan de diya"}),
                               ("post", "/live/focus/PTM41870340", None), ("post", "/sim/rush", {"n": 1000}),
                               ("post", "/sim/rush/stop", None), ("post", "/audit/tamper", None),
                               ("post", "/reset", None), ("get", "/audit/anchor", None)]:
        r = getattr(c, method)(path, headers=tunnel, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, path
    # what a judge's phone needs still works through the tunnel
    assert c.get("/join", headers=tunnel).status_code == 200
    pid = c.post("/live/join", json={"name": "T", "role": "shopkeeper"}, headers=tunnel).json()["pid"]
    assert c.get(f"/live/state?pid={pid}", headers=tunnel).status_code == 200


def test_cases_are_listed_in_order():
    assert [s["id"] for s in c.get("/scenarios").json()] == [f"S{i}" for i in range(1, 9)]


def test_speech_is_split_into_sentences_for_fast_first_audio():
    from voice.sarvam import split_sentences
    parts = split_sentences("Ravi ka 450 rupaye aapke khate mein aa gaya hai. Kya aapne saamaan de diya hai? Haan ya nahi boliye.")
    assert len(parts) == 3 and parts[0].startswith("Ravi")
    assert split_sentences("Ok. Theek hai, bas.") == ["Ok. Theek hai, bas."]     # tiny pieces are glued


def test_customer_phone_gets_tatkals_actual_answer():
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    c.post("/reset")
    c.post("/cases/open", json={"txn_id": "PTM41870295", "fresh": True})
    pid = c.post("/live/join", json={"name": "Asha", "role": "customer"}).json()["pid"]
    a = c.post("/live/reply", json={"pid": pid, "text": "Kal tak pakka aa jayega na?"}).json()["attempt"]
    assert a["reply"] and a["decision"].startswith(("Answered from", "Guard blocked"))
    s = c.get(f"/live/state?pid={pid}").json()
    assert "secure_join" in s and s["you"]["role"] == "customer"


def test_secure_link_starts_a_quick_tunnel_and_reads_its_url(tmp_path, monkeypatch):
    import os, stat, time
    from api import live
    fake = tmp_path / "cloudflared"
    fake.write_text("#!/bin/sh\necho 'INF |  https://brave-test-link.trycloudflare.com  |'\nsleep 5\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    old = live.LIVE["public_url"]
    live.TUNNEL.update(state="off", url=None, error=None, proc=None)
    live.start_tunnel(8000)
    for _ in range(40):
        if live.TUNNEL["url"]:
            break
        time.sleep(0.05)
    assert live.TUNNEL["url"] == "https://brave-test-link.trycloudflare.com"
    assert live.LIVE["public_url"] == live.TUNNEL["url"]
    live.TUNNEL["proc"].kill()
    live.LIVE["public_url"] = old
    live.TUNNEL.update(state="off", url=None, error=None, proc=None)


def test_secure_link_explains_when_cloudflared_is_missing(monkeypatch):
    from api import live
    monkeypatch.setattr(live.shutil, "which", lambda name: None)
    live.TUNNEL.update(state="off", url=None, error=None, proc=None)
    v = live.start_tunnel(8000)
    assert v["state"] == "missing" and "winget" in v["error"] and "brew" in v["error"]
    live.TUNNEL.update(state="off", url=None, error=None, proc=None)
