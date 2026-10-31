"""Engine owned message templates. Every number and date comes from the Ruling or the rail.

Consent questions to the merchant are fixed text on purpose: the exact words of a question
that leads to a UDIR action should not vary with an LLM's mood. The LLM may only rephrase
explanations to the customer, and its draft is checked by engine.guard against the same facts.
"""
from __future__ import annotations

from datetime import date

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def human_date(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTH_ABBR[d.month - 1]}"


def escalate_reason_hi(r: dict) -> str:
    parts = []
    reasons = " ".join(r.get("escalate_reasons", [])).lower()
    if "deadline passed" in reasons:
        parts.append(f"RBI ki deadline {human_date(r.get('deadline'))} nikal chuki hai aur bank par "
                     f"₹{r.get('compensation_due_inr', 0):,} ka muavza banta hai")
    if "returned goods" in reasons:
        parts.append("lautaye gaye saamaan ke refund ki jaanch karni hai")
    if "goods dispute" in reasons:
        parts.append("saamaan ko lekar vivaad hai")
    if "review limit" in reasons:
        parts.append("rakam badi hai")
    if "contradictory" in reasons:
        parts.append("bank aur ledger ki jaankari mel nahi kha rahi")
    if "asked for a human" in reasons:
        parts.append("aapne insaan se baat karni chahi")
    if "distressed" in reasons:
        parts.append("aap pareshan lag rahe hain")
    if "refund already" in reasons or "verification" in reasons:
        parts.append("dukaandar ki baat ki jaanch karni hai")
    if "unclear" in reasons or "declined" in reasons or "acknowledged" in reasons:
        parts.append("is case ko insaan ki nazar chahiye")
    return " aur ".join(dict.fromkeys(parts)) or "is case ko insaan ki nazar chahiye"


def complaint_line(ack: dict, complaint_id: str | None, pending: bool) -> str:
    if ack.get("udir_complaint") and complaint_id:
        return f" Aapki shikayat darj ho gayi hai, reference {complaint_id}."
    if pending:
        return " Aapki shikayat NPCI ko bheji ja rahi hai; pushti milte hi bataunga."
    return ""


def customer_message(r: dict, txn: dict, ack: dict, complaint_id: str | None, *, pending: bool = False) -> str:
    amt = f"₹{txn['amount_inr']:,}"
    shop = txn.get("merchant_name") or "dukaan"
    dl = human_date(r.get("deadline"))
    path = r["path"]
    c = complaint_line(ack, complaint_id, pending)
    if r.get("escalate"):
        return f"Aapka {amt} wala case ek insaan dekhega, kyunki {escalate_reason_hi(r)}. Main abhi koi tareekh ka vaada nahi kar sakta.{c}"
    if path == "SOUNDBOX_MISSED":
        return f"Aapka {amt} {shop} ke khate mein pahunch chuka hai. Sirf Soundbox ki awaaz nahi aayi. Aapko kuch karne ki zaroorat nahi."
    if path == "PAYEE_CREDITED":
        return f"Aapka {amt} {shop} ke khate mein pahunch chuka hai. Saamaan ke baare mein dukaan se pooch raha hoon.{c}"
    if path == "P2M_PENDING":
        return (f"Aapka {amt} abhi {shop} ke khate mein confirm nahi hua hai. RBI ke niyam ke hisaab se {dl} tak"
                f" ya to dukaan ko mil jayega ya aapke khate mein wapas aa jayega. Ye zyada se zyada samay hai.{c}")
    if path == "P2M_FAILED_DEBITED":
        return (f"Ye payment fail hua tha, isliye {shop} ko paisa nahi mila. Aapka {amt} aapka bank {dl} tak wapas karega."
                f" Ye zyada se zyada samay hai. Dobara payment se pehle dekh lijiye ki pehla wapas aaya ya nahi.{c}")
    if path == "P2M_REVERSED":
        return f"Ye payment fail hua tha aur aapka {amt} {human_date(txn.get('reversed_on'))} ko wapas pahunch chuka hai."
    if path == "DUPLICATE":
        return f"Aapse {amt} do baar kata gaya. Main {shop} se ek payment ke refund ki manzoori le raha hoon.{c}"
    if path in ("P2P_PENDING", "P2P_FAILED_DEBITED"):
        return (f"Aapka {amt} ka transfer abhi poora nahi hua hai. RBI ke niyam ke hisaab se {dl} tak paisa ya to pahunch"
                f" jayega ya aapke khate mein wapas aa jayega. Ye zyada se zyada samay hai.{c}")
    if path == "P2P_CREDITED":
        return f"Aapka {amt} ka transfer saamne wale ke khate mein pahunch chuka hai."
    if path == "NO_DEBIT":
        return "Aapke khate se paisa kata hi nahi hai. Aap dobara payment kar sakte hain."
    return f"Aapka {amt} wala case ek insaan dekhega. Main abhi koi tareekh ka vaada nahi kar sakta."


