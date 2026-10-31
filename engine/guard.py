"""Grounding guard v3: every claim a draft makes must be backed by the case file.

It does not try to understand language in general. It extracts the claims that can hurt a
customer or merchant in a payment dispute and checks each one against facts the engine
produced. When in doubt it blocks, and the engine's own (also checked) template is spoken.

  empty       blank or whitespace output                        always blocked
  amount      ₹ figures, "450 rupaye", "chaar sau pachaas",     must be in facts.amounts
              bare numbers next to money verbs ("900 milenge")
  multiplier  "do baar milega", "double", "teen guna"            never (unless it states a
                                                                 true duplicate debit)
  date        5 formats, Roman and Devanagari                    must be in facts.dates
  duration    "24 ghante", "do din", "36 hours", "kal",          must not beat the rule, and
              "aaj raat", "thodi der"                            the rule's own limit must be
                                                                 phrased as an outer limit
  guarantee   "pakka", "guarantee", "100%", "zaroor"             never next to money
  completion  "refund ho chuka", "response submit kar diya",     only after the rail
              "shikayat darj ho gayi"                            acknowledged it
  landed      "paisa aa gaya / aa chuka / pahunch chuka"         only if the ledger shows it
  credential  PIN, MPIN, OTP, CVV, password, card number         never; a warning not to share
                                                                 is allowed only when the
                                                                 negation sits next to it

Works on English, Hinglish (Roman Hindi) and Devanagari (Saaras codemix output), with
Devanagari digits and Hindi and English number words.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date

DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

UNITS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "chhah": 6,
    "saat": 7, "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "pandrah": 15, "bees": 20,
    "chaubees": 24, "chaubis": 24, "chobis": 24, "tees": 30, "chattis": 36, "chhattis": 36, "chaalis": 40,
    "pachaas": 50, "pachas": 50, "saath": 60, "assi": 80, "nabbe": 90,
    "adhe": 0.5, "aadhe": 0.5, "dedh": 1.5, "dhai": 2.5,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "सात": 7, "आठ": 8, "नौ": 9,
    "दस": 10, "बारह": 12, "पंद्रह": 15, "बीस": 20, "चौबीस": 24, "तीस": 30, "छत्तीस": 36, "चालीस": 40,
    "पचास": 50, "साठ": 60, "अस्सी": 80, "डेढ़": 1.5, "ढाई": 2.5,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "twenty four": 24, "twenty-four": 24,
    "thirty": 30, "thirty six": 36, "forty": 40, "forty eight": 48, "fifty": 50,
}
HUNDRED = {"sau": 100, "सौ": 100, "hundred": 100}
THOUSAND = {"hazaar": 1000, "hazar": 1000, "हज़ार": 1000, "हजार": 1000, "thousand": 1000, "k": 1000}
LAKH = {"lakh": 100000, "lac": 100000, "लाख": 100000}

MONTHS = {
    "jan": 1, "january": 1, "जनवरी": 1, "feb": 2, "february": 2, "फरवरी": 2, "mar": 3, "march": 3, "मार्च": 3,
    "apr": 4, "april": 4, "अप्रैल": 4, "may": 5, "मई": 5, "jun": 6, "june": 6, "जून": 6, "jul": 7, "july": 7,
    "जुलाई": 7, "aug": 8, "august": 8, "अगस्त": 8, "sep": 9, "sept": 9, "september": 9, "सितंबर": 9,
    "सितम्बर": 9, "oct": 10, "october": 10, "अक्टूबर": 10, "nov": 11, "november": 11, "नवंबर": 11,
    "नवम्बर": 11, "dec": 12, "december": 12, "दिसंबर": 12, "दिसम्बर": 12,
}
UNIT_HOURS = {
    "minute": 1 / 60, "minutes": 1 / 60, "min": 1 / 60, "mins": 1 / 60, "मिनट": 1 / 60,
    "hour": 1, "hours": 1, "hr": 1, "hrs": 1, "ghanta": 1, "ghante": 1, "ghanton": 1, "घंटा": 1, "घंटे": 1, "घंटों": 1,
    "day": 24, "days": 24, "din": 24, "dino": 24, "dinon": 24, "दिन": 24, "दिनों": 24,
    "working day": 24, "working days": 24, "business days": 24,
    "week": 168, "weeks": 168, "hafta": 168, "hafte": 168, "हफ्ता": 168, "हफ्ते": 168, "हफ़्ते": 168,
}
RELATIVE_HOURS = {
    "abhi": 0, "turant": 0, "fauran": 0, "immediately": 0, "instantly": 0, "right now": 0, "अभी": 0, "तुरंत": 0,
    "thodi der": 1, "kuch der": 1, "kuch hi minutes": 0.2, "few minutes": 0.2, "कुछ देर": 1, "थोड़ी देर": 1,
    "aaj": 12, "today": 12, "tonight": 12, "aaj raat": 12, "आज": 12, "jaldi": 12, "soon": 12, "जल्दी": 12,
    "kal": 24, "tomorrow": 24, "कल": 24, "parso": 48, "परसों": 48,
    "jald": 12, "jald hi": 12, "jaldi hi": 12, "kuch din": 48, "kuch dino": 48, "kuch dinon": 48, "thode din": 48,
    "kuch hi din": 48, "few days": 48, "couple of days": 48, "कुछ दिन": 48, "कुछ दिनों": 48,
}
MONEY_VERBS = (r"(aa\s*jaye?ga|aa\s*jayenge|aayega|aaega|aayenge|mil\s*jaye?ga|mil\s*jayenge|milenge|milega|"
               r"wapas|vapas|refund|revers|credit|settle|lautega|laut\s*aaye?ga|"
               r"पैसा|पैसे|वापस|मिल\s*जाए?गा|मिल\s*जाएंगे|आ\s*जाए?गा|आ\s*जाएंगे|मिलेंगे|मिलेगा|रिफंड|"
               r"will\s+(?:get|receive|come\s+back|be\s+back|be\s+(?:credited|refunded|reversed|settled))|"
               r"get\s+(?:your|the)\s+(?:money|amount)|money\s+will|amount\s+will)")
COMMIT_VERBS = (r"(jaye?ga|jayenge|jaayega|hoga|honge|milega|milenge|denge|dega|karega|karenge|karegi|lag\s*sakt|"
                r"lagenge|lagega|update|will|shall|जाएगा|जाएंगे|होगा|मिलेगा|मिलेंगे|देंगे|करेंगे)")
MONEY_NOUNS = r"(paisa|paise|money|refund|payment|amount|rakam|muavza|पैसा|पैसे|रकम|मुआवज़ा|मुआवजा|रिफंड)"
LIMIT_MARKERS = (r"(tak|se\s*pehle|se\s*pahle|ke\s*andar|zyada\s*se\s*zyada|adhik\s*se\s*adhik|latest|"
                 r"no\s*later\s*than|outer|within|by\s+\d|तक|से\s*पहले|के\s*अंदर|ज़्यादा\s*से\s*ज़्यादा|ज्यादा\s*से\s*ज्यादा)")
GUARANTEE = r"(guarantee|guaranteed|pakka|pakki|100\s*%|hundred\s*percent|definitely|zaroor|promise|पक्का|गारंटी|ज़रूर|जरूर)"
MULTIPLIER = (r"((?:do|2|teen|3|char|4|दो|तीन)\s*(?:baar|guna|गुना|बार)|double|dugna|dugne|duguna|twice|triple|"
              r"\d+\s*x\b|दुगना|दोगुना)")
COMPLETION = {
    "udir_complaint": r"(complaint|shikayat|शिकायत)\s*(?:\S+\s*){0,3}?(darj\s*ho\s*ga[yi]i?|darj\s*kar\s*d[iy]a?|darj\s*ho\s*chuki|register\s*ho\s*ga[yi]i?|register\s*kar\s*di|registered|filed|raised|lodged|दर्ज\s*हो\s*गई|दर्ज\s*कर\s*दी)",
    "udir_response": r"(response|jawab|reply|जवाब|105|106)\s*(?:\S+\s*){0,4}?(bhej\s*d[iy]a?|bhej\s*chuk|submit|sent|de\s*diya|kar\s*diya|ho\s*gaya|भेज\s*दिया|सबमिट)",
    "refund_initiated": r"(refund|रिफंड)\s*(?:\S+\s*){0,3}?(ho\s*gaya|ho\s*chuka|kar\s*diya|kar\s*chuk|initiate|initiated|processed|process\s*ho|process\s*kar\s*rah|process\s*shuru|shuru\s*ho|shuru\s*kar|chal\s*raha|already|credit\s*ho|हो\s*गया|हो\s*चुका|कर\s*दिया|शुरू)",
}
LANDED = (r"(paisa|paise|payment|amount|money|पैसा|पैसे|भुगतान)\s*(?:\S+\s*){0,5}?(aa\s*gaya|aa\s*gaye|aa\s*chuka|mil\s*gaya|"
          r"mil\s*gaye|mil\s*chuka|credit\s*ho\s*gaya|pahunch\s*gaya|pahunch\s*chuka|received|has\s+reached|"
          r"आ\s*गया|आ\s*गए|आ\s*चुका|मिल\s*गया|मिल\s*चुका|पहुँच\s*गया|पहुंच\s*गया|पहुँच\s*चुका|पहुंच\s*चुका)")
HUMAN_CLAIM = (r"(team|insaan|insan|human|agent|specialist|expert|executive|officer|manager|staff|team\s*member|"
               r"इंसान|टीम|एजेंट)\s*(?:\S+\s*){0,5}?(check|dekh|review|kaam|jaanch|handle|look|देख|जांच|जाँच)")
NOT_NEAR = r"(nahi|nahin|not|नहीं|na)\b"
CREDENTIAL = r"(upi\s*pin|mpin|m-pin|atm\s*pin|pin\s*number|pin|otp|cvv|password|passcode|card\s*number|पिन|ओटीपी|पासवर्ड)"
NEGATION = r"(mat|nahi|nahin|never|don'?t|do\s+not|kabhi\s+nahi|kabhi\s+bhi\s+nahi|na|no\s+one|मत|नहीं|कभी\s+नहीं|ना)"
CLAUSE_SPLIT = r"[,;:]|\b(?:but|lekin|magar|bas|sirf|only|however)\b|लेकिन|मगर|बस"


@dataclass
class Violation:
    kind: str
    text: str
    start: int
    end: int
    reason: str


@dataclass
class Verdict:
    ok: bool
    violations: list[Violation] = field(default_factory=list)
    claims: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "violations": [asdict(v) for v in self.violations], "claims": self.claims}


def _norm(text: str) -> str:
    return text.translate(DEV_DIGITS)


def _sentences(text: str) -> list[tuple[int, str]]:
    out, start = [], 0
    for m in re.finditer(r"[.!?।\n]+", text):
        out.append((start, text[start:m.end()]))
        start = m.end()
    if start < len(text):
        out.append((start, text[start:]))
    return out


def _alt(words) -> str:
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


_UNIT_ALT = _alt(UNITS)
_NUM = rf"(\d[\d,]*(?:\.\d+)?|{_UNIT_ALT})"
# compound number words: "chaar sau pachaas", "paanch sau", "do hazaar", "1.5 lakh"
_COMPOUND = (rf"((?:{_NUM}\s*)?(?:{_alt(LAKH)})\s*)?((?:{_NUM}\s*)?(?:{_alt(THOUSAND)})\s*)?"
             rf"((?:{_NUM}\s*)?(?:{_alt(HUNDRED)})\s*)?({_NUM})?")


def _val(tok: str | None) -> float | None:
    if not tok:
        return None
    tok = tok.strip().lower().replace(",", "")
    try:
        return float(tok)
    except ValueError:
        return UNITS.get(tok)


def _parse_compound(s: str) -> float | None:
    s = s.strip().lower()
    if not s:
        return None
    total, matched = 0.0, False
    for table in (LAKH, THOUSAND, HUNDRED):
        m = re.search(rf"(?:{_NUM}\s*)?({_alt(table)})(?![\w])", s)
        if m:
            head = _val(m.group(1)) if m.group(1) else 1
            total += (head or 1) * table[m.group(2).lower()]
            s = s[:m.start()] + " " + s[m.end():]
            matched = True
    rest = re.findall(_NUM, s)
    if rest:
        v = _val(rest[-1])
        if v is not None:
            total += v
            matched = True
    return total if matched else None


_MULT_ALT = _alt({**LAKH, **THOUSAND, **HUNDRED})
_DIGITS_AMT = rf"\d[\d,]*(?:\.\d+)?(?:\s*(?:{_alt(LAKH)}|{_alt(THOUSAND)})(?![\w]))?"
_WORDS_AMT = rf"(?:(?<![\w])(?:{_UNIT_ALT})(?![\w])\s*(?:(?:{_MULT_ALT})(?![\w]))?\s*)+|(?<![\w])(?:{_MULT_ALT})(?![\w])"
_AMT_CORE = rf"(?:{_DIGITS_AMT}|{_WORDS_AMT})"
_AMOUNT_RE = re.compile(
    rf"(?:₹|rs\.?|inr|रु\.?)\s*({_AMT_CORE})"
    rf"|(?<![\w₹])({_AMT_CORE})\s*(?:rupaye|rupaiye|rupees|rupee|rs\b|रुपये|रुपए|रुपया)",
    re.I)


def extract_amounts(text: str) -> list[tuple[int, int, int, str]]:
    s = _norm(text)
    out = []
    for m in _AMOUNT_RE.finditer(s):
        core = m.group(1) or m.group(2)
        v = _parse_compound(core)
        if v is None:
            continue
        out.append((int(round(v)), m.start(), m.end(), text[m.start():m.end()].strip()))
    return out


def extract_dates(text: str, year: int) -> list[tuple[str, int, int, str]]:
    s = _norm(text)
    out = []
    mon = _alt(MONTHS)
    for m in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", s):
        out.append((f"{m.group(1)}-{m.group(2)}-{m.group(3)}", m.start(), m.end(), m.group(0)))
    for m in re.finditer(rf"(?<!\d)(\d{{1,2}})\s*(?:st|nd|rd|th)?\s*(?:of\s+)?({mon})(?![\w])\.?(?:\s*(20\d{{2}}))?", s, re.I):
        try:
            d = date(int(m.group(3) or year), MONTHS[m.group(2).lower()], int(m.group(1)))
            out.append((d.isoformat(), m.start(), m.end(), text[m.start():m.end()]))
        except (ValueError, KeyError):
            pass
    for m in re.finditer(rf"(?<![\w])({mon})\.?\s+(\d{{1,2}})(?!\d)", s, re.I):
        try:
            d = date(year, MONTHS[m.group(1).lower()], int(m.group(2)))
            if not any(a <= m.start() < b for _, a, b, _ in out):
                out.append((d.isoformat(), m.start(), m.end(), text[m.start():m.end()]))
        except (ValueError, KeyError):
            pass
    for m in re.finditer(r"(?<![\d\w])(\d{1,2})[-.](\d{1,2})[-.](20\d{2}|\d{2})(?![\d])", s):
        try:
            y = int(m.group(3)); y = y + 2000 if y < 100 else y
            out.append((date(y, int(m.group(2)), int(m.group(1))).isoformat(), m.start(), m.end(), m.group(0)))
        except ValueError:
            pass
    for m in re.finditer(r"(?<![\d.\w-])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/])", s):
        try:
            y = int(m.group(3)) if m.group(3) else year
            y = y + 2000 if y < 100 else y
            out.append((date(y, int(m.group(2)), int(m.group(1))).isoformat(), m.start(), m.end(), m.group(0)))
        except ValueError:
            pass
    return out


def extract_durations(text: str) -> list[tuple[float, int, int, str]]:
    s = _norm(text).lower()
    unit_alt = _alt(UNIT_HOURS)
    out = []
    pat = rf"(?<![\w]){_NUM}(?:\s*(?:se|ya|or|to|-)\s*{_NUM}|\s+{_NUM})?\s*({unit_alt})(?![\w])"
    for m in re.finditer(pat, s, re.I):
        nums = [_val(g) for g in (m.group(1), m.group(2), m.group(3)) if g]
        nums = [n for n in nums if n is not None]
        if not nums:
            continue
        out.append((max(nums) * UNIT_HOURS[m.group(4).lower()], m.start(), m.end(), text[m.start():m.end()]))
    return out


def _bare_numbers(sent: str, taken: list[tuple[int, int]]) -> list[tuple[int, int, int, str]]:
    """Numbers with no currency marker that sit in a money sentence: '900 milenge'."""
    s = _norm(sent)
    out = []
    for m in re.finditer(r"(?<![\w₹\-/.])(\d[\d,]*)(?![\w\-/.%])", s):
        if any(a <= m.start() < b for a, b in taken):
            continue
        tail = s[m.end():m.end() + 14].lower()
        if re.match(r"\s*(?:" + _alt(UNIT_HOURS) + r"|" + _alt(MONTHS) + r"|baar|guna|x\b|%|st|nd|rd|th)", tail, re.I):
            continue
        v = int(m.group(1).replace(",", ""))
        if v >= 10:
            out.append((v, m.start(), m.end(), sent[m.start():m.end()]))
    return out


def _clause_bounds(low: str, pos: int) -> tuple[int, int]:
    a, b = 0, len(low)
    for m in re.finditer(CLAUSE_SPLIT, low, re.I):
        if m.end() <= pos:
            a = m.end()
        elif m.start() >= pos:
            b = m.start()
            break
    return a, b


def _negated(low: str, start: int, end: int, before: int = 30, after: int = 40) -> bool:
    """True if a negation sits in the same clause close to the claim: 'guarantee nahi de sakta',
    'abhi refund initiate nahi hua', 'koi pakki tareekh nahi'."""
    a, b = _clause_bounds(low, start)
    window = low[max(a, start - before):min(b, end + after)]
    return re.search(rf"(?<![\w]){NEGATION}(?![\w])", window, re.I) is not None


def _credential_violations(sent: str, sent_start: int) -> list[Violation]:
    """A credential mention is allowed only if a negation sits within its own clause, close to it."""
    out = []
    low = _norm(sent).lower()
    bounds = [0] + [m.end() for m in re.finditer(CLAUSE_SPLIT, low, re.I)] + [len(low)]
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        clause = low[a:b]
        for m in re.finditer(rf"(?<![\w]){CREDENTIAL}(?![\w])", clause, re.I):
            window = clause[max(0, m.start() - 40):m.end() + 45]
            if re.search(rf"(?<![\w]){NEGATION}(?![\w])", window, re.I):
                continue
            out.append(Violation("credential", sent[a + m.start():a + m.end()], sent_start + a + m.start(),
                                 sent_start + a + m.end(), "Asks for a secret credential; Tatkal never does this"))
    return out


def check(draft: str | None, facts: dict) -> Verdict:
    """facts keys:
       amounts: list[int]           rupee figures the case supports
       dates: list[str]             ISO dates the case supports (usually the RBI deadline)
       deadline_days: int | None    outer limit in days for money to move, if any
       acknowledged: dict           {"udir_complaint": bool, "udir_response": bool, "refund_initiated": bool}
       money_landed: bool           ledger shows merchant credited (merchant view) or payer reversed
       duplicate: bool              the case is a true duplicate debit
       names: list[str]             merchant and customer names (numbers inside them are not amounts)
       promise_dates: list[str]     dates that may be stated as commitments (the live RBI deadline only)
       human_assigned: bool         a human reviewer really has the case (only after escalation)
       either_outcome: bool         P2M pending: money ends with the shop OR back with the payer
       year: int
    """
    v: list[Violation] = []
    claims: dict = {"amounts": [], "dates": [], "durations_hours": [], "completions": []}
    if draft is None or not draft.strip() or len(draft.strip()) < 3:
        return Verdict(False, [Violation("empty", draft or "", 0, len(draft or ""), "Empty or unusable output")], claims)

    allowed_amounts = set(facts.get("amounts", []))
    allowed_dates = set(facts.get("dates", []))
    dl_days = facts.get("deadline_days")
    ack = facts.get("acknowledged", {})
    year = facts.get("year", date.today().year)

    amounts = extract_amounts(draft)
    dates = extract_dates(draft, year)
    for amt, a, b, raw in amounts:
        claims["amounts"].append(amt)
        if amt not in allowed_amounts:
            v.append(Violation("amount", raw, a, b, f"₹{amt:,} is not in the case file (allowed: {sorted(allowed_amounts)})"))
    promise_dates = set(facts.get("promise_dates", allowed_dates))
    for iso, a, b, raw in dates:
        claims["dates"].append(iso)
        sent = next((x for x in _sentences(draft) if x[0] <= a < x[0] + len(x[1])), (0, draft))[1]
        low_s = _norm(sent).lower()
        committing = re.search(COMMIT_VERBS, low_s, re.I) is not None or re.search(MONEY_VERBS, low_s, re.I) is not None
        if iso not in allowed_dates:
            v.append(Violation("date", raw, a, b, f"{iso} is not a date the case file supports"))
        elif committing and iso not in promise_dates and not (
                iso in facts.get("settled_dates", []) and facts.get("money_landed")
                and re.search(r"(chuka|chuki|ho gaya|aa gaya|mil gaya|pahunch gaya|received|credited|reversed|"
                              r"चुका|चुकी|हो गया|आ गया)", low_s)):
            # a past settlement the ledger confirms ("31 Oct ko wapas pahunch chuka hai") is a fact, not a promise
            v.append(Violation("date", raw, a, b, f"{iso} is a real date in the case, but nothing is promised for it"))

    for s0, sent in _sentences(draft):
        low = _norm(sent).lower()
        money_ctx = re.search(MONEY_VERBS, low, re.I) is not None
        commit_ctx = money_ctx or re.search(COMMIT_VERBS, low, re.I) is not None
        money_any = money_ctx or re.search(MONEY_NOUNS, low, re.I) is not None
        limit_ctx = re.search(LIMIT_MARKERS, low, re.I) is not None
        durs = extract_durations(sent)
        taken = [(a - s0, b - s0) for _, a, b, _ in amounts + dates if s0 <= a < s0 + len(sent)]
        taken += [(a, b) for _, a, b, _ in durs]
        taken += [(m.start(), m.end()) for m in re.finditer(r"[a-z]+-\d[\w-]*", low)]   # references like UDIR-870295-U022
        for name in facts.get("names", []):                                          # "Chai Point Sector 18"
            for m in re.finditer(re.escape(name.lower()), low):
                taken.append((m.start(), m.end()))

        for hrs, a, b, raw in durs:
            claims["durations_hours"].append(hrs)
            if not commit_ctx:
                continue
            if dl_days is None:
                why = "No rule gives a time limit for this case; do not promise one"
            elif hrs < dl_days * 24 - 0.01:
                why = f"Promises {hrs:g} hours; the rule only guarantees an outer limit of {dl_days} day(s)"
            elif abs(hrs - dl_days * 24) < 0.01:
                why = None if limit_ctx else f"States {dl_days} day(s) as a promise; say it is the outer limit (tak / ke andar)"
            else:
                why = f"States {hrs:g} hours; the rule's limit is {dl_days} day(s)"
            if why:
                v.append(Violation("duration", raw, s0 + a, s0 + b, why))

        if money_ctx:
            for word, hrs in RELATIVE_HOURS.items():
                for m in re.finditer(rf"(?<![\w]){re.escape(word)}(?![\w])", low):
                    if any(a <= m.start() < b for a, b in taken):
                        continue
                    if _negated(low, m.start(), m.end(), before=0):
                        continue      # "abhi nahi aaya", "abhi refund initiate nahi hua"
                    if dl_days is None or hrs < dl_days * 24:
                        v.append(Violation("duration", sent[m.start():m.end()], s0 + m.start(), s0 + m.end(),
                                           f"'{word}' promises money sooner than any rule guarantees"))
            for amt, a, b, raw in _bare_numbers(sent, taken):
                claims["amounts"].append(amt)
                if amt not in allowed_amounts:
                    v.append(Violation("amount", raw, s0 + a, s0 + b,
                                       f"{amt} next to a money promise is not in the case file"))

        if money_any:
            g = re.search(GUARANTEE, low, re.I)
            if g and not _negated(low, g.start(), g.end()):
                v.append(Violation("guarantee", sent[g.start():g.end()], s0 + g.start(), s0 + g.end(),
                                   "Guarantees about money outcomes are never allowed"))
            mm = re.search(MULTIPLIER, low, re.I)
            if mm:
                true_dup = facts.get("duplicate") and re.search(r"(kat|kata|kate|cut|debit|deduct|कट)", low)
                if not true_dup:
                    v.append(Violation("multiplier", sent[mm.start():mm.end()], s0 + mm.start(), s0 + mm.end(),
                                       "Promises a multiple of the amount; the case file supports only the amount itself"))

        for key, pat in COMPLETION.items():
            for m in re.finditer(pat, low, re.I):
                if _negated(low, m.start(), m.end(), before=0, after=25):
                    continue      # "refund initiate nahi hua"
                claims["completions"].append(key)
                if not ack.get(key):
                    v.append(Violation("completion", sent[m.start():m.end()], s0 + m.start(), s0 + m.end(),
                                       f"Claims '{key}' is done but the rail has not acknowledged it"))

        for lm in re.finditer(LANDED, low, re.I):
            seg = low[lm.start():lm.end() + 6]
            if re.search(rf"(?<![\w]){NOT_NEAR}", seg):
                continue
            if not facts.get("money_landed"):
                v.append(Violation("landed", sent[lm.start():lm.end()], s0 + lm.start(), s0 + lm.end(),
                                   "Says the money arrived but the ledger does not show it"))

        # a limit with nothing to limit: "zyada se zyada tak aa jayega", "zyada se zyada din ke andar"
        for lm2 in re.finditer(r"(zyada\s*se\s*zyada|zyaada\s*se\s*zyaada|maximum|ज़्यादा\s*से\s*ज़्यादा)", low, re.I):
            tail = low[lm2.end():lm2.end() + 22]
            if re.match(r"\s*(tak|din|update)\b", tail) and not re.search(r"\d", low[max(0, lm2.start() - 25):lm2.end() + 22]):
                v.append(Violation("vague", sent[lm2.start():lm2.end()], s0 + lm2.start(), s0 + lm2.end(),
                                   "A limit with no date or number: reads as a promise without content"))
        hm = re.search(HUMAN_CLAIM, low, re.I)
        if hm and not facts.get("human_assigned") and not _negated(low, hm.start(), hm.end()):
            v.append(Violation("human", sent[hm.start():hm.end()], s0 + hm.start(), s0 + hm.end(),
                               "Says a person is working on the case, but no human has been assigned"))

        v.extend(_credential_violations(sent, s0))

    seen, uniq = set(), []
    for x in v:
        k = (x.kind, x.start, x.end)
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return Verdict(ok=not uniq, violations=uniq, claims=claims)
