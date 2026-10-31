r"""Tatkal orchestrator: one case, two audiences, a state machine, a guard and a two key rule.

  LLM rewrites words      ->  guard checks every claim  ->  engine template if blocked or failed
  replies read twice      ->  rules authorise actions   ->  rail must acknowledge
  every step              ->  hash chained audit log

States
  OPEN -> RULED -> AWAITING_ACK -> COMPLAINT_RAISED -> MERCHANT_ASKED -> RESOLVED
               \-> NO_ACTION_NEEDED                                  \-> ESCALATED
  A human can be requested from any state, including RESOLVED.
"""
from __future__ import annotations

import functools
import threading

from dataclasses import dataclass, field
from datetime import date

from engine import messages as M
from engine.audit import AuditLog
from engine.guard import check
from engine.rules import Txn, classify, authorize_merchant_response, POSITIVE
from engine.warrant import PolicySigner, WarrantError
from agent import nlu
from agent.llm import LLMFailure

TRANSITIONS = {
    "OPEN": {"RULED"},
    "RULED": {"AWAITING_ACK", "COMPLAINT_RAISED", "NO_ACTION_NEEDED", "ESCALATED"},
    "AWAITING_ACK": {"AWAITING_ACK", "COMPLAINT_RAISED", "ESCALATED"},
    "COMPLAINT_RAISED": {"MERCHANT_ASKED", "ESCALATED", "RESOLVED"},
    "MERCHANT_ASKED": {"MERCHANT_ASKED", "RESOLVED", "ESCALATED"},
    "NO_ACTION_NEEDED": {"ESCALATED"},
    "RESOLVED": {"ESCALATED"},
    "ESCALATED": set(),
}
NO_COMPLAINT_PATHS = {"SOUNDBOX_MISSED", "NO_DEBIT", "P2M_REVERSED", "P2P_CREDITED", "HUMAN_REVIEW"}
MAX_ACK_RECHECKS = 2
QUESTIONS = {
    "ask_goods": ("Did you hand over the goods for this payment?", ["goods_delivered_yes", "goods_not_delivered", "unclear"]),
    "ask_refund": ("Do you authorise a refund of this payment?", ["refund_yes", "refund_declined", "unclear"]),
}


class IllegalTransition(Exception):
    pass


@dataclass
class Case:
    txn_id: str
    state: str = "OPEN"
    ruling: dict = field(default_factory=dict)
    complaint: dict | None = None
    response: dict | None = None
    asked: str | None = None               # ask_goods | ask_refund
    unclear_count: int = 0
    ack_rechecks: int = 0
    incoming: bool = False
    ack: dict = field(default_factory=lambda: {"udir_complaint": False, "udir_response": False, "refund_initiated": False})
    transcript: list[dict] = field(default_factory=list)
    guard_log: list[dict] = field(default_factory=list)
    handoff: dict | None = None
    last_gate: dict | None = None
    pending_confirm: dict | None = None     # a spoken yes waiting for its read back confirmation

    def view(self) -> dict:
        return {k: getattr(self, k) for k in ("txn_id", "state", "ruling", "complaint", "response", "asked", "incoming",
                                              "unclear_count", "ack", "transcript", "guard_log", "handoff", "last_gate",
                                              "pending_confirm")}



_LOCKS_GUARD = threading.Lock()


def _per_case(fn):
    """Serialise state changing calls on one case (two judges' phones answering at once).
    Behaviour preserving: a re entrant lock per txn_id, held for the whole call."""
    @functools.wraps(fn)
    def run(self, txn_id, *a, **k):
        with _LOCKS_GUARD:
            locks = self.__dict__.setdefault("_case_locks", {})
            lock = locks.setdefault(txn_id, threading.RLock())
        with lock:
            return fn(self, txn_id, *a, **k)
    return run


