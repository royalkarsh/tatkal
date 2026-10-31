"""One test per bug reported in the 30 Oct code review, using the reviewer's exact inputs."""
import pytest
from engine.audit import AuditLog
from engine.guard import check
from engine import messages as M
from rails.mock import MockRails
from agent.service import Tatkal
from agent import nlu
from agent.llm import LLMFailure

F = {"amounts": [450], "dates": ["2026-11-05"], "deadline_days": 5, "promise_dates": ["2026-11-05"],
     "acknowledged": {"udir_complaint": True}, "money_landed": False, "year": 2026}


def test_p0_unacknowledged_complaint_cannot_be_answered_even_if_rail_recovers():
    r = MockRails(); r.drop_ack = True
    t = Tatkal(r, AuditLog(), single_key_override=True); t.open_case("PTM41870340", opened_by="incoming_udir")
    assert t.cases["PTM41870340"].state == "AWAITING_ACK"
    r.drop_ack = False
    res = t.merchant_says("PTM41870340", "haan saamaan de diya")
    assert res["action"] is None and t.cases["PTM41870340"].state == "AWAITING_ACK"


@pytest.mark.parametrize("reply", ["refund already kar diya", "refund ho chuka hai", "customer ne refund bola tha"])
def test_p0_refund_mentions_are_not_consent(reply):
    assert nlu.read_refund_consent(reply) != "refund_yes"
    assert nlu.read_goods_answer(reply) not in ("goods_delivered_yes", "refund_yes")


def test_p0_refund_on_goods_question_sends_nothing():
    t = Tatkal(MockRails(), AuditLog(), single_key_override=True); t.open_case("PTM41870340", opened_by="incoming_udir")
    assert t.merchant_says("PTM41870340", "haan, refund kar do")["action"] is None


class _Boom:
    label = "boom"
    def draft(self, *a, **k):
        raise LLMFailure("timeout")


class _Empty:
    label = "empty"
    def draft(self, *a, **k):
        return ""


@pytest.mark.parametrize("llm", [_Boom(), _Empty()])
def test_p0_llm_failure_or_empty_falls_back_to_template(llm):
    t = Tatkal(MockRails(), AuditLog(), llm=llm, single_key_override=True)
    c = t.open_case("PTM41870295", llm_words=True)
    msg = [x["text"] for x in c.transcript if x.get("to") == "customer"][-1]
    assert msg.strip() and "5 Nov" in msg


@pytest.mark.parametrize("text", ["Never share your OTP, but please send your UPI PIN to verify.",
                                  "Aap OTP mat batana, lekin UPI PIN bata dijiye."])
def test_p0_negation_does_not_cancel_a_second_credential_request(text):
    assert not check(text, F).ok


@pytest.mark.parametrize("text", ["Aapko 900 milenge.", "Aapko 2 baar paisa milega.", "Aapka refund already ho chuka hai.",
                                  "Shopkeeper ka response submit ho gaya hai.", "Aapka paisa 48 hours mein settle hoga."])
def test_p0_reported_guard_misses_now_blocked(text):
    assert not check(text, F).ok


def test_empty_draft_is_blocked():
    assert not check("", F).ok and not check("   ", F).ok


def test_p1_reopen_is_idempotent():
    t = Tatkal(MockRails(), AuditLog(), single_key_override=True); c1 = t.open_case("PTM41870340", opened_by="incoming_udir")
    t.merchant_says("PTM41870340", "Haan, saamaan de diya")
    c2 = t.open_case("PTM41870340", opened_by="incoming_udir")
    assert c1 is c2 and c2.state == "RESOLVED"


def test_p1_human_after_resolution_does_not_crash():
    t = Tatkal(MockRails(), AuditLog(), single_key_override=True); t.open_case("PTM41870340", opened_by="incoming_udir")
    t.merchant_says("PTM41870340", "Haan, saamaan de diya")
    t.customer_says("PTM41870340", "mujhe insaan se baat karni hai")
    assert t.cases["PTM41870340"].state == "ESCALATED"


