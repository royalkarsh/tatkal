"""Diwali rush: push a large batch of SYNTHETIC stuck payments through the REAL rule engine and
the REAL grounding guard, and count what happens.

Nothing here is a production volume estimate. The transaction mix below is our assumption,
chosen to exercise every path; the decisions and every message check are made by the same
code the console uses (engine.rules.classify, engine.messages, engine.guard.check).

Context shown next to the run: UPI processed 754 million payments worth Rs 1.02 lakh crore on
18 Oct 2025, Dhanteras (NPCI data reported by Business Today, 22 Oct and 1 Nov 2025).
"""
from __future__ import annotations

import random
import threading
import time
from datetime import date, timedelta

from engine import messages as M
from engine.guard import check
from engine.rules import Txn, classify

MIX = {   # shown in the UI as "our assumption"
    "P2M share": "88%", "status": "deemed 55%, failed 30%, success 15%",
    "shop already credited (deemed or success)": "35%", "payer app is not Paytm (incoming U022)": "50% of credited",
    "failed payment already reversed": "40%", "duplicate debit": "3%", "goods disputed": "2% of credited",
    "payer not debited": "5%", "amount at or above Rs 25,000": "about 1%", "payment date": "today to 8 days ago",
}
AMOUNTS = [20, 40, 60, 99, 120, 150, 199, 250, 320, 450, 499, 600, 750, 899, 1200, 1500, 2400, 3999, 6500, 30000]
WEIGHTS = [6, 8, 8, 7, 8, 7, 7, 7, 6, 6, 5, 5, 4, 4, 3, 3, 2, 1.5, 1, 0.6]
SHOPS = ["Sharma Kirana", "Gupta Medical", "Verma Sweets", "Bansal Electricals", "Chai Point", "Agarwal Stores",
         "Singh Dairy", "Mehta Mobiles", "Khan Bakery", "Rao Tiffins", "Iyer Provisions", "Das Garments"]
NAMES = ["Ravi", "Anita", "Imran", "Neha", "Vikas", "Pooja", "Sameer", "Kavya", "Arjun", "Fatima", "Rohan", "Meera"]
BUCKET = {   # what the person on the ground experiences
    "SOUNDBOX_MISSED": "told", "NO_DEBIT": "told", "P2M_REVERSED": "told", "P2P_CREDITED": "told",
    "P2M_PENDING": "clock", "P2M_FAILED_DEBITED": "clock", "P2P_PENDING": "clock", "P2P_FAILED_DEBITED": "clock",
    "DUPLICATE": "ask", "PAYEE_CREDITED": "ask", "GOODS_DISPUTE": "person", "HUMAN_REVIEW": "person",
}


def synth(rng: random.Random, i: int, today: date) -> tuple[Txn, bool]:
    p2m = rng.random() < 0.88
    debited = rng.random() >= 0.05
    s = rng.random()
    status = "deemed" if s < 0.55 else "failed" if s < 0.85 else "success"
    credited = status != "failed" and rng.random() < 0.35
    d = today - timedelta(days=min(8, int(rng.expovariate(0.55))))
    t = Txn(txn_id=f"SIM{i:07d}", amount_inr=rng.choices(AMOUNTS, WEIGHTS)[0], txn_date=d,
            kind="P2M" if p2m else "P2P", status=status, payer_debited=debited, merchant_credited=credited and debited,
            merchant_name=rng.choice(SHOPS), customer_name=rng.choice(NAMES),
            payer_app="Paytm" if rng.random() < 0.5 else rng.choice(["PhonePe", "Google Pay", "BHIM"]))
    if status == "failed" and rng.random() < 0.4:
        t.reversed_on = min(today, d + timedelta(days=rng.randint(0, 7)))
    if p2m and debited and rng.random() < 0.03:
        t.duplicate_of = f"SIM{i:07d}A"
    if t.merchant_credited and rng.random() < 0.02:
        t.goods_disputed = True
    incoming = p2m and t.merchant_credited and t.payer_app != "Paytm"
    return t, incoming


def new_stats(n: int) -> dict:
    return {"target": n, "done": 0, "running": False, "finished": False, "engine_seconds": 0.0, "wall_seconds": 0.0,
            "buckets": {"told": 0, "clock": 0, "ask": 0, "person": 0}, "paths": {},
            "complaints": 0, "hold_goods": 0, "questions": 0, "to_person": 0, "late": 0, "compensation_inr": 0,
            "at_risk_inr": 0, "messages_checked": 0, "unique_messages": 0, "guard_failures": 0, "recent": [],
            "sample": None, "mix": MIX}


