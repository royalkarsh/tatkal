from engine.audit import AuditLog

def test_chain_valid_then_tamper_detected():
    a = AuditLog()
    for i in range(5):
        a.append("e", {"i": i}, case_id="c")
    assert a.verify()["valid"]
    a.tamper_for_demo(3, {"i": 99})
    v = a.verify(); assert not v["valid"] and v["broken_at"] == 3


def test_simulated_tamper_leaves_real_log_valid():
    a = AuditLog()
    for i in range(4):
        a.append("e", {"i": i})
    assert not a.simulate_tamper(2, {"i": 9})["valid"] and a.verify()["valid"]