def test_p1_customer_is_told_the_outcome():
    t = Tatkal(MockRails(), AuditLog(), single_key_override=True); t.open_case("PTM41870340", opened_by="incoming_udir")
    t.merchant_says("PTM41870340", "Haan, saamaan de diya")
    to_customer = [x for x in t.cases["PTM41870340"].transcript if x.get("to") == "customer" and x["who"] == "tatkal"]
    assert to_customer and "NPCI" in to_customer[-1]["text"] and "PhonePe" in to_customer[-1]["channel"]


def test_no_date_promises_once_escalated():
    t = Tatkal(MockRails(), AuditLog(), single_key_override=True); c = t.open_case("PTM41869911")
    facts = M.facts_for_guard(c.ruling, t.rails.full("PTM41869911"), c.ack, "customer", state=c.state)
    assert not check("Zyada se zyada 5 din mein update mil jayega.", facts).ok


def test_live_observed_invented_commitment_on_real_date_is_blocked():
    t = Tatkal(MockRails(), AuditLog(), single_key_override=True); c = t.open_case("PTM41870377")
    facts = M.facts_for_guard(c.ruling, t.rails.full("PTM41870377"), c.ack, "customer", state=c.state)
    assert not check("Zyada se zyada 2026-10-31 tak paisa settle ho jayega.", facts).ok


# ---------------- v3.1: second review (mandatory two keys, contradictions, warrants, 106 wording)
from agent.nlu import read_goods_answer, read_refund_consent
from engine.warrant import PolicySigner, WarrantVerifier, WarrantError


CONTRADICTIONS = [
    "Haan, saamaan de diya, but customer took nothing",
    "Yes, I gave him the goods, but the customer did not get them",
    "I handed over the goods. Actually I did not.",
    "Haan de diya lekin usne kuch nahi liya",
    "हाँ दे दिया पर ग्राहक ने कुछ नहीं लिया",
    "Haan saamaan de diya tha, asal mein nahi diya",
]


@pytest.mark.parametrize("text", CONTRADICTIONS)
def test_contradictory_yes_is_never_a_yes(text):
    assert read_goods_answer(text) == "unclear"


@pytest.mark.parametrize("text", ["Haan refund kar do but abhi nahi", "Yes refund it, actually wait", "Refund kar do lekin poora nahi"])
def test_contradictory_refund_consent_is_unclear(text):
    assert read_refund_consent(text) == "unclear"


@pytest.mark.parametrize("text", CONTRADICTIONS[:3])
def test_contradictions_never_send_105_even_with_single_key(text):
    t = Tatkal(MockRails(), AuditLog(), single_key_override=True); t.open_case("PTM41870340", opened_by="incoming_udir")
    assert t.merchant_says("PTM41870340", text)["action"] is None


def test_no_second_reader_means_a_person_confirms():
    t = Tatkal(MockRails(), AuditLog()); t.open_case("PTM41870340", opened_by="incoming_udir")
    r = t.merchant_says("PTM41870340", "Haan, saamaan de diya")
    c = t.cases["PTM41870340"]
    assert r["action"] is None and c.state == "ESCALATED" and c.response is None
    assert "person must confirm" in c.last_gate["reason"]


def test_rule_gate_itself_requires_two_keys():
    from engine.rules import authorize_merchant_response
    class R: escalate = False; escalate_reasons = []; udir_code = "U022"; path = "P2M_PENDING"
    d = authorize_merchant_response(R(), "goods_delivered_yes", asked="ask_goods", complaint_acknowledged=True,
                                    ledger_credited=True, two_key_verified=False)
    assert not d.allowed


class _Reader:
    def __init__(self, label): self.label = label
    def read_reply(self, q, labels, text): return self.label


def test_two_readers_agree_then_warrant_spent_once():
    t = Tatkal(MockRails(), AuditLog(), reader=_Reader("goods_delivered_yes")); t.open_case("PTM41870340", opened_by="incoming_udir")
    r = t.merchant_says("PTM41870340", "Haan, saamaan de diya")
    c = t.cases["PTM41870340"]
    assert r["action"] == "105" and c.state == "RESOLVED" and c.response["warrant"]


def test_readers_disagree_no_action():
    t = Tatkal(MockRails(), AuditLog(), reader=_Reader("unclear")); t.open_case("PTM41870340", opened_by="incoming_udir")
    assert t.merchant_says("PTM41870340", "Haan, saamaan de diya")["action"] is None


