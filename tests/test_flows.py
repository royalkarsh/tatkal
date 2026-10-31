from engine.audit import AuditLog
from rails.mock import MockRails
from agent.service import Tatkal, IllegalTransition
from agent.llm import SimulatedRiskyDrafter
from engine.guard import check
from engine import messages as M
import pytest

def fresh(**kw):
    return Tatkal(MockRails(), AuditLog(), **{'single_key_override': True, **kw})

def test_u022_merchant_path_sends_105_only_after_yes_and_ack():
    t = fresh(); t.open_case("PTM41870340", opened_by="incoming_udir")
    c = t.cases["PTM41870340"]; assert c.state == "MERCHANT_ASKED"
    r = t.merchant_says("PTM41870340", "Haan, saamaan de diya")
    assert r["action"] == "105" and r["acknowledged"] and c.state == "RESOLVED"

def test_unclear_reasks_once_then_escalates():
    t = fresh(); t.open_case("PTM41870340", opened_by="incoming_udir")
    assert t.merchant_says("PTM41870340", "pata nahi")["action"] is None
    assert t.cases["PTM41870340"].state == "MERCHANT_ASKED"
    t.merchant_says("PTM41870340", "hmm")
    c = t.cases["PTM41870340"]; assert c.state == "ESCALATED" and c.handoff

def test_dropped_ack_never_says_done():
    rails = MockRails()
    t = Tatkal(rails, AuditLog(), single_key_override=True); t.open_case("PTM41870340", opened_by="incoming_udir")
    rails.drop_ack = True
    r = t.merchant_says("PTM41870340", "haan de diya")
    c = t.cases["PTM41870340"]
    assert not r["acknowledged"] and c.state == "ESCALATED"
    assert not any("bhej diya gaya" in x["text"] for x in c.transcript if x["who"] == "tatkal")

def test_soundbox_missed_tells_both_sides_money_arrived():
    t = fresh(); c = t.open_case("PTM41870301")
    assert c.state == "NO_ACTION_NEEDED" and c.complaint is None
    assert any("aa gaya" in x["text"] for x in c.transcript if x.get("to") == "merchant")

def test_risky_llm_drafts_never_reach_customer():
    for seed in range(20):
        t = fresh(llm=SimulatedRiskyDrafter(seed=seed)); c = t.open_case("PTM41870295", llm_words=True)
        final = [x["text"] for x in c.transcript if x.get("to") == "customer"][-1]
        txn = t.rails.full("PTM41870295")
        assert check(final, M.facts_for_guard(c.ruling, txn, c.ack, "customer", state=c.state)).ok

def test_every_template_passes_its_own_guard():
    t = fresh()
    for tid in list(t.rails.txns):
        c = t.open_case(tid)
        assert all(not g["blocked"] for g in c.guard_log if g["source"] == "template"), tid

def test_illegal_transition_rejected():
    t = fresh(); c = t.open_case("PTM41870301")
    with pytest.raises(IllegalTransition):
        t._move(c, "RESOLVED", "skip ahead")

def test_customer_can_always_reach_a_human():
    t = fresh(); t.open_case("PTM41870295")
    t.customer_says("PTM41870295", "Mujhe kisi insaan se baat karni hai")
    assert t.cases["PTM41870295"].state == "ESCALATED"


def test_not_delivered_then_explicit_refund_consent_sends_106():
    t = fresh(); t.open_case("PTM41870340", opened_by="incoming_udir")
    assert t.merchant_says("PTM41870340", "nahi, saamaan nahi diya")["action"] is None
    assert t.cases["PTM41870340"].asked == "ask_refund"
    assert t.merchant_says("PTM41870340", "haan refund kar do")["action"] == "106"
