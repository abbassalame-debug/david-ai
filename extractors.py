# extractors.py — David v9.0 — multi-format content + asset extractor
# describe(path) -> (text, asset_type). NEVER raises: on failure it returns a
# catalog stub (name/size/path) so the file is still findable by RAG.
#
# Two tracks:
#   - Text track   : pdf, ai, docx, pptx, xlsx, rtf, txt/md/csv  -> real body text
#   - Asset catalog: psd, image, audio, video -> metadata sentence (searchable)

import os
import re
import datetime
import unicodedata

# ── Extension groups ──────────────────────────────────────────────────────────
DOC_PDF  = {".pdf", ".ai"}        # .ai is PDF-compatible in every modern Illustrator
DOC_DOCX = {".docx"}
DOC_PPTX = {".pptx"}
DOC_XLSX = {".xlsx", ".xlsm"}
DOC_RTF  = {".rtf"}
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".log", ".srt", ".vtt", ".json"}
DESIGN   = {".psd", ".psb"}
IMAGE    = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg", ".avif", ".jfif"}
AUDIO    = {".wav", ".mp3", ".flac", ".aiff", ".aif", ".m4a", ".ogg", ".opus"}
VIDEO    = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}

MAX_TEXT = 200_000   # cap extracted body chars per file

_FRONTMATTER_RE = re.compile(r"^---[\s\S]*?---\s*\n")


def _category(ext):
    if ext in DOC_PDF and ext == ".ai": return "design"
    if ext in DOC_PDF or ext in DOC_DOCX or ext in DOC_PPTX or ext in DOC_XLSX \
       or ext in DOC_RTF or ext in TEXT_EXT: return "document"
    if ext in DESIGN: return "design"
    if ext in IMAGE:  return "image"
    if ext in AUDIO:  return "audio"
    if ext in VIDEO:  return "video"
    return "file"


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


# ── Text-track extractors ─────────────────────────────────────────────────────

def optional_pymupdf():
    """PyMuPDF (`import fitz`) or None. PyMuPDF is AGPL-3.0, so it is an OPTIONAL extra (requirements-media.txt), never a requirement of the core:
    when it is missing every PDF reader in David falls back to pypdf (BSD-3-Clause)."""
    try:
        import fitz
        return fitz
    except ImportError:
        return None


def _pdf(path):
    # PyMuPDF first, when installed. pypdf returns raw font glyph names ("/uni0630 /uni0633 ...") for Arabic PDFs whose
    # fonts carry no Unicode map, which can't be searched; PyMuPDF reads the same file as real Arabic
    # (measured on a folder of Arabic business documents). pypdf stays as the fallback.
    fitz = optional_pymupdf()
    if fitz is not None:
        try:
            with fitz.open(path) as doc:
                text = "\n".join(doc[i].get_text() for i in range(min(len(doc), 300))).strip()
            # Some Arabic PDFs come out of PyMuPDF as unjoined presentation-form glyphs (U+FB50-FEFF), which never match a
            # normal Arabic query; NFKC turns them back into ordinary letters (measured: 3 of 15 PDFs, no change on the rest).
            text = unicodedata.normalize("NFKC", text)
            if text:
                return text
        except Exception:
            pass
    from pypdf import PdfReader
    reader = PdfReader(path)
    out = []
    for i, page in enumerate(reader.pages):
        if i >= 300:
            break
        try:
            out.append(page.extract_text() or "")
        except Exception:
            continue
    return unicodedata.normalize("NFKC", "\n".join(out)).strip()


def _docx(path):
    import docx
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs if p.text and p.text.strip()]
    for tbl in d.tables:
        for row in tbl.rows:
            cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts).strip()


def _pptx(path):
    from pptx import Presentation
    prs = Presentation(path)
    parts = []
    for n, slide in enumerate(prs.slides, 1):
        slide_text = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                slide_text.append(shape.text_frame.text.strip())
        if slide_text:
            parts.append(f"[Slide {n}] " + "  ".join(slide_text))
    return "\n".join(parts).strip()


def _xlsx(path):
    import openpyxl
    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append(f"Sheet: {ws.title}")
        for r, row in enumerate(ws.iter_rows(values_only=True)):
            if r >= 2000:
                break
            cells = [str(c) for c in row if c is not None]
            if cells:
                parts.append(" | ".join(cells))
    wb.close()
    return "\n".join(parts).strip()


def _rtf(path):
    from striprtf.striprtf import rtf_to_text
    with open(path, encoding="utf-8", errors="ignore") as f:
        return rtf_to_text(f.read()).strip()


