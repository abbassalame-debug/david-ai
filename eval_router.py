# eval_router.py — Gate 1 evaluation harness
# Run: python eval_router.py
# Routing pass threshold : 35/39 (90%)
# DLP pass threshold     : 5/5  (100% — security, no partial credit)

import sys
sys.stdout.reconfigure(encoding="utf-8")

from david_router import layer0, layer1, dlp_flag

# ── Routing test cases ────────────────────────────────────────────────────────
# (input_text, "L0"|"L1", expected_route_or_agent, expected_domain_or_None)
TESTS = [
    # ── Layer 0 — instant dispatch, no LLM ───────────────────────────────────
    ("park this idea: voice-controlled mixer",                         "L0", "incubation_vault",  None),
    ("mode b",                                                          "L0", "mode_switch",       None),
    ("schedule a call with Ahmed on Monday at 2pm",                    "L0", "chief_of_staff",    None),
    ("open the ledger",                                                 "L0", "task_ledger",       None),
    ("remind me to back up at 9pm",                                    "L0", "chief_of_staff",    None),
    # Arabic content after park trigger — L0 regex must still fire
    ("park this idea: استخدام RAG في المشاريع العربية",               "L0", "incubation_vault",  None),
    # Image generation trigger
    ("generate an image of a sunset over Lisbon",                     "L0", "generate_image",    None),

    # ── Layer 1 — technical ──────────────────────────────────────────────────
    ("write a Python script to rename all files in a folder",          "L1", "developer",          "technical"),
    ("fix this JavaScript function that keeps returning undefined",     "L1", "developer",          "technical"),
    ("explain what a binary search tree is",                           "L1", "developer",           "technical"),
    # Code-switched Gulf Arabic
    ("كيف أعمل API endpoint بـ FastAPI؟",                              "L1", "developer",           "technical"),

    # ── Layer 1 — general ────────────────────────────────────────────────────
    ("What is the capital of Japan?",                                  "L1", "analyst",             "general"),
    ("Who invented the telephone?",                                    "L1", "analyst",             "general"),

    # ── Layer 1 — music (English) ────────────────────────────────────────────
    ("What reverb settings work best for Arabic vocals?",              "L1", "beat_architect",      "music"),
    ("How do I set up sidechain compression in Ableton?",              "L1", "beat_architect",      "music"),
    ("Should I record vocals at 44.1 or 48 kHz?",                     "L1", "beat_architect",      "music"),

    # ── Layer 1 — music (Arabic / code-switched) ─────────────────────────────
    ("بدي أعمل beat بـ 90 BPM وأحتاج reverb مناسب للصوت العربي",    "L1", "beat_architect",      "music"),
    ("وش رأيك في maqam bayati على electric guitar؟",                  "L1", "beat_architect",      "music"),

    # ── Layer 1 — business (English) ─────────────────────────────────────────
    ("How should I price my design services for clients?",             "L1", "executive_advisor",   "business"),
    ("What's the best legal structure for a small media company?",     "L1", "legal_counsel",        "business"),

    # ── Layer 1 — business (Arabic) ──────────────────────────────────────────
    ("كيف أسعر خدماتي التصميمية للعملاء؟",                           "L1", "executive_advisor",   "business"),
    ("ما هي أفضل استراتيجية للتوسع في العقارات العراقية؟",           "L1", "venture_architect",    "business"),

    # ── Layer 1 — lore ───────────────────────────────────────────────────────
    ("Help me name a city in a post-apocalyptic coastal setting",     "L1", "lore_keeper",         "lore"),
    ("What would the political structure of a desert civilization look like?", "L1", "lore_keeper", "lore"),

    # ── Layer 1 — research ─────────────────────────────────────────────────
    ("Analyze the recurring structural patterns across this corpus of primary-source documents",           "L1", "linguist",            "research"),

    # ── Layer 1 — personal (English) ─────────────────────────────────────────
    ("I've been sleeping badly for a week, what should I change?",     "L1", "performance_coach",   "personal"),
    ("How do I build a morning routine that actually sticks?",         "L1", "performance_coach",   "personal"),

    # ── Layer 1 — personal (Gulf Arabic) ─────────────────────────────────
    ("كيف بناء morning routine يثبت معي وما بقدر أتركه؟",            "L1", "performance_coach",   "personal"),

    # ── Layer 1 — previously unreachable agents (Phase 3 routing fix) ────────
    ("Review this Figma mockup: the CTA is too small and the hierarchy is broken", "L1", "uiux_engineer",    "technical"),
    ("Write a system prompt for a legal document summariser LLM",                  "L1", "prompt_architect",  "technical"),
    ("What bass-trap placement will fix my 80Hz room mode?",                       "L1", "acoustician",       "music"),
    ("What is my projected rental income for Q3?",                            "L1", "cfo",               "business"),
    ("Build a 12-month runway model for the Lisbon co-working space",              "L1", "venture_architect", "business"),
    ("Review this NDA clause — does it restrict sublicensing?",                    "L1", "legal_counsel",     "business"),
    ("Design a TikTok content strategy for the the operator brand",               "L1", "brand_audience",    "business"),
    ("Define the visual identity tone for the real estate launch",            "L1", "creative_director", "business"),
    ("Translate this paragraph to Gulf Arabic and explain the register shifts","L1", "language_specialist","research"),
]

