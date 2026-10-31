"""Pre voice the demo. At startup, replay every scenario and every quick reply in a private sandbox
(its own mock rails and an in memory audit log, nothing touches the real chain), collect every line
Tatkal would say, and hand them to Sarvam Bulbul in the background. When the same line appears on
stage, play starts from the cache instead of waiting 2 to 3 s for synthesis.

Reference codes are stripped by messages.speakable, so the spoken text does not depend on the
complaint id the sandbox happened to get."""
from __future__ import annotations

import threading
from datetime import date

from agent.service import Tatkal
from engine import messages as M
from engine.audit import AuditLog
from rails.mock import MockRails, SCENARIOS

CUSTOMER = ["Paisa kab tak wapas aayega?", "Kal tak pakka aa jayega na?", "Mujhe kisi insaan se baat karni hai"]
GOODS = ["Haan, saamaan de diya", "Nahi, saamaan nahi diya", "Haan de diya, lekin usne kuch nahi liya", "Refund kar diya maine"]
REFUND = ["Haan, refund kar do", "Nahi, refund mat karo", "Refund already kar diya", "Shayad, pata nahi"]
FLOWS = ([[(t, "text")] for t in GOODS + REFUND]
         + [[(t, "voice"), ("Haan, confirm", "voice")] for t in GOODS + REFUND]
         + [[(t, "voice"), ("Nahi", "voice")] for t in ("Haan, saamaan de diya", "Haan, refund kar do")]
         + [[("Haan de diya, lekin usne kuch nahi liya", "text"), ("Haan, saamaan de diya", "text")],
            [("Nahi, saamaan nahi diya", "text"), ("Refund already kar diya", "text")],
            [("Nahi, saamaan nahi diya", "text"), ("Haan, refund kar do", "voice"), ("Haan, confirm", "voice")]])


def _open(t: Tatkal, sc: dict):
    """Open exactly as the console route does."""
    return t.open_case(sc["txn"]["txn_id"], opened_by="incoming_udir" if sc.get("incoming_complaint") else "customer")


def demo_lines(today: date) -> list[str]:
    """Every distinct Tatkal line the scenarios and quick replies can produce (rule reader, labelled
    single key sandbox so the yes paths are reached)."""
    seen: dict[str, None] = {}

    def sandbox() -> Tatkal:
        return Tatkal(MockRails(), AuditLog(":memory:"), today=today, single_key_override=True)

    def collect(t: Tatkal, txn: str):
        for x in t.cases[txn].transcript:
            if x.get("who") == "tatkal":
                seen.setdefault(x["text"], None)

    for sc in SCENARIOS:
        txn = sc["txn"]["txn_id"]
        t = sandbox(); _open(t, sc); collect(t, txn)              # the opening lines come first: most likely on stage
    for sc in SCENARIOS:
        txn = sc["txn"]["txn_id"]
        for q in CUSTOMER:
            t = sandbox(); _open(t, sc)
            try:
                t.customer_says(txn, q)
            except Exception:  # noqa: BLE001
                pass
            collect(t, txn)
        for flow in FLOWS:
            t = sandbox(); _open(t, sc)
            for text, source in flow:
                try:
                    t.merchant_says(txn, text, source=source)
                except Exception:  # noqa: BLE001  some flows do not apply to some cases
                    break
            collect(t, txn)
    return list(seen)


STATUS = {"lines": 0, "started": False}


def start(voice, today: date) -> None:
    """Background warm up; never blocks startup and never raises."""
    if STATUS["started"]:
        return
    STATUS["started"] = True

    def run():
        try:
            lines = demo_lines(today)
            STATUS["lines"] = len(lines)
            for line in lines:
                voice.prefetch(M.speakable(line))
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=run, daemon=True, name="tts-warm").start()
