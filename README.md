# Tatkal v3.9 (judges' phones release)

UPI dispute teammate. Paytm Build for India AI Hackathon, Track 3 (Autonomous AI Teammates). Team Think Spice: Utkarsh Goyal, Kashvi Agarwal, Nakul Shah.

**In one line:** when a UPI payment at a shop gets stuck, Tatkal opens the case itself and applies the RBI and NPCI rules in code. It acts on the UDIR rails, asks the shopkeeper one clear question by voice and tells both sides the truth in Hindi. **The rules decide. Sarvam listens, reads and speaks. Nothing moves on one reader's word.**

Every payment rail here is a mock. No money moves and nothing connects to NPCI, a bank or Paytm.

---

## Start here: step by step

**Windows (PowerShell)**

```powershell
# 1. Get Python 3.11 or newer from python.org (tick "Add python.exe to PATH"), then:
cd C:\path\to\tatkal                      # the folder you unzipped
python -m venv .venv
.venv\Scripts\Activate.ps1                  # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
copy .env.example .env
notepad .env                                # SARVAM_API_KEY=your_new_key   (no quotes, no spaces)
python -c "import config,os; print('key loaded:', bool(os.getenv('SARVAM_API_KEY')))"
python -m pytest -q                         # expect 126 passed
python -m eval.run_eval                     # refreshes the Evaluation tab
uvicorn api.main:app --host 0.0.0.0 --port 8000
# open http://localhost:8000 on the laptop
```

**macOS or Linux**

```bash
cd ~/path/to/tatkal
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && open -e .env        # or: nano .env
python -c "import config,os; print('key loaded:', bool(os.getenv('SARVAM_API_KEY')))"
python -m pytest -q
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

**Judges' phones (optional, needs internet):** in a second terminal run `cloudflared tunnel --url http://localhost:8000` (install: `winget install --id Cloudflare.cloudflared` or `brew install cloudflared`), copy the `https://….trycloudflare.com` address, open 📱 Judges on the console and paste it under the QR code. Phones only reach `/join`, `/live/*` and `/voice`; every operator control is refused for anything that comes through the tunnel or from another device. To drive the console from a second device, set `TATKAL_ADMIN_TOKEN=some_secret` in `.env` and open `http://<laptop>:8000/?admin=some_secret` there.

**If something goes wrong:** the status chip under Controls says "No second reader" → the key did not load (check `.env` is in the `tatkal` folder, restart). Port busy → use `--port 8001`. No internet → add `TATKAL_SINGLE_KEY_DEMO=1` to `.env` (red badge, say it is a demo override).

## Run it (short form)

```bash
pip install -r requirements.txt && cp .env.example .env    # add your key
python -m pytest -q                                          # 126 tests, never call Sarvam
uvicorn api.main:app --host 0.0.0.0 --port 8000             # open http://localhost:8000
```

`.env` is git ignored. The key is read from the environment and never printed or logged.

| Setting | What it does |
|---|---|
| `SARVAM_API_KEY` | Turns on the second reader (Sarvam 105B), voice (Saaras v4, Bulbul v4) and the experimental rephrasing mode |
| no key | Templates still talk, but **every yes from a shopkeeper goes to a person**, because only one reader saw it |
| `TATKAL_SINGLE_KEY_DEMO=1` | Lets the rule reader act alone, for a demo with no key. The console shows a red "SINGLE KEY DEMO: not production" badge and every action is logged as an override |
| `TATKAL_AUDIT_KEY` | Signs the audit anchor. If unset, a key is created once in `data/.audit_key` (git ignored) so anchors stay verifiable across restarts |
| `TATKAL_DB`, `TATKAL_DEMO_DATE`, `TATKAL_ADMIN_TOKEN`, `TATKAL_OFFLINE=1` | Audit DB path, demo date (default 31 Oct 2026), admin token for demo switches, no Sarvam calls at all |

Live evaluation (needs a key, costs credits): `python -m eval.run_eval --live --k 3`, or add `--skip-f` to rerun only the two key and voice blocks.