def customer_outcome(code: str, txn: dict, r: dict) -> str:
    """After the rail acknowledged the payee side response."""
    amt = f"₹{txn['amount_inr']:,}"
    shop = txn.get("merchant_name") or "dukaandar"
    if code == "105":
        return (f"{shop} ne confirm kiya hai ki aapko {amt} ka saamaan diya gaya tha, aur unka jawab NPCI ne darj kar liya hai."
                f" Agar aap isse sahmat nahi hain to 'insaan se baat' boliye.")
    return (f"{shop} ne {amt} ke refund ki manzoori di hai aur NPCI ne ye jawab darj kar liya hai."
            f" Refund ek alag transaction se hoga. Abhi paisa wapas aane ki pushti nahi hui hai.")


def merchant_message(r: dict, txn: dict, stage: str = "status") -> str:
    amt = f"₹{txn['amount_inr']:,}"
    cust = txn.get("customer_name") or "Customer"
    path = r["path"]
    if stage == "ask_goods":
        return (f"{cust} ka {amt} aapke khate mein aa gaya hai, par unhe confirmation nahi mila. "
                f"Kya aapne unhe saamaan de diya hai? Haan ya nahi boliye.")
    if stage == "ask_refund":
        if path == "DUPLICATE":
            return f"{cust} se {amt} do baar kata gaya. Kya aap ek payment ka refund karne ki manzoori dete hain? Haan ya nahi boliye."
        return f"Theek hai. Kya aap {cust} ko {amt} refund karne ki manzoori dete hain? Haan ya nahi boliye."
    if stage == "reask_goods":
        return f"Maaf kijiye, samajh nahi aaya. {amt} wale payment par, kya aapne saamaan diya? Sirf haan ya nahi boliye."
    if stage == "reask_refund":
        return f"Maaf kijiye, samajh nahi aaya. Kya aap {amt} refund karne ki manzoori dete hain? Sirf haan ya nahi boliye."
    if stage == "sent_105":
        return "Theek hai. Aapka jawab bhej diya gaya hai ki saamaan de diya gaya tha."
    if stage == "sent_106":
        return "Theek hai. Refund ki manzoori ka jawab bhej diya gaya hai. Refund ka paisa alag se bheja jayega."
    if stage == "pending_ack":
        return "Aapka jawab bheja ja raha hai. Pakki khabar milte hi bataunga."
    if stage == "human":
        return "Ye case ek insaan dekhega. Woh aapse sampark karenge."
    if path == "SOUNDBOX_MISSED":
        return f"{amt} ka payment aapke khate mein aa gaya hai. Saamaan de sakte hain."
    if path == "P2M_FAILED_DEBITED":
        return f"{amt} ka payment fail hua hai, ye aapke khate mein nahi aayega. Saamaan na dein; customer ka paisa uska bank lautayega."
    if path in ("P2M_PENDING", "DUPLICATE"):
        return f"{amt} ka payment abhi aapke khate mein nahi aaya hai. Payment aane par hi saamaan dein."
    if path == "P2M_REVERSED":
        return f"{amt} ka payment fail hua tha aur customer ko wapas mil chuka hai."
    if path == "NO_DEBIT":
        return f"{amt} customer ke khate se kata hi nahi. Unse dobara payment karne ko kahiye."
    if r.get("escalate"):
        return f"{amt} wala case ek insaan dekh raha hai."
    return f"{amt} wale payment par aapko abhi kuch karne ki zaroorat nahi."


