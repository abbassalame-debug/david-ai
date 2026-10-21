# chat_attachments.py — turns files attached in the David chat UI into model context.
# Reuses extractors.py for documents/data/PSD; adds Whisper transcription for audio/video
# and XMP/IDML readers for InDesign. Short files go in whole; long ones are read in full
# part-by-part into notes (map step) and the chat model writes the answer from those.
# Every limitation is reported as a "caveat" and shown to the user — nothing is cut silently.

from david_config import DAVID_DATA
import asyncio
import gc
import glob
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import zipfile

import extractors
from ollama_config import num_ctx_for, OLLAMA_HOST

UPLOAD_DIR = str(DAVID_DATA / "uploads")
MAX_UPLOAD_BYTES = 2 * 1024 ** 3
MAX_FILES_PER_MESSAGE = 5
SINGLE_PASS_CHARS = 20000     # total inline budget; fits the 16k-token chat context beside prompt + history
CHUNK_CHARS = 7000            # one reading pass; small enough that the note-taker doesn't drop details (12k lost 6 of 30 checked facts, 7k lost 3)
MAX_MAPREDUCE_CHARS = 200000  # ~29 passes; beyond this the tail is skipped and the user is told
NOTES_TOKEN_BUDGET = 8000     # target for all notes together
TRANSCRIBE_MAX_SEC = 1800
NOTES_MODEL = "qwen3.5:9b"   # base model follows extraction instructions; the tuned chat models are trained for short answers
OLLAMA_CHAT = OLLAMA_HOST + "/api/chat"

_ID_RE = re.compile(r"^[0-9a-f]{32}$")

AUDIO_EXT = extractors.AUDIO | {".webm"}
VIDEO_EXT = extractors.VIDEO | {".wmv", ".flv", ".mpg", ".mpeg", ".3gp"}
TEXT_LIKE = extractors.TEXT_EXT | {
    ".tsv", ".xml", ".html", ".htm", ".yaml", ".yml", ".svg", ".py", ".js", ".ts",
    ".tsx", ".jsx", ".css", ".sql", ".ini", ".toml", ".sh", ".bat", ".ps1", ".cfg",
}
_PDF_LIKE = {".pdf", ".ai"}

_REPORT_REQUEST = re.compile(
    r"su[mn]{1,2}[aeu]r|anal[iy]|report|review|overview|key (points|takeaways)|break ?down|assess|evaluat", re.I)

REPORT_HINT = (
    "(Write this as a structured report using ONLY the attached content: "
    "1) Executive summary: 3-5 sentences built from the HEADLINE lines — lead with the headline numbers "
    "(costs, returns, thresholds) and the main recommendation; "
    "2) Key facts & figures: bullets with the exact numbers, names and dates, drawn from the WHOLE document "
    "(every page range), not just the first pages; "
    "3) What the document decides or recommends; "
    "4) Risks, gaps or contradictions: only ones the document itself states, each with its page or section — "
    "if it states none, say so; "
    "5) Open items / next steps / inputs still needed, exactly as the document lists them. "
    "Do not invent sections, objectives or figures the document does not contain; label any inference "
    "of your own as 'Inference:'. If a COVERAGE line reports a limitation, state it in the report.)"
)


def wants_report(msg):
    return bool(_REPORT_REQUEST.search(msg or ""))


_ANALYSIS_REQUEST = re.compile(r"anal[iy]|assess|evaluat|critiq|review|insight|report|opinion|recommend|your thoughts", re.I)

ANALYSIS_HINT = (
    "(Write an ANALYSIS of the attached document for its owner, as a sharp senior business analyst would — "
    "do not just restate it. Use these sections: "
    "1) Bottom line: 3-4 sentences — what the document proposes, the headline numbers, and your verdict. "
    "2) What the document says: the key facts, figures and decisions, organized by theme, from the WHOLE document. "
    "3) What is strong: the parts that are well founded. "
    "4) Assumptions the numbers depend on: for each headline figure say what has to be true for it to hold and "
    "what happens if it is wrong. "
    "5) What the figures leave out or overstate: costs, effects or risks the document's own numbers do not include, "
    "and any claim the document itself qualifies or contradicts elsewhere. "
    "6) Gaps and dependencies: information or decisions still missing, and what each one blocks. "
    "7) Timing and feasibility: compare the document's own dates and lead times with today's date. "
    "8) Recommended next actions: 5 concrete actions in priority order. "
    "Rules: use ONLY the attached content; cite the page range for every factual claim; label anything that is "
    "your own inference as 'Assessment:'. Never invent numbers, durations, names or dates, and do not do "
    "arithmetic the document does not do — if you must calculate, show the document figures you used. "
    "Only say the document 'does not address / mention / specify' something if it is absent from BOTH the "
    "digest and the 'WHAT EACH PART COVERS' list; when unsure, make no absence claim. "
    "If a COVERAGE line reports a limitation, state it.)"
)


def wants_analysis(msg):
    return bool(_ANALYSIS_REQUEST.search(msg or ""))


# ── Upload storage ────────────────────────────────────────────────────────────

def resolve_upload(upload_id):
    if not isinstance(upload_id, str) or not _ID_RE.match(upload_id):
        return None
    d = os.path.join(UPLOAD_DIR, upload_id)
    try:
        names = os.listdir(d)
    except OSError:
        return None
    return os.path.join(d, names[0]) if names else None


def _cache_path(upload_id):
    return os.path.join(UPLOAD_DIR, upload_id + ".cache.json")


