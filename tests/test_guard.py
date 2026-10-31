import pytest
from engine.guard import check, extract_amounts, extract_dates, extract_durations

F = {"amounts": [450], "dates": ["2026-11-05"], "deadline_days": 5,
     "acknowledged": {"udir_complaint": True}, "money_landed": False, "year": 2026}

@pytest.mark.parametrize("text", [
    "Chinta mat kijiye, 24 ghante mein paisa wapas aa jayega.",
    "आपका ₹४५० दो दिन में वापस आ जाएगा।",
    "Aapka paisa 5 din mein wapas aa jayega.",          # right number, but a promise, not a limit
    "Kal tak paisa mil jayega.",
    "Aapko ₹900 milenge.",
    "Deadline 6 Nov hai.",
    "Pakka ₹450 wapas milenge.",
    "Refund ho gaya hai.",
    "Paisa aa gaya hai.",
    "Apna UPI PIN batayiye.",
    "You will get your money back within twenty four hours.",
])
def test_blocks(text):
    assert not check(text, F).ok

@pytest.mark.parametrize("text", [
    "Aapka ₹450 5 Nov tak ya to dukaan ko mil jayega ya aapke khate mein wapas aa jayega.",
    "पैसे ५ नवंबर तक वापस आ जाएंगे।",
    "Aapka paisa 5 din ke andar wapas aa jayega.",
    "Kabhi bhi apna PIN kisi ko mat batayiye.",
    "Main abhi status check kar raha hoon.",
    "Paisa abhi nahi aaya hai.",
    "Aapki shikayat darj ho gayi hai.",
])
def test_allows(text):
    assert check(text, F).ok

def test_completion_needs_ack():
    assert not check("Aapki shikayat darj ho gayi hai.", {**F, "acknowledged": {}}).ok

def test_extractors():
    assert [a[0] for a in extract_amounts("₹1.5 lakh aur 450 rupaye aur ₹४५०")] == [150000, 450, 450]
    assert [d[0] for d in extract_dates("5 Nov, 5 नवंबर, 2026-11-05, Nov 5", 2026)] == ["2026-11-05"] * 4
    assert [round(d[0]) for d in extract_durations("24 ghante, do din, 1 hafta")] == [24, 48, 168]

def test_spans_point_at_the_claim():
    v = check("Chinta mat kijiye, 24 ghante mein paisa wapas aa jayega.", F).violations[0]
    assert "Chinta mat kijiye, 24 ghante mein paisa wapas aa jayega."[v.start:v.end] == "24 ghante"
