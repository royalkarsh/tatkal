from datetime import date
from engine.rules import Txn, classify, deadline, compensation, authorize_merchant_response

T = date(2026, 10, 31)

def mk(**kw):
    base = dict(txn_id="X", amount_inr=450, txn_date="2026-10-31", kind="P2M", status="deemed",
                payer_debited=True, merchant_credited=False)
    base.update(kw)
    return Txn.from_dict(base)

def test_row_4b_is_t_plus_5_calendar_days():
    assert deadline(date(2026, 10, 31), "4b") == date(2026, 11, 5)

def test_row_4a_is_t_plus_1():
    assert deadline(date(2026, 10, 31), "4a") == date(2026, 11, 1)

def test_compensation_100_per_day_after_deadline():
    assert compensation(date(2026, 10, 24), "4b", None, T) == (2, 200)
    assert compensation(date(2026, 10, 20), "4b", date(2026, 10, 28), T) == (3, 300)

def test_deemed_not_credited_is_u022():
    r = classify(mk(), T)
    assert (r.path, r.udir_code, r.rbi_row, r.deadline) == ("P2M_PENDING", "U022", "4b", "2026-11-05")

def test_soundbox_missed_needs_no_complaint():
    r = classify(mk(status="success", merchant_credited=True), T)
    assert r.path == "SOUNDBOX_MISSED" and r.udir_code is None and r.money_at_risk_inr == 0

def test_failed_debit_is_u009_not_u022():
    r = classify(mk(status="failed"), T)
    assert r.udir_code == "U009" and r.merchant_should_hand_over_goods is False

def test_no_debit_nothing_owed():
    assert classify(mk(status="failed", payer_debited=False), T).path == "NO_DEBIT"

def test_duplicate_is_u023():
    assert classify(mk(status="success", merchant_credited=True, duplicate_of="Y"), T).udir_code == "U023"

def test_high_value_and_dispute_escalate():
    assert classify(mk(amount_inr=25000), T).escalate
    assert not classify(mk(amount_inr=24999), T).escalate
    assert classify(mk(status="success", merchant_credited=True, goods_disputed=True), T).escalate

def test_missed_deadline_escalates_with_compensation():
    r = classify(mk(txn_date="2026-10-24"), T)
    assert r.escalate and r.compensation_due_inr == 200

def auth(r, choice, asked="ask_goods", acked=True, unclear=0):
    return authorize_merchant_response(r, choice, asked=asked, complaint_acknowledged=acked, ledger_credited=True, two_key_verified=True, unclear_count=unclear)

def test_105_needs_explicit_yes_to_the_goods_question():
    r = classify(mk(), T)
    assert auth(r, "goods_delivered_yes").code == "105"
    assert not auth(r, "unclear").allowed
    assert not auth(r, "goods_not_delivered").allowed
    assert not auth(r, "unclear", unclear=1).allowed

def test_no_action_on_unacknowledged_complaint():
    r = classify(mk(), T)
    assert not auth(r, "goods_delivered_yes", acked=False).allowed
    assert not auth(r, "refund_yes", asked="ask_refund", acked=False).allowed

def test_answer_must_match_the_question():
    r = classify(mk(), T)
    assert not auth(r, "refund_yes", asked="ask_goods").allowed          # refund talk is not a goods answer
    assert not auth(r, "goods_delivered_yes", asked="ask_refund").allowed
    assert auth(r, "refund_yes", asked="ask_refund").code == "106"

def test_refund_already_claimed_never_acts():
    r = classify(mk(), T)
    assert not auth(r, "refund_already_claimed").allowed
    assert not auth(r, "refund_already_claimed", asked="ask_refund").allowed

def test_no_response_on_escalated_case():
    r = classify(mk(amount_inr=30000), T)
    assert not auth(r, "goods_delivered_yes").allowed

def test_duplicate_cannot_close_with_105():
    r = classify(mk(status="success", merchant_credited=True, duplicate_of="Y"), T)
    assert not auth(r, "goods_delivered_yes").allowed
    assert auth(r, "refund_yes", asked="ask_refund").code == "106"

def test_p2p_credited_is_not_human_review():
    r = classify(mk(kind="P2P", status="success", merchant_credited=True), T)
    assert r.path == "P2P_CREDITED" and not r.escalate

def test_contradictory_signals_go_to_a_human():
    assert classify(mk(status="failed", merchant_credited=True), T).path == "HUMAN_REVIEW"
    assert classify(mk(payer_debited=False, merchant_credited=True), T).escalate