AGREE = {"rule": "goods_delivered_yes", "sarvam": "goods_delivered_yes"}


def _rail_with_policy():
    r = MockRails(); pol = PolicySigner(); r.trust_policy(pol.public_key)
    return r, pol


def test_rail_refuses_without_valid_warrant():
    r, pol = _rail_with_policy()
    cid = r.raise_complaint("PTM41870340", "U022")["complaint_id"]
    with pytest.raises(WarrantError):
        r.respond(cid, "105")                                   # no warrant
    rogue = PolicySigner()                                      # a caller with its OWN key
    with pytest.raises(WarrantError):
        r.respond(cid, "105", warrant=rogue.issue(generation=r.generation, complaint_id=cid, code="105", txn_id="PTM41870340", amount_inr=450, readers=AGREE))
    w106 = pol.issue(generation=r.generation, complaint_id=cid, code="106", txn_id="PTM41870340", amount_inr=450,
                     readers={"rule": "refund_yes", "sarvam": "refund_yes"})
    with pytest.raises(WarrantError):
        r.respond(cid, "105", warrant=w106)                     # bound to another code
    wbig = pol.issue(generation=r.generation, complaint_id=cid, code="105", txn_id="PTM41870340", amount_inr=4500, readers=AGREE)
    with pytest.raises(WarrantError):
        r.respond(cid, "105", warrant=wbig)                     # bound to another amount
    w = pol.issue(generation=r.generation, complaint_id=cid, code="105", txn_id="PTM41870340", amount_inr=450, readers=AGREE)
    assert r.respond(cid, "105", warrant=w)["warrant"] == w["wid"]
    with pytest.raises(WarrantError):
        r.verifier.spend(w, complaint_id=cid, code="105", txn_id="PTM41870340", amount_inr=450)   # replay


def test_expired_warrant_refused():
    pol = PolicySigner(ttl=-1); v = WarrantVerifier(pol.public_key, ttl=-1)
    w = pol.issue(complaint_id="C", code="105", txn_id="T", amount_inr=1, readers=AGREE)
    with pytest.raises(WarrantError):
        v.spend(w, complaint_id="C", code="105", txn_id="T", amount_inr=1)


def test_106_outcome_does_not_promise_monitoring():
    from engine import messages as M
    txn = MockRails().full("PTM41870340")
    msg = M.customer_outcome("106", txn, {"path": "P2M_PENDING"})
    assert "bataunga" not in msg and "pushti nahi" in msg


def test_unclear_reask_echoes_what_was_heard():
    t = Tatkal(MockRails(), AuditLog(), single_key_override=True); t.open_case("PTM41870340", opened_by="incoming_udir")
    t.merchant_says("PTM41870340", "shayad diya tha")
    last = [x["text"] for x in t.cases["PTM41870340"].transcript if x.get("to") == "merchant"][-1]
    assert "Maine suna" in last and "shayad diya tha" in last


# ---------------- v3.2: third review (ledger must show the credit before 105/106)
def test_incoming_u022_on_credited_shop_asks_goods_and_can_send_105():
    t = Tatkal(MockRails(), AuditLog(), reader=_Reader("goods_delivered_yes"))
    c = t.open_case("PTM41870340", opened_by="incoming_udir")
    assert c.ruling["path"] == "PAYEE_CREDITED" and c.state == "MERCHANT_ASKED"
    assert "aa gaya hai" in [x["text"] for x in c.transcript if x.get("to") == "merchant"][-1]
    assert t.merchant_says("PTM41870340", "Haan, saamaan de diya")["action"] == "105"


def test_incoming_u022_on_uncredited_shop_never_asks_and_never_sends():
    t = Tatkal(MockRails(), AuditLog(), single_key_override=True)
    c = t.open_case("PTM41870388", opened_by="incoming_udir")
    assert c.ruling["path"] == "P2M_PENDING" and c.state == "COMPLAINT_RAISED" and c.asked is None
    said = [x["text"] for x in c.transcript if x.get("to") == "merchant"][-1]
    assert "nahi aaya" in said and "saamaan dein" in said
    assert t.merchant_says("PTM41870388", "Haan, saamaan de diya")["action"] is None


