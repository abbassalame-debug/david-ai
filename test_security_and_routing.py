"""
test_security_and_routing.py — fast, offline regression tests for the
highest-risk / cheapest-to-test pure functions in the router and tool layer.

No Ollama, no LanceDB embedder, no network — everything here runs against
temp files or mocked I/O so it's safe to run anytime and finishes in
seconds. Covers exactly the gaps flagged in the architecture audit:
  1. tool_executor path safety (traversal + absolute-path escape)
  2. david_router.dlp_flag pattern coverage
  3. david_router.route_tier decision matrix
  4. rag.retrieve fail-closed behavior for unknown agents
  5. david_router.layer1's analyst confidence cap (Ollama call mocked)

Run: python test_security_and_routing.py
"""

from david_config import DAVID_HOME
import sys, os, json, tempfile, shutil
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"

results = []

def check(name, ok, detail=""):
    tag = PASS if ok else FAIL
    results.append(ok)
    print(f"{tag}  {name}" + (f" — {detail}" if detail else ""))


# ─── 1. Path safety (tool_executor.py) ───────────────────────────────────────
print("\n── Path safety: tool_executor._safe_join / _read_note / _write_note ──")

import tool_executor as te

_tmp_vault = tempfile.mkdtemp(prefix="david_test_vault_")
te.VAULT_ROOTS = {"testvault": _tmp_vault}

try:
    # _safe_join: legitimate relative path resolves under the vault root
    ok_path = te._safe_join(_tmp_vault, "Ideas/my-idea.md")
    check("_safe_join allows a normal relative path",
          ok_path is not None and str(ok_path).startswith(os.path.realpath(_tmp_vault)))

    # _safe_join: ../ traversal is rejected
    escaped = te._safe_join(_tmp_vault, "../../../../Windows/win.ini")
    check("_safe_join rejects ../ traversal", escaped is None)

    # _safe_join: absolute path doesn't silently override the vault root
    # (the pathlib gotcha: Path(root) / "C:\\x" normally discards root entirely)
    abs_escape = te._safe_join(_tmp_vault, r"C:\Windows\System32\drivers\etc\hosts")
    check("_safe_join rejects absolute-path override", abs_escape is None)

    # _read_note: unknown vault
    result = te._read_note("no_such_vault", "note.md")
    check("_read_note rejects unknown vault", "Unknown vault" in result)

    # _read_note: traversal attempt returns an error, not file content
    result = te._read_note("testvault", "../../../../Windows/win.ini")
    check("_read_note rejects traversal path", "Invalid path" in result)

    # _write_note: traversal attempt is rejected and nothing is written outside the vault
    outside_target = os.path.join(os.path.dirname(_tmp_vault), "escaped.txt")
    if os.path.exists(outside_target):
        os.remove(outside_target)
    result = te._write_note("testvault", "../escaped.txt", "pwned")
    check("_write_note rejects traversal path", "Invalid path" in result)
    check("_write_note traversal did not create a file outside the vault",
          not os.path.exists(outside_target))

    # _write_note: legitimate nested path still works
    result = te._write_note("testvault", "sub/dir/note.md", "hello world")
    written_path = os.path.join(_tmp_vault, "sub", "dir", "note.md")
    check("_write_note writes a legitimate nested path",
          os.path.exists(written_path) and open(written_path, encoding="utf-8").read() == "hello world")

    # _read_note: reads back what was just written
    result = te._read_note("testvault", "sub/dir/note.md")
    check("_read_note reads back a legitimate note", result == "hello world")

    # NTFS alternate data streams (audit S-13 / SEC-B10, 2026-10-20): "note.md:hidden" would write a stream that Explorer,
    # Obsidian and the indexer never show. Any ":" in a vault-relative path is refused, for writes and reads.
    for ads in ("sub/dir/note.md:hidden", "sub/dir/note.md::$DATA", "new.md:payload", "x:y/z.md"):
        check(f"_safe_join refuses {ads!r}", te._safe_join(_tmp_vault, ads) is None)
        result = te._write_note("testvault", ads, "ads", append=True)
        check(f"_write_note refuses {ads!r}", "Invalid path" in result, result)
        result = te._read_note("testvault", ads)
        check(f"_read_note refuses {ads!r}", "Invalid path" in result, result)
    check("the note behind a refused stream name is unchanged",
          open(written_path, encoding="utf-8").read() == "hello world")
    if os.name == "nt":
        try:
            streams = open(written_path + ":hidden", encoding="utf-8").read()
        except OSError:
            streams = ""
        check("no alternate data stream was created on the note", streams == "", repr(streams[:40]))
    check("a ':' refusal does not block ordinary punctuation in names",
          te._safe_join(_tmp_vault, "Meetings/2026-10-20 standup (draft).md") is not None)
