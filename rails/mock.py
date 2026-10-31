"""Mock payment rails: PSP status, acquiring ledger and NPCI UDIR.

No money moves and nothing here talks to NPCI, a bank or Paytm. The mocks follow the
shape of the real flows closely enough to exercise every rule in the engine:
  * UDIR complaints are accepted or rejected with a reference and an acknowledgement flag.
  * Payee side responses (105, 106) are idempotent on (complaint_id, code).
  * A rail can be told to drop its acknowledgement, to prove Tatkal never says "done"
    without one.
"""
from __future__ import annotations

import copy
import itertools
import secrets
import threading

from engine.warrant import WarrantVerifier

from engine.rules import UDIR_CODES, RESOLVE_CODES

SCENARIOS = [
    {"id": "S1", "title": "₹450 at Sharma Kirana: debited, Soundbox silent (deemed)", "tag": "U022 · T+5",
     "txn": {"txn_id": "PTM41870295", "amount_inr": 450, "txn_date": "2026-10-31", "kind": "P2M",
             "status": "deemed", "payer_debited": True, "merchant_credited": False, "merchant_confirmed": False,
             "payer_app": "Paytm", "merchant_name": "Sharma Kirana Store", "customer_name": "Ravi"}},
    {"id": "S2", "title": "₹320: shop was paid, only the Soundbox missed it", "tag": "Soundbox",
     "txn": {"txn_id": "PTM41870301", "amount_inr": 320, "txn_date": "2026-10-31", "kind": "P2M",
             "status": "success", "payer_debited": True, "merchant_credited": True, "merchant_confirmed": False,
             "credited_on": "2026-10-31", "payer_app": "PhonePe", "merchant_name": "Sharma Kirana Store",
             "customer_name": "Anita"}},
    {"id": "S3", "title": "₹850: payment failed but money debited", "tag": "U009 · T+5",
     "txn": {"txn_id": "PTM41870333", "amount_inr": 850, "txn_date": "2026-10-31", "kind": "P2M",
             "status": "failed", "payer_debited": True, "merchant_credited": False, "merchant_confirmed": False,
             "payer_app": "Google Pay", "merchant_name": "Gupta Medical", "customer_name": "Imran"}},
    {"id": "S4", "title": "₹450 U022 from PhonePe; ledger shows the shop was paid: Soundbox asks the shopkeeper", "tag": "Payee · 105",
     "txn": {"txn_id": "PTM41870340", "amount_inr": 450, "txn_date": "2026-10-31", "kind": "P2M",
             "status": "deemed", "payer_debited": True, "merchant_credited": True, "merchant_confirmed": False,
             "credited_on": "2026-10-31", "payer_app": "PhonePe", "merchant_name": "Sharma Kirana Store",
             "customer_name": "Ravi"},
     "incoming_complaint": "U022"},
    {"id": "S5", "title": "₹199 charged twice for one purchase", "tag": "U023 · 106",
     "txn": {"txn_id": "PTM41870355", "amount_inr": 199, "txn_date": "2026-10-31", "kind": "P2M",
             "status": "success", "payer_debited": True, "merchant_credited": True, "merchant_confirmed": True,
             "duplicate_of": "PTM41870354", "credited_on": "2026-10-31", "payer_app": "Paytm",
             "merchant_name": "Chai Point Sector 18", "customer_name": "Neha"}},
    {"id": "S6", "title": "₹30,000 phone: paid, customer says no goods", "tag": "To a person",
     "txn": {"txn_id": "PTM41870377", "amount_inr": 30000, "txn_date": "2026-10-31", "kind": "P2M",
             "status": "success", "payer_debited": True, "merchant_credited": True, "merchant_confirmed": True,
             "goods_disputed": True, "credited_on": "2026-10-31", "payer_app": "Paytm",
             "merchant_name": "Mobile Zone Karol Bagh", "customer_name": "Vikas"}},
    {"id": "S7", "title": "₹600 deemed since 24 Oct: RBI deadline missed", "tag": "Late · ₹100/d",
     "txn": {"txn_id": "PTM41869911", "amount_inr": 600, "txn_date": "2026-10-24", "kind": "P2M",
             "status": "deemed", "payer_debited": True, "merchant_credited": False, "merchant_confirmed": False,
             "payer_app": "Paytm", "merchant_name": "Verma Sweets", "customer_name": "Pooja"}},
    {"id": "S8", "title": "₹700 U022 from Google Pay, but the shop was never credited", "tag": "No credit",
     "txn": {"txn_id": "PTM41870388", "amount_inr": 700, "txn_date": "2026-10-31", "kind": "P2M",
             "status": "deemed", "payer_debited": True, "merchant_credited": False, "merchant_confirmed": False,
             "payer_app": "Google Pay", "merchant_name": "Bansal Electricals", "customer_name": "Sameer"},
     "incoming_complaint": "U022"},
]