def _txt(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        content = f.read()
    # Obsidian YAML front-matter is metadata, not semantic content — vectorizing
    # it pollutes every chunk of every note with the same boilerplate keys.
    if os.path.splitext(path)[1].lower() in (".md", ".markdown"):
        content = _FRONTMATTER_RE.sub("", content, count=1)
    return content.strip()


# ── Asset-catalog extractors ──────────────────────────────────────────────────

def _psd(path):
    from psd_tools import PSDImage
    psd = PSDImage.open(path)
    layers = list(psd.descendants())
    parts = [f"Photoshop document, {psd.width}x{psd.height}px, {len(layers)} layers."]
    names, texts = [], []
    for layer in layers:
        if layer.name and layer.name.lower() not in ("background", "layer 0"):
            names.append(layer.name)
        if str(getattr(layer, "kind", "")) == "type":
            try:
                if layer.text:
                    texts.append(layer.text.strip())
            except Exception:
                pass
    if names:
        parts.append("Layers: " + ", ".join(names[:60]))
    if texts:
        parts.append("Text in design: " + " | ".join(texts[:40]))
    return "\n".join(parts)


def _image(path):
    from PIL import Image
    import warnings
    Image.MAX_IMAGE_PIXELS = None
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)
        with Image.open(path) as im:
            return f"Image, {im.width}x{im.height}px, {im.mode}, {im.format}"


def _audio(path, analyze=False):
    try:
        import mutagen           # GPL-2.0-or-later: an optional extra (requirements-media.txt); without it files are cataloged by name and size only
    except ImportError:
        mutagen = None
    parts = []
    try:
        mf = mutagen.File(path)
        if mf is not None and getattr(mf, "info", None) is not None:
            length = getattr(mf.info, "length", None)
            if length:
                parts.append(f"{length:.1f}s")
            sr = getattr(mf.info, "sample_rate", None)
            if sr:
                parts.append(f"{sr}Hz")
            br = getattr(mf.info, "bitrate", None)
            if br:
                parts.append(f"{br // 1000}kbps")
            if mf.tags:
                for k in ("title", "artist", "album", "genre", "TBPM", "bpm", "TKEY", "key"):
                    try:
                        v = mf.tags.get(k)
                    except Exception:
                        v = None
                    if v:
                        parts.append(f"{k}={v[0] if isinstance(v, list) else v}")
    except Exception as e:
        parts.append(f"[meta: {type(e).__name__}]")
    desc = "Audio sample: " + ", ".join(str(p) for p in parts) if parts else "Audio sample"
    if analyze:
        try:
            desc += "\n" + _audio_analyze(path)
        except Exception as e:
            desc += f"\n[analysis skipped: {type(e).__name__}]"
    return desc


def _audio_analyze(path):
    import librosa
    import numpy as np
    y, sr = librosa.load(path, mono=True, duration=60)
    tempo = float(librosa.beat.beat_track(y=y, sr=sr)[0])
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    keys = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    key = keys[int(np.argmax(chroma))]
    return f"Analyzed: ~{tempo:.0f} BPM, dominant pitch class {key}"


def _video(path):
    try:
        import mutagen           # GPL-2.0-or-later: an optional extra (requirements-media.txt); without it files are cataloged by name and size only
    except ImportError:
        mutagen = None
    parts = []
    try:
        mf = mutagen.File(path)
        if mf is not None and getattr(mf, "info", None) is not None:
            length = getattr(mf.info, "length", None)
            if length:
                parts.append(f"{length:.0f}s ({length / 60:.1f} min)")
    except Exception:
        pass
    return "Video file" + (": " + ", ".join(parts) if parts else " (metadata unavailable)")


# ── Dispatcher ────────────────────────────────────────────────────────────────

def describe(path, analyze_audio=False, catalog_only=False):
    """Always returns (text, asset_type). Never raises."""
    ext  = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)
    atype = _category(ext)
    try:
        size  = os.path.getsize(path)
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
    except OSError:
        size, mtime = 0, "?"
    stub = f"File: {name} | {atype} | {_human(size)} | modified {mtime} | path {path}"

    if catalog_only:
        return stub, atype

    body = ""
    try:
        if ext in DOC_PDF:    body = _pdf(path)
        elif ext in DOC_DOCX: body = _docx(path)
        elif ext in DOC_PPTX: body = _pptx(path)
        elif ext in DOC_XLSX: body = _xlsx(path)
        elif ext in DOC_RTF:  body = _rtf(path)
        elif ext in TEXT_EXT: body = _txt(path)
        elif ext in DESIGN:   body = _psd(path)
        elif ext in IMAGE:    body = _image(path)
        elif ext in AUDIO:    body = _audio(path, analyze=analyze_audio)
        elif ext in VIDEO:    body = _video(path)
        else:                 body = ""   # unknown type -> catalog stub only
    except Exception as e:
        # A file that cannot be read still gets its file-name entry, but the error message is NOT
        # indexed as if it were the file's content (15 business files had "[extraction failed:
        # PackageNotFoundError ..." as their searchable text on 2026-10-04).
        body = ""

    if not body:
        return stub, atype
    return f"{stub}\n\n{body[:MAX_TEXT]}", atype


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("Usage: python extractors.py <file> [--analyze-audio]")
        sys.exit(0)
    txt, kind = describe(sys.argv[1], analyze_audio="--analyze-audio" in sys.argv)
    print(f"[asset_type: {kind}]\n")
    print(txt[:3000])
