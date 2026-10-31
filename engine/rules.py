"""Deterministic case engine. No LLM is involved in any decision made here.

Sources encoded in this file:
  * RBI circular RBI/2019-20/67 (DPSS.CO.PD No.629/02.01.014/2019-20, 20 Sep 2019),
    Annex row 4: UPI.
      4(a) Account debited but the beneficiary account is not credited (transfer of funds):
           auto reversal by the beneficiary bank latest on T+1; Rs 100 per day beyond T+1.
      4(b) Account debited but transaction confirmation not received at merchant location
           (payment to merchant): auto reversal within T+5; Rs 100 per day beyond T+5.
    T is the day of transaction and refers to the calendar date. Compensation is paid
    suo moto, without waiting for a complaint (para 5).
  * NPCI UDIR adjustment codes as documented for TPAP integrations
    (Juspay UPI Consumer Stack, complaints guide):
      U2 (P2M) success or deemed: U008 goods not provided, U021 credit not processed for
      returned goods, U022 debited but confirmation not received by merchant, U023 paid by
      alternate means or duplicate payment.  U2 (P2M) failure: U009 customer not credited back.
      P2P deemed: U010 beneficiary not credited.  P2P failure: U005 customer not credited back.
    Payee side resolve codes: 105 goods or services delivered, 106 refund will be initiated.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import Literal

RBI_CIRCULAR = "RBI/2019-20/67"

RBI_ROWS = {
    "4a": {"tat_days": 1, "per_day": 100,
           "text": "Account debited but beneficiary account not credited (transfer of funds)"},
    "4b": {"tat_days": 5, "per_day": 100,
           "text": "Account debited but transaction confirmation not received at merchant location"},
}

UDIR_CODES = {
    "U005": "Customer account not credited back for failed P2P transaction",
    "U008": "Goods or services are not provided",
    "U009": "Customer account not credited back for failed merchant transaction",
    "U010": "Beneficiary account is not credited for a pending or timeout transaction",
    "U021": "Credit not processed for cancelled or returned goods and services",
    "U022": "Account debited but transaction confirmation not received by merchant",
    "U023": "Paid by alternate means or duplicate payment",
}
RESOLVE_CODES = {"105": "Merchant confirms goods or services delivered",
                 "106": "Refund will be initiated"}

HIGH_VALUE_INR = 25_000          # policy: human review at or above this amount
MAX_AUTONOMOUS_REASK = 1         # ask an unclear merchant at most once more, then escalate

Path = Literal[
    "SOUNDBOX_MISSED",      # merchant was credited, only the confirmation failed
    "PAYEE_CREDITED",       # incoming U022, and the payee ledger DOES show the credit: answer 105 or 106
    "RETURN_CREDIT",        # U021: goods returned or order cancelled, refund credit not received: a person decides
    "P2M_PENDING",          # success or deemed, payer debited, merchant not credited -> U022
    "P2M_FAILED_DEBITED",   # failed, payer debited, not yet reversed -> U009
    "P2M_REVERSED",         # failed, payer debited, reversal already done
    "P2P_PENDING",          # P2P deemed, beneficiary not credited -> U010
    "P2P_FAILED_DEBITED",   # P2P failed, not reversed -> U005
    "DUPLICATE",            # two debits for one purchase -> U023
    "GOODS_DISPUTE",        # paid, merchant credited, customer says goods not given -> U008
    "NO_DEBIT",             # payer not debited: nothing is owed
    "P2P_CREDITED",         # P2P transfer landed: nothing stuck
    "HUMAN_REVIEW",         # anything the rules do not cover
]


@dataclass
class Txn:
    txn_id: str
    amount_inr: int
    txn_date: date
    kind: Literal["P2M", "P2P"]
    status: Literal["success", "deemed", "failed", "pending"]
    payer_debited: bool
    merchant_credited: bool = False
    merchant_confirmed: bool = False      # Soundbox or app confirmation received
    reversed_on: date | None = None       # date the payer got the money back
    credited_on: date | None = None       # date the merchant got the money
    duplicate_of: str | None = None
    goods_disputed: bool = False
    return_credit_pending: bool = False   # customer says goods were returned or the order cancelled, refund not received
    payer_app: str = "Paytm"
    merchant_name: str = ""
    customer_name: str = ""

    @staticmethod
    def from_dict(d: dict) -> "Txn":
        d = dict(d)
        for k in ("txn_date", "reversed_on", "credited_on"):
            if d.get(k) and isinstance(d[k], str):
                d[k] = date.fromisoformat(d[k])
        allowed = set(Txn.__dataclass_fields__)
        return Txn(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class Ruling:
    txn_id: str
    path: str
    udir_code: str | None
    rbi_row: str | None
    tat_days: int | None
    deadline: str | None              # ISO date, outer limit only
    days_late: int
    compensation_due_inr: int
    money_at_risk_inr: int
    who_acts: str
    merchant_should_hand_over_goods: bool | None
    escalate: bool
    escalate_reasons: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def deadline(txn_date: date, row: str) -> date:
    if row not in RBI_ROWS:
        raise ValueError(f"Unknown RBI row {row}")
    return txn_date + timedelta(days=RBI_ROWS[row]["tat_days"])


def compensation(txn_date: date, row: str, settled_on: date | None, today: date) -> tuple[int, int]:
    """Return (days_late, rupees_due). Settled_on is when reversal or credit concluded."""
    end = settled_on or today
    late = max(0, (end - deadline(txn_date, row)).days)
    return late, late * RBI_ROWS[row]["per_day"]


def classify(t: Txn, today: date, *, customer_requests_human: bool = False,
             incoming_code: str | None = None) -> Ruling:
    """incoming_code is set when the case arrived as a UDIR complaint from the payer side
    (for example U022 raised in another UPI app). The payee side must then answer it."""
    reasons: list[str] = []
    esc: list[str] = []

    def done(path, code=None, row=None, who="", goods=None, settled=None):
        tat = RBI_ROWS[row]["tat_days"] if row else None
        dl = deadline(t.txn_date, row).isoformat() if row else None
        late, due = compensation(t.txn_date, row, settled, today) if row else (0, 0)
        if late > 0:
            esc.append(f"RBI deadline passed {late} day(s) ago; Rs {due} compensation due from the bank")
        if t.amount_inr >= HIGH_VALUE_INR and path not in ("SOUNDBOX_MISSED", "NO_DEBIT", "P2P_CREDITED"):
            esc.append(f"Amount Rs {t.amount_inr} is at or above the Rs {HIGH_VALUE_INR} review limit")
        if customer_requests_human:
            esc.append("Customer asked for a human")
        at_risk = 0 if path in ("SOUNDBOX_MISSED", "NO_DEBIT", "P2M_REVERSED", "P2P_CREDITED") else t.amount_inr
        return Ruling(t.txn_id, path, code, row, tat, dl, late, due, at_risk, who, goods,
                      bool(esc), esc, reasons)

    if not t.payer_debited and t.merchant_credited:
        reasons.append("Ledger says the merchant was credited but the payer was never debited")
        esc.append("Contradictory PSP and ledger signals")
        return done("HUMAN_REVIEW", who="Human reviewer")
    if t.status == "failed" and t.merchant_credited:
        reasons.append("PSP says failed but the acquiring ledger shows a credit")
        esc.append("Contradictory PSP and ledger signals")
        return done("HUMAN_REVIEW", who="Human reviewer")

    if not t.payer_debited:
        reasons.append("Payer was not debited, so no money is stuck")
        return done("NO_DEBIT", who="Nobody: ask the customer to retry", goods=False)

    if t.kind == "P2P":
        if t.status == "failed":
            reasons.append("P2P failed after debit: beneficiary bank must reverse (row 4a)")
            return done("P2P_FAILED_DEBITED", "U005", "4a", "Remitter and beneficiary banks",
                        settled=t.reversed_on)
        if t.merchant_credited:
            reasons.append("Beneficiary was credited: nothing is stuck")
            return done("P2P_CREDITED", who="Nobody: tell the sender it arrived", goods=None)
        reasons.append("P2P deemed or pending, beneficiary not credited (row 4a)")
        return done("P2P_PENDING", "U010", "4a", "Beneficiary bank", settled=t.credited_on or t.reversed_on)

    # P2M from here on
    if t.duplicate_of:
        reasons.append(f"Second debit for the same purchase as {t.duplicate_of}")
        return done("DUPLICATE", "U023", None, "Merchant, by authorising a refund", goods=True)

    if t.status in ("success", "deemed") and t.merchant_credited:
        if t.return_credit_pending:
            reasons.append("Goods returned or order cancelled, refund credit not received (U021)")
            esc.append("Returned goods credit needs evidence of the return: a person decides. The payee side may answer "
                       "106 with the shop's consent or 'unable to update', which moves to chargeback after T+3")
            return done("RETURN_CREDIT", "U021", None, "Human reviewer, with the shop's records", goods=None)
        if t.goods_disputed:
            reasons.append("Merchant was paid, customer says goods were not given")
            esc.append("Goods dispute needs a human: the rules cannot decide what happened at the counter")
            return done("GOODS_DISPUTE", "U008", None, "Human reviewer", goods=None)
        if incoming_code == "U022":
            reasons.append("Incoming U022, and the payee ledger shows the credit: the payee side must answer "
                           "(105 if goods were handed over, 106 if a refund is authorised)")
            return done("PAYEE_CREDITED", "U022", "4b",
                        "Payee side: 105 if goods were given, 106 if the shop authorises a refund", goods=True,
                        settled=t.credited_on)
        reasons.append("Merchant ledger shows the credit; only the Soundbox or app confirmation was missed")
        return done("SOUNDBOX_MISSED", None, None, "Nobody: tell both sides the money arrived", goods=True)

    if t.status == "failed":
        if t.reversed_on:
            reasons.append(f"Failed payment, payer already reversed on {t.reversed_on.isoformat()}")
            return done("P2M_REVERSED", None, "4b", "Nobody: already reversed", goods=False,
                        settled=t.reversed_on)
        reasons.append("Failed P2M after debit: auto reversal owed within T+5 (row 4b)")
        return done("P2M_FAILED_DEBITED", "U009", "4b", "Payer's bank (remitter)", goods=False)

    if t.status in ("success", "deemed", "pending") and not t.merchant_credited:
        reasons.append("Payer debited, merchant not credited or confirmed (row 4b)"
                       + ("; incoming U022 cannot be answered with 105 or 106 because the shop holds no money"
                          if incoming_code == "U022" else ""))
        return done("P2M_PENDING", "U022", "4b",
                    "Payee side answers U022; banks reverse if it never lands", goods=False,
                    settled=t.credited_on or t.reversed_on)

    reasons.append("Signals do not match any rule")
    esc.append("Unrecognised combination of status and ledger signals")
    return done("HUMAN_REVIEW", who="Human reviewer")


# ---------------------------------------------------------------- action policy
@dataclass
class ActionDecision:
    allowed: bool
    reason: str
    code: str | None = None


MerchantChoice = Literal["goods_delivered_yes", "refund_yes", "goods_not_delivered", "refund_declined",
                         "refund_already_claimed", "unclear"]


POSITIVE = ("goods_delivered_yes", "refund_yes")


def authorize_merchant_response(ruling: Ruling, choice: str, *, asked: str | None, complaint_acknowledged: bool,
                                ledger_credited: bool, two_key_verified: bool,
                                unclear_count: int = 0) -> ActionDecision:
    """May a payee side UDIR response be sent? Pure function, unit tested. This is the LAST gate,
    so every condition lives here, not in the caller:
       1. the complaint itself must be acknowledged by the rail
       2. the case must not be flagged for a human
       3. the code must exist for this UDIR path
       4. the merchant must have answered THE question that was asked, explicitly
       5. two independent readers must have read that answer the same way (two_key_verified).
          There is no default: the caller has to state it. One reader is never enough.
       6. the payee ledger must show the credit (ledger_credited). A shop that never received the
          money can neither confirm delivery against it (105) nor refund it (106).
    """
    if not complaint_acknowledged:
        return ActionDecision(False, "The UDIR complaint is not acknowledged yet; nothing can be answered")
    if ruling.escalate:
        return ActionDecision(False, "Case is flagged for human review: " + "; ".join(ruling.escalate_reasons))
    if ruling.udir_code not in ("U022", "U023"):
        return ActionDecision(False, f"No payee side response applies to path {ruling.path}")
    if not ledger_credited:
        return ActionDecision(False, "Payee ledger shows no credit: the shop holds no money, so no 105 or 106; banks reverse by the RBI deadline")
    if choice in POSITIVE and not two_key_verified:
        return ActionDecision(False, "Only one reader confirmed this answer; a person must confirm before any 105 or 106")
    if asked == "ask_goods" and choice == "goods_delivered_yes":
        if ruling.udir_code == "U023":
            return ActionDecision(False, "Duplicate payment cannot be closed with 105")
        return ActionDecision(True, "Merchant explicitly confirmed goods were handed over", "105")
    if asked == "ask_refund" and choice == "refund_yes":
        return ActionDecision(True, "Merchant explicitly authorised a refund when asked", "106")
    if choice == "goods_delivered_yes" or choice == "refund_yes":
        return ActionDecision(False, f"Answer '{choice}' does not match the question asked ({asked})")
    if choice == "refund_already_claimed":
        return ActionDecision(False, "Merchant says a refund already happened: needs verification by a human")
    if choice in ("goods_not_delivered",):
        return ActionDecision(False, "Goods not handed over: ask for refund consent separately")
    if choice == "refund_declined":
        return ActionDecision(False, "Merchant declined a refund: human review")
    if unclear_count >= MAX_AUTONOMOUS_REASK:
        return ActionDecision(False, "Merchant reply still unclear after a re-ask: human review")
    return ActionDecision(False, "Reply unclear: ask once more in plain words")