---

## Stage features (v3.3)

### 📱 Aap dukaandar hain: the judges play the shopkeeper

Press **📱 Judges** in the console. A QR code appears. A judge scans it and their own phone becomes the Soundbox (or the customer's app) for whatever case is on the big screen. Tatkal speaks the question out loud from their phone; they answer in their own words, by voice or by tapping. Their reply goes through exactly the same path as the console: rule reader and Sarvam 105B, the seven check action gate, the signed warrant and the mock NPCI rail. The big screen updates live and keeps score: replies from judges, replies that reached NPCI, replies held back. Try "Haan de diya, lekin usne kuch nahi liya" or a prompt injection.

**Venue setup (5 minutes before you present):**

1. Start the server so phones can reach it: `uvicorn api.main:app --host 0.0.0.0 --port 8000`.
2. Phones need HTTPS to use the microphone (browsers only allow `getUserMedia` on HTTPS or localhost). For voice, open a second terminal and run `cloudflared tunnel --url http://localhost:8000` (install: `winget install --id Cloudflare.cloudflared` on Windows, `brew install cloudflared` on Mac). Copy the `https://….trycloudflare.com` address it prints, paste it into the box under the QR code and press Use. Quick tunnels need no account.
3. No tunnel, or venue Wi-Fi that blocks phones from seeing your laptop? Turn on your phone's hotspot, connect the laptop to it, and let judges join the same hotspot: the QR then uses your laptop's local address. Judges can tap the replies or use their keyboard's mic to dictate.
4. Test once with your own phone before you go on stage.

### 🪔 Diwali rush: one lakh stuck payments in fifteen seconds

Press **🪔 Rush**, then **Run 1 lakh cases**. 1,00,000 synthetic stuck payments go through the same rule engine and the same guard the console uses, and a wall of diyas lights up by outcome, with live counters: complaints raised, shopkeepers told to hold goods, shopkeepers asked one question, cases handed to a person, RBI deadline breaches and compensation flagged, and the number of messages that failed the guard (0 of 1,88,023). The context line uses a real number: on Dhanteras, 18 October 2025 UPI carried 75.4 crore payments worth ₹1.02 lakh crore in a day (NPCI data reported by Business Today, 22 Oct 2025). The transaction mix is our assumption and says so on screen.

Scope: the rush exercises the rule engine, the templates and the guard (1,88,023 message instances, 4,765 distinct message and fact combinations); it is not 1,00,000 full PSP, UDIR or voice sessions. The first rush found two real template bugs that 96 unit tests had missed; both are fixed with regression tests (eval block R).

The ▶ Play demo autoplay now ends with the Diwali rush.

Photo on the rush screen and phone page: "Diya set in a rangoli" by siddarth varanasi, CC BY 2.0, via Wikimedia Commons (`web/assets/CREDITS.txt`).

## How it decides

```
Customer app / Soundbox (text or voice)
   |  Saaras v4 STT, codemix, UPI keyterms
   v
Rule engine            RBI/2019-20/67 TAT and UDIR codes. Pure code with dates.
   |
State machine          OPEN > RULED > AWAITING_ACK > COMPLAINT_RAISED > MERCHANT_ASKED > RESOLVED or ESCALATED
   |
Two readers            rule reader AND Sarvam 105B read the shopkeeper's reply. A yes with a "but" or a "not" is unclear.
   |                   Both read the SAME transcript: independent classifiers, not independent evidence.
Read back (voice)      a spoken yes is repeated back; nothing is signed until the shopkeeper says "haan, confirm"
   |
Action gate            authorize_merchant_response(): complaint acknowledged, payee ledger shows the credit,
   |  (last gate)        case not with a person, code allows a reply, answer matches the question,
   |                     two_key_verified=True (no default)
   v
Warrant                Ed25519 signed by the policy gate (private key stays there), single use, bound to
   |                   complaint + code + transaction + amount + both reader labels, 120 s expiry
Rail adapter           holds only the PUBLIC key: can verify, cannot mint. Re checks on its own that both readers
   |                   agree with the code, the acknowledgement and the ledger credit at commit, and allows ONE
   |                   terminal reply per complaint (105 then 106, or 106 then 105, is refused, even after a lost ack)
   v
Words                  engine templates by default; every sentence checked by the grounding guard before it is spoken
Audit                  SHA256 hash chain in SQLite; signed checkpoints next to the DB and, if TATKAL_ANCHOR_DIR is set,
                       a second copy elsewhere; every checkpoint re verified on /health. Tamper evident, not tamper proof.
```

Why the warrant: an instruction such as "only act when both readers agree" lives in the agent and disappears with one bug. GateArena (Jiang, Quinzan, Bhatt, NeurIPS 2026 workshop "Agents in the Wild", [neurips.cc/virtual/2026/168290](https://neurips.cc/virtual/2026/168290), [OpenReview](https://openreview.net/forum?id=2fckzsPCbK)) moved the same check from the instruction into the tool handler and took protocol violating executions from 91/400 to 0/400. Santos Grueiro (arXiv 2607.10487, Jul 2026) found that 207 of 216 agent runs committed an effect after their authorisation had gone stale, so authority must be checked again at the moment of commit. Tatkal does both.

Eight demo cases in `rails/mock.py`: S1 deemed ₹450 (Paytm customer), S2 missed Soundbox ₹320, S3 failed but debited ₹850, S4 incoming U022 from PhonePe where the ledger shows the shop WAS paid (₹450), S8 incoming U022 from Google Pay where the shop was NOT paid (₹700), S5 duplicate ₹199, S6 ₹30,000 goods dispute and S7 ₹600 past its T+5 deadline.

---

## How to use the app (the console at http://localhost:8000)

Press **?** in the top bar at any time for the same guide inside the app.

1. **Pick a case.** The eight chips across the top are S1 to S8 (keys 1 to 8). Each one is a real stuck payment shape: S1 deemed U022, S2 Soundbox missed, S3 failed but debited, S4 shop paid (the 105 flow), S5 charged twice (106), S6 ₹30,000 goes to a person, S7 deadline missed with ₹100 a day, S8 shop never credited.
2. **Read the pipeline strip.** Seven steps light up left to right: ledger read, rule engine, NPCI UDIR complaint, question to the shopkeeper, two readers, gate plus signed warrant, outcome. A greyed step says why it was skipped (S8: "nothing to send: no credit").
3. **Customer phone (left).** What the payer sees in their UPI app. Type a reply in the box under it.
4. **Centre card.** The decision: path, NPCI code, RBI row, the T to T+7 calendar with the deadline ringed, who acts, money at risk, compensation and whether goods may be handed over.
5. **Shopkeeper Soundbox (right).** Tatkal asks one question. Answer with the quick reply buttons, type, or press the mic and speak Hindi or Hinglish. A spoken yes gets read back first ("Maine suna…"); tap **Haan, confirm** before anything reaches NPCI.
6. Every new message is spoken automatically with Sarvam Bulbul (A toggles it). **▶ play** on any bubble speaks it again. While Tatkal speaks, a bar at the bottom offers **⏸ Pause / ▶ Resume** and **■ Stop** (keys K and Esc); pausing the voice also holds the guided demo. On the judges' phones, the **▶ suniye** button becomes **⏸ roko** while speaking.
7. **Lower tabs.** Audit chain (try Tamper and watch verification fail), the evaluation results and the reply sent to NPCI.
8. **📱 Judges (J).** Shows a QR. A judge scans it, gets the shopkeeper screen on their phone and answers the live case by voice or buttons. Needs the Cloudflare tunnel for the mic (see above).
9. **🪔 Rush (R).** Runs 1 lakh synthetic stuck payments through the real engine and guard in about 15 seconds.
10. **▶ Play demo (P).** The two minute guided run with captions. **Controls** holds the reply mode, the "rail drops acknowledgements" fault and Reset demo. Esc closes any panel.

## What changed in v3.9 (judges' phones, 31 Oct)

Simulated end to end with a big screen and two phones (customer and shopkeeper), on plain Wi‑Fi and on a secure link, including a real spoken answer through Sarvam Saaras. Tests: 142 passing.

| Gap found | Fix |
|---|---|
| Choosing Grahak still showed a Soundbox | The customer now gets a UPI app screen: payment card (amount, shop, date, status, complaint and NPCI acknowledgement) and the full chat with Tatkal, own messages included |
| No voice option on the phone | The mic is always there. On plain Wi‑Fi (where browsers block the microphone) tapping it explains why, offers the secure link if one is running, and focuses the box for keyboard dictation. On https it records with a timer (up to 12 s), shows what Saaras heard, then sends it as voice |
| Getting an https link was manual | **🔒 Start secure link** in the Judges panel starts a Cloudflare quick tunnel and switches the QR to it; phones still on the old link see an "Open the secure link" button. `TATKAL_TUNNEL=1` starts it with the server. Needs the free `cloudflared` (Windows: `winget install --id Cloudflare.cloudflared`, Mac: `brew install cloudflared`) |
| Shopkeeper buttons ignored the question | Quick replies now follow the question: goods replies for "saamaan diya?", refund replies for the 106 question, only **Haan, confirm / Nahi** during a read back, none when nothing is asked (with a line saying why) |
| Customer results said only "answered from the rules" | Each attempt shows Tatkal's actual reply and whether the guard blocked a draft or a person took over |
| Other | Switch role without rejoining, automatic rejoin after a reload or server restart, no flicker on refresh, iPhone recordings sent with the right file type |

## What changed in v3.8 (hardening, 31 Oct)

Tests: 139 passing (128 before; 11 added in `tests/test_v38_hardening.py`, including one that syntax checks the console JavaScript; one assertion in `tests/test_api.py` now expects 400 for arbitrary TTS text instead of 200 or 503).

| Area | Change |
|---|---|
| `/voice/tts` | Speaks only text Tatkal itself sent in an open case (400 otherwise; every line on screen qualifies), up to 20 sentences per message, and a 600 per minute per phone abuse brake that a demo never reaches |
| Voice, full speed | Prefetch is ON by default: every new Tatkal message is voiced the moment it appears (set `TATKAL_TTS_PREFETCH=0` to turn off; message text, including names and amounts, goes to Sarvam). At startup Tatkal also replays every scenario and quick reply in a private sandbox (`api/warm.py`, own mock rails and in memory audit, the real chain is untouched) and pre voices all 40 lines it can say. Auto speak is on by default and the guided demo waits for Bulbul to finish each line. Measured on the full autoplay run: first audio 7 to 38 ms after each message, down from 2 to 3 s |
| Memory | The prefetch record is bounded at 400 lines (it grew without limit before) |
| Concurrency | State changing calls on one case (open, recheck, customer reply, shopkeeper reply, draft check) run under a per case lock, so two judges' phones cannot interleave on the same case |
| Demo date | `TATKAL_TODAY` (or the older `TATKAL_DEMO_DATE`) sets the RBI clock date; default 2026-10-31 so the scenarios still work |
| Theme | Text wordmark only ("Tatkal, for the Paytm ecosystem"), no logo files. Contrast tuned to WCAG AA, visible focus rings, a red "SINGLE KEY DEMO: not production" badge in the top bar whenever that override is on, and a "Demo · mock rails · no money moves" tag on the pipeline |
| Voice control | Pause, resume and stop while Tatkal speaks (bottom bar, K, Esc); a paused voice holds the guided demo; phones get ⏸ roko |
| Offline | Inter and JetBrains Mono are self hosted under `web/assets` (SIL OFL 1.1, licences included), so nothing loads from the internet |
| Photo | The Diwali photo appears only on the Rush screen (tinted navy, credit kept); it is gone from the console chrome and the phone page |

Unchanged on purpose: `engine/`, `agent/nlu.py`, `agent/llm.py` and `eval/`, so the published evaluation numbers still hold.

## What changed in v3.7 (design, 31 Oct)

| Area | Change |
|---|---|
| Palette | Light stage in Paytm's brand colours: navy #002E6E and cyan #00B9F1 on white cards. The pipeline is a navy strip so the "brain" reads at a glance; the Soundbox and the Diwali rush stay dark on purpose |
| Shopkeeper side | The Soundbox now sits on a shop counter next to a UPI "Scan and Pay" standee that shows the live shop name |
| Customer side | Navy app header, cyan actions, navy sent bubbles |
| Judges' phone page | Same palette, cyan mic, readable hero |
| Logo slot | Added in v3.7, removed in v3.8: the console uses a text wordmark only and no Paytm logo or product photo |

## What changed in v3.6 (stage polish, 31 Oct)

| Issue you saw | Fix |
|---|---|
| S8 sat between S4 and S5 | Cases now run S1 to S8 in order, with short tags that fit at 1280 to 1920 px |
| Controls menu hidden behind the pipeline | Top bar gets its own stacking layer; the menu now sits above everything |
| Speak took about 10 s | Bulbul now returns MP3 sentence by sentence, the server starts voicing every new message the moment it appears and caches it, and the browser plays part 1 while part 2 is still coming. Measured: a message that was prefetched plays in under 10 ms; a cold first sentence takes about 2 to 3 s |
| No feedback while waiting | Your reply appears instantly with a typing indicator; the readers step shows "reading with both readers…"; a "Preparing Bulbul voice" toast appears if audio takes over 0.7 s |
| No way to learn the app | In app guide (?) and keyboard shortcuts: 1 to 8, P, J, R, A, Esc |
| Chips overflowed at 1366 px | Compact chips and breakpoints; zero overflow at 1280, 1366 and 1536 |

## What changed in v3.5 (fifth review, 31 Oct)

| Finding | Fix | Evidence |
|---|---|---|
| P0: two concurrent requests could commit both 105 and 106 | The whole check, warrant spend and commit runs under one lock in the mock rail (a real adapter: one DB transaction with a unique constraint on complaint id) | threaded test, 5 rounds: exactly one commits. The same test without the lock commits both, so it really catches the bug |
| P0: an old warrant worked after Reset because complaint ids were reused | Complaint ids never repeat (counter survives reset); every warrant carries the rail's session generation; Reset changes the generation and rotates the policy signing key | test: mint, reset, reopen, old warrant refused; service test for key rotation |
| Operator routes open to anyone who could reach the server | Open case, console replies, recheck, draft, focus, rush, tamper, anchor, reset, mode and rail faults now need the operator: either `TATKAL_ADMIN_TOKEN`, or, with no token, a request made on the laptop itself. Anything through a Cloudflare tunnel, a proxy or from another device is refused | test: 8 routes refused through a tunnel header; join, state and reply still work |
| QR test failed without segno | Test accepts the documented fallback (join link) when segno is missing; segno stays in requirements | |
| Judge summary wording | 18 Oct 2025 is Dhanteras; Soundbox at 1.64 crore storefronts (Q2 FY27); UDIR already exists, Tatkal adds the payee side conversation; LLM claim limited to the default mode; Moffatt described accurately; false block rates, voice miss and rush scope added; UPI Help question added | Judge summary |

## What changed in v3.4 (fourth review, 30 Oct: safety release)

| Finding | Fix | Evidence |
|---|---|---|
| P0: the same complaint could receive both 105 and 106 | Responses keyed by complaint id; a different code after a committed one is refused (also when the first ack was lost); same code retry returns the committed result | 4 tests (both orders × ack kept or lost), E |
| P0: any caller could mint a valid warrant through `rails.warrants` | Signer and verifier split: the policy gate holds an Ed25519 private key, the rail holds only the public key and re checks reader agreement itself; the policy refuses to sign on disagreement | tests: rogue key, correctly signed but mismatched readers, rail has no mint method; E |
| P1: both readers read the same transcript | Called what it is (independent classifiers, not independent evidence). A spoken yes is read back and needs "haan, confirm"; anything else goes to a person. Taps and typed replies count as a physical confirmation | 6 tests, E |
| P1: anchors share the host's trust boundary | Checkpoints stored next to their own DB, optional second copy in `TATKAL_ANCHOR_DIR`, every checkpoint re verified on `/health` and `/audit/anchors/verify`; described as tamper evident | test: rewritten chain is caught |
| P1: U021 listed but not implemented | New RETURN_CREDIT path: U021, always a person (needs evidence of the return; payee may answer 106 with consent or "unable to update", which moves to chargeback after T+3) | test |
| P2: 2 MB cap is not a duration check | Real duration via ffprobe/ffmpeg; clips over 30 s refused with the real length | test with a 35 s clip under 2 MB |
| P2: version and counts | FastAPI version 3.4; E now 17 checks; dossier method text fixed | |

Scope, said plainly: signer and verifier still run in one Python process, so this is an in process policy control. In production the private key belongs in a separate policy service or HSM, and the rail adapter only ever receives the public key.

## What changed in v3.2 (third review, 30 Oct)

| Finding | Fix | Evidence |
|---|---|---|
| 105 was sent while the ledger said the shop was NOT credited (S4) | New sixth gate condition `ledger_credited`, read again at decision time; the rail also refuses 105/106 for an uncredited transaction | E 11/11, five new tests |
| An incoming U022 for a shop that WAS paid was treated as "Soundbox missed" and never answered | New path PAYEE_CREDITED: the payee side asks about goods and answers 105 or 106. Incoming U022 with no credit: no question, "hold the goods", banks reverse by T+5 | S4 and new S8 |
| Deck listed React and LangGraph | Deck v2 lists what the code uses: HTML console, a hand written state machine, pytest | deck slide 8 |
| Deck promised "40 scripted cases" | Deck v2 quotes what was run: 7 × 3 × 3 = 63 drafts, 30 of 62 with a real problem, guard caught 15 | deck slide 10 |
| Console not impressive enough | Rebuilt: live agent pipeline, device mockups, RBI clock, animated gate with a warrant ticket, chain feed, one click autoplay demo with captions | (screenshots not included in this zip) |

GateArena citation: authors and venue are from the NeurIPS 2026 virtual site (neurips.cc/virtual/2026/168290, workshop poster, Agents in the Wild).

## What changed in v3.1 (second external review, 30 Oct)

| Finding | Fix | Evidence |
|---|---|---|
| Two reader rule was optional: with no key, one reader could send 105 | `two_key_verified` is a required argument of the final gate. No second reader means a person confirms. Single key mode exists only behind a labelled switch | D3: 0/30 sent with one reader, 8/8 clear yeses handed to a person |
| "Haan de diya, but customer took nothing" and two English variants sent 105 | Any yes that also carries a negation or a contradiction word (but, lekin, magar, actually, asal mein) is unclear | D2: 0/6 sent (v3 sent 3/6). Not blind: these lines drove the fix |
| Nothing stopped a buggy caller from sending 105 | Single use warrant, checked by the rail adapter (made a true signer/verifier split in v3.4) | E (then 9 checks), including no warrant, wrong code and replay |
| 106 outcome said "main aapko bataunga" with no refund monitoring behind it | "Refund ek alag transaction se hoga. Abhi paisa wapas aane ki pushti nahi hui hai." | test_106_outcome_does_not_promise_monitoring |
| Voice block oversold | Renamed: scripted TTS to STT loop. Synthetic speech, one voice, no shop noise | H |
| Anchor key changed on restart | Persistent key in `.env` or `data/.audit_key`; every signed head appended to `data/anchors.jsonl` | test_api |
| 170 to 190 million declines a month estimate | Removed. The 0.7 to 0.8% figure was an NPCI comment from Nov 2024, not a Sep 2026 rate, and a technical decline is not a dispute | Q&A below |
| Unclear replies got a generic "samajh nahi aaya" | The re ask repeats what was heard, then asks again (targeted clarification, Stoyanchev, Liu, Hirschberg, SIGDIAL 2013) | test_unclear_reask_echoes_what_was_heard |
| UI too dense on a projector | Rebuilt console: phone and Soundbox devices, case progress, an action gate checklist with the warrant, a handoff ticket, five headline tiles with folded detail | (screenshots not included in this zip) |

The v3 table of earlier fixes (ack gate, refund consent, LLM failure fallback, credential negation, audit reset, archive instead of delete, RESOLVED to ESCALATED, customer outcome, P2P credited, held out guard sets) still stands; each has a test in `tests/test_review_regressions.py`.

---

## Results (mock rails, cases written by us: prototype numbers, not production accuracy)

| Block | What it measures | Result |
|---|---|---|
| A | Authored RBI/UDIR rule fixtures matched expected outcomes | **47/47** |
| D | 30 adversarial shopkeeper replies, rule reader | **0/30 unauthorised 105/106**, 30/30 as labelled |
| D2 | 6 contradictory replies (regression set, not blind) | **0/6 sent** |
| D3 | Default with no second reader | **0/30 sent**, 8/8 clear yeses to a person |
| E | Ack, ledger, state, warrant, terminal reply and read back gates | **17/17** |
| G | Live, 36 replies, rule reader AND Sarvam 105B (v3.4 run) | **0/36 unauthorised**, 36/36 as labelled (v3.2 run: 36/36; v3.1 run: 35/36, a safe re ask) |
| H | Live scripted TTS to STT loop, 6 lines (v3.4 run) | **0/6 unsafe**, 5/6 as labelled. The miss: Saaras heard "Haan, refund kar do" as "हा refund करतो", which both readers called unclear, so Tatkal re asked. A live example of why the two readers are not independent evidence |
| F audit | All 62 live Sarvam rewrites under pressure, read by hand (v3, 30 Oct) | **30/62 had a real problem; the guard caught 15/30**, 1 false block |
| F | Same run, judged by the guard itself | pass^k 11/21 OFF, 21/21 ON. Circular, so the audit above is the honest number |
| B, C1, C2, C3 | Guard on dev and three held out sets | 48/50 and 1/25 · 24/25 and 0/15 · 20/20 and 0/10 · **C3 blind: 15/15 and 2/10** |

CSVs: `eval/results/`. Manual audit: `F_live_run3_manual_audit.csv`, one row per draft with a reason.

### The finding we pitch

Asked to rephrase a correct message for a customer who is pushing ("kal tak pakka?"), a 105B model changed something that matters in about half its drafts: a date, a promise, which way the money goes. A rule guard caught half of those. So Tatkal does not let the LLM write payment facts and does not let any single reader trigger a payment action. Sarvam is used where it is strongest: hearing Hinglish, speaking Hindi and acting as an independent second key.

---

## 3 minute demo script

Press **▶ Play demo** for a captioned, hands free run of the whole story (needs a Sarvam key, or `TATKAL_SINGLE_KEY_DEMO=1`, for the clear yes in step 5 to go through). Or drive it by hand:

1. **S4, ₹450 from PhonePe.** The pipeline reads the ledger: the shop WAS credited, so the Soundbox asks one question.
2. **Contradiction.** Tap "Haan… lekin". Both readers say unclear; the Soundbox repeats what it heard.
3. **Clear yes.** Say "Haan, saamaan de diya" into the mic. Seven checks go green, a single use warrant is minted, 105 is acknowledged by NPCI, Ravi is told.
4. **Refund trap.** Reopen S4: "Nahi diya", then "Refund already kar diya". Not consent. No 106. Handoff packet.
5. **S8, ₹700, shop never credited.** Tatkal does not ask about goods. Hold them; the banks reverse by 5 Nov. The gate shows the red ledger check.
6. **S7, late.** The RBI clock turns red: 2 days late, ₹200 owed, straight to a person.
7. **Audit and evaluation.** Simulate an insider edit: every later block turns red on the copy, the real log stays valid. Close on the five tiles.

## Judge Q&A

- **Why not let the LLM decide?** RBI TAT and UDIR codes are rules with dates. Code gets them right every time. Our own audit shows a strong model rewriting under pressure changes a material fact in about half its drafts.
- **What if Sarvam is down?** Every yes goes to a person and nothing is sent. Slower, never wrong. That is D3.
- **What stops a bug in the agent from sending 105?** The rail adapter holds only the policy's public key. No warrant, a warrant from any other key, mismatched readers, wrong code or amount, expired or reused: refused. And one terminal reply per complaint (E).
- **What does Sarvam do?** Saaras v4 STT in codemix mode, Bulbul v4 TTS for the Soundbox and the second key on every 105/106.
- **Why Paytm?** Soundbox is deployed at 1.64 crore storefronts (Paytm Q2 FY27). Those merchant relationships make the payee side conversation a natural extension; a live UDIR integration runs through the authorised PSP bank and acquiring partners.
- **Doesn't NPCI UPI Help already do this?** UPI Help supports the payer. Tatkal complements it on the payee side: it answers U022 from the shop's Soundbox, tells the shopkeeper whether to hand over goods and keeps both sides on the same facts.
- **How big is this?** UPI ran 24.07 billion transactions in September 2026, about 802 million a day (NPCI, via MediaNama). RBI Ombudsman complaints reached 13.34 lakh in 2024 to 25, up 13.55%. We do not claim a dispute count from decline rates: a technical decline usually never debits the customer, so it is not a dispute.
- **Production path?** Replace `rails/mock.py` with the bank's UDIR switch adapter (same five calls, plus warrant verification). Add PSP auth, PII redaction, persistent case state (today only the audit trail persists) and an RBI aligned retention policy.

## Limits

Mock rails only. The policy signer and the rail verifier run in one process (an in process control, not yet an independently secured boundary). The two readers share one speech to text transcript. The audit chain is tamper evident, not tamper proof, unless checkpoints are shipped off the host. Cases and corpora were written by our team. Case state and mock rails live in memory; only the audit log persists. The guard is rules, not semantic understanding. The manual audit was done by us, not independent raters. Live runs are small (62 drafts, 36 replies, 6 voice clips). Voice was tested with synthetic speech; real counter noise and many accents are untested.

## Sources

- RBI/2019-20/67, harmonisation of TAT for failed transactions, Annex rows 4a and 4b.
- NPCI UDIR circulars and OC 213; Dvara Research, "NPCI's UDIR" (upigrm.dvararesearch.com).
- RBI Integrated Ombudsman Scheme Annual Report 2024 to 25 (reported by The Tribune, 1 Dec 2025).
- NPCI UPI statistics, September 2026 (MediaNama, Oct 2026).
- Jiang, Quinzan, Bhatt. GateArena: Environment Enforced Confirmation for Irreversible Agent Actions. NeurIPS 2026 workshop.
- Santos Grueiro. Temporary Authority, Permanent Effects: Commit Time Authorization for LLM Agents. arXiv 2607.10487, 2026.
- Shi et al. Progent: Programmable Privilege Control for LLM Agents. arXiv 2504.11703.
- Stoyanchev, Liu, Hirschberg. Modelling Human Clarification Strategies. SIGDIAL 2013.
- Yao et al. tau-bench (2024); tau2-bench (2025). Moffatt v. Air Canada, 2024 BCCRT 149.
- docs.sarvam.ai: chat completions (sarvam-105b), speech to text (saaras:v4), text to speech (bulbul:v4).

**Security:** never commit `.env` or `data/.audit_key`. Rotate any key that was pasted into a chat.
