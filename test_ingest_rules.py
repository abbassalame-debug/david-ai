"""
test_ingest_rules.py -- offline tests for the sample-library indexing rules (library_rules.py)
and the turbo_ingest options that use them (--only-path, --limit-files, "mode": "docs_only").

No Ollama, no database, no GPU: real files in a temp folder, a recording stand-in for the table.

Run: py -3.11 test_ingest_rules.py
"""

from david_config import DAVID_DATA, DAVID_HOME
import sys, os, json, tempfile, shutil, inspect
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import numpy as np
import library_rules as lr


print("\n-- which files count as a manual or readme --")
yes = ["Manual.pdf", "EPM Manual.pdf", "guide.docx", "Quick Start.rtf", "README.txt", "readme.md", "Read Me.txt",
       "User_Guide.html", "Instructions.txt", "documentation.nfo", "HOW TO INSTALL.txt"]
no = ["kick_01.wav", "pad.ncw", "bank.nxs", "preset.fxp", "loop.mid", "cover.png", "lyrical_vocal_phrases.txt",
      "script.txt", "notes.txt", "Setup.exe", "library.nki", "song.mp3", "changelog.txt"]
check("manuals, guides and readmes are accepted", all(lr.is_library_doc(f) for f in yes), str([f for f in yes if not lr.is_library_doc(f)]))
check("audio, samples, presets, MIDI, images and installers are rejected", not any(lr.is_library_doc(f) for f in no), str([f for f in no if lr.is_library_doc(f)]))
check("every PDF/Word file is treated as documentation", lr.is_library_doc("anything.pdf") and lr.is_library_doc("x.docx"))
check("a plain .txt is only a manual if its NAME says so", not lr.is_library_doc("script.txt") and lr.is_library_doc("manual.txt"))
eula = ["EULA_Native Instruments French.rtf", "License Agreement.pdf", "Documentation/License Agreement/EULA English.rtf",
        "Documentation/Licence/terms.pdf", "Legal notice.docx", "End User Agreement.pdf"]
check("license agreements and EULAs are NOT manuals, by file name or by folder", not any(lr.is_library_doc(f) for f in eula), str([f for f in eula if lr.is_library_doc(f)]))
check("a manual stored next to a license folder is still accepted", lr.is_library_doc("Documentation/Cerberus Manual.pdf") and lr.is_library_doc("Kontakt/Lib/Documentation/Readme.pdf"))


print("\n-- scanning a library folder --")
tmp = tempfile.mkdtemp(prefix="libtest_")
root = os.path.join(tmp, "Big Library")
plain = os.path.join(tmp, "Plain Folder")
def touch(p, data=b"x" * 10):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
for i in range(30): touch(os.path.join(root, "Drums A", "Samples", f"kick_{i:02d}.wav"))
for i in range(12): touch(os.path.join(root, "Strings B", f"note_{i}.ncw"))
touch(os.path.join(root, "Strings B", "Documentation", "Strings Manual.pdf"))
touch(os.path.join(root, "Drums A", "README.txt"), b"readme text")
touch(os.path.join(root, "Drums A", "scripts", "ui_script.txt"))
touch(os.path.join(root, "top_level_loop.wav"))
touch(os.path.join(root, ".hidden", "Manual.pdf"))
touch(os.path.join(root, "Crm", "interactions", "Manual.pdf"))
touch(os.path.join(root, "Strings B", "Documentation", "License Agreement", "EULA English.rtf"))
for f in ("a.wav", "Manual.pdf", "b.mid"): touch(os.path.join(plain, f))

SUPPORTED = {".wav", ".ncw", ".pdf", ".txt", ".mid", ".md", ".docx", ".rtf"}
excl = lambda p: p.lower().replace("/", "\\").find("crm\\interactions") >= 0
scan = {"music|" + root: ("music", root), "music|" + plain: ("music", plain)}
files, entries = lr.scan_files(scan, SUPPORTED, {"stems"}, excl, docs_only_roots={root})
rel = sorted(os.path.relpath(f[2], tmp).replace("\\", "/") for f in files)

lib_files = [r for r in rel if r.startswith("Big Library/")]
check("only the 2 manuals/readmes in the library folder are indexed", len(lib_files) == 2, str(lib_files))
check("no audio or sample file in the library is indexed", not any(r.endswith((".wav", ".ncw")) for r in lib_files))
check("the EULA in the library is NOT indexed", not any("EULA" in r for r in lib_files))
check("a script .txt in the library is NOT indexed", not any("ui_script" in r for r in lib_files))
check("hidden folders are still skipped", not any(".hidden" in r for r in rel))
check("the chat-log exclusion still applies inside a library", not any("interactions" in r.lower() for r in rel))
plain_files = [r for r in rel if r.startswith("Plain Folder/")]
check("a normal folder (not docs_only) is indexed as before, audio included", len(plain_files) == 3, str(plain_files))
check("exactly one summary entry, for the docs_only folder", len(entries) == 1 and entries[0][1] == root)