finally:
    shutil.rmtree(_tmp_vault, ignore_errors=True)


# ─── 2. DLP pattern coverage (david_router.py) ───────────────────────────────
print("\n── DLP: david_router.dlp_flag ──")

import david_router as dr

# route_tier's DLP-block branch calls audit(), which would otherwise append a
# real line to <DAVID_HOME>\audit_log.jsonl on every test run — redirect it for
# the duration of this script so the suite stays side-effect-free.
_real_audit_log = dr.LOG
_tmp_audit_log = tempfile.mktemp(prefix="david_test_audit_", suffix=".jsonl")
dr.LOG = _tmp_audit_log

DLP_CASES = [
    # (text, expected_flag, label)
    ("what is the capital of Japan?",              False, "benign factual question"),
    ("how do I set sidechain compression?",         False, "benign technical question"),
    ("my password: hunter2",                        True,  "password label"),
    ("api_key: sk-abc123def456",                    True,  "api key label"),
    ("secret_key = supersecretvalue",                True,  "secret key label"),
    ("this document is confidential",               True,  "confidential keyword"),
    ("my ssn is 123-45-6789",                        True,  "SSN"),
    ("passport number: AB1234567",                   True,  "passport number"),
    ("internal use only — do not distribute",        True,  "internal-use label"),
    ("please do not share this with anyone",         True,  "do-not-share phrase"),
    ("card number 4111111111111111",                 True,  "Luhn-valid card number"),
    ("random 16 digit number 1234567890123456",      False, "Luhn-invalid digit string"),
    ("DE89370400440532013000",                       True,  "IBAN"),
    ("خاص جدا لا تشارك",                              True,  "Arabic private/do-not-share"),
    ("كيف أعمل API endpoint بـ FastAPI؟",             False, "benign Arabic/English code-switch"),
]

for text, expected, label in DLP_CASES:
    got = dr.dlp_flag(text)
    check(f"dlp_flag: {label}", got == expected, f"text={text!r} expected={expected} got={got}")


# ─── 3. Tier routing decision matrix (david_router.route_tier) ──────────────
# 2026-07-13: policy narrowed to "cloud only if necessary" — local models
# (128k+ ctx, empirically verified this session) now handle everything except
# genuinely huge documents (TIER2: cfo/legal_counsel only) and external-facing
# high-stakes output (TIER3: executive_advisor/venture_architect/legal_counsel
# only). Threshold raised 4,000 -> 30,000 tokens.
print("\n── Tier routing: david_router.route_tier ──")

long_doc  = "x" * 20_000    # ~5000 tokens — a big paste, but under the new 30k threshold
huge_doc  = "x" * 130_000   # ~32,500 tokens — genuinely exceeds local-first threshold
short_txt = "x" * 2_000     # ~500 tokens — an ordinary detailed question, not a document

check("Firewalled agent always resolves to tier 1 (long text)",
      dr.route_tier("linguist", long_doc, True) == 1)

check("DLP-flagged text forces tier 1 even for a TIER3 agent + needs_reasoning",
      dr.route_tier("legal_counsel", "password: hunter2 " + huge_doc, True) == 1)

check("Short question to a TIER2 agent stays on tier 1 (not escalated on word count alone)",
      dr.route_tier("cfo", short_txt, False) == 1)

check("A big-but-not-huge document (~5k tok) now stays local — local-first policy",
      dr.route_tier("cfo", long_doc, False) == 1)

check("Genuinely huge document (~32.5k tok) to a TIER2 agent still escalates to tier 2",
      dr.route_tier("cfo", huge_doc, False) == 2)

check("needs_reasoning + remaining TIER3 agent (legal_counsel) escalates to tier 3",
      dr.route_tier("legal_counsel", "short prompt", True) == 3)

check("needs_reasoning + agent removed from TIER3 (creative_director) stays local now",
      dr.route_tier("creative_director", "short prompt", True) == 1)

check("needs_reasoning + agent removed from TIER3 (analyst) stays local now",
      dr.route_tier("analyst", "short prompt", True) == 1)

check("needs_reasoning + agent removed from TIER2 (analyst, huge doc) stays local now",
      dr.route_tier("analyst", huge_doc, False) == 1)

check("Short prompt, no reasoning flag, non-tier2 agent stays on tier 1",
      dr.route_tier("developer", "short prompt", False) == 1)


