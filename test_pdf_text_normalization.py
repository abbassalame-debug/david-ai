"""
test_pdf_text_normalization.py -- the NFKC step on PyMuPDF text (2026-10-17).

Some Arabic PDFs come out of PyMuPDF as unjoined "presentation form" glyphs (U+FB50-FEFF) that never match a normal Arabic query; Latin PDFs can carry fi/fl ligatures.
extractors._pdf and chat_attachments._pdf_pages both normalise PyMuPDF's text with NFKC. PyMuPDF is replaced here by a small fake, so no real PDF,
no index and no model is touched.
Run: python test_pdf_text_normalization.py
"""
from david_config import DAVID_HOME
import sys, os, re, types, unicodedata
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


PRES = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")
LIG = re.compile(r"[\uFB00-\uFB06]")
ARABIC_PRES = "ﺑﻤﻮاﻓﻘﺔ ﻣﺠﻠﺲ اﻻﻋﯿﺎن"        # as PyMuPDF returned it for a real law text: presentation-form glyphs
ARABIC_BASE = unicodedata.normalize("NFKC", ARABIC_PRES)
LATIN_LIG = "the ﬁnal ﬂow of the oﬃce"          # fi, fl, ffi ligatures
PLAIN = "مرحبا بالعالم hello world 123"          # must pass through unchanged


class FakePage:
    def __init__(self, text): self._t = text
    def get_text(self): return self._t


class FakeDoc:
    def __init__(self, pages): self._p = [FakePage(t) for t in pages]
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __len__(self): return len(self._p)
    def __getitem__(self, i): return self._p[i]
    def __iter__(self): return iter(self._p)


def install_fake(pages):
    fake = types.ModuleType("fitz")
    fake.open = lambda path: FakeDoc(pages)
    sys.modules["fitz"] = fake


saved = sys.modules.get("fitz")
try:
    import extractors
    import chat_attachments

    print("-- the test data itself --")
    check("the Arabic sample really is presentation-form text, and NFKC turns it into ordinary letters",
          bool(PRES.search(ARABIC_PRES)) and not PRES.search(ARABIC_BASE) and "بموافقة" in ARABIC_BASE, ARABIC_BASE)
    check("the Latin sample really carries ligatures that NFKC removes", bool(LIG.search(LATIN_LIG)) and not LIG.search(unicodedata.normalize("NFKC", LATIN_LIG)))

    print("\n-- extractors._pdf (the indexer's reader) --")
    install_fake([ARABIC_PRES + "\n" + LATIN_LIG])
    out = extractors._pdf("fake.pdf")
    check("no presentation-form glyph or ligature survives, and the ordinary letters are there",
          not PRES.search(out) and not LIG.search(out) and "بموافقة" in out and "final flow of the office" in out, out[:60])
    install_fake([PLAIN])
    check("plain Arabic + English + digits come through unchanged", extractors._pdf("fake.pdf") == PLAIN)

    print("\n-- chat_attachments._pdf_pages (files attached in the chat) --")
    install_fake([ARABIC_PRES, LATIN_LIG])
    text, total, read = chat_attachments._pdf_pages("fake.pdf")
    check("every page is normalised and keeps its [Page N] marker", not PRES.search(text) and not LIG.search(text) and "[Page 1]" in text and "[Page 2]" in text and "بموافقة" in text and (total, read) == (2, 2), text[:60])
    install_fake([PLAIN])
    check("plain text passes through unchanged", chat_attachments._pdf_pages("fake.pdf")[0] == f"[Page 1]\n{PLAIN}")

    print("\n-- the guard, in the source --")
    for f in ("extractors.py", "chat_attachments.py"):
        s = open(os.path.join(str(DAVID_HOME), f), encoding="utf-8").read()
        check(f"{f} normalises PyMuPDF text with NFKC", 'unicodedata.normalize("NFKC"' in s and "import unicodedata" in s)
finally:
    if saved is None:
        sys.modules.pop("fitz", None)
    else:
        sys.modules["fitz"] = saved

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