vault, r_, text = entries[0]
check("summary names the library and counts ALL 47 files (indexed or not), not the chat-log or hidden ones",
      "Big Library" in text and " 47 files" in text.replace(",", ""), text[:140])
check("summary lists the sub-libraries with their file counts", "Drums A" in text and "Strings B" in text)
check("summary lists file types", ".wav" in text and ".ncw" in text)
check("summary says how many manuals/readmes are indexed and that audio is not listed per file",
      "(2 files) are indexed one by one" in text and "not listed individually" in text)
check("the summary is a single chunk (<400 words)", len(text.split()) < 400, f"{len(text.split())} words")

big = lr.LibraryStats(root)
for i in range(200): big.sub[f"Lib {i:03d}"] = 200 - i
big.files = 5000
_sm = big.summary()
check("a library with 200 sub-folders still gives one short summary that says how many it left out",
      len(_sm.split()) < 400 and ", and " in _sm and " more" in _sm, f"{len(_sm.split())} words")
check("the biggest libraries are listed with counts, the rest by name", "Lib 000 (200 files)" in _sm and "Lib 010" in _sm)


print("\n-- writing the summary entry --")
class _Table:
    def __init__(self): self.deleted, self.added = [], []
    def delete(self, expr): self.deleted.append(expr)
    def add(self, rows): self.added.extend(rows)
class _Emb:
    def __init__(self): self.seen = []
    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False): self.seen += texts; return [np.ones(4) for _ in texts]
def _chunk(t, min_words=20): return [t]
def _doc(f, t): return f"Document: {f}\n{t}"
tb, em = _Table(), _Emb()
lr.add_library_entries(tb, em, entries, _chunk, _doc)
check("one row written", len(tb.added) == 1)
row = tb.added[0]
check("row is typed 'library', points at the folder, vault kept",
      row["asset_type"] == "library" and row["source_path"] == root and row["vault"] == "music")
check("an older summary for the same folder is replaced, not duplicated",
      len(tb.deleted) == 1 and root in tb.deleted[0])
check("the summary is embedded with the folder name as its document name", em.seen[0].startswith("Document: Big Library (library folder)\n"))
check("stored text is the plain summary (no Document: prefix)", not row["text"].startswith("Document:"))

shutil.rmtree(tmp, ignore_errors=True)


print("\n-- turbo_ingest wiring and config --")
import turbo_ingest as ti
src = inspect.getsource(ti.run)
sig = inspect.signature(ti.run).parameters
check("run() takes only_path, limit_files and seed", {"only_path", "limit_files", "seed"} <= set(sig))
check("the scan goes through library_rules.scan_files", "library_rules.scan_files" in src)
check("library summaries are written before files are processed", src.index("add_library_entries") < src.index("for fi, (vault_name"))
check("a test slice never rewrites a library summary", "library_entries = []" in src)
check("--only-path skips the five vaults", "scan = {} if only_path else dict(VAULTS)" in src)
main_src = open(str(DAVID_HOME / "turbo_ingest.py"), encoding="utf-8").read()
check("command line has --only-path, --limit-files and --seed",
      all(f in main_src for f in ('"--only-path"', '"--limit-files"', '"--seed"')))
cfg = json.load(open(ti.EXTRA_PATHS_FILE, encoding="utf-8"))
by = {os.path.basename(e["path"].rstrip("/\\")): e for e in cfg}
check("the sample library folder is marked docs_only", by["sample_libraries"].get("mode") == "docs_only")
check("the business folder is NOT docs_only (full indexing)", "mode" not in by["academy_docs"])
check("run() can re-ingest named files only (only_files), and says which ones it could not find",
      "only_files" in sig and "NOT FOUND in the scanned folders" in src)
check("only_files narrows the scan BEFORE any file is processed", src.index("if only_files:") < src.index("for fi, (vault_name"))
check("re-ingesting named files never rewrites a library summary", src.index("if only_files:") < src.index("library_entries = []   # re-ingesting"))
check("command line has --only-files", '"--only-files"' in main_src)
check("the business folder comes first in the ingest order", os.path.basename(cfg[0]["path"].rstrip("/\\")) == "academy_docs")

print("\n-- every file gets at least a file-name entry --")
HDR = "File: A1.ai | design | 1.5MB | modified 2024-10-27 | path /data/m/design/A1.ai"
check("header-only text counts zero body words", ti._body_words(HDR) == 0)
check("a drawing/photo/unknown file only needs the header to be indexed",
      all(ti.min_words_for(HDR + "\n\nA1", t) == 3 for t in ("design", "image", "file", "audio", "video")))
