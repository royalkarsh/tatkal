"""Strict, deterministic reading of short replies (Hinglish, Devanagari as Saaras codemix
returns it, and English).

Each question has its own reader, because "haan" means different things depending on
what was asked. Consent must be present tense and first person. Anything mixed, hedged,
past tense ("refund kar diya"), reported speech ("customer ne bola") or off topic is not
consent. When a live LLM is available, agent.service also asks it to read the same reply
and only acts when both readers agree (two key rule).
"""
from __future__ import annotations

import re

YES = (r"(?<![\w])(haan|haa|han|ha|ji\s*haan|haanji|yes|yeah|yup|bilkul|sahi\s*hai|correct|"
       r"हाँ|हां|जी\s*हाँ|जी\s*हां|बिल्कुल|बिलकुल|सही)(?![\w])")
NO = r"(?<![\w])(nahi|nahin|nai|na|no|nope|mat|नहीं|ना|मत)(?![\w])"
GAVE = (r"(de\s*diya|de\s*diye|de\s*di|dediya|diya\s*tha|diya\s*hai|saamaan\s*(?:de|mil)|samaan\s*(?:de|mil)|"
        r"saman\s*(?:de|mil)|handed|delivered|given|le\s*gaya|le\s*gaye|"
        r"दे\s*दिया|दे\s*दी|दे\s*दिए|दिया\s*था|दिया\s*है|सामान\s*(?:दे|मिल)|ले\s*गया|ले\s*गए)")
NOT_GAVE = (r"(nahi\s*diya|nahin\s*diya|nahi\s*di|nahi\s*mila|nahi\s*liya|liya\s*hi\s*nahi|kuch\s*nahi\s*liya|"
            r"not\s+(?:delivered|given|handed)|didn'?t\s+(?:give|deliver)|नहीं\s*दिया|नहीं\s*दी|नहीं\s*लिया)")
REFUND_WORD = r"(refund|wapas|vapas|lauta|return|रिफंड|वापस|लौटा)"
REFUND_PAST = (r"(kar\s*diya|kar\s*di|ho\s*gaya|ho\s*chuka|ho\s*chuki|kar\s*chuk|already|pehle\s*hi|de\s*diya|"
               r"done|processed|कर\s*दिया|हो\s*गया|हो\s*चुका|पहले\s*ही)")
REFUND_CONSENT = (r"(kar\s*do|kar\s*dijiye|kar\s*dena|kar\s*sakte|karna\s*hai|kar\s*denge|manzoor|approve|"
                  r"go\s*ahead|please\s*refund|refund\s*it|कर\s*दो|कर\s*दीजिए|मंज़ूर|मंजूर)")
REPORTED = (r"(customer\s*ne|usne|unhone|grahak\s*ne|bola\s*tha|keh\s*raha|keh\s*rahi|maang\s*raha|maanga|"
            r"asked\s+for|said|ग्राहक\s*ने|उसने|बोला\s*था|मांग)")
HEDGE = (r"(shayad|pata\s*nahi|maybe|not\s*sure|yaad\s*nahi|lagta|baad\s*mein|busy|check\s*kar|dekh\s*ke|"
         r"शायद|पता\s*नहीं|याद\s*नहीं|बाद\s*में)")
INJECTION = r"(system|ignore|instruction|send\s*code|code\s*10[56]|prompt|assistant:|tatkal:|admin)"
# A "yes" is only a yes when nothing in the same reply pulls the other way.
# "Haan de diya, but customer took nothing" / "I handed it over. Actually I did not." -> unclear.
CONTRA = (r"(?<![\w])(but|lekin|magar|par\s+(?:customer|grahak|usne|unhone|wo|woh|vo|maine|abhi)|however|actually|"
          r"asal\s*mein|waise|पर\s+(?:ग्राहक|उसने|मैंने|अभी)|लेकिन|मगर|असल\s*में)(?![\w])")