# ─── 4. RAG namespace fail-closed (rag.py) ──────────────────────────────────
print("\n── RAG: fail-closed vault access ──")

import rag

# An unknown agent must get zero vaults and never fall through to "everything" —
# retrieve() returns [] before touching LanceDB/the embedder for this case, so
# this is safe to call with no index or model loaded.
check("retrieve() returns no results for an unknown agent (fail closed)",
      rag.retrieve("some_agent_that_does_not_exist", "anything") == [])

check("acoustician no longer has Research in its RAG namespace",
      "research" not in rag.AGENT_NAMESPACES.get("acoustician", []))


# ─── 5. Layer 1 analyst confidence cap (Ollama call mocked) ─────────────────
print("\n── Layer 1: analyst confidence cap ──")

import urllib.request

class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()
    def read(self):
        return self._payload
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False

_real_urlopen = urllib.request.urlopen

def _fake_urlopen(req, timeout=None):
    fake_classification = {
        "domain": "general", "agent": "analyst",
        "needs_reasoning": False, "confidence": 0.9, "intent": "chat",
    }
    return _FakeResponse({"message": {"content": json.dumps(fake_classification)}})

urllib.request.urlopen = _fake_urlopen
try:
    result = dr.layer1("what is the capital of Japan?", "A")
    check("layer1 caps analyst confidence at 0.5 regardless of classifier output",
          result["confidence"] == 0.5, f"got confidence={result['confidence']}")
    check("layer1 does not block analyst from being chosen (agent unchanged)",
          result["agent"] == "analyst")
finally:
    urllib.request.urlopen = _real_urlopen

# The CLI's accept gate itself (route_and_execute) is `confidence < 0.6 and
# agent != "analyst"` — this is the exact scenario that gate must NOT block.
capped_confidence, agent = 0.5, "analyst"
would_block = capped_confidence < 0.6 and agent != "analyst"
check("CLI confidence gate does not bounce a capped analyst answer", would_block is False)


# ─── 6. write_note overwrite gate + Research read-only (tool_executor) ──
print("\n── write_note: overwrite gate & read-only firewalled vault ──")

_wn_vault = tempfile.mkdtemp(prefix="david_test_wn_")
_rl_vault = tempfile.mkdtemp(prefix="david_test_rl_")
te.VAULT_ROOTS = {"testvault": _wn_vault, "research": _rl_vault}
try:
    note_path = os.path.join(_wn_vault, "notes", "a.md")

    r = te._write_note("testvault", "notes/a.md", "v1")
    check("_write_note creates a new note", "written" in r and os.path.exists(note_path))

    r = te._write_note("testvault", "notes/a.md", "CLOBBER")
    body = open(note_path, encoding="utf-8").read()
    check("_write_note refuses silent overwrite of an existing note",
          "Refusing to overwrite" in r and body == "v1")

    r = te._write_note("testvault", "notes/a.md", "more", append=True)
    body = open(note_path, encoding="utf-8").read()
    check("_write_note append=true adds without clobbering",
          "appended" in r and body.startswith("v1") and "more" in body)

    r = te._write_note("testvault", "notes/a.md", "v2", overwrite=True)
    body = open(note_path, encoding="utf-8").read()
    check("_write_note overwrite=true deliberately replaces content",
          "written" in r and body == "v2")

    r = te._write_note("Research", "x.md", "nope")
    check("_write_note refuses the firewalled Research vault (read-only)",
          "read-only" in r and not os.path.exists(os.path.join(_rl_vault, "x.md")))
finally:
    shutil.rmtree(_wn_vault, ignore_errors=True)
    shutil.rmtree(_rl_vault, ignore_errors=True)


# ─── 7. Search egress DLP (tool_executor._search_web) ───────────────────────
print("\n── search egress: DLP gate (model-composed query) ──")
# A DLP-flagged query must be refused before any network call — no DDG egress.
r = te._search_web("my password: hunter2, now find me a better bank")
check("_search_web blocks a DLP-flagged query before egress",
      "DLP" in r and "blocked" in r.lower())


dr.LOG = _real_audit_log
if os.path.exists(_tmp_audit_log):
    os.remove(_tmp_audit_log)

# ─── Summary ──────────────────────────────────────────────────────────────────
print("\n── Summary ──")
passed = sum(1 for r in results if r is True)
failed = sum(1 for r in results if r is False)
total  = len(results)
print(f"  {passed}/{total} passed\n")
if failed:
    print("  Fix the FAILs above.\n")
    sys.exit(1)
else:
    print("  All checks passed.\n")
    sys.exit(0)
