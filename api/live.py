"""Stage features, kept apart from the core API so nothing existing changes.

1. "Aap dukaandar hain": judges scan a QR code and their own phone becomes the shopkeeper's
   Soundbox (or the customer's app) for the case on the big screen. Their replies go through
   exactly the same path as the console: two readers, the action gate, the warrant, the rail.
2. "Diwali rush": a large batch of synthetic stuck payments through the real rule engine and
   the real guard, with live counters.

Phones poll (no websockets or SSE), so it also works through a Cloudflare quick tunnel, which
gives the HTTPS a phone needs for its microphone (getUserMedia only works in a secure context).
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import socket
import threading
import time
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from sim.rush import Rush

WEB = Path(__file__).resolve().parent.parent / "web"
LOCK = threading.Lock()
LIVE = {"focus": None, "players": {}, "attempts": [], "public_url": os.getenv("TATKAL_PUBLIC_URL", "").rstrip("/")}
RUSH = Rush()


class JoinReq(BaseModel):
    name: str = Field(default="Judge", max_length=40)
    role: str = Field(default="shopkeeper", pattern="^(shopkeeper|customer)$")


class ReplyReq(BaseModel):
    pid: str = Field(max_length=40)
    text: str = Field(min_length=1, max_length=300)
    source: str = Field(default="text", pattern="^(text|voice)$")


class UrlReq(BaseModel):
    url: str = Field(max_length=300)


class RushReq(BaseModel):
    n: int = Field(default=100_000, ge=1000, le=500_000)
    seconds: float = Field(default=14.0, ge=3, le=60)


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # no packet is sent; this only picks the outgoing interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def qr_svg(url: str) -> str | None:
    try:
        import segno
    except ImportError:
        return None
    import io
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=8, border=2, dark="#0b1222", light="#ffffff")
    return buf.getvalue().decode()


TUNNEL = {"state": "off", "url": None, "error": None, "proc": None}


def start_tunnel(port: int) -> dict:
    """Start a Cloudflare quick tunnel (no account needed) so judges get an https link and the phone
    microphone works. Needs the free `cloudflared` binary on PATH."""
    if TUNNEL["state"] in ("starting", "on") and TUNNEL["proc"] and TUNNEL["proc"].poll() is None:
        return tunnel_view()
    exe = shutil.which("cloudflared")
    if not exe:
        TUNNEL.update(state="missing", url=None, error="cloudflared is not installed. Windows: winget install --id Cloudflare.cloudflared · Mac: brew install cloudflared")
        return tunnel_view()
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    TUNNEL.update(state="starting", url=None, error=None, proc=proc)

    def watch():
        for line in proc.stdout:                      # cloudflared prints the URL in a banner on stderr
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not TUNNEL["url"]:
                TUNNEL.update(state="on", url=m.group(0))
                LIVE["public_url"] = m.group(0)
        if TUNNEL["proc"] is proc:
            TUNNEL.update(state="off" if TUNNEL["state"] == "on" else "failed",
                          error=TUNNEL["error"] or "cloudflared stopped")
    threading.Thread(target=watch, daemon=True, name="tunnel").start()
    return tunnel_view()


def tunnel_view() -> dict:
    return {k: TUNNEL[k] for k in ("state", "url", "error")}


def register(app, tatkal, rails, view, configure, admin_dep, demo_port: int = 8000):

    def focus_case():
        f = LIVE["focus"]
        return tatkal.cases.get(f) if f else None

    def player(pid: str) -> dict:
        p = LIVE["players"].get(pid)
        if not p:
            raise HTTPException(404, "Join first")
        p["seen"] = time.time()
        return p

    @app.get("/join")
    def join_page():
        return FileResponse(WEB / "join.html")

    @app.get("/assets/{name}")
    def asset(name: str):
        p = (WEB / "assets" / name).resolve()
        if p.parent != (WEB / "assets").resolve() or not p.exists():
            raise HTTPException(404, "no such asset")
        return FileResponse(p)

    @app.get("/live/info")
    def info(port: int = demo_port):
        lan = f"http://{lan_ip()}:{port}/join"
        url = (LIVE["public_url"] + "/join") if LIVE["public_url"] else lan
        alive = [{"name": p["name"], "role": p["role"]} for p in LIVE["players"].values() if time.time() - p["seen"] < 12]
        return {"join_url": url, "lan_url": lan, "public_url": LIVE["public_url"], "qr_svg": qr_svg(url),
                "focus": LIVE["focus"], "players": alive, "attempts": LIVE["attempts"][-12:],
                "mic_note": "Phones need HTTPS for the microphone. On plain Wi-Fi (http) judges can tap or type; "
                            "run a Cloudflare quick tunnel for voice."}

    @app.post("/live/url", dependencies=[admin_dep])
    def set_url(req: UrlReq):
        LIVE["public_url"] = req.url.strip().rstrip("/").removesuffix("/join")
        return info()

    @app.post("/live/focus/{txn_id}", dependencies=[admin_dep])
    def set_focus(txn_id: str):
        if txn_id not in tatkal.cases:
            raise HTTPException(404, "Open the case first")
        LIVE["focus"] = txn_id
        return {"focus": txn_id}

    @app.post("/live/tunnel", dependencies=[admin_dep])
    def tunnel_start(port: int = demo_port):
        return start_tunnel(port)

    @app.get("/live/tunnel")
    def tunnel_status():
        return tunnel_view()

    @app.post("/live/join")
    def join(req: JoinReq):
        pid = secrets.token_hex(6)
        LIVE["players"][pid] = {"pid": pid, "name": req.name.strip() or "Judge", "role": req.role, "seen": time.time()}
        return {"pid": pid, **LIVE["players"][pid]}

    @app.get("/live/state")
    def state(pid: str):
        p = player(pid)
        c = focus_case()
        return {"you": {"name": p["name"], "role": p["role"]}, "focus": LIVE["focus"],
                "secure_join": (LIVE["public_url"] + "/join") if LIVE["public_url"].startswith("https://") else None,
                "case": view(c) if c else None, "attempts": [a for a in LIVE["attempts"] if a["pid"] == pid][-6:]}

    @app.post("/live/reply")
    def reply(req: ReplyReq):
        p = player(req.pid)
        c = focus_case()
        if not c:
            raise HTTPException(409, "No case on the big screen yet")
        configure()
        with LOCK:
            before_state = c.state
            if p["role"] == "shopkeeper":
                res = tatkal.merchant_says(c.txn_id, req.text, source=req.source)
                processed = before_state == "MERCHANT_ASKED"
                g = c.last_gate if processed else None
                a = {"pid": p["pid"], "name": p["name"], "role": "shopkeeper", "text": req.text, "case": c.txn_id,
                     "ignored": not processed,
                     "rule": (g or {}).get("readers", {}).get("rule"), "sarvam": (g or {}).get("readers", {}).get("sarvam"),
                     "decision": ("ALLOW " + g["code"]) if g and g.get("allowed") else ("DENY" if g else None),
                     "reason": (g or {}).get("reason"), "action": res.get("action"), "state": c.state, "t": time.time()}
            else:
                n_before = len(c.transcript)
                tatkal.customer_says(c.txn_id, req.text)
                last = c.guard_log[-1] if c.guard_log else {}
                said = [x["text"] for x in c.transcript[n_before:] if x.get("who") == "tatkal" and x.get("to") == "customer"]
                a = {"pid": p["pid"], "name": p["name"], "role": "customer", "text": req.text, "case": c.txn_id,
                     "decision": ("Guard blocked a risky draft; the rule template was sent" if last.get("blocked")
                                  else "Answered from the RBI and NPCI rules: no promise beyond the rule"),
                     "reply": said[-1] if said else None, "handed_to_person": c.state == "ESCALATED",
                     "action": None, "state": c.state, "t": time.time()}
            LIVE["attempts"].append(a)
            LIVE["attempts"] = LIVE["attempts"][-200:]
        return {"attempt": a, "case": view(c)}

    @app.get("/live/scoreboard")
    def scoreboard():
        at = LIVE["attempts"]
        shop = [a for a in at if a["role"] == "shopkeeper" and not a.get("ignored")]
        return {"attempts": len(at), "shopkeeper_replies": len(shop),
                "npci_replies_sent": sum(1 for a in shop if a.get("action")),
                "held_back": sum(1 for a in shop if not a.get("action")),
                "players": len({a["pid"] for a in at}), "recent": at[-8:]}

    @app.post("/sim/rush", dependencies=[admin_dep])
    def rush_start(req: RushReq):
        return RUSH.start(req.n, req.seconds, today=tatkal.today)

    @app.get("/sim/rush")
    def rush_status():
        return RUSH.stats

    @app.post("/sim/rush/stop", dependencies=[admin_dep])
    def rush_stop():
        RUSH.stop()
        return RUSH.stats

    return LIVE
