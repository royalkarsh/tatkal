"""Tatkal API and demo console.

Run locally only:  uvicorn api.main:app --host 127.0.0.1 --port 8000
Admin endpoints (reset, tamper, mode, rail switches) require the X-Demo-Admin header when
TATKAL_ADMIN_TOKEN is set. Payment rails are mocks; no money moves.
"""
from __future__ import annotations

import config  # noqa: F401  loads .env before anything reads SARVAM_API_KEY

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import date
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from agent.llm import SarvamLLM, SimulatedRiskyDrafter
from agent.service import Tatkal, IllegalTransition
from engine.audit import AuditLog
from engine import messages as M
from rails.mock import MockRails, SCENARIOS
from voice import sarvam as voice
from api import warm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
DEMO_DATE = date.fromisoformat(os.getenv("TATKAL_TODAY") or os.getenv("TATKAL_DEMO_DATE") or "2026-10-31")
ADMIN_TOKEN = os.getenv("TATKAL_ADMIN_TOKEN")
def _anchor_key() -> bytes:
    """Signing key for the audit anchor. TATKAL_AUDIT_KEY from .env if set; otherwise a key created
    once and kept in data/.audit_key (chmod 600, git ignored), so anchors stay verifiable across
    restarts. Never printed."""
    k = os.getenv("TATKAL_AUDIT_KEY")
    if k:
        return k.encode()
    f = DATA / ".audit_key"
    if not f.exists():
        f.write_text(secrets.token_hex(32), encoding="utf-8")
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
    return f.read_text(encoding="utf-8").strip().encode()


ANCHOR_KEY = _anchor_key()
DB_PATH = Path(os.getenv("TATKAL_DB", str(DATA / "audit.sqlite3")))
ANCHOR_LOG = DB_PATH.with_name(DB_PATH.stem + ".anchors.jsonl")    # checkpoints belong to their own chain
ANCHOR_SINKS = [ANCHOR_LOG] + ([Path(os.environ["TATKAL_ANCHOR_DIR"]) / "tatkal_anchors.jsonl"]
                               if os.getenv("TATKAL_ANCHOR_DIR") else [])
MAX_AUDIO_SECONDS = 30              # Sarvam REST STT limit
MAX_AUDIO_BYTES = 2_000_000          # about 30 s of webm; Sarvam REST STT accepts up to 30 s

app = FastAPI(title="Tatkal", version="3.9",
              description="UPI dispute teammate. Demo only: PSP, acquiring ledger and UDIR rails are mocked; no money moves.")

rails = MockRails()
audit = AuditLog(str(DB_PATH))   # persistent: never wiped on startup
tatkal = Tatkal(rails, audit, today=DEMO_DATE)
STATE = {"mode": "templates"}                      # templates | live | simulated


LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def admin(request: Request, x_demo_admin: str | None = Header(default=None)):
    """Operator controls (open cases, speak as the console, reset, rush, tamper, focus).
    With TATKAL_ADMIN_TOKEN set: the X-Demo-Admin header must match.
    Without it: only the presenting laptop itself may use them. A request that came through a
    Cloudflare tunnel or a proxy, or from another device on the Wi-Fi, is refused. Judges' phones
    only ever need /join, /live/join, /live/state, /live/reply and /voice."""
    if ADMIN_TOKEN:
        if not hmac.compare_digest(x_demo_admin or "", ADMIN_TOKEN):
            raise HTTPException(401, "admin token required")
        return
    proxied = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    host = request.client.host if request.client else ""
    if proxied or host not in LOOPBACK:
        raise HTTPException(403, "Operator controls only work on the presenting laptop. "
                                 "Set TATKAL_ADMIN_TOKEN to use them from another device.")


def _offline() -> bool:
    return os.getenv("TATKAL_OFFLINE") == "1"


def _second_reader():
    if _offline():
        return None
    r = SarvamLLM()
    return r if r.available else None