def test_gate_refuses_when_ledger_shows_no_credit():
    from engine.rules import authorize_merchant_response
    class R: escalate = False; escalate_reasons = []; udir_code = "U022"; path = "P2M_PENDING"
    for choice, asked in (("goods_delivered_yes", "ask_goods"), ("refund_yes", "ask_refund")):
        d = authorize_merchant_response(R(), choice, asked=asked, complaint_acknowledged=True,
                                        ledger_credited=False, two_key_verified=True)
        assert not d.allowed and "no credit" in d.reason


def test_rail_refuses_reply_when_shop_not_credited_even_with_warrant():
    r, pol = _rail_with_policy(); cid = r.raise_complaint("PTM41870388", "U022")["complaint_id"]
    w = pol.issue(generation=r.generation, complaint_id=cid, code="105", txn_id="PTM41870388", amount_inr=700, readers=AGREE)
    with pytest.raises(ValueError):
        r.respond(cid, "105", warrant=w)


def test_ledger_flip_between_question_and_answer_is_caught_at_decision_time():
    r = MockRails(); t = Tatkal(r, AuditLog(), reader=_Reader("goods_delivered_yes"))
    t.open_case("PTM41870340", opened_by="incoming_udir")
    r.txns["PTM41870340"]["merchant_credited"] = False          # credit reversed after the question
    assert t.merchant_says("PTM41870340", "Haan, saamaan de diya")["action"] is None


# ---------------- found by the Diwali rush simulator (1 lakh synthetic cases), 30 Oct
def test_reversed_payment_message_with_past_date_passes_guard():
    from engine.rules import Txn, classify
    from engine import messages as M
    from engine.guard import check
    from datetime import date
    t = Txn("R1", 600, date(2026, 10, 27), "P2M", "failed", True, reversed_on=date(2026, 10, 31))
    r = classify(t, date(2026, 10, 31)).to_dict()
    txn = {"amount_inr": 600, "txn_date": "2026-10-27", "reversed_on": "2026-10-31", "merchant_name": "S", "customer_name": "C"}
    msg = M.customer_message(r, txn, {}, None)
    assert "31 Oct" in msg and check(msg, M.facts_for_guard(r, txn, {}, "customer")).ok


def test_settled_date_is_not_a_licence_to_promise():
    from engine.guard import check
    facts = {"amounts": [600], "dates": ["2026-10-31"], "settled_dates": ["2026-10-31"], "money_landed": True,
             "promise_dates": [], "year": 2026}
    assert not check("Aapka ₹600 31 Oct tak aa jayega.", facts).ok


def test_no_debit_shopkeeper_message_does_not_claim_a_person():
    from engine import messages as M
    msg = M.merchant_message({"path": "NO_DEBIT", "escalate": False}, {"amount_inr": 250}, "status")
    assert "insaan" not in msg and "kata hi nahi" in msg


def test_rush_simulation_every_template_passes_the_guard():
    from sim.rush import run_sync
    s = run_sync(3000)
    assert s["done"] == 3000 and s["guard_failures"] == 0 and s["messages_checked"] > 3000
    assert sum(s["buckets"].values()) == 3000



# ---------------- v3.4: fourth review (rail boundary), 30 Oct
def _acked_complaint(drop=False):
    r, pol = _rail_with_policy(); r.drop_ack = drop
    c = r.raise_complaint("PTM41870340", "U022")
    if drop:                                   # acknowledge the complaint, then lose the response ack
        r.complaints[c["complaint_id"]]["acknowledged"] = True
    return r, pol, c["complaint_id"]


@pytest.mark.parametrize("first,second", [("105", "106"), ("106", "105")])
@pytest.mark.parametrize("lost_ack", [False, True])
def test_one_terminal_response_per_complaint(first, second, lost_ack):
    r, pol, cid = _acked_complaint(drop=lost_ack)
    lab = {"105": "goods_delivered_yes", "106": "refund_yes"}
    w1 = pol.issue(generation=r.generation, complaint_id=cid, code=first, txn_id="PTM41870340", amount_inr=450, readers={"rule": lab[first], "sarvam": lab[first]})
    r1 = r.respond(cid, first, warrant=w1)
    assert r1["response_code"] == first and r1["acknowledged"] is (not lost_ack)
    w2 = pol.issue(generation=r.generation, complaint_id=cid, code=second, txn_id="PTM41870340", amount_inr=450, readers={"rule": lab[second], "sarvam": lab[second]})
    with pytest.raises(ValueError, match="terminal response"):
        r.respond(cid, second, warrant=w2)
    again = r.respond(cid, first, warrant=w1)             # same code retry is idempotent
    assert again["duplicate_request"] and again["response_code"] == first