def facts_for_guard(r: dict, txn: dict, ack: dict, audience: str, *, state: str | None = None) -> dict:
    """promise_ok is False once the case is escalated, resolved or past its deadline: from then on
    no time or date may be promised at all, because no rule guarantees one."""
    amounts = {txn["amount_inr"]}
    if r.get("compensation_due_inr"):
        amounts.add(r["compensation_due_inr"])
    dates = {d for d in (r.get("deadline"), txn.get("reversed_on"), txn.get("credited_on")) if d}
    if audience == "merchant":
        landed = bool(txn.get("merchant_credited"))
    else:
        landed = bool(txn.get("reversed_on") or txn.get("merchant_credited"))
    td = txn["txn_date"]
    promise_ok = bool(r.get("deadline")) and not r.get("escalate") and not r.get("days_late") \
        and state not in ("RESOLVED", "ESCALATED") and r.get("path") not in ("SOUNDBOX_MISSED", "P2M_REVERSED", "P2P_CREDITED", "PAYEE_CREDITED")
    return {
        "amounts": sorted(amounts),
        "dates": sorted(dates),
        "deadline_days": r.get("tat_days") if promise_ok else None,
        "promise_dates": [r["deadline"]] if promise_ok else [],
        "human_assigned": bool(r.get("escalate")) or state == "ESCALATED",
        "either_outcome": r.get("path") == "P2M_PENDING" and promise_ok,
        "acknowledged": dict(ack),
        "money_landed": landed,
        "duplicate": r.get("path") == "DUPLICATE",
        "names": [n for n in (txn.get("merchant_name"), txn.get("customer_name")) if n],
        "year": date.fromisoformat(td).year if isinstance(td, str) else td.year,
        "settled_dates": [d for d in (txn.get("reversed_on"), txn.get("credited_on")) if d],
    }


def speakable(text: str) -> str:
    """Make a message easier for TTS: drop reference codes, spell months, say rupees."""
    import re
    t = re.sub(r",?\s*reference\s+[A-Z]+-[\w-]+", ". Reference app mein dikh raha hai", text)
    t = re.sub(r"₹\s*([\d,]+)", lambda m: m.group(1).replace(",", "") + " rupaye", t)
    full = {"Jan": "January", "Feb": "February", "Mar": "March", "Apr": "April", "Jun": "June", "Jul": "July",
            "Aug": "August", "Sep": "September", "Oct": "October", "Nov": "November", "Dec": "December"}
    t = re.sub(r"\b(\d{1,2}) (Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", lambda m: f"{m.group(1)} {full[m.group(2)]}", t)
    return t.replace("UDIR", "U D I R").replace("NPCI", "N P C I").replace("RBI", "R B I")


def tighten_for_llm(facts: dict, template: str) -> dict:
    """The LLM only rephrases the engine message. So any date or rupee figure in its draft that the
    engine message did not contain is invented, even if it is a real date somewhere in the case file
    (live testing showed Sarvam 105B turning the payment date into fake follow up deadlines)."""
    from engine.guard import extract_amounts, extract_dates
    t_dates = {d for d, *_ in extract_dates(template, facts.get("year", 2026))}
    t_amounts = {a for a, *_ in extract_amounts(template)}
    return {**facts,
            "dates": sorted(t_dates),
            "promise_dates": sorted(t_dates & set(facts.get("promise_dates", []))),
            "amounts": sorted(t_amounts | set(facts.get("amounts", [])[:1]))}