NEG_ANY = (r"((?<![\w])(not|nothing|never|nobody|no|nope|nahi|nahin|nai|na|mat|kuch\s*nahi|नहीं|ना|मत|कुछ\s*नहीं)(?![\w])"
           r"|n't|n’t)")


def _contradicted(s: str) -> bool:
    return _has(CONTRA, s) or _has(NEG_ANY, s)


def _has(p: str, s: str) -> bool:
    return re.search(p, s, re.I) is not None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def read_goods_answer(text: str) -> str:
    """Answer to: 'Did you hand over the goods?'  ->  goods_delivered_yes | goods_not_delivered |
    refund_already_claimed | unclear"""
    s = _clean(text)
    if not s or _has(INJECTION, s) or _has(HEDGE, s):
        return "unclear"
    if _has(REFUND_WORD, s) and _has(REFUND_PAST, s):
        return "refund_already_claimed"
    if _has(REFUND_WORD, s):
        return "unclear"                       # refund talk is not an answer to the goods question
    not_gave = _has(NOT_GAVE, s)
    gave = _has(GAVE, s) and not not_gave
    yes, no = _has(YES, s), _has(NO, s)
    if not_gave and not yes:
        return "goods_not_delivered"
    if no and not yes and not gave:
        return "goods_not_delivered"
    if (gave or yes) and not no and not not_gave:
        return "unclear" if _contradicted(s) else "goods_delivered_yes"
    return "unclear"


def read_refund_consent(text: str) -> str:
    """Answer to: 'Do you authorise a refund of ₹X?'  ->  refund_yes | refund_declined |
    refund_already_claimed | unclear"""
    s = _clean(text)
    if not s or _has(INJECTION, s) or _has(HEDGE, s) or _has(REPORTED, s):
        return "unclear"
    if _has(REFUND_PAST, s):
        return "refund_already_claimed"
    yes, no = _has(YES, s), _has(NO, s)
    consent = _has(REFUND_CONSENT, s)
    if no and not yes and not consent:
        return "refund_declined"
    if (yes or consent) and not no:
        return "unclear" if _contradicted(s) else "refund_yes"
    return "unclear"


CONFIRM = r"(?<![\w])(confirm|haan|haa|han|ji|yes|sahi|theek|1|हाँ|हां|जी|सही|ठीक)(?![\w])"


def read_confirm(text: str) -> str:
    """Answer to the read back after a SPOKEN yes: 'confirm' or 'not_confirmed'. Strict: any negation,
    contradiction, hedge or injection is not a confirmation."""
    s = _clean(text)
    if not s or _has(INJECTION, s) or _has(HEDGE, s) or _contradicted(s):
        return "not_confirmed"
    return "confirm" if _has(CONFIRM, s) else "not_confirmed"


def read_merchant(text: str, asked: str) -> str:
    return read_goods_answer(text) if asked == "ask_goods" else read_refund_consent(text)


def customer_intent(text: str) -> str:
    s = _clean(text)
    if _has(r"(insaan|insan|human|agent\s*se|manager|kisi\s*se\s*baat|call\s*karo|asli\s*aadmi|इंसान|किसी\s*से\s*बात|मैनेजर)", s):
        return "ask_human"
    if _has(r"(emergency|hospital|dawai|bahut\s*pareshan|madad\s*karo|ilaj|अस्पताल|बहुत\s*परेशान|दवाई|मदद)", s):
        return "distress"
    if _has(r"(kab|when|kitne\s*din|kitna\s*time|kitna\s*samay|कब|कितने\s*दिन|कितना\s*समय)", s):
        return "ask_when"
    if _has(r"(kat\s*gaye|kat\s*gaya|cut|debit|deduct|nahi\s*mila|nahi\s*aaya|soundbox|stuck|atak|कट\s*गए|कट\s*गया|नहीं\s*मिल|अटक)", s):
        return "report_stuck"
    if _has(r"(thank|shukriya|dhanyavad|धन्यवाद|शुक्रिया)", s):
        return "thanks"
    return "other"