def test_rail_object_cannot_mint_warrants():
    r, pol = _rail_with_policy()
    assert not hasattr(r, "warrants") and not hasattr(r.verifier, "issue") and not hasattr(r.verifier, "_key")


def test_policy_refuses_to_sign_when_readers_disagree():
    pol = PolicySigner()
    with pytest.raises(WarrantError):
        pol.issue(complaint_id="C", code="105", txn_id="T", amount_inr=1, readers={"rule": "goods_delivered_yes", "sarvam": "unclear"})


def test_correctly_signed_but_mismatched_reader_warrant_is_refused_by_the_rail():
    r, pol, cid = _acked_complaint()
    w = pol._sign({"wid": "x1", "complaint_id": cid, "code": "105", "txn_id": "PTM41870340", "amount_inr": 450,
                   "readers": {"rule": "goods_delivered_yes", "sarvam": "unclear"}, "override": None,
                   "issued_at": int(__import__("time").time()), "generation": r.generation})
    with pytest.raises(WarrantError, match="do not agree"):
        r.respond(cid, "105", warrant=w)


def test_single_key_override_warrant_needs_the_rail_to_allow_it():
    r, pol, cid = _acked_complaint()
    w = pol.issue(generation=r.generation, complaint_id=cid, code="105", txn_id="PTM41870340", amount_inr=450,
                  readers={"rule": "goods_delivered_yes", "sarvam": None}, single_key_override=True)
    with pytest.raises(WarrantError):
        r.respond(cid, "105", warrant=w)
    r.allow_single_key = True
    w2 = pol.issue(generation=r.generation, complaint_id=cid, code="105", txn_id="PTM41870340", amount_inr=450,
                   readers={"rule": "goods_delivered_yes", "sarvam": None}, single_key_override=True)
    assert r.respond(cid, "105", warrant=w2)["response_code"] == "105"


def test_service_cannot_send_second_code_after_lost_ack():
    rails = MockRails(); t = Tatkal(rails, AuditLog(), reader=_Reader("goods_delivered_yes"))
    t.open_case("PTM41870340", opened_by="incoming_udir")
    rails.drop_ack = True
    t.merchant_says("PTM41870340", "Haan, saamaan de diya")          # 105 committed, ack lost -> escalated
    cid = t.cases["PTM41870340"].complaint["complaint_id"]
    assert rails.responses[cid]["response_code"] == "105"
    w = t._policy.issue(generation=rails.generation, complaint_id=cid, code="106", txn_id="PTM41870340", amount_inr=450,
                        readers={"rule": "refund_yes", "sarvam": "refund_yes"})
    with pytest.raises(ValueError, match="terminal response"):
        rails.respond(cid, "106", warrant=w)


def test_spoken_yes_needs_read_back_before_105():
    t = Tatkal(MockRails(), AuditLog(), reader=_Reader("goods_delivered_yes")); t.open_case("PTM41870340", opened_by="incoming_udir")
    r = t.merchant_says("PTM41870340", "haan saamaan de diya", source="voice")
    c = t.cases["PTM41870340"]
    assert r["action"] is None and c.pending_confirm and c.state == "MERCHANT_ASKED"
    assert "confirm" in [x["text"] for x in c.transcript if x.get("to") == "merchant"][-1]
    r = t.merchant_says("PTM41870340", "haan, confirm", source="voice")
    assert r["action"] == "105" and c.state == "RESOLVED" and c.last_gate["read_back"] == "confirmed"