check("a scanned PDF with a 2-character body still gets its file-name entry", ti.min_words_for("File: D2.pdf | document | 1MB | path /data/x/D2.pdf\n\nD2", "document") == 3)
check("a real document still needs the normal 20 words", ti.min_words_for(HDR + "\n\n" + " ".join(["word"] * 12), "document") == 20)
text19 = "File: D2.pdf | document | 1.1MB | modified 2024-11-16 | path /data/m/D2.pdf\n\nD"
check("the exact text that was dropped (19 words) now passes the floor",
      len(text19.split()) >= ti.min_words_for(text19, "document"), f"{len(text19.split())} words >= {ti.min_words_for(text19, 'document')}")

print("\n-- PDF text extraction --")
import extractors
from reportlab.pdfgen import canvas as _cv
_pdf = os.path.join(tempfile.mkdtemp(prefix="pdfx_"), "t.pdf")
_c = _cv.Canvas(_pdf); _c.drawString(60, 780, "Tune knob changes the pitch of the instrument"); _c.showPage(); _c.save()
check("PDF text is extracted with PyMuPDF first", "Tune knob changes the pitch" in extractors._pdf(_pdf))
_saved = sys.modules.get("fitz"); sys.modules["fitz"] = None   # makes `import fitz` raise ImportError
try:
    check("if PyMuPDF is unavailable the old reader (pypdf) still extracts it", "Tune knob changes the pitch" in extractors._pdf(_pdf))
finally:
    if _saved is not None: sys.modules["fitz"] = _saved
    else: sys.modules.pop("fitz", None)
check("the PDF extractor documents why PyMuPDF comes first", "uni0630" in inspect.getsource(extractors._pdf))

print("\n-- junk files are never indexed --")
_jd = tempfile.mkdtemp(prefix="junk_")
def _mk(name, data):
    p = os.path.join(_jd, name); open(p, "wb").write(data); return p
lock = _mk("~$ب تغير دوام.docx", b"x" * 162)
empty = _mk("New Microsoft Word Document.docx", b"")
real = _mk("report.docx", b"PK real content")
check("an Office lock file (~$...) is junk", lr.is_junk_file(lock))
check("a zero-byte file is junk", lr.is_junk_file(empty))
check("a real file is not junk", not lr.is_junk_file(real))
check("a path that does not exist is not called junk (the caller decides)", not lr.is_junk_file(os.path.join(_jd, "missing.docx")))
files_j, _ = lr.scan_files({"business|" + _jd: ("business", _jd)}, {".docx"}, set(), lambda p: False)
check("the folder scan skips lock files and empty files, keeps real ones", [os.path.basename(f[2]) for f in files_j] == ["report.docx"], str([os.path.basename(f[2]) for f in files_j]))
_rag_src = open(str(DAVID_HOME / "rag.py"), encoding="utf-8").read()
check("rag.ingest_file skips junk files", "or library_rules.is_junk_file(filepath)" in _rag_src)
check("rag.import_folder skips junk files", "or library_rules.is_junk_file(fpath)" in _rag_src)
check("the file watcher skips junk files", "is_junk_file(fpath)" in open(str(DAVID_HOME / "file_watcher.py"), encoding="utf-8").read())
shutil.rmtree(_jd, ignore_errors=True)

print("\n-- photo types and failed extraction --")
check(".avif and .jfif photos are supported", {".avif", ".jfif"} <= extractors.IMAGE)
check("the folder scan would pick them up", {".avif", ".jfif"} <= ti._EXTRACTOR_EXTS)
_ph = tempfile.mkdtemp(prefix="photo_")
from PIL import Image as _Im
_jf = os.path.join(_ph, "pic.jfif"); _Im.new("RGB", (40, 30), (10, 120, 200)).save(_jf, "JPEG")
_txt, _at = extractors.describe(_jf)
check(".jfif is read as a photo with its dimensions", _at == "image" and "Image, 40x30px" in _txt, _txt[-60:])
_bad = os.path.join(_ph, "broken.docx"); open(_bad, "wb").write(b"not a real docx")
_btxt, _bat = extractors.describe(_bad)
check("a file that cannot be read gets a file-name entry, not its error message",
      _btxt.startswith("File: broken.docx") and "extraction failed" not in _btxt and "PackageNotFoundError" not in _btxt, _btxt[:50])
shutil.rmtree(_ph, ignore_errors=True)

print("\n-- Summary --")
passed = sum(1 for r in results if r)
print(f"  {passed}/{len(results)} passed\n")
sys.exit(0 if passed == len(results) else 1)