class Tatkal:
    def __init__(self, rails, audit: AuditLog, llm=None, today: date | None = None, reader=None,
                 single_key_override: bool = False):
        self.rails, self.audit, self.llm = rails, audit, llm
        self.reader = reader                       # second key for merchant replies (Sarvam 105B)
        # Without a second reader, a "yes" goes to a person. single_key_override exists ONLY for offline
        # tests and a no key demo; it is logged on every action and shown in red in the console.
        self.single_key_override = single_key_override
        self.rotate_policy()
        if single_key_override:
            rails.allow_single_key = True             # labelled demo config, applies to both sides
        self.today = today or date(2026, 10, 31)
        self.cases: dict[str, Case] = {}


    # ---------------------------------------------------------- plumbing
    def rotate_policy(self) -> None:
        """New signing key; the rail trusts only the new public key, so every outstanding warrant dies.
        Called at start and on every demo reset."""
        self._policy = PolicySigner()                       # private key stays with the policy gate
        self.rails.trust_policy(self._policy.public_key)    # the rail only ever gets the public key

    def _log(self, case: Case, kind: str, payload: dict, actor: str = "engine"):
        return self.audit.append(kind, payload, case_id=case.txn_id, actor=actor)

    def _move(self, case: Case, to: str, why: str):
        if to not in TRANSITIONS[case.state]:
            raise IllegalTransition(f"{case.state} -> {to} is not allowed ({why})")
        self._log(case, "state", {"from": case.state, "to": to, "why": why})
        case.state = to

    def _txn(self, txn_id: str) -> dict:
        return self.rails.full(txn_id)

    def _channel(self, txn: dict) -> str:
        app = txn.get("payer_app") or "Paytm"
        return "Paytm app" if app == "Paytm" else f"{app} app (status via NPCI UDIR)"

    def _say(self, case: Case, audience: str, template: str, *, allow_llm: bool = False, said: str = "") -> dict:
        """The LLM may rewrite customer explanations; the guard checks; the template is the fallback."""
        txn = self._txn(case.txn_id)
        facts = M.facts_for_guard(case.ruling, txn, case.ack, audience, state=case.state)
        source, draft, llm_error = "template", template, None
        if allow_llm and audience == "customer" and self.llm is not None:
            try:
                draft = self.llm.draft({**facts, "path": case.ruling["path"], "escalate": case.ruling["escalate"]},
                                       audience, template, said=said)
                source = getattr(self.llm, "label", "llm")
            except LLMFailure as e:
                llm_error = str(e)
                self._log(case, "llm_failed", {"error": llm_error, "fallback": "engine template"}, actor="llm")
            except Exception as e:  # noqa: BLE001  never let a model error break the case
                llm_error = f"{type(e).__name__}: {e}"
                self._log(case, "llm_failed", {"error": llm_error, "fallback": "engine template"}, actor="llm")
        verdict = check(draft, M.tighten_for_llm(facts, template) if source != "template" else facts)
        final, blocked = draft, not verdict.ok
        if blocked:
            final = template
            if not check(template, facts).ok:
                final = "Main aapka case check kar raha hoon. Abhi koi tareekh ya rakam ka vaada nahi kar sakta."
                self._log(case, "template_blocked", {"template": template}, actor="guard")
        entry = {"audience": audience, "source": source, "draft": draft, "verdict": verdict.to_dict(),
                 "final": final, "blocked": blocked, "llm_error": llm_error}
        case.guard_log.append(entry)
        self._log(case, "guard_blocked" if blocked else "guard_passed",
                  {"audience": audience, "source": source, "draft": draft,
                   "violations": [v["reason"] for v in verdict.to_dict()["violations"]]}, actor="guard")
        chan = self._channel(txn) if audience == "customer" else "Soundbox"
        case.transcript.append({"who": "tatkal", "to": audience, "text": final, "channel": chan})
        self._log(case, "message_sent", {"to": audience, "channel": chan, "text": final}, actor="tatkal")
        return entry

    def _escalate(self, case: Case, reason: str) -> dict:
        txn = self._txn(case.txn_id)
        r = case.ruling
        said = [t["text"] for t in case.transcript if t["who"] == "tatkal"]
        case.handoff = {
            "summary": (f"{txn.get('customer_name', 'Customer')} paid ₹{txn['amount_inr']:,} to {txn.get('merchant_name', 'merchant')} "
                        f"on {txn['txn_date']} via {txn.get('payer_app')}. Engine path {r['path']}"
                        f"{', UDIR ' + r['udir_code'] if r.get('udir_code') else ''}"
                        f"{', RBI row ' + r['rbi_row'] + ' deadline ' + r['deadline'] if r.get('rbi_row') else ''}. "
                        f"State before handoff: {case.state}. Escalated because: {reason}."),
            "reason": reason,
            "facts": {"txn": txn, "ruling": r, "complaint": case.complaint, "response": case.response},
            "already_told": said[-3:],
            "do_not": ["promise a date earlier than the RBI deadline", "ask for PIN, MPIN or OTP",
                       "send 105 or 106 without the merchant's explicit answer"],
            "next_best_action": "Call both parties, check counter evidence and the rail status, then decide 105, 106 or chargeback",
        }
        case.ruling = {**r, "escalate": True, "escalate_reasons": list(dict.fromkeys(r.get("escalate_reasons", []) + [reason]))}
        if case.state != "ESCALATED":
            self._move(case, "ESCALATED", reason)
        self._log(case, "handoff_created", {"reason": reason})
        return case.handoff

    def _ask_merchant(self, case: Case, stage: str):
        case.asked = stage
        self._move(case, "MERCHANT_ASKED", f"payee side question: {stage}")
        self._say(case, "merchant", M.merchant_message(case.ruling, self._txn(case.txn_id), stage))

    def _after_complaint_acked(self, case: Case):
        code = case.ruling["udir_code"]
        if case.incoming and code == "U022":
            if self.rails.ledger(case.txn_id)["merchant_credited"]:
                self._ask_merchant(case, "ask_goods")
            else:   # the shop never got the money: nothing to confirm or refund, so do not ask
                self._log(case, "no_payee_question", {"why": "payee ledger shows no credit; hold goods, banks reverse"})
                self._say(case, "merchant", M.merchant_message(case.ruling, self._txn(case.txn_id), "status"))
        elif code == "U023":
            self._ask_merchant(case, "ask_refund")

    def _register_complaint(self, case: Case, *, incoming: bool):
        code = case.ruling["udir_code"]
        c = self.rails.raise_complaint(case.txn_id, code)
        case.complaint = c
        ok = bool(c.get("acknowledged"))
        case.ack["udir_complaint"] = ok
        kind = ("udir_complaint_received" if incoming else "udir_complaint_raised") + ("" if ok else "_unacknowledged")
        self._log(case, kind, c, actor="rail")
        self._move(case, "COMPLAINT_RAISED" if ok else "AWAITING_ACK",
                   f"{code} {'acknowledged' if ok else 'sent, no acknowledgement yet'}")

    # ---------------------------------------------------------- flows
    @_per_case
    def open_case(self, txn_id: str, *, opened_by: str = "customer", llm_words: bool = False,
                  incoming_code: str = "U022") -> Case:
        if txn_id in self.cases:
            return self.cases[txn_id]                    # idempotent: opening twice returns the same case
        txn = self.rails.full(txn_id)
        case = Case(txn_id, incoming=(opened_by == "incoming_udir"))
        self.cases[txn_id] = case
        self._log(case, "case_opened", {"opened_by": opened_by, "psp_status": self.rails.status(txn_id),
                                        "ledger": self.rails.ledger(txn_id)})
        ruling = classify(Txn.from_dict(txn), self.today,
                          incoming_code=incoming_code if case.incoming else None).to_dict()
        case.ruling = ruling
        self._log(case, "ruling", ruling, actor="rule_engine")
        self._move(case, "RULED", ruling["path"])

        if case.incoming:
            case.transcript.append({"who": "system", "to": "customer", "channel": self._channel(txn),
                                    "text": f"{txn.get('customer_name', 'Customer')} raised {ruling['udir_code']} in "
                                            f"{txn.get('payer_app')}; it reached the payee side through NPCI UDIR."})
        else:
            case.transcript.append({"who": "customer", "text": "Mere paise kat gaye par dukaan wale ko nahi mile"})

        code = ruling["udir_code"]
        if code and ruling["path"] not in NO_COMPLAINT_PATHS:
            self._register_complaint(case, incoming=case.incoming)
        elif not ruling["escalate"]:
            self._move(case, "NO_ACTION_NEEDED", "ledger already explains what happened")

        self._say(case, "customer", M.customer_message(ruling, txn, case.ack, (case.complaint or {}).get("complaint_id"),
                                                       pending=case.state == "AWAITING_ACK"), allow_llm=llm_words)

        if ruling["escalate"]:
            self._say(case, "merchant", M.merchant_message(ruling, txn, "human"))
            self._escalate(case, "; ".join(ruling["escalate_reasons"]))
            return case
        if case.state == "COMPLAINT_RAISED" and (case.incoming or code == "U023"):
            self._after_complaint_acked(case)
        elif txn.get("kind") != "P2P":          # a P2P transfer has no shopkeeper to talk to
            self._say(case, "merchant", M.merchant_message(ruling, txn, "status"))
        return case

    @_per_case
    def recheck_ack(self, txn_id: str) -> Case:
        """Ask the rail again whether it acknowledged the complaint (UDIR status check)."""
        case = self.cases[txn_id]
        if case.state != "AWAITING_ACK":
            return case
        case.ack_rechecks += 1
        c = self.rails.recheck(case.complaint["complaint_id"])
        case.complaint = c
        self._log(case, "udir_status_check", c, actor="rail")
        if c.get("acknowledged"):
            case.ack["udir_complaint"] = True
            self._move(case, "COMPLAINT_RAISED", "acknowledged on recheck")
            txn = self._txn(txn_id)
            if not case.incoming:
                self._say(case, "customer", M.customer_message(case.ruling, txn, case.ack, c["complaint_id"]))
            if case.incoming or case.ruling["udir_code"] == "U023":
                self._after_complaint_acked(case)
        elif case.ack_rechecks >= MAX_ACK_RECHECKS:
            self._say(case, "merchant", M.merchant_message(case.ruling, self._txn(txn_id), "human"))
            self._escalate(case, "UDIR complaint never acknowledged after rechecks")
        else:
            self._move(case, "AWAITING_ACK", f"still no acknowledgement (check {case.ack_rechecks})")
        return case

    @_per_case
    def customer_says(self, txn_id: str, text: str, llm_words: bool = False) -> dict:
        case = self.cases[txn_id]
        case.transcript.append({"who": "customer", "text": text})
        intent = nlu.customer_intent(text)
        self._log(case, "customer_said", {"text": text, "intent": intent}, actor="customer")
        txn = self._txn(txn_id)
        if intent in ("ask_human", "distress") and case.state != "ESCALATED":
            self._escalate(case, "Customer asked for a human" if intent == "ask_human" else "Customer sounds distressed")
        if case.state == "RESOLVED" and case.response:
            tmpl = M.customer_outcome(case.response["response_code"], txn, case.ruling)
        else:
            tmpl = M.customer_message(case.ruling, txn, case.ack, (case.complaint or {}).get("complaint_id"),
                                      pending=case.state == "AWAITING_ACK")
        return self._say(case, "customer", tmpl, allow_llm=llm_words, said=text)

    def _read(self, case: Case, text: str) -> tuple[str, dict]:
        """Two key reading. Returns the label and how it was verified. A positive label is only
        two_key_verified when an independent second reader returned the same label."""
        det = nlu.read_merchant(text, case.asked)
        info = {"deterministic": det, "llm": None, "two_key_verified": False}
        if self.reader is not None:
            q, labels = QUESTIONS[case.asked]
            try:
                info["llm"] = self.reader.read_reply(q, labels, text)
            except Exception as e:  # noqa: BLE001
                info["llm"] = f"failed: {e}"
            if det in POSITIVE and info["llm"] != det:
                return "unclear", {**info, "two_key": "disagree: no action"}
            info["two_key"] = "agree" if info["llm"] == det else "no yes to confirm"
            info["two_key_verified"] = det in POSITIVE and info["llm"] == det
        elif det in POSITIVE:
            if self.single_key_override:
                info["two_key"] = "SINGLE KEY OVERRIDE (offline demo, not for production)"
                info["two_key_verified"] = True
            else:
                info["two_key"] = "second reader unavailable: a person must confirm"
        return det, info

    def _read_back(self, case: Case, text: str, code: str) -> str:
        txn = self._txn(case.txn_id)
        meaning = "aapne saamaan de diya" if code == "105" else "aap refund ki manzoori de rahe hain"
        plain = f"Ek baar confirm kijiye: {meaning}. Sahi hai to 'haan, confirm' boliye ya button dabaiye."
        heard = " ".join(text.split())[:70]
        cand = f"Maine suna: \"{heard}\". " + plain
        facts = M.facts_for_guard(case.ruling, txn, case.ack, "merchant", state=case.state)
        return cand if check(cand, facts).ok else plain

    def _reprise(self, case: Case, text: str, stage: str) -> str:
        """Targeted re ask: repeat what was heard, then ask the single question again
        (Stoyanchev, Liu, Hirschberg, SIGDIAL 2013: people clarify by echoing the heard part)."""
        txn = self._txn(case.txn_id)
        plain = M.merchant_message(case.ruling, txn, stage)
        heard = " ".join(text.split())[:70]
        cand = f"Maine suna: \"{heard}\". Isme saaf jawab nahi mila. " + plain.split(". ", 1)[-1]
        facts = M.facts_for_guard(case.ruling, txn, case.ack, "merchant", state=case.state)
        return cand if check(cand, facts).ok else plain

    @_per_case
    def merchant_says(self, txn_id: str, text: str, source: str = "text") -> dict:
        """source='voice' means the text came from speech to text. Both readers then read the SAME
        transcript, so they are independent classifiers, not independent evidence: a dropped 'nahi'
        fools both. A spoken yes therefore needs a read back confirmation before any 105/106."""
        case = self.cases[txn_id]
        case.transcript.append({"who": "merchant", "text": text, "source": source})
        txn = self._txn(txn_id)
        if case.state != "MERCHANT_ASKED":
            self._log(case, "merchant_said", {"text": text, "ignored": f"no question pending in state {case.state}"}, actor="merchant")
            return {"final": None, "action": None}
        confirmed = False
        if case.pending_confirm:
            pc, case.pending_confirm = case.pending_confirm, None
            verdict = nlu.read_confirm(text)
            self._log(case, "read_back_answer", {"text": text, "source": source, "verdict": verdict,
                                                 "confirms": pc["choice"]}, actor="merchant")
            if verdict != "confirm":
                self._escalate(case, "Spoken answer was not confirmed on read back: a person must check")
                self._say(case, "customer", M.customer_message(case.ruling, txn, case.ack, (case.complaint or {}).get("complaint_id")))
                return {**self._say(case, "merchant", M.merchant_message(case.ruling, txn, "human")), "action": None}
            choice, info, confirmed = pc["choice"], pc["info"], True
        else:
            choice, info = self._read(case, text)
        self._log(case, "merchant_said", {"text": text, "source": source, "asked": case.asked, "read_as": choice,
                                          "read_back_confirmed": confirmed, **info}, actor="merchant")
        ledger_ok = bool(self.rails.ledger(txn_id)["merchant_credited"])      # read again at decision time
        decision = authorize_merchant_response(_RulingView(case.ruling), choice, asked=case.asked,
                                               complaint_acknowledged=case.ack["udir_complaint"],
                                               ledger_credited=ledger_ok,
                                               two_key_verified=info["two_key_verified"],
                                               unclear_count=case.unclear_count)
        case.last_gate = {"choice": choice, "readers": {"rule": info["deterministic"], "sarvam": info["llm"]},
                          "two_key": info.get("two_key"), "two_key_verified": info["two_key_verified"],
                          "acknowledged": case.ack["udir_complaint"], "escalated": bool(case.ruling.get("escalate")),
                          "ledger_credited": ledger_ok,
                          "code_path": case.ruling.get("udir_code"), "asked": case.asked,
                          "allowed": decision.allowed, "reason": decision.reason, "code": decision.code, "warrant": None}
        self._log(case, "action_decision", {"choice": choice, "allowed": decision.allowed, "reason": decision.reason,
                                            "code": decision.code, "two_key_verified": info["two_key_verified"]},
                  actor="rule_engine")
        if decision.allowed and source == "voice" and not confirmed:
            case.pending_confirm = {"choice": choice, "info": info, "code": decision.code}
            case.last_gate["read_back"] = "waiting"
            self._move(case, "MERCHANT_ASKED", f"spoken yes for {decision.code}: read back before acting")
            return {**self._say(case, "merchant", self._read_back(case, text, decision.code)), "action": None}
        if confirmed:
            case.last_gate["read_back"] = "confirmed"
        if decision.allowed:
            try:
                w = self._policy.issue(complaint_id=case.complaint["complaint_id"], code=decision.code, txn_id=txn_id,
                                       single_key_override=self.single_key_override, amount_inr=txn["amount_inr"],
                                       generation=getattr(self.rails, "generation", None),
                                       readers={"rule": info["deterministic"], "sarvam": info["llm"]})
                case.last_gate["warrant"] = {k: w[k] for k in ("wid", "code", "complaint_id", "amount_inr")}
                self._log(case, "warrant_issued", {k: w[k] for k in ("wid", "complaint_id", "code", "amount_inr", "readers", "override")},
                          actor="policy")
                resp = self.rails.respond(case.complaint["complaint_id"], decision.code, warrant=w)
            except (KeyError, ValueError) as e:
                self._log(case, "rail_refused", {"error": str(e)}, actor="rail")
                self._escalate(case, f"Rail refused the response: {e}")
                return {**self._say(case, "merchant", M.merchant_message(case.ruling, txn, "human")), "action": None}
            case.response = resp
            acked = bool(resp.get("acknowledged"))
            self._log(case, "udir_response_acknowledged" if acked else "udir_response_unacknowledged", resp, actor="rail")
            if acked:
                case.ack["udir_response"] = True
                self._move(case, "RESOLVED", f"{decision.code} acknowledged")
                entry = self._say(case, "merchant", M.merchant_message(case.ruling, txn, f"sent_{decision.code}"))
                self._say(case, "customer", M.customer_outcome(decision.code, txn, case.ruling))
            else:
                entry = self._say(case, "merchant", M.merchant_message(case.ruling, txn, "pending_ack"))
                self._escalate(case, "Rail accepted the response but never acknowledged it")
            return {**entry, "action": decision.code, "acknowledged": acked}

        if choice == "goods_not_delivered" and case.asked == "ask_goods":
            case.asked = "ask_refund"
            self._move(case, "MERCHANT_ASKED", "goods not given: ask for refund consent separately")
            return {**self._say(case, "merchant", M.merchant_message(case.ruling, txn, "ask_refund")), "action": None}
        if choice == "unclear" and case.unclear_count < 1:
            case.unclear_count += 1
            self._move(case, "MERCHANT_ASKED", "re-ask once")
            stage = "reask_goods" if case.asked == "ask_goods" else "reask_refund"
            return {**self._say(case, "merchant", self._reprise(case, text, stage)), "action": None}
        self._escalate(case, decision.reason)
        self._say(case, "customer", M.customer_message(case.ruling, txn, case.ack, (case.complaint or {}).get("complaint_id")))
        return {**self._say(case, "merchant", M.merchant_message(case.ruling, txn, "human")), "action": None}

    @_per_case
    def check_draft(self, txn_id: str, draft: str, audience: str = "customer") -> dict:
        case = self.cases[txn_id]
        txn = self._txn(txn_id)
        facts = M.facts_for_guard(case.ruling, txn, case.ack, audience, state=case.state)
        v = check(draft, facts)
        self._log(case, "guard_blocked" if not v.ok else "guard_passed",
                  {"audience": audience, "source": "typed by user", "draft": draft,
                   "violations": [x["reason"] for x in v.to_dict()["violations"]]}, actor="guard")
        tmpl = (M.customer_message(case.ruling, txn, case.ack, (case.complaint or {}).get("complaint_id"),
                                   pending=case.state == "AWAITING_ACK")
                if audience == "customer" else M.merchant_message(case.ruling, txn, "status"))
        return {"draft": draft, "verdict": v.to_dict(), "final": draft if v.ok else tmpl, "facts": facts}


class _RulingView:
    def __init__(self, d: dict):
        self.__dict__.update(d)