# ── DLP bypass probes — verify Phase 1 fixes ─────────────────────────────────
# (input_text, should_flag, description)
DLP_PROBES = [
    ("GB82 WEST 1234 5698 7654 32",          True,  "spaced IBAN"),
    ("هذه الوثيقة سري للغاية لا تشاركها",           True,  "Arabic top-secret label"),
    ("my رقم الحساب at the bank is important",       True,  "Arabic account-number phrase"),
    ("4532015112830366",                             True,  "valid Visa card (Luhn pass)"),
    ("what is my real estate cash flow today",  False, "clean business query — must NOT flag"),
]


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_routing():
    total  = len(TESTS)
    passed = 0
    failed = []

    print(f"Running routing eval — {total} tests...\n")
    for i, (text, layer, expected_agent, expected_domain) in enumerate(TESTS, 1):
        if layer == "L0":
            route, _ = layer0(text)          # FIX: unpack tuple (was: route = layer0(text))
            ok = route == expected_agent
            status = "✅" if ok else "❌"
            print(f"  {status} [{i:02d}] L0 | {str(route):25s} expected {expected_agent!r}")
        else:
            try:
                d = layer1(text, mode="A")   # FIX: pass mode arg (was: layer1(text))
                agent_ok  = d["agent"]  == expected_agent
                domain_ok = d["domain"] == expected_domain
                ok = agent_ok and domain_ok
                status = "✅" if ok else "❌"
                af = f" (agent={d['agent']!r})"   if not agent_ok  else ""
                df = f" (domain={d['domain']!r})" if not domain_ok else ""
                print(f"  {status} [{i:02d}] L1 | {d['agent']:25s} {d['domain']:12s} "
                      f"conf={d['confidence']:.2f}{af}{df}")
            except Exception as e:
                ok = False
                print(f"  ❌ [{i:02d}] L1 | ERROR: {e}")

        if ok:
            passed += 1
        else:
            failed.append(i)

    threshold = int(total * 0.90)
    verdict   = f"PASS ✅" if passed >= threshold else f"FAIL ❌"
    print(f"\n{'='*60}")
    print(f"  Routing score : {passed}/{total}  ({verdict} — threshold {threshold}/{total})")
    if failed:
        print(f"  Failed tests  : {failed}")
    print(f"{'='*60}\n")
    return passed >= threshold


def run_dlp():
    total  = len(DLP_PROBES)
    passed = 0
    failed = []

    print(f"Running DLP probes — {total} tests...\n")
    for i, (text, should_flag, desc) in enumerate(DLP_PROBES, 1):
        result = dlp_flag(text)
        ok     = result == should_flag
        status = "✅" if ok else "❌"
        flag_label = "FLAGGED" if result else "clean"
        print(f"  {status} [{i}] {flag_label:7s}  {desc}")
        if ok:
            passed += 1
        else:
            failed.append(i)

    verdict = "PASS ✅" if passed == total else "FAIL ❌"
    print(f"\n{'='*60}")
    print(f"  DLP score     : {passed}/{total}  ({verdict} — must be {total}/{total})")
    if failed:
        print(f"  Failed probes : {failed}")
    print(f"{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    routing_ok = run_routing()
    dlp_ok     = run_dlp()
    if routing_ok and dlp_ok:
        print("Gate 1: ALL SECTIONS PASS ✅\n")
    else:
        print("Gate 1: FAIL ❌ — see failures above\n")
        sys.exit(1)