def _configure():
    # No second reader means every "yes" goes to a person, unless the labelled single key demo switch is on.
    tatkal.single_key_override = os.getenv("TATKAL_SINGLE_KEY_DEMO") == "1"
    rails.allow_single_key = tatkal.single_key_override
    if STATE["mode"] == "live":
        if _offline():
            raise HTTPException(400, "TATKAL_OFFLINE=1; live Sarvam mode is off")
        llm = SarvamLLM()
        if not llm.available:
            raise HTTPException(400, "SARVAM_API_KEY is not set; use templates or simulated")
        tatkal.llm, tatkal.reader = llm, llm
    elif STATE["mode"] == "simulated":
        tatkal.llm, tatkal.reader = SimulatedRiskyDrafter(), None
    else:
        # default: the engine writes every word; Sarvam (if a key is set) is only the second reader
        tatkal.llm, tatkal.reader = None, _second_reader()


class OpenReq(BaseModel):
    txn_id: str
    fresh: bool = False


class SayReq(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    source: str = Field(default="text", pattern="^(text|voice)$")


class DraftReq(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    audience: str = "customer"


class ModeReq(BaseModel):
    mode: str


class FlagReq(BaseModel):
    on: bool


class TTSReq(BaseModel):
    text: str = Field(min_length=1, max_length=1200)
    part: int = Field(default=0, ge=0, le=20)


_WARMED: "OrderedDict[str, None]" = OrderedDict()     # bounded, same size as the voice cache
_WARMED_MAX = 400
TTS_MAX_PARTS = 20                   # sentences per message we will ever synthesise (no Tatkal message comes close)
TTS_RATE = (600, 60.0)               # 600 sentence requests per phone per minute: abuse brake only, never hit in a demo
_TTS_HITS: dict[str, deque] = {}
_TTS_LOCK = threading.Lock()


def _prefetch_on() -> bool:
    """On by default for the demo: every new Tatkal message (names, amounts) is voiced by Sarvam the
    moment it appears, so play and auto speak start at once. Set TATKAL_TTS_PREFETCH=0 to turn off."""
    return (os.getenv("TATKAL_TTS_PREFETCH", "1") != "0" and os.getenv("TATKAL_OFFLINE") != "1"
            and bool(os.getenv("SARVAM_API_KEY")))


def _warm_voice(c) -> None:
    """Start text to speech for every new Tatkal message in the background (cached), so pressing
    play or auto speak is near instant. On unless TATKAL_TTS_PREFETCH=0, offline or without a key."""
    if not _prefetch_on():
        return
    for t in c.transcript:
        if t.get("who") == "tatkal" and t["text"] not in _WARMED:
            _WARMED[t["text"]] = None
            while len(_WARMED) > _WARMED_MAX:
                _WARMED.popitem(last=False)
            try:
                voice.prefetch(M.speakable(t["text"]))
            except Exception:  # noqa: BLE001  speech is a nicety; never break the case
                pass


def _is_tatkal_line(text: str) -> bool:
    """Only words Tatkal actually said in an open case may be voiced."""
    for case in list(tatkal.cases.values()):
        for t in list(case.transcript):
            if t.get("who") == "tatkal" and t.get("text") == text:
                return True
    return False


def _tts_rate_ok(client: str) -> bool:
    limit, window = TTS_RATE
    now = time.monotonic()
    with _TTS_LOCK:
        q = _TTS_HITS.setdefault(client, deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        if len(_TTS_HITS) > 1000:                      # keep the table itself bounded
            for k in [k for k, v in _TTS_HITS.items() if not v][:500]:
                _TTS_HITS.pop(k, None)
        return True



if _prefetch_on():          # pre voice every line the scenarios can produce (sandboxed, background)
    warm.start(voice, DEMO_DATE)


def _view(c) -> dict:
    _warm_voice(c)
    return {**c.view(), "txn": rails.full(c.txn_id)}


def _case(txn_id: str):
    if txn_id not in tatkal.cases:
        raise HTTPException(404, "Open the case first")
    return tatkal.cases[txn_id]


@app.get("/")
def home():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/health")
def health():
    return {"anchors": verify_anchors(), "ok": True, "rails": "mock", "mode": STATE["mode"], "sarvam_key": bool(os.getenv("SARVAM_API_KEY")),
            "drop_ack": rails.drop_ack, "demo_today": tatkal.today.isoformat(), "admin_protected": bool(ADMIN_TOKEN),
            "voice_prefetch": _prefetch_on(), "voice_warm_lines": warm.STATUS["lines"],
            "second_reader": ("sarvam-105b" if _second_reader() else
                              "single key override" if os.getenv("TATKAL_SINGLE_KEY_DEMO") == "1" else "none")}


@app.get("/scenarios")
def scenarios():
    return [{"id": s["id"], "title": s["title"], "tag": s["tag"], "txn_id": s["txn"]["txn_id"],
             "payer_app": s["txn"].get("payer_app"), "incoming": bool(s.get("incoming_complaint"))} for s in SCENARIOS]


@app.post("/mode", dependencies=[Depends(admin)])
def set_mode(req: ModeReq):
    if req.mode not in ("templates", "live", "simulated"):
        raise HTTPException(400, "mode must be templates, live or simulated")
    STATE["mode"] = req.mode
    _configure()
    return health()


@app.post("/rails/drop_ack", dependencies=[Depends(admin)])
def drop_ack(req: FlagReq):
    rails.drop_ack = req.on
    return health()


@app.post("/reset", dependencies=[Depends(admin)])
def reset():
    """Explicit demo reset. The audit chain keeps a record that it was reset."""
    rails.reset()                  # new rail generation: complaint ids continue, old warrants are refused
    tatkal.rotate_policy()         # and the signing key changes too
    tatkal.cases.clear()
    audit.append("demo_reset", {"by": "operator"}, case_id="", actor="operator")
    return {"ok": True}


@app.post("/cases/open", dependencies=[Depends(admin)])
def open_case(req: OpenReq):
    scen = next((s for s in SCENARIOS if s["txn"]["txn_id"] == req.txn_id), None)
    if scen is None:
        raise HTTPException(404, "Unknown transaction")
    _configure()
    if req.fresh and req.txn_id in tatkal.cases:
        archived = rails.restart_scenario(req.txn_id)
        tatkal.cases.pop(req.txn_id)
        audit.append("scenario_restarted", {"archived_complaints": archived}, case_id=req.txn_id, actor="operator")
    try:
        c = tatkal.open_case(req.txn_id, opened_by="incoming_udir" if scen.get("incoming_complaint") else "customer",
                             llm_words=STATE["mode"] != "templates")
    except IllegalTransition as e:
        raise HTTPException(409, str(e))
    LIVE["focus"] = c.txn_id          # judges' phones follow whatever case is on the big screen
    return _view(c)


@app.get("/cases/{txn_id}")
def get_case(txn_id: str):
    return _view(_case(txn_id))


@app.post("/cases/{txn_id}/customer", dependencies=[Depends(admin)])
def customer(txn_id: str, req: SayReq):
    _case(txn_id)
    _configure()
    tatkal.customer_says(txn_id, req.text, llm_words=STATE["mode"] != "templates")
    return _view(tatkal.cases[txn_id])


@app.post("/cases/{txn_id}/merchant", dependencies=[Depends(admin)])
def merchant(txn_id: str, req: SayReq):
    _case(txn_id)
    _configure()
    try:
        tatkal.merchant_says(txn_id, req.text, source=req.source)
    except IllegalTransition as e:
        raise HTTPException(409, str(e))
    return _view(tatkal.cases[txn_id])


@app.post("/cases/{txn_id}/recheck", dependencies=[Depends(admin)])
def recheck(txn_id: str):
    _case(txn_id)
    return _view(tatkal.recheck_ack(txn_id))


@app.post("/cases/{txn_id}/draft", dependencies=[Depends(admin)])
def draft(txn_id: str, req: DraftReq):
    _case(txn_id)
    return tatkal.check_draft(txn_id, req.text, req.audience)


@app.get("/audit")
def audit_events(case_id: str | None = None, limit: int = 200):
    return {"verify": audit.verify(), "events": audit.events(case_id, min(limit, 500))}


@app.get("/audit/anchor", dependencies=[Depends(admin)])
def anchor():
    """Signed chain head. Store it outside the database (or print it) so a rewrite of the
    whole chain is detectable too, not just an edit of one row."""
    v = audit.verify()
    head = v.get("head", "")
    out = {"head": head, "count": v.get("count"), "valid": v["valid"],
           "signature": hmac.new(ANCHOR_KEY, f"{head}:{v.get('count')}".encode(), hashlib.sha256).hexdigest()}
    for path in ANCHOR_SINKS:                                # local copy plus an optional second location
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(out) + "\n")
    return {**out, "copies": [str(p) for p in ANCHOR_SINKS]}


def verify_anchors() -> dict:
    """Re-check every saved checkpoint against the live chain: the signature must match and the
    event at position `count` must still have hash `head`. Detects a chain rewritten after a
    checkpoint was taken. Tamper EVIDENT, not tamper proof: someone with write access to the
    host and the key could rewrite everything; ship checkpoints to TATKAL_ANCHOR_DIR on another
    machine or an append only store to close that gap."""
    events = {e["seq"]: e["hash"] for e in audit.events()}
    res = {"checked": 0, "ok": 0, "bad": []}
    for path in ANCHOR_SINKS:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                a = json.loads(line)
            except ValueError:
                continue
            res["checked"] += 1
            sig = hmac.new(ANCHOR_KEY, f"{a['head']}:{a['count']}".encode(), hashlib.sha256).hexdigest()
            if hmac.compare_digest(sig, a.get("signature", "")) and (not a["count"] or events.get(a["count"]) == a["head"]):
                res["ok"] += 1
            else:
                res["bad"].append({"file": path.name, "count": a.get("count")})
    return res


@app.get("/audit/anchors/verify")
def anchors_verify():
    return verify_anchors()


@app.post("/audit/tamper", dependencies=[Depends(admin)])
def tamper():
    """Simulates an insider editing the latest ruling to claim a T+1 deadline, on a copy of the
    chain, and shows that verification catches it. The real log is left untouched."""
    ev = next((e for e in reversed(audit.events()) if e["kind"] == "ruling"), None)
    if not ev:
        raise HTTPException(400, "Open a case first")
    forged = {**ev["payload"], "deadline": "2026-11-01", "tat_days": 1}
    return {"tampered_seq": ev["seq"], "verify": audit.simulate_tamper(ev["seq"], forged), "real_log": audit.verify()}


@app.post("/voice/stt")
async def stt(file: UploadFile = File(...)):
    data = await file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Clip larger than 2 MB: record a shorter answer")
    secs = await anyio.to_thread.run_sync(voice.duration_seconds, data)
    if secs is not None and secs > MAX_AUDIO_SECONDS:
        raise HTTPException(413, f"Clip is {secs:.0f} s; Sarvam's REST speech to text accepts up to {MAX_AUDIO_SECONDS} s")
    try:
        return await anyio.to_thread.run_sync(voice.transcribe, data, file.filename or "clip.webm",
                                              file.content_type or "audio/webm")
    except Exception as e:  # noqa: BLE001  demo must degrade to text
        raise HTTPException(503, f"Speech to text unavailable: {str(e)[:160]}")


@app.post("/voice/tts")
async def tts(req: TTSReq, request: Request):
    """One sentence of a Tatkal message as MP3. Refuses any text Tatkal did not say in an open case,
    caps sentences per message and rate limits each client, so a tunnel URL cannot burn credits."""
    if not _is_tatkal_line(req.text):
        raise HTTPException(400, "Only messages Tatkal sent in an open case can be spoken")
    text = M.speakable(req.text)
    parts = voice.split_sentences(text)
    if len(parts) > TTS_MAX_PARTS or req.part >= len(parts):
        raise HTTPException(400, f"Message has {len(parts)} sentences; at most {TTS_MAX_PARTS} are spoken")
    # Behind the Cloudflare tunnel every phone arrives from 127.0.0.1, so key on the visitor IP the
    # tunnel reports; the console on the laptop itself keys on its own address.
    who = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
    if not _tts_rate_ok(who):
        raise HTTPException(429, "Too many speech requests; wait a minute")
    try:
        return await anyio.to_thread.run_sync(voice.speak_part, text, req.part)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Text to speech unavailable: {str(e)[:160]}")


@app.get("/eval/latest")
def eval_latest():
    p = ROOT / "eval" / "results" / "summary.json"
    if not p.exists():
        return JSONResponse({"error": "Run: python -m eval.run_eval"}, status_code=404)
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- stage features (separate module)
from api import live as _live  # noqa: E402

LIVE = _live.register(app, tatkal, rails, _view, _configure, Depends(admin),
                      demo_port=int(os.getenv("TATKAL_PORT", "8000")))
if os.getenv("TATKAL_TUNNEL") == "1":      # start the https link for judges' phones with the server
    _live.start_tunnel(int(os.getenv("TATKAL_PORT", "8000")))