def process(t: Txn, incoming: bool, today: date, st: dict, cache: dict) -> str:
    r = classify(t, today, incoming_code="U022" if incoming else None)
    b = "person" if r.escalate else BUCKET.get(r.path, "person")
    st["buckets"][b] += 1
    st["paths"][r.path] = st["paths"].get(r.path, 0) + 1
    st["complaints"] += bool(r.udir_code) and r.path not in ("HUMAN_REVIEW",)
    st["hold_goods"] += r.merchant_should_hand_over_goods is False and t.kind == "P2M" and t.payer_debited
    st["questions"] += b == "ask"
    st["to_person"] += b == "person"
    st["late"] += r.days_late > 0
    st["compensation_inr"] += r.compensation_due_inr
    st["at_risk_inr"] += r.money_at_risk_inr
    rd = r.to_dict()
    txn = {"amount_inr": t.amount_inr, "txn_date": t.txn_date.isoformat(), "merchant_name": t.merchant_name,
           "customer_name": t.customer_name, "merchant_credited": t.merchant_credited,
           "reversed_on": t.reversed_on.isoformat() if t.reversed_on else None, "credited_on": None}
    ack = {"udir_complaint": bool(r.udir_code), "udir_response": False, "refund_initiated": False}
    said = [("customer", M.customer_message(rd, txn, ack, "UDIR-SIM" if r.udir_code else None))]
    if t.kind == "P2M":
        said.append(("merchant", M.merchant_message(rd, txn, "human" if r.escalate else "ask_goods" if r.path == "PAYEE_CREDITED"
                                                    else "ask_refund" if r.path == "DUPLICATE" else "status")))
    for audience, msg in said:
        st["messages_checked"] += 1
        key = (audience, msg, r.path, r.escalate, r.days_late > 0)
        if key not in cache:
            cache[key] = check(msg, M.facts_for_guard(rd, txn, ack, audience)).ok
            st["unique_messages"] += 1
        if not cache[key]:
            st["guard_failures"] += 1
    if st["sample"] is None or b == "ask":
        st["sample"] = {"case": t.txn_id, "amount": t.amount_inr, "path": r.path, "shop": t.merchant_name,
                        "customer": t.customer_name, "deadline": r.deadline}
    return b


class Rush:
    """Runs one simulation at a time in a background thread, paced so people can watch it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.stats = new_stats(0)
        self._stop = False

    def start(self, n: int = 100_000, seconds: float = 14.0, seed: int = 31, today: date | None = None) -> dict:
        with self.lock:
            if self.stats.get("running"):
                return self.stats
            self.stats = new_stats(n)
            self.stats["running"] = True
            self._stop = False
        threading.Thread(target=self._run, args=(n, seconds, seed, today or date(2026, 10, 31)), daemon=True).start()
        return self.stats

    def stop(self):
        self._stop = True

    def _run(self, n, seconds, seed, today):
        rng, cache, st = random.Random(seed), {}, self.stats
        chunk = max(500, n // 60)
        t_wall = time.perf_counter()
        i = 0
        while i < n and not self._stop:
            t0 = time.perf_counter()
            recent = []
            for _ in range(min(chunk, n - i)):
                t, inc = synth(rng, i, today)
                recent.append(process(t, inc, today, st, cache)[0])
                i += 1
            st["engine_seconds"] += time.perf_counter() - t0
            st["done"] = i
            st["recent"] = "".join(recent[:400])
            st["wall_seconds"] = time.perf_counter() - t_wall
            time.sleep(max(0.0, seconds / max(1, n / chunk) - (time.perf_counter() - t0)))
        st["running"], st["finished"] = False, True
        st["wall_seconds"] = time.perf_counter() - t_wall


def run_sync(n: int, seed: int = 31, today: date | None = None) -> dict:
    """Unpaced run for tests and the evaluation."""
    today = today or date(2026, 10, 31)
    rng, cache, st = random.Random(seed), {}, new_stats(n)
    t0 = time.perf_counter()
    for i in range(n):
        t, inc = synth(rng, i, today)
        process(t, inc, today, st, cache)
    st["done"], st["finished"], st["engine_seconds"] = n, True, time.perf_counter() - t0
    return st