class MockRails:
    def __init__(self):
        self.verifier: WarrantVerifier | None = None   # holds the policy PUBLIC key only; cannot sign
        self.allow_single_key = False                  # labelled demo override, off by default
        self._lock = threading.RLock()                 # check, spend and commit happen as one step
        self._ids = itertools.count(870295)            # complaint ids are NEVER reused, not even after reset
        self.reset()

    def trust_policy(self, public_key: bytes) -> None:
        """Install the policy gate's public key. The rail can verify warrants, never mint them."""
        if self.verifier is None or self.verifier._pub.public_bytes_raw() != public_key:
            self.verifier = WarrantVerifier(public_key)

    def reset(self):
        self.txns = {s["txn"]["txn_id"]: copy.deepcopy(s["txn"]) for s in SCENARIOS}
        self.complaints: dict[str, dict] = {}
        self.responses: dict[str, dict] = {}   # ONE terminal 105/106 per complaint, keyed by complaint id
        self.generation = secrets.token_hex(4)          # bound into every warrant; changes on reset
        self.drop_ack = False          # demo switch: rail accepts but never acknowledges
        self.archive: list[dict] = []  # complaints from earlier runs of a scenario, never deleted
        self.require_warrant = True

    def restart_scenario(self, txn_id: str) -> int:
        """Demo helper: restore a scenario's transaction and archive (not delete) its complaints."""
        scen = next(s for s in SCENARIOS if s["txn"]["txn_id"] == txn_id)
        self.txns[txn_id] = copy.deepcopy(scen["txn"])
        old = [c for c in self.complaints.values() if c["txn_id"] == txn_id]
        for c in old:
            self.archive.append({**c, "archived": True})
            self.complaints.pop(c["complaint_id"])
            if c["complaint_id"] in self.responses:
                self.archive.append({**self.responses.pop(c["complaint_id"]), "archived": True})
        return len(old)

    def recheck(self, complaint_id: str) -> dict:
        """Status enquiry on a complaint (UDIR ReqChkTxn). Acknowledges if the rail is healthy now."""
        c = self.complaints[complaint_id]
        if c["status"] == "accepted" and not self.drop_ack:
            c["acknowledged"] = True
        return dict(c)

    # PSP and acquiring
    def status(self, txn_id: str) -> dict:
        if txn_id not in self.txns:
            raise KeyError(f"Transaction {txn_id} not found")
        t = self.txns[txn_id]
        return {k: t.get(k) for k in ("txn_id", "amount_inr", "txn_date", "kind", "status", "payer_debited",
                                      "payer_app", "reversed_on", "duplicate_of")}

    def ledger(self, txn_id: str) -> dict:
        t = self.txns[txn_id]
        return {"txn_id": txn_id, "merchant_credited": t.get("merchant_credited", False),
                "merchant_confirmed": t.get("merchant_confirmed", False), "credited_on": t.get("credited_on"),
                "merchant_name": t.get("merchant_name"), "goods_disputed": t.get("goods_disputed", False)}

    def full(self, txn_id: str) -> dict:
        if txn_id not in self.txns:
            raise KeyError(f"Transaction {txn_id} not found")
        return copy.deepcopy(self.txns[txn_id])

    # UDIR
    def raise_complaint(self, txn_id: str, code: str) -> dict:
        if code not in UDIR_CODES:
            raise ValueError(f"{code} is not a UDIR code")
        t = self.txns[txn_id]
        for c in self.complaints.values():           # idempotent: one open complaint per txn and code
            if c["txn_id"] == txn_id and c["code"] == code:
                return {**c, "duplicate_request": True}
        cid = f"UDIR-{next(self._ids)}-{code}"
        ok = t["payer_debited"]
        c = {"complaint_id": cid, "txn_id": txn_id, "code": code,
             "status": "accepted" if ok else "rejected", "acknowledged": ok and not self.drop_ack,
             "route": "payer PSP -> NPCI UDIR -> payee PSP" if t["kind"] == "P2M" else "payer PSP -> NPCI UDIR -> beneficiary bank"}
        self.complaints[cid] = c
        return dict(c)

    def respond(self, complaint_id: str, code: str, warrant: dict | None = None) -> dict:
        """Payee side UDIR response. Commit time checks, in order: complaint exists, is still
        acknowledged, code is valid, no OTHER terminal response exists for this complaint, the ledger
        shows the credit, and the call carries a warrant signed by the policy gate (verified with its
        public key), bound to exactly this complaint, code, transaction and amount, unspent, unexpired,
        whose two readers agree with the code.

        105 (delivered) and 106 (refund will be initiated) are alternative outcomes. Once one is
        committed, even if its acknowledgement was lost, a different code is refused; the same code
        again returns the committed result (idempotent retry)."""
        with self._lock:   # a real adapter: one DB transaction with a unique constraint on complaint_id
            if complaint_id not in self.complaints:
                raise KeyError("Complaint not found")
            c = self.complaints[complaint_id]
            if not c.get("acknowledged"):
                raise ValueError("Complaint not acknowledged; the payee side cannot answer it yet")
            if code not in RESOLVE_CODES:
                raise ValueError(f"{code} is not a payee resolve code")
            prior = self.responses.get(complaint_id)
            if prior:
                if prior["response_code"] == code:
                    return {**prior, "duplicate_request": True}
                raise ValueError(f"Complaint already has terminal response {prior['response_code']}; {code} refused")
            if not self.txns[c["txn_id"]].get("merchant_credited"):
                raise ValueError("Payee ledger shows no credit for this transaction; 105/106 refused")
            wid = None
            if self.require_warrant:
                t = self.txns[c["txn_id"]]
                if self.verifier is None:
                    raise ValueError("Rail has no policy public key installed; 105/106 refused")
                wid = self.verifier.spend(warrant, complaint_id=complaint_id, code=code, txn_id=c["txn_id"],
                                          amount_inr=t["amount_inr"], allow_single_key=self.allow_single_key,
                                          generation=self.generation)
            r = {"complaint_id": complaint_id, "response_code": code, "meaning": RESOLVE_CODES[code],
                 "status": "accepted", "acknowledged": not self.drop_ack, "warrant": wid}
            self.responses[complaint_id] = r
            self.complaints[complaint_id]["response_code"] = code
            return dict(r)