def _load_cache(upload_id):
    try:
        with open(_cache_path(upload_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_cache(upload_id, ex):
    tmp = _cache_path(upload_id) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(ex, f, ensure_ascii=False)
        os.replace(tmp, _cache_path(upload_id))
    except OSError:
        pass


def purge_old_uploads(max_age_hours=24):
    cutoff = time.time() - max_age_hours * 3600
    for p in glob.glob(os.path.join(UPLOAD_DIR, "*")):
        try:
            if os.path.getmtime(p) >= cutoff:
                continue
            if os.path.isdir(p):
                for f in os.listdir(p):
                    os.remove(os.path.join(p, f))
                os.rmdir(p)
            else:
                os.remove(p)
        except OSError:
            pass


# ── Audio / video transcription ───────────────────────────────────────────────

def _mmss(sec):
    return f"{int(sec) // 60}:{int(sec) % 60:02d}"


def _transcribe_with(model, path):
    segments, info = model.transcribe(path, beam_size=1, vad_filter=True)
    lines, truncated = [], False
    for s in segments:
        if s.start > TRANSCRIBE_MAX_SEC:
            truncated = True
            break
        t = s.text.strip()
        if t:
            lines.append(f"[{_mmss(s.start)}] {t}")
    return lines, info.language, truncated


def transcribe(path):
    # Whisper must NOT load inside the server process: ctranslate2's CUDA libs clash
    # with the torch already loaded there and hard-kill the worker (seen live). A
    # child process isolates that and frees all GPU memory when it exits.
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--transcribe", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=1500, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("__RESULT__"):
            r = json.loads(line[len("__RESULT__"):])
            if "error" in r:
                raise RuntimeError(r["error"])
            return r["lines"], r["language"], r["truncated"]
    raise RuntimeError(f"transcriber exited with code {proc.returncode}: {proc.stderr[-300:]}")


def _transcribe_inprocess(path):
    _sp = os.path.join(os.path.dirname(os.__file__), "site-packages")
    dirs = glob.glob(os.path.join(_sp, "nvidia", "*", "bin"))
    if dirs:  # cuBLAS must be on PATH before faster_whisper loads
        os.environ["PATH"] = ";".join(dirs) + ";" + os.environ.get("PATH", "")
    from faster_whisper import WhisperModel
    for device, size, ctype in (("cuda", "large-v3", "int8_float16"), ("cpu", "small", "int8")):
        model = None
        try:
            model = WhisperModel(size, device=device, compute_type=ctype)
            return _transcribe_with(model, path)
        except Exception:
            if device == "cpu":
                raise
        finally:
            del model
            gc.collect()


def _media_transcript(path, meta):
    try:
        lines, lang, truncated = transcribe(path)
    except Exception as e:
        return f"{meta}\n[transcription failed: {type(e).__name__}: {e}]", ["transcription failed"]
    if not lines:
        return f"{meta}\n[no speech detected]", ["no speech detected"]
    caveats = [f"transcript covers only the first {_mmss(TRANSCRIBE_MAX_SEC)} of a longer file"] if truncated else []
    return f"{meta}\nTranscript (language: {lang}):\n" + "\n".join(lines), caveats


# ── Adobe / format-specific readers ───────────────────────────────────────────

def _xmp_summary(path):
    with open(path, "rb") as f:
        blob = f.read(64 * 1024 * 1024)
    s = blob.find(b"<x:xmpmeta")
    e = blob.find(b"</x:xmpmeta>", s) if s >= 0 else -1
    if s < 0 or e < 0:
        return ""
    xml = blob[s:e + 12].decode("utf-8", "ignore")

    def one(name):
        m = re.search(rf'{name}="([^"]+)"', xml) or re.search(rf"<{name}>([^<]+)</{name}>", xml)
        return html.unescape(m.group(1)) if m else None

    def many(name):
        seen = []
        for v in re.findall(rf"<{name}>([^<]+)</{name}>", xml):
            v = html.unescape(v)
            if v not in seen:
                seen.append(v)
        return seen

    parts = []
    for label, key in (("Created with", "xmp:CreatorTool"), ("Pages", "xmpTPg:NPages"),
                       ("Modified", "xmp:ModifyDate")):
        v = one(key)
        if v:
            parts.append(f"{label}: {v}")
    titles = re.findall(r"<dc:title>.*?<rdf:li[^>]*>([^<]+)</rdf:li>", xml, re.S)
    if titles:
        parts.append("Title: " + html.unescape(titles[0]))
    for label, key in (("Fonts", "stFnt:fontName"), ("Swatches", "xmpG:swatchName")):
        v = many(key)
        if v:
            parts.append(f"{label}: " + ", ".join(v[:40]))
    return "\n".join(parts)


def _indd(path):
    meta = _xmp_summary(path)
    body = "InDesign document (.indd)."
    if meta:
        body += "\n" + meta
    return body


def _idml(path):
    out = []
    with zipfile.ZipFile(path) as z:
        stories = sorted(n for n in z.namelist() if re.match(r"Stories/Story_.*\.xml$", n))
        for n in stories:
            xml = z.read(n).decode("utf-8", "ignore")
            paras = re.findall(r"<ParagraphStyleRange.*?</ParagraphStyleRange>", xml, re.S)
            for p in paras or [xml]:
                text = "".join(html.unescape(t) for t in re.findall(r"<Content>(.*?)</Content>", p, re.S))
                if text.strip():
                    out.append(text.strip())
    return f"InDesign IDML, {len(stories)} stories.\n" + "\n".join(out)


def _looks_like_text(path):
    with open(path, "rb") as f:
        head = f.read(4096)
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return sum(32 <= b < 127 or b in (9, 10, 13) for b in head) > 0.9 * max(len(head), 1)


def _read_text(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read().strip()


def _pdf_pages(path):
    """-> (text with [Page N] markers, total pages or None, pages read or None)."""
    # PyMuPDF (optional, AGPL-3.0: see extractors.optional_pymupdf) keeps Arabic/RTL word order and shaping far better than pypdf.
    fitz = extractors.optional_pymupdf()
    if fitz is not None:
        try:
            with fitz.open(path) as doc:
                total = len(doc)
                read = min(total, 300)
                parts = []
                for i in range(read):
                    # NFKC: joins back the unjoined Arabic presentation-form glyphs some PDFs give (see extractors._pdf)
                    t = unicodedata.normalize("NFKC", doc[i].get_text()).strip()
                    if t:
                        parts.append(f"[Page {i + 1}]\n{t}")
            if parts:
                return "\n\n".join(parts), total, read
        except Exception:
            pass
    return extractors._pdf(path), None, None


def _body_for(path, ext):
    """-> (text, caveats). Caveats are limits on how much of the file was read."""
    if ext in _PDF_LIKE:
        text, total, read = _pdf_pages(path)
        caveats = [f"only the first {read} of {total} pages were read"] if total and read < total else []
        if not text and ext == ".ai":
            return _xmp_summary(path) or "Illustrator file with no readable text.", caveats
        return text or "[no extractable text — likely a scanned/image-only PDF]", caveats
    if ext in extractors.DOC_DOCX: return extractors._docx(path), []
    if ext in extractors.DOC_PPTX: return extractors._pptx(path), []
    if ext in extractors.DOC_XLSX:
        return extractors._xlsx(path), ["spreadsheets: only the first 2,000 rows of each sheet are read"]
    if ext in extractors.DOC_RTF:  return extractors._rtf(path), []
    if ext in extractors.DESIGN:   return extractors._psd(path), []
    if ext in (".indd", ".indt"):
        return _indd(path), ["InDesign text frames are not readable without InDesign — only document metadata "
                             "was extracted (export to IDML or PDF for the text)"]
    if ext == ".idml": return _idml(path), []
    if ext in AUDIO_EXT: return _media_transcript(path, extractors._audio(path))
    if ext in VIDEO_EXT: return _media_transcript(path, extractors._video(path))
    if ext in TEXT_LIKE: return _read_text(path), []
    if ext in extractors.IMAGE: return extractors._image(path), ["image: only size/format metadata was read"]
    if _looks_like_text(path): return _read_text(path), []
    return "[binary file — no reader available for this format; metadata only]", ["no reader for this format"]


def extract_upload(upload_id, name):
    """Full extraction of one upload, cached beside it. Returns None if the upload is gone."""
    cached = _load_cache(upload_id) if isinstance(upload_id, str) and _ID_RE.match(upload_id) else None
    if cached:
        return cached
    path = resolve_upload(upload_id)
    if not path:
        return None
    ext = os.path.splitext(name)[1].lower()
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    try:
        text, caveats = _body_for(path, ext)
    except Exception as e:
        text, caveats = f"[could not read file: {type(e).__name__}: {e}]", [f"file could not be read: {type(e).__name__}"]
    text = (text or "").strip()
    if not text:
        text, caveats = "[file contained no extractable content]", caveats + ["no extractable content"]
    kind = "design" if ext in {".indd", ".indt", ".idml"} else extractors._category(ext)
    ex = {"name": name, "kind": kind, "size": extractors._human(size), "text": text,
          "caveats": caveats, "notes": None, "notes_caveats": []}
    _save_cache(upload_id, ex)
    return ex


# ── Long documents: read every part into notes ────────────────────────────────

_PAGE_SPLIT = re.compile(r"(?=\[Page \d+\]\n)")


def split_chunks(text, size=CHUNK_CHARS):
    pieces = []
    for seg in (s for s in _PAGE_SPLIT.split(text) if s):
        while len(seg) > size:  # one huge page/paragraph: cut on a line boundary
            cut = seg.rfind("\n", 0, size)
            cut = cut if cut > size // 2 else size
            pieces.append(seg[:cut])
            seg = seg[cut:]
        pieces.append(seg)
    chunks, cur = [], ""
    for p in pieces:
        if cur and len(cur) + len(p) > size:
            chunks.append(cur)
            cur = ""
        cur += p
    if cur:
        chunks.append(cur)
    return chunks


def _label(chunk, i, n):
    pg = [int(x) for x in re.findall(r"\[Page (\d+)\]", chunk)]
    if not pg:
        return f"part {i} of {n}"
    return f"page {pg[0]}" if pg[0] == pg[-1] else f"pages {pg[0]}-{pg[-1]}"


_NOTES_PROMPT = (
    'You are reading part {i} of {n} of the document "{name}" ({label}) for a report writer who '
    "will never see the text itself. Reply with exactly these six headings, in this order, each "
    'followed by short "- " bullets (write "- none" when the part has nothing for that heading):\n'
    "TOPIC: one line — what this part is about.\n"
    "HEADLINE: the 1-2 most important sentences of this part — what a reader must remember — with their numbers.\n"
    "FIGURES: every number, price, percentage, range (e.g. 60-80%), date, time and quantity, each "
    "with what it refers to. Copy them exactly.\n"
    "DECISIONS: what this part decides, specifies, requires or recommends (rules, mechanics, requirements).\n"
    "RISKS: risks, weaknesses, blind spots, corrections or contradictions that the text ITSELF states. "
    'Never give your own opinion; write "- none" if the text states none.\n'
    "NEXT: open items, next steps, inputs still needed, and deadlines that the text states.\n"
    "Go through the text in order, all the way to its LAST sentence — the final paragraph matters as "
    "much as the first. Compress descriptions of visual layout (sizes, margins, colors) to one line. "
    "Keep proper nouns and any Arabic text exactly as written. At most about {words} words in total.\n\n"
    "---\n{chunk}"
)

_SECTIONS = ("TOPIC", "HEADLINE", "FIGURES", "DECISIONS", "RISKS", "NEXT")
_HEADING = re.compile(r"^\W*(TOPIC|HEADLINE|FIGURES|DECISIONS|RISKS|NEXT)\b\W*:?\s*(.*)$", re.I)
SECTION_TITLES = {"HEADLINE": "HEADLINE OF EACH PART (the most important point, in document order)",
                  "FIGURES": "KEY FIGURES", "DECISIONS": "DECISIONS / WHAT IT SPECIFIES",
                  "RISKS": "RISKS THE DOCUMENT ITSELF STATES", "NEXT": "OPEN ITEMS / NEXT STEPS / INPUTS NEEDED"}


def _parse_sections(text):
    """Split one part's structured reply into {heading: [bullets]}. Repeated headings (from a
    split re-read) accumulate; 'none' bullets are dropped."""
    out, cur = {s: [] for s in _SECTIONS}, None
    for raw in text.splitlines():
        line = raw.strip()
        m = _HEADING.match(line)
        if m and (len(line) < 14 or line.rstrip().endswith(":") or ":" in line[:14]):
            cur = m.group(1).upper()
            line = m.group(2).strip()
        if not cur or not line:
            continue
        line = line.lstrip("-•*+ \t").strip()
        if line and line.lower().strip(".") != "none":
            out[cur].append(line)
    return out


async def _notes_for_chunk(client, name, i, n, label, chunk, tokens):
    prompt = _NOTES_PROMPT.format(i=i, n=n, name=name, label=label, words=int(tokens * 0.6), chunk=chunk)
    return await _ask(client, prompt, tokens)


_FIGURE = re.compile(r"(?:USD\s?|LBP\s?|\$)\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:%|×)")


def _missing_figures(chunk, notes):
    """The note-taker sometimes drops a headline number (a run lost '9.2×' and '$1,050'). Money,
    percentage and multiplier figures are therefore checked against the source and any missing
    one is copied in verbatim with its line — deterministic, so the key numbers can't vanish."""
    strip = lambda s: re.sub(r"[\s,]", "", s)
    have, seen, lines, size = strip(notes), set(), [], 0
    for m in _FIGURE.finditer(chunk):
        tok = strip(m.group(0))
        if tok in have or tok in seen:
            continue
        seen.add(tok)
        end = chunk.find("\n", m.end())
        end = len(chunk) if end < 0 else end
        line_start = chunk.rfind("\n", 0, m.start()) + 1
        start = chunk.rfind("\n", 0, max(line_start - 1, 0)) + 1  # include the previous line for context
        line = " ".join(chunk[start:end].split())
        if line and line not in lines:
            lines.append(line)
            size += len(line)
        if size > 1500:
            break
    return lines


async def _notes_for_text(client, name, i, n, label, chunk, tokens, depth=0):
    # A part whose notes hit the token cap loses its END — where a document's conclusions and
    # next steps usually are — so re-read that part in two halves until nothing is cut off.
    text, cut = await _notes_for_chunk(client, name, i, n, label, chunk, tokens)
    if text is None or not cut or depth >= 2 or len(chunk) < 2000:
        return text
    mid = chunk.rfind("\n", len(chunk) // 4, len(chunk) // 2 + len(chunk) // 4)
    mid = mid if mid > 0 else len(chunk) // 2
    a = await _notes_for_text(client, name, i, n, label, chunk[:mid], tokens, depth + 1)
    b = await _notes_for_text(client, name, i, n, label, chunk[mid:], tokens, depth + 1)
    return a + "\n" + b if a and b else text


async def _ask(client, prompt, tokens, model=None, temperature=0.1, think=None):
    # num_ctx is NOT a per-call choice: a different value per call makes Ollama reload the
    # model (this file used 4096/8192/12288 for the notes model and 10240/12288 for the
    # judge -- every switch was a full reload). ollama_config.num_ctx_for owns the value.
    model = model or NOTES_MODEL
    body = {"model": model, "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": temperature, "num_ctx": num_ctx_for(model), "num_predict": tokens}}
    if think is None and model == NOTES_MODEL:
        think = False   # the notes model: thinking eats num_predict and returns empty. Explicit-model callers (deepseek-r1) keep their own behaviour
    if think is not None:
        body["think"] = think
    for _ in range(2):
        try:
            r = await client.post(OLLAMA_CHAT, json=body)
            r.raise_for_status()
            data = r.json()
            text = re.sub(r"<think>.*?</think>", "", data.get("message", {}).get("content") or "", flags=re.S).strip()
            if text:
                return text, data.get("done_reason") == "length"
        except Exception:
            pass
    return None, False


_CONDENSE_PROMPT = (
    "Below are notes on one aspect ({title}) of a document, gathered from every part of it, each "
    "with its page label. Condense them into at most {k} bullets: merge duplicates, drop 'none', drop "
    "generic filler and items that are not really about this aspect, keep exact numbers, ranges and "
    "names, and keep the page label on each bullet. Cover the WHOLE range of pages — do not favor the "
    "earliest ones. Put the most decision-relevant items first (financial results and returns, costs, "
    "thresholds, counts, requirements). Do not add anything that is not in the notes. Bullets only.\n\n---\n{body}"
)
_CONDENSE_TARGET = {"FIGURES": 30, "DECISIONS": 20, "RISKS": 12, "NEXT": 15}
_CONDENSE_ABOVE = 4500   # chars; smaller merged lists are kept verbatim
_CONDENSE_INPUT_MAX = 12000


def _only_bullets(text):
    """Drop the model's chatter ('Here are the condensed notes...') — keep only bullet lines."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    return "\n".join(ln for ln in lines if re.match(r"^([-•*+]|\d+[.)])\s", ln))


async def _condense(client, key, bullets, k):
    body = "\n".join("- " + b for b in bullets)
    if len(body) <= _CONDENSE_ABOVE and len(bullets) <= k * 1.3:
        return body, False
    if len(body) > _CONDENSE_INPUT_MAX:  # too big for one call: condense each half, then join
        half = len(bullets) // 2
        a, fa = await _condense(client, key, bullets[:half], k)
        b, fb = await _condense(client, key, bullets[half:], k)
        return a + "\n" + b, fa or fb
    out, _ = await _ask(client, _CONDENSE_PROMPT.format(title=SECTION_TITLES[key].lower(), k=k, body=body), 900)
    out = _only_bullets(out)
    return (out, False) if out else (body[:_CONDENSE_ABOVE], True)


async def _long_doc_digest(client, upload_id, ex, name, announce, progress):
    """Read every part into report-shaped notes, then condense each section. -> (digest, caveats)."""
    caveats = list(ex.get("caveats") or [])
    if ex.get("digest"):
        return ex["digest"], caveats + list(ex.get("digest_caveats") or [])
    text, extra = ex["text"], []
    if len(text) > MAX_MAPREDUCE_CHARS:
        extra.append(f"only the first {MAX_MAPREDUCE_CHARS:,} of {len(text):,} characters were read")
        text = text[:MAX_MAPREDUCE_CHARS]
    chunks = split_chunks(text)
    n = len(chunks)
    tokens = max(300, min(900, NOTES_TOKEN_BUDGET // n))
    pages = len(re.findall(r"\[Page \d+\]", text))
    size_desc = f"{pages} pages" if pages else f"{len(text):,} characters"
    if announce:
        await progress(f"📎 {name} — {size_desc}, reading in {n} parts: ")
    merged, failed = {s: [] for s in _SECTIONS}, []
    for i, chunk in enumerate(chunks, 1):
        label = _label(chunk, i, n)
        t = await _notes_for_text(client, name, i, n, label, chunk, tokens)
        if t is None:
            failed.append(label)
            merged["DECISIONS"].append(f"({label}) [could not be summarized — raw excerpt] " + " ".join(chunk[:800].split()))
        else:
            secs = _parse_sections(t)
            for s in _SECTIONS:
                merged[s] += [f"{label}: {b}" if s == "TOPIC" else f"({label}) {b}" for b in secs[s]]
            merged["FIGURES"] += [f"({label}) [verbatim] {ln}" for ln in _missing_figures(chunk, t)]
        if announce:
            await progress(f"{i}{'✗' if label in failed else '✓'} ")
    if announce:
        await progress("condensing… ")
    digest, trimmed = {"HEADLINE": "\n".join("- " + b for b in merged["HEADLINE"]) or "- none stated",
                       "OUTLINE": "\n".join("- " + b for b in merged["TOPIC"]) or "- none stated"}, []
    for s in ("FIGURES", "DECISIONS", "RISKS", "NEXT"):
        if not merged[s]:
            digest[s] = "- none stated"
            continue
        digest[s], lossy = await _condense(client, s, merged[s], _CONDENSE_TARGET[s])
        if lossy:
            trimmed.append(SECTION_TITLES[s].lower())
    if announce:
        await progress("\n\n")
    if trimmed:
        extra.append("could not condense " + ", ".join(trimmed) + " so they were cut short")
    if failed:
        extra.append("could not read " + ", ".join(failed) + " (raw excerpts used instead)")
    else:
        ex["digest"], ex["digest_caveats"] = digest, extra
        _save_cache(upload_id, ex)
    return digest, caveats + extra


def _render_digest(d):
    parts = [f"{SECTION_TITLES[s]}:\n{d[s]}" for s in ("HEADLINE", "FIGURES", "DECISIONS", "RISKS", "NEXT")]
    if d.get("OUTLINE"):
        parts.append("WHAT EACH PART COVERS (use this to check any claim that something is missing):\n" + d["OUTLINE"])
    return "\n\n".join(parts)


# ── Questions about a long document: search the raw text (BM25), no long read ─

def make_passages(text, size=1200):
    passages, cur, cur_len, page, cur_page = [], [], 0, None, None

    def flush():
        nonlocal cur, cur_len
        if cur:
            passages.append({"page": cur_page, "idx": len(passages) + 1, "text": "\n".join(cur).strip()})
        cur, cur_len = [], 0

    for line in text.split("\n"):
        m = re.fullmatch(r"\[Page (\d+)\]", line.strip())
        if m:
            flush()
            page = int(m.group(1))
            continue
        if not cur:
            cur_page = page
        cur.append(line)
        cur_len += len(line) + 1
        if cur_len >= size:
            flush()
    flush()
    return [p for p in passages if p["text"]]


def _tokens(s):
    return re.findall(r"\w+", s.lower())


def top_passages(passages, query, budget=11000, max_n=10):
    """BM25 over passages; returns the best ones in document order, up to `budget` chars."""
    import math
    q = set(_tokens(query))
    docs = [_tokens(p["text"]) for p in passages]
    n = len(docs)
    if not n or not q:
        return []
    avg = sum(len(d) for d in docs) / n or 1
    df = {t: sum(1 for d in docs if t in d) for t in q}
    scored = []
    for idx, d in enumerate(docs):
        s = 0.0
        for t in q:
            tf = d.count(t)
            if tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * len(d) / avg))
        scored.append((s, idx))
    picked, used = [], 0
    for s, idx in sorted(scored, reverse=True):
        if s <= 0 or len(picked) >= max_n:
            break
        if used + len(passages[idx]["text"]) > budget and picked:
            continue
        picked.append(idx)
        used += len(passages[idx]["text"])
    return [passages[i] for i in sorted(picked)]


def _pages_of(passages):
    pages = sorted({p["page"] for p in passages if p["page"]})
    return ", ".join(str(p) for p in pages)


_CLAIMED_FIGURE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:%|×|\bx\b|\btimes\b|\b(?:days?|weeks?|months?|hours?|minutes?|years?)\b)",
    re.I)


def _ungrounded_figures(src, answer):
    src = src.replace(",", "")
    answer = re.sub(r"\*\[calculated:.*?\]\*", "", answer)  # figures computed in code are not the model's
    bad = []
    for m in _CLAIMED_FIGURE.finditer(answer):
        core = re.match(r"\$?\s?[\d.]+", m.group(0).replace(",", "")).group(0).replace(" ", "").rstrip(".")
        if core and not re.search(rf"(?<![\d.]){re.escape(core.lstrip('$'))}(?!\d)", src):
            fig = " ".join(m.group(0).split())
            if fig not in bad:
                bad.append(fig)
    return bad


def grounding_note(files, answer):
    """Money, percentages, multiples and durations in David's answer that appear nowhere in the
    attached document — his own arithmetic or a mistake (a model once wrote '$467.75' for a
    what-if the document never computed). Returns a warning to append, or ''."""
    src = ""
    for f in [f for f in (files or []) if isinstance(f, dict)][-MAX_FILES_PER_MESSAGE:]:
        uid = f.get("id")
        ex = _load_cache(uid) if isinstance(uid, str) and _ID_RE.match(uid) else None
        if ex:
            src += "\n" + ex["text"]
    bad = _ungrounded_figures(src, answer) if src else []
    if not bad:
        return ""
    return ("\n\n⚠ **Check these figures** — they do not appear in the attached document, so they are my own "
            "calculations or mistakes: " + ", ".join(bad[:8]))


def _caveat_text(caveats):
    return "⚠ " + "; ".join(caveats) if caveats else ""


async def build_context(files, msg, progress, history=None):
    """files: [{id, name, new}] from the chat message. progress(text) streams a line to the UI."""
    import httpx
    files = [f for f in (files or []) if isinstance(f, dict)][-MAX_FILES_PER_MESSAGE:]
    if not files:
        return ""
    loop = asyncio.get_running_loop()
    docs = []
    for f in files:
        name = os.path.basename(str(f.get("name") or "file"))[:120]
        ex = await loop.run_in_executor(None, extract_upload, f.get("id"), name)
        docs.append((f.get("id"), name, bool(f.get("new", True)), ex))
    share = SINGLE_PASS_CHARS // len(docs)
    prev_user = next((h.get("content", "") for h in reversed(history or []) if h.get("role") == "user"), "")
    query = f"{msg} {prev_user[:300]}"
    report = wants_report(msg)
    blocks = []
    async with httpx.AsyncClient(timeout=300) as client:
        for uid, name, announce, ex in docs:
            if ex is None:
                if announce:
                    await progress(f"📎 {name} — ⚠ no longer on the server (uploads are kept 24 hours)\n\n")
                blocks.append(f"[ATTACHED FILE: {name}] It is no longer on the server (uploads are kept for 24 "
                              "hours) — tell the user to attach it again.")
                continue
            head = f"[ATTACHED FILE: {name} | {ex['kind']} | {ex['size']}]"
            caveats = list(ex.get("caveats") or [])
            if len(ex["text"]) <= share:
                if announce:
                    await progress(f"📎 {name} — read in full" + (f" ({_caveat_text(caveats)})" if caveats else "") + "\n\n")
                cov = "COVERAGE: " + (_caveat_text(caveats) or "the whole file was read")
                blocks.append(f"{head}\n{ex['text']}\n[{cov}]")
            elif report:
                digest, caveats = await _long_doc_digest(client, uid, ex, name, announce, progress)
                if announce and caveats:
                    await progress(f"📎 {name} — {_caveat_text(caveats)}\n\n")
                cov = "COVERAGE: " + (_caveat_text(caveats) or "every part of the file was read")
                blocks.append(f"{head} — LONG DOCUMENT read in full. Below is a structured digest of ALL of it. "
                              f"Build the report from these sections; cite pages when useful.\n"
                              f"{_render_digest(digest)}\n[{cov}]")
            else:
                top = top_passages(make_passages(ex["text"]), query)
                pages = _pages_of(top)
                where = f"pages {pages}" if pages else f"{len(top)} passages"
                await progress(f"📎 {name} — searched the document; answering from {where}\n\n")
                body = "\n\n".join(
                    f"[page {p['page']}]\n{p['text']}" if p["page"] else f"[passage {p['idx']}]\n{p['text']}" for p in top)
                cov = (f"COVERAGE: this is a long document; only the {len(top)} passages most relevant to the "
                       f"question ({where}) are shown, not the whole file. If the answer is not in them, say the "
                       "shown passages do not contain it — do not guess. " + _caveat_text(caveats)).strip()
                blocks.append(f"{head} — LONG DOCUMENT. Most relevant passages:\n{body}\n[{cov}]")
    return ("The user attached the file(s) below. Base your analysis on their actual content. "
            "If a COVERAGE line reports a limitation, say so plainly in your answer.\n\n" + "\n\n".join(blocks))


# ── Deep analysis: findings that must quote the document, then get checked ────
#
# A local model asked to "analyse this" in one go produced fluent but unreliable analysis: it
# claimed a document ignored things it covered at length, said the opposite of what a passage
# says, and invented a "competition" point. So the analysis is built from FINDINGS, each with a
# verbatim quote: (1) the quote must exist in the document (deterministic), (2) a second model
# must judge that the quote really supports the claim. Findings that fail either are dropped and
# the count is disclosed.

ANALYSIS_MODEL = "deepseek-r1:14b"
_ABSENCE_FACETS = {"omissions"}  # claims that something is missing are checked against ALL the evidence
# ...and so is any claim, from any pass, that says something is not stated/covered (a fact pass once reported "the
# odds are not explicitly stated" when the document lists them in a table).
_ABSENCE_CLAIM = re.compile(
    r"\bnot (?:explicitly |clearly |fully |specifically )?(?:stated|specified|mentioned|detailed|quantified|addressed|"
    r"defined|discussed|included|covered|given|provided|compared)\b|\bdoes not\b|\bdo not\b|\bdoesn't\b|"
    r"\bno (?:mention|details?|information|data)\b|\black(?:s|ing)?\b|\bomits?\b|\bmissing\b|\bunspecified\b",
    re.I)
# Only the passes that need real judgement use the slow reasoning model (~30s each, and it always "thinks":
# `think=False` is ignored, and a tiny answer budget is eaten entirely by the thinking, returning nothing —
# that emptied a whole report once). Facts, checks and short write-ups use the fast model (~6s each).
_THINKING_FACETS = {"assumptions", "omissions", "contradictions", "timing", "stated_risks"}
_FACETS = [
    # (key, title, group, retrieval query, task). "facts" findings say what the document contains;
    # "assess" findings are the analyst's judgement, each anchored to a quote.
    ("proposal", "What the document proposes", "facts",
     "propose program plan mechanic how it works threshold prize tiers redemption window launch objective goal",
     "What does the document propose and how does it work? Findings state the core mechanics, rules and goals."),
    ("numbers", "The numbers", "facts",
     "cost per net return revenue redemption rate spend total percent model financial roi average order",
     "What are the document's headline numbers (costs, returns, rates, thresholds, counts)? "
     "Each finding states a number and what it refers to."),
    ("returns", "The financial return", "facts",
     "net return incremental revenue return visits benefit profit payoff roi times cost promo scenario low end",
     "What financial return does the document project (revenue, net return, multiples, best and worst scenarios), "
     "and on what assumptions?"),
    ("rules", "Key rules, thresholds and odds", "facts",
     "threshold day night qualifying validity 30 days one card per receipt tiers probability weighting prize",
     "What are the key rules, thresholds, prize tiers and odds?"),
    ("operations", "How it would run", "facts",
     "staff register verification serial inventory branches delivery whatsapp print production pilot quantities rules",
     "How would this run in practice — roles, processes, channels, production and quantities?"),
    ("stated_risks", "Risks the document itself states", "facts",
     "risk risks blind spots negatives weaknesses problem abandoned friction breaks drift unlogged fraud overstates "
     "certainty exclusion misfires fails",
     "Which risks, weaknesses or blind spots does the document itself state?"),
    ("strong", "What is strong", "assess",
     "clear defined guaranteed proven simple benefit advantage strength effective works well low cost high value",
     "What is well founded or well designed in this document? Findings about genuine strengths only."),
    ("assumptions", "Assumptions the numbers depend on", "assess",
     "assume assumed assumption modeled estimate estimated expected projected placeholder redemption rate average return if",
     "What assumptions do the document's headline numbers rely on? Each finding states an assumption, what has to be "
     "true for it to hold, and what changes if it is wrong. The quote must show the assumption or the number it feeds."),
    ("omissions", "What the figures leave out or overstate", "assess",
     "cost costs excludes not include only counts leaves out overstates upfront printing design labour staff time budget total",
     "What do the document's own cost or return figures leave out or overstate? Each finding names what is missing; "
     "the quote must show what the figure does cover or how it is described. Do NOT report something as missing "
     "if the evidence itself goes on to include it."),
    ("contradictions", "Where the document qualifies or contradicts itself", "assess",
     "however but overstates overstated correction corrected ceiling not 100 actually revised replaces instead caveat",
     "Where does the document itself qualify, correct or contradict one of its own claims elsewhere? "
     "The quote must be the qualifying or correcting text."),
    ("gaps", "Gaps and dependencies", "assess",
     "need needed before required missing confirm input still open not yet unknown pending decide inputs depends",
     "What information, decisions or inputs does the document say are still missing, needed or pending? "
     "The quote must be where the document says so."),
    ("timing", "Timing and feasibility", "assess",
     "date deadline launch week weeks days lead time end of september timeline schedule proof print run start",
     "Compare the document's own dates, deadlines and lead times with today's date ({today}). The quote must contain "
     "the date or lead-time text. Say why it matters for whether the plan can go ahead. Do NOT calculate durations "
     "or days remaining yourself (they are added separately), and remember that phrases like 'two weeks out as of "
     "this report' are relative to the document's own date, not to today."),
]

_FINDINGS_PROMPT = (
    "Today's date is {today}. You are a sharp business analyst reviewing the document \"{name}\".\n"
    "EVIDENCE (passages from the document, each with its page):\n{evidence}\n\n"
    "TASK: {task}\n"
    "Write 3 to 5 findings. Each finding is ONE line in exactly this format:\n"
    "- <the finding in one sentence> || p.<page number> || \"<an exact quote copied word-for-word from the evidence, "
    "at most 25 words>\"\n"
    "Rules: use only the evidence; every quote must be copied exactly; never invent numbers, names or dates; "
    "state each finding as the document states it — do NOT add causes, reasons or consequences the quote does not "
    "contain (e.g. never write 'due to the lack of X' unless the quote says X is lacking); "
    "never claim the document lacks something unless the evidence itself shows it is missing. "
    "If the evidence supports no finding, write NONE."
)

_BOTTOM_PROMPT = (
    "Today's date is {today}. Verified findings about the document \"{name}\" (each was checked against its text):\n"
    "{facts}\n\n"
    "{headlines}"
    "Write the BOTTOM LINE: 3-4 sentences for the document's owner. Start with what the document proposes and its "
    "headline financial result (cost AND return, if the document gives them), then the main dependency or risk, then "
    "one sentence starting 'Assessment:' weighing the headline result against the main weakness. Use only the "
    "material above; do not invent numbers; do not say information is missing if the material shows the document "
    "already covers it (for example, it does model a redemption rate)."
)

_ACTIONS_PROMPT = (
    "Today's date is {today}. These are verified findings about the document \"{name}\":\n{findings}\n\n"
    "Write exactly 5 concrete next actions for the document's owner, in priority order. Each starts with a verb and "
    "ends with the finding it addresses in brackets, e.g. [gaps]. Do not introduce numbers, dates or names that are "
    "not in the findings. One line each, numbered."
)


def _norm(s):
    return re.sub(r"\W+", "", (s or "").lower())


def _parse_findings(text):
    out = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-•*0123456789.) ").strip()
        parts = [x.strip() for x in line.split("||")]
        if len(parts) < 3:
            continue
        claim, quote = parts[0], parts[-1].strip().strip("\"“”'").strip()
        if len(claim) > 8 and len(quote) > 8:
            out.append((claim, quote))
    return out


def _locate_quote(quote, passages, norm_passages, norm_text):
    """(found, page). A quote may elide with '...'; every fragment must appear in the document."""
    frags = [_norm(f) for f in re.split(r"…|\.\.\.", quote)]
    frags = [f for f in frags if len(f) >= 12]
    if not frags or not all(f in norm_text for f in frags):
        return False, None
    for pas, npas in zip(passages, norm_passages):
        if frags[0] in npas:
            return True, pas["page"]
    return True, None


def _yes_no(text):
    m = re.search(r"\b(YES|NO)\b", re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).upper())
    return m.group(1) if m else None


# Claims that add a cause, hedge or consequence are where a small model's paraphrase drifts from the quote
# ("... due to the lack of X" when the quote says X is the fix), so they get the strict A/B/C check; plain
# restatements get the lenient check (a strict check on those dropped good findings such as "the 5.7% figure is
# a ceiling"). Calibrated on 12 labelled claims from a real document.
_SPIN = re.compile(
    r"\b(?:due to|because|as a result|resulting in|leading to|lead(?:s)? to|therefore|thus|implies|suggests|indicating|"
    r"so that|vulnerab\w*|prone)\b|lack of",
    re.I)


def _claim_numbers_in_quote(claim, quote):
    """Every figure in a claim must appear in its own quote — this is what removes 'net $821' or
    '1 day remaining', numbers the model made up by arithmetic."""
    q = quote.replace(",", "")
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", claim):
        num = m.group(0).replace(",", "")
        if not re.search(rf"(?<![\d.]){re.escape(num)}(?!\d)", q):
            return False
    return True


async def _supports(client, claim, quote):
    if not _claim_numbers_in_quote(claim, quote):
        return False
    if _SPIN.search(claim):
        out, _ = await _ask(
            client,
            f'Claim: {claim}\nQuote from the document: "{quote}"\n'
            "Choose one letter:\n"
            "A = the quote states what the claim says (same meaning, nothing important added).\n"
            "B = the quote is about the same topic, but the claim adds a cause, reason, consequence or detail the "
            "quote does not contain.\n"
            "C = the quote does not support the claim.\n"
            "Answer with the single letter only.",
            10, temperature=0.0)
        m = re.search(r"\b([ABC])\b", (out or "").upper())
        return bool(m and m.group(1) == "A")
    out, _ = await _ask(
        client,
        f'Claim: {claim}\nQuote from the document: "{quote}"\n'
        "Is the claim directly stated in the quote, or a clear and necessary implication of it? "
        "Answer with exactly one word: YES or NO.",
        10, temperature=0.0)
    return _yes_no(out) == "YES"


async def _holds_against(client, claim, passages):
    """For 'the document leaves X out' claims a matching quote proves nothing about what is absent.
    Retrieve the passages most relevant to the claim itself (wherever they are in the document) and keep
    the claim only if none of them already covers it. An unclear answer keeps nothing."""
    top = top_passages(passages, claim, budget=5000, max_n=6)
    evidence = "\n\n".join(f"[page {x['page']}]\n{x['text']}" if x["page"] else x["text"] for x in top)
    out, _ = await _ask(
        client,
        f"Passages from a document:\n{evidence}\n\n"
        f"A reviewer claims the document leaves this out: {claim}\n"
        "Do the passages above already include, cover or address it, even partly? "
        "Answer with exactly one word: YES or NO.",
        10, temperature=0.0)
    return _yes_no(out) == "NO"


_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"], 1)}
_MONTH_RE = "|".join(_MONTHS)


def _doc_age_line(text, today_date, today):
    import datetime
    m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", text[:2000])
    if not m:
        return ""
    try:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return ""
    n = (today_date - d).days
    if n <= 0:
        return ""
    return (f"*[calculated: the document is dated {d.isoformat()}, {n} days before today ({today}), so anything it "
            f"says 'as of this report' or 'N weeks out' is {n} days out of date]*")


def _days_annotation(quote, today_date):
    """Days between today and dates named in a quote — done in code, never by the model."""
    import calendar
    import datetime
    parts = []
    for m in re.finditer(rf"end of ({_MONTH_RE})(?:\s+(20\d\d))?", quote, re.I):
        yr = int(m.group(2) or today_date.year)
        mon = _MONTHS[m.group(1).lower()]
        parts.append((m.group(0), datetime.date(yr, mon, calendar.monthrange(yr, mon)[1])))
    for m in re.finditer(r"\b(20\d\d)-(\d\d)-(\d\d)\b", quote):
        try:
            parts.append((m.group(0), datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        except ValueError:
            pass
    out = []
    for label, d in parts:
        n = (d - today_date).days
        when = f"{n} days from today" if n > 0 else "today" if n == 0 else f"{-n} days ago"
        out.append(f"“{label}” = {d.isoformat()}, {when}")
    return f" *[calculated: {'; '.join(out)}]*" if out else ""


async def _analyse_doc(client, uid, ex, name, today, today_date, progress):
    caveats = list(ex.get("caveats") or [])
    text = ex["text"]
    passages = make_passages(text)
    norm_passages = [_norm(x["text"]) for x in passages]
    norm_text = _norm(text)

    digest = None
    if len(text) > SINGLE_PASS_CHARS:  # build (or load) the digest so the later Q&A and reports share the read
        digest, digest_caveats = await _long_doc_digest(client, uid, ex, name, True, progress)
        caveats += [c for c in digest_caveats if c not in caveats]

    await progress("analysing: ")
    # Draft findings — reasoning-model passes first, then fast-model passes, so the GPU swaps models once.
    drafts, evidence_of = {}, {}
    order = ([f for f in _FACETS if f[0] in _THINKING_FACETS] + [f for f in _FACETS if f[0] not in _THINKING_FACETS])
    for key, title, group, query, task in order:
        top = top_passages(passages, query, budget=8000 if key == "stated_risks" else 6000,
                           max_n=10 if key == "stated_risks" else 8)
        evidence = "\n\n".join(f"[page {x['page']}]\n{x['text']}" if x["page"] else x["text"] for x in top)
        evidence_of[key] = evidence
        task = task.format(today=today)
        prompt = _FINDINGS_PROMPT.format(today=today, name=name, evidence=evidence, task=task)
        if key in _THINKING_FACETS:
            out, _ = await _ask(client, prompt, 3500, model=ANALYSIS_MODEL, temperature=0.3)
        else:
            out, _ = await _ask(client, prompt, 1200, temperature=0.2)
        drafts[key] = _parse_findings(out or "")
        await progress(f"{key}✓ ")

    # Verify — deterministic quote check, then a fast-model judgement, all on one model.
    kept, dropped = {}, 0
    await progress("verifying… ")
    for key, *_ in _FACETS:
        rows = []
        for claim, quote in drafts.get(key, []):
            found, page = _locate_quote(quote, passages, norm_passages, norm_text)
            is_absence = key in _ABSENCE_FACETS or bool(_ABSENCE_CLAIM.search(claim))
            if found and (await _holds_against(client, claim, passages) if is_absence
                          else await _supports(client, claim, quote)):
                rows.append((claim, quote, page))
            else:
                dropped += 1
        kept[key] = rows

    n_kept = sum(len(v) for v in kept.values())
    if n_kept < 0.35 * (n_kept + dropped):  # a checker rejecting nearly everything is broken, not thorough
        raise RuntimeError(f"analysis verification kept only {n_kept} of {n_kept + dropped} drafted findings")

    facts = "\n".join(f"- {c}" for k in ("proposal", "numbers", "returns", "rules", "stated_risks", "strong",
                                        "assumptions", "omissions", "contradictions", "gaps") for c, _, _ in kept.get(k, []))
    bottom = ""
    if facts:
        headlines = ""
        if digest and digest.get("HEADLINE"):
            headlines = ("Most important point of each part of the document (from reading all of it; not individually "
                         "quote-checked):\n" + digest["HEADLINE"][:3500] + "\n\n")
        bottom_prompt = _BOTTOM_PROMPT.format(today=today, name=name, facts=facts, headlines=headlines)
        bottom, _ = await _ask(client, bottom_prompt, 3000, model=ANALYSIS_MODEL, temperature=0.3)
        bad = _ungrounded_figures(text, bottom or "")
        if bad:  # a wrong figure in the headline paragraph is the worst place for one: redo, then cut if needed
            bottom, _ = await _ask(
                client, bottom_prompt + f"\nYour previous draft used figures that are not in the material ({', '.join(bad)}). "
                "Rewrite it using only figures that appear in the material.", 3000, model=ANALYSIS_MODEL,
                temperature=0.2)
            bad = _ungrounded_figures(text, bottom or "")
            if bad:
                sentences = re.split(r"(?<=[.!?])\s+", bottom or "")
                bottom = " ".join(x for x in sentences if not any(b in x for b in bad))
    flat = [f"[{k}] {c}" for k in ("assumptions", "omissions", "contradictions", "gaps", "timing")
            for c, _, _ in kept.get(k, [])]
    actions = ""
    if flat:
        actions, _ = await _ask(client, _ACTIONS_PROMPT.format(today=today, name=name, findings="\n".join(flat)), 3000,
                                model=ANALYSIS_MODEL, temperature=0.3)
    await progress("\n\n")

    def render(rows, dated=False):
        return [f"- {c} *({'p.' + str(pg) + ': ' if pg else ''}“{q}”)*" + (_days_annotation(q, today_date) if dated else "")
                for c, q, pg in rows] or ["- Nothing in the document supports a finding here."]

    out = [f"## Analysis of {name}\n", "### Bottom line", (bottom or "(could not be written)").strip(),
           "\n### What the document says"]
    for key, title, group, _, _ in _FACETS:
        if group == "facts":
            out += [f"\n**{title}**"] + render(kept.get(key) or [])
    out.append("\n### Assessment")
    for key, title, group, _, _ in _FACETS:
        if group == "assess":
            out += [f"\n**{title}**"]
            if key == "timing" and _doc_age_line(text, today_date, today):
                out.append(_doc_age_line(text, today_date, today))
            out += render(kept.get(key) or [], dated=(key == "timing"))
    if actions:
        out.append("\n### Recommended next actions\n" + actions.strip())
    total = dropped + sum(len(v) for v in kept.values())
    note = (f"\n---\n*How this was checked: every finding above quotes the document. {dropped} of {total} drafted "
            "findings were dropped because their quote is not in the document or does not support the claim.*")
    if caveats:
        note += f"\n*Coverage: {_caveat_text(caveats)}*"
    return "\n".join(out) + note


async def deep_analysis(files, msg, progress):
    """Analysis of the newly attached document(s); returns the finished report text."""
    import datetime
    import httpx
    files = [f for f in (files or []) if isinstance(f, dict)][-MAX_FILES_PER_MESSAGE:]
    targets = [f for f in files if f.get("new", True)] or files[-1:]
    now = datetime.date.today()
    today = now.strftime("%A %d %B %Y")
    loop = asyncio.get_running_loop()
    reports = []
    async with httpx.AsyncClient(timeout=900) as client:
        for f in targets[:2]:
            name = os.path.basename(str(f.get("name") or "file"))[:120]
            ex = await loop.run_in_executor(None, extract_upload, f.get("id"), name)
            if ex is None:
                reports.append(f"📎 {name} is no longer on the server (uploads are kept 24 hours) — attach it again.")
                continue
            reports.append(await _analyse_doc(client, f.get("id"), ex, name, today, now, progress))
    return "\n\n".join(reports)


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--transcribe":
    try:
        _lines, _lang, _trunc = _transcribe_inprocess(sys.argv[2])
        _res = {"lines": _lines, "language": _lang, "truncated": _trunc}
    except Exception as _e:
        _res = {"error": f"{type(_e).__name__}: {_e}"}
    print("__RESULT__" + json.dumps(_res, ensure_ascii=False), flush=True)