@pytest.mark.parametrize("reply", ["nahi", "nahi nahi maine nahi diya", "pata nahi", "hmm"])
def test_spoken_yes_not_confirmed_goes_to_a_person(reply):
    t = Tatkal(MockRails(), AuditLog(), reader=_Reader("goods_delivered_yes")); t.open_case("PTM41870340", opened_by="incoming_udir")
    t.merchant_says("PTM41870340", "haan saamaan de diya", source="voice")
    r = t.merchant_says("PTM41870340", reply, source="voice")
    assert r["action"] is None and t.cases["PTM41870340"].state == "ESCALATED"


def test_typed_or_tapped_yes_needs_no_read_back():
    t = Tatkal(MockRails(), AuditLog(), reader=_Reader("goods_delivered_yes")); t.open_case("PTM41870340", opened_by="incoming_udir")
    assert t.merchant_says("PTM41870340", "Haan, saamaan de diya")["action"] == "105"


def test_u021_returned_goods_credit_is_human_routed():
    from engine.rules import Txn, classify
    from engine import messages as M
    from engine.guard import check
    from datetime import date
    t = Txn("R2", 899, date(2026, 10, 29), "P2M", "success", True, merchant_credited=True, return_credit_pending=True)
    r = classify(t, date(2026, 10, 31))
    assert r.path == "RETURN_CREDIT" and r.udir_code == "U021" and r.escalate
    rd = r.to_dict(); txn = {"amount_inr": 899, "txn_date": "2026-10-29", "merchant_credited": True, "merchant_name": "S", "customer_name": "C"}
    msg = M.customer_message(rd, txn, {"udir_complaint": True}, "UDIR-1")
    assert "lautaye" in msg and check(msg, M.facts_for_guard(rd, txn, {"udir_complaint": True}, "customer")).ok



# ---------------- v3.5: fifth review (concurrency, reset replay, operator routes), 31 Oct
def test_concurrent_105_and_106_exactly_one_commits():
    import threading, time
    for _ in range(5):
        r, pol, cid = _acked_complaint()
        real_spend = r.verifier.spend
        def slow_spend(*a, **k):                      # widen the race window on purpose
            time.sleep(0.05); return real_spend(*a, **k)
        r.verifier.spend = slow_spend
        ws = {"105": pol.issue(generation=r.generation, complaint_id=cid, code="105", txn_id="PTM41870340", amount_inr=450, readers=AGREE),
              "106": pol.issue(generation=r.generation, complaint_id=cid, code="106", txn_id="PTM41870340", amount_inr=450,
                               readers={"rule": "refund_yes", "sarvam": "refund_yes"})}
        results, barrier = {}, threading.Barrier(2)
        def go(code):
            barrier.wait()
            try:
                results[code] = r.respond(cid, code, warrant=ws[code])["response_code"]
            except ValueError as e:
                results[code] = "refused: " + str(e)
        th = [threading.Thread(target=go, args=(c,)) for c in ("105", "106")]
        [t.start() for t in th]; [t.join() for t in th]
        ok = [c for c, v in results.items() if not v.startswith("refused")]
        assert len(ok) == 1 and r.responses[cid]["response_code"] == ok[0]


def test_old_warrant_refused_after_reset_even_if_ids_matched():
    r, pol, cid = _acked_complaint()
    w = pol.issue(generation=r.generation, complaint_id=cid, code="105", txn_id="PTM41870340", amount_inr=450, readers=AGREE)
    r.reset()
    cid2 = r.raise_complaint("PTM41870340", "U022")["complaint_id"]
    assert cid2 != cid                                   # complaint ids are never reused
    w_forged_id = {**w, "complaint_id": cid2}            # even re-pointing the old warrant breaks its signature
    for ww in (w, w_forged_id):
        with pytest.raises(WarrantError):
            r.respond(cid2, "105", warrant=ww)
    r.complaints[cid] = {**r.complaints[cid2], "complaint_id": cid}   # worst case: the old id reappears
    with pytest.raises(WarrantError, match="earlier rail session"):
        r.respond(cid, "105", warrant=w)


def test_service_reset_rotates_the_policy_key():
    rails = MockRails(); t = Tatkal(rails, AuditLog())
    old_pub = t._policy.public_key
    rails.reset(); t.rotate_policy()
    assert t._policy.public_key != old_pub and rails.verifier._pub.public_bytes_raw() == t._policy.public_key
