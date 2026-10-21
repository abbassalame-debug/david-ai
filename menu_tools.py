"""
menu_tools.py -- the menu glossary and its two companions for Job 1 (restaurant operations, Demo Bistro).

  get_menu_glossary       the branch's menu as one clean list, built from the staff "Price & Menu Cheat Sheet" PDF: Arabic and English
                          names, category, price, meat grams per protein, and what is unresolved                          (agent: analyst)
  match_menu_item         raw OCR / vision text of an item name -> the closest glossary item with a score:
                          100 = exact after Arabic normalisation, 85-99 = suggested, anything lower = needs a human     (agent: analyst)
  validate_draft_package  checks a compiled draft package against the draft_package v0 schema, its matched items against the
                          glossary and its math against the same code that computed it, BEFORE the review sheet is rendered (analyst)

Like restaurant_tools.py these are deterministic code, not model judgement. They only READ local files, never write, never touch the
network, and never invent: a name that is not in the glossary is reported as "needs human", not forced onto the nearest item.

The single source of truth is the cheat sheet PDF in agents/jobs/01_restaurant_ops/reference/. Its text layer is unreadable (broken font
encoding), so the menu is read from menu_cheat_sheet.json, a transcription of the five pages; the glossary refuses to load if the PDF's
SHA-256 no longer matches the one recorded in that file. The sheet has two parts: the customer menu (prices and weights) and the
"Kitchen Portion Reference" (internal recipe weights). Where they disagree the glossary does not choose:
  * the same Arabic name with two recipes (Kaiser Chicken 360 g priced vs the 90 g recipe-sheet kaizer) is marked `ambiguous`, the sheet
    itself says to confirm which format is in use, and the matcher sends such lines to a human;
  * an item whose meat type the sheet does not state (Shawarma Caesar Salad, "2 Large Shawarma") is `unspecified`;
  * the app's menu (backup export) is NOT a source of names, prices or grams. It is read only to attach its ids to entries whose
    Arabic name is identical, and to report where its weights differ from the sheet (`differs_from_app`; the sheet's weights are used).
Spellings that receipts print but the sheet does not contain are never guessed: they come from menu_aliases.json, and only the ones the
manager has marked "confirmed" are used.
"""

from david_config import DAVID_HOME, DAVID_RESTAURANT_ROOT
import functools
import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction

import restaurant_tools as RT

RESTAURANT_ROOT = str(DAVID_RESTAURANT_ROOT)   # only used to link the app's menu ids
SCHEMA_PATH = str(DAVID_HOME / "agents" / "jobs" / "01_restaurant_ops" / "contracts" / "draft_package.schema.json")
CANDIDATE_FLOOR = 50                  # below this a candidate is noise and is not listed
MAX_TEXT_CHARS = 300
MAX_TEXTS = 200
MAX_ERRORS_SHOWN = 60
NOT_ASSERTED = "Read from local files; nothing was booked or written."


class GlossaryError(Exception):
    status = "no_menu"


# ══ Arabic-aware text normalisation ══════════════════════════════════════════════════════════════════════════════════════════

_DIACRITICS = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")           # tashkeel, Quranic marks, tatweel
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\u00ad]")  # bidi controls, zero-width, BOM
_FOLD = {ord(k): v for k, v in {
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",        # every alef -> bare alef
    "ى": "ي", "ی": "ي", "ئ": "ي", "ې": "ي",         # alef maqsura / Persian yeh / hamza-on-yeh -> yeh
    "ؤ": "و", "ة": "ه", "ک": "ك", "ء": "",         # hamza-on-waw, teh marbuta, Persian kaf, lone hamza
}.items()}
_DIGITS = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGITS.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_BOUNDARY = re.compile(r"(?<=\d)(?=[^\W\d_])|(?<=[^\W\d_])(?=\d)")
_OCR_SPLIT = re.compile(r"(?<!\S)ساند ويش(?!\S)")        # the one split that OCR makes all the time: ساند ويش -> ساندويش


def normalize(text):
    """The comparison form of a name. Equal normal forms = the same name written differently (diacritics, tatweel, alef/yeh/teh-marbuta
    variants, Arabic-Indic digits, punctuation, spacing, letter case, and the OCR split of شاورما into two words). It never changes the
    meaning of a word."""
    s = unicodedata.normalize("NFKC", str(text))
    s = _INVISIBLE.sub("", s)
    s = _DIACRITICS.sub("", s)
    s = s.translate(_FOLD).translate(_DIGITS).casefold()
    s = "".join(ch if ch.isalnum() else " " for ch in s)
    return _OCR_SPLIT.sub("ساندويش", " ".join(_BOUNDARY.sub(" ", s).split()))


@functools.lru_cache(maxsize=262144)
def _lev(a, b):
    """Levenshtein distance (insert / delete / substitute, each 1)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@functools.lru_cache(maxsize=262144)
def _ratio(a, b):
    n = max(len(a), len(b))
    return Fraction(1) if n == 0 else Fraction(n - _lev(a, b), n)


def _pct(x):
    return int((Fraction(x) * 100 + Fraction(1, 2)) // 1)          # round half up, exact


# Words that change WHAT the item is. A name that differs from a candidate in any of these is a different item, however many
# letters the two share ("12 chicken pieces" and "12 beef pieces" are 90% the same letters). Compared per class after mapping each
# word (and its one-letter-off spelling) to a label.
_VOCAB = {
    "protein": {"دجاج": "chicken", "لحمه": "beef", "لحم": "beef", "سجق": "sojok"},
    "portion": {"صغير": "small", "كبير": "large", "تورتيلا": "markouk", "راب": "wrap", "ستيك": "doner"},
    "form": {"كومبو": "combo", "وجبه": "meal", "صحن": "plate", "سلايدر": "kaizer", "عرض": "offer", "ناجتس": "bites", "دوبل": "double",
             "موظف": "staff", "مكس": "mix", "كيلو": "kilo", "نص": "half", "اضافه": "add"},
}


@functools.lru_cache(maxsize=4096)
def _label(tok):
    for cls, words in _VOCAB.items():
        if tok in words:
            return cls, words[tok]
    if tok.startswith("و") and len(tok) > 3:                      # "ولحمة" = "and" + "لحمة"
        for cls, words in _VOCAB.items():
            if tok[1:] in words:
                return cls, words[tok[1:]]
    if len(tok) >= 4:
        hits = {(cls, words[w]) for cls, words in _VOCAB.items() for w in words if len(w) >= 4 and _lev(tok, w) <= 1}
        if len(hits) == 1:
            return next(iter(hits))
    return None


def _signature(tokens):
    sig = {cls: set() for cls in _VOCAB}
    sig["number"] = set()
    for t in tokens:
        if t.isdigit():
            sig["number"].add(t)
            continue
        lab = _label(t)
        if lab:
            sig[lab[0]].add(lab[1])
    return {k: frozenset(v) for k, v in sig.items()}


@functools.lru_cache(maxsize=16384)
def _prep(text):          # callers never mutate the result
    norm = normalize(text)
    toks = norm.split()
    return {"norm": norm, "tokens": toks, "sig": _signature(toks)}


def _pair_tokens(qt, ct):
    """Pair every query token once with its most similar candidate token (>= 0.75 similar).
    Returns (sum of similarities, query tokens left unpaired, candidate tokens left unpaired)."""
    pairs = sorted(((_ratio(a, b), i, j) for i, a in enumerate(qt) for j, b in enumerate(ct) if _ratio(a, b) >= Fraction(3, 4)),
                   key=lambda p: (-p[0], p[1], p[2]))
    used_q, used_c, total = set(), set(), Fraction(0)
    for r, i, j in pairs:
        if i not in used_q and j not in used_c:
            used_q.add(i), used_c.add(j)
            total += r
    return total, [t for i, t in enumerate(qt) if i not in used_q], [t for j, t in enumerate(ct) if j not in used_c]


def _token_f1(qt, ct):
    total, _, _ = _pair_tokens(qt, ct)
    if not qt or not ct or total == 0:
        return Fraction(0)
    p, r_ = total / len(qt), total / len(ct)
    return 2 * p * r_ / (p + r_)


def _score_variant(q, v, english):
    """(score 0-100, [reasons]) of the query against one name of one item."""
    if q["norm"] == v["norm"]:
        return 100, []
    score = max(_pct(_ratio(q["norm"], v["norm"])),
                _pct(_ratio(" ".join(sorted(q["tokens"])), " ".join(sorted(v["tokens"])))),
                _pct(_token_f1(q["tokens"], v["tokens"])))
    reasons = []
    score = min(score, 99)                                         # only a normalised-equal name is a 100
    differs = [k for k in q["sig"] if q["sig"][k] != v["sig"][k]]
    if differs:
        def shown(sig):
            return "/".join(sorted(sig)) or "none"
        reasons.append("differs in " + "; ".join(f"{k}: {shown(q['sig'][k])} vs {shown(v['sig'][k])}" for k in differs))
        score = min(score, 79)
    _, extra_q, extra_c = _pair_tokens(q["tokens"], v["tokens"])
    if extra_c:
        reasons.append("only part of the item name")
        score = min(score, 84)
    if extra_q:
        reasons.append("has words the menu name does not")
        score = min(score, 84)
    if english:
        reasons.append("matched on the English name, which is never auto-suggested")
        score = min(score, 84)
    return score, reasons


# ══ the glossary ════════════════════════════════════════════════════════════════════════════════════════════════════════════

REF_DIR = str(DAVID_HOME / "agents" / "jobs" / "01_restaurant_ops" / "reference")
CHEAT_JSON = "menu_cheat_sheet.json"
ALIASES_JSON = "menu_aliases.json"
CHEAT_SCHEMA = "menu_cheat_sheet/v1"
PROTEINS = ("chicken", "beef", "sojok")
_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_cache = {}


class SourceChanged(GlossaryError):
    status = "source_changed"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dec(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        d = Decimal(str(v).strip().replace(",", ""))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _safe_total(expr):
    """Total grams of one of the app's grams_expr strings ('36*(130/6)', '6*30 [chicken] + 12*30 [beef]'), or None. Numbers and + - * / only."""
    import ast
    try:
        tree = ast.parse(re.sub(r"\[[a-z]+\]", "", str(expr)), mode="eval")
    except SyntaxError:
        return None

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return Fraction(str(n.value))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            return a / b
        raise ValueError
    try:
        return float(ev(tree))
    except (ValueError, ZeroDivisionError):
        return None


def _read_json(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise GlossaryError(f"the {what} cannot be read ({path}): {e}")
    except ValueError as e:
        raise GlossaryError(f"the {what} is not valid JSON ({path}): {e}")


def _read_cheat(ref_dir):
    path = os.path.join(ref_dir, CHEAT_JSON)
    doc = _read_json(path, "menu cheat sheet extraction")
    if not isinstance(doc, dict) or doc.get("schema") != CHEAT_SCHEMA:
        raise GlossaryError(f"{path} is not a {CHEAT_SCHEMA} file")
    rows = []
    for kind, field in (("customer", "customer_menu"), ("kitchen", "kitchen_portions")):
        for r in doc.get(field) or []:
            if not isinstance(r, dict) or not r.get("key") or not (r.get("name_ar") or r.get("name_en")):
                raise GlossaryError(f"{path}: a {field} row has no key or no name")
            rows.append((kind, r))
    keys = [r["key"] for _, r in rows]
    if len(set(keys)) != len(keys):
        raise GlossaryError(f"{path}: duplicate keys {sorted({k for k in keys if keys.count(k) > 1})}")
    if not rows:
        raise GlossaryError(f"{path} has no menu rows")
    src = doc.get("source") or {}
    pdf = os.path.join(ref_dir, src.get("file") or "")
    warn = []
    if not src.get("file") or not os.path.isfile(pdf):
        warn.append(f"the cheat sheet PDF ({src.get('file')}) is not in {ref_dir}, so the extraction cannot be checked against it")
        pdf_ok = None
    else:
        pdf_ok = _sha256(pdf) == src.get("sha256")
        if not pdf_ok:
            raise SourceChanged(f"the cheat sheet PDF changed since {CHEAT_JSON} was extracted from it (SHA-256 differs). "
                                "Re-extract the menu from the new PDF before anything is matched against it.")
    return doc, rows, pdf_ok, warn


def _read_aliases(ref_dir, keys):
    path = os.path.join(ref_dir, ALIASES_JSON)
    if not os.path.isfile(path):
        return [], [], []
    doc = _read_json(path, "menu alias list")
    confirmed, proposed, warn = [], [], []
    for a in doc.get("aliases") or []:
        if not isinstance(a, dict) or not str(a.get("ar") or "").strip() or a.get("item") not in keys:
            warn.append(f"alias {a!r} was ignored: it needs an Arabic spelling and the key of an item on the sheet")
            continue
        if a.get("status") == "confirmed":
            confirmed.append(a)
        elif a.get("status") == "proposed":
            proposed.append(a)
        else:
            warn.append(f"alias {a.get('ar')!r} was ignored: status must be 'confirmed' or 'proposed'")
    return confirmed, proposed, warn


def _read_decisions(ref_dir, keys):
    """The manager's confirmed rulings on a POS line the sheet leaves ambiguous (`pos_line_decisions` in the alias file). ([confirmed], [warnings])."""
    path = os.path.join(ref_dir, ALIASES_JSON)
    if not os.path.isfile(path):
        return [], []
    out, warn = [], []
    for d in _read_json(path, "menu alias list").get("pos_line_decisions") or []:
        ok = (isinstance(d, dict) and str(d.get("ar") or "").strip() and d.get("item") in keys and isinstance(d.get("grams"), dict)
              and set(d["grams"]) <= set(PROTEINS) and all(n in keys for n in d.get("not_item") or []))
        if not ok:
            warn.append(f"decision {d!r} was ignored: it needs an Arabic name, the key of an item on the sheet, grams per protein and valid not_item keys")
        elif d.get("status") == "confirmed":
            out.append(d)
    return out, warn


def _read_ignored(ref_dir):
    """Receipt lines the manager ruled are NOT items (`ignored_line_rules` in the alias file): 'without ...', 'extra ...', '... free'. A rule has a kind,
    words a line STARTS with, words it CONTAINS and whole lines it EQUALS (compared after normalising). ([confirmed rules], [warnings])."""
    path = os.path.join(ref_dir, ALIASES_JSON)
    if not os.path.isfile(path):
        return [], []
    out, warn = [], []
    for d in _read_json(path, "menu alias list").get("ignored_line_rules") or []:
        starts = [normalize(w) for w in (d.get("starts_with") or []) if isinstance(w, str) and normalize(w)] if isinstance(d, dict) else []
        has = [normalize(w) for w in (d.get("contains") or []) if isinstance(w, str) and normalize(w)] if isinstance(d, dict) else []
        same = [normalize(w) for w in (d.get("equals") or []) if isinstance(w, str) and normalize(w)] if isinstance(d, dict) else []
        if not isinstance(d, dict) or not str(d.get("kind") or "").strip() or not (starts or has or same):
            warn.append(f"ignored-line rule {d!r} was ignored: it needs a kind and at least one word")
        elif d.get("status") == "confirmed":
            out.append({"kind": str(d["kind"]).strip(), "starts_with": starts, "contains": has, "equals": same, "confirmed_on": d.get("confirmed_on"), "decision": d.get("decision")})
    return out, warn


def ignored_line_kind(text, glossary=None):
    """The kind ('without', 'extra', 'free') of a receipt line the manager ruled is not an item, or None. Compared on the normalised name."""
    g = glossary or load_glossary()
    t = normalize(str(text or ""))
    if not t:
        return None
    first = t.split()[0]
    for r in g.get("ignored_line_rules") or []:
        if any(first.startswith(w) for w in r["starts_with"]) or any(w in t for w in r["contains"]) or t in r["equals"]:
            return r["kind"]
    return None


def _read_unlisted(ref_dir, keys, sheet_names):
    """Items the POS prints that the cheat sheet does not list, added by the manager in the alias file (`unlisted_items`, ids X01, X02, ...).
    ([confirmed items], [warnings]). The sheet always wins: an id or a name the sheet has is ignored here."""
    path = os.path.join(ref_dir, ALIASES_JSON)
    if not os.path.isfile(path):
        return [], []
    out, warn, seen = [], [], set()
    for u in _read_json(path, "menu alias list").get("unlisted_items") or []:
        g = u.get("grams") if isinstance(u, dict) else None
        price = u.get("price_usd") if isinstance(u, dict) else None
        ok = (isinstance(u, dict) and re.fullmatch(r"X\d{2}", str(u.get("item_id") or "")) and str(u.get("name_ar") or "").strip() and isinstance(g, dict) and set(g) == set(PROTEINS)
              and all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in g.values())
              and (price is None or (isinstance(price, (int, float)) and not isinstance(price, bool) and price >= 0)))
        if not ok:
            warn.append(f"unlisted item {u!r} was ignored: it needs an id like X01, an Arabic name, grams for chicken, beef and sojok (0 if none) and a price or null")
        elif u["item_id"] in keys or u["item_id"] in seen or normalize(u["name_ar"]) in sheet_names:
            warn.append(f"unlisted item {u['item_id']} ({u['name_ar']}) was ignored: the id or the name is already on the cheat sheet or used twice")
        elif u.get("status") == "confirmed":
            seen.add(u["item_id"])
            out.append(u)
    return out, warn


def _read_pos(restaurant_root):
    """The app's menu_items export, ONLY to link ids and to report disagreements. (items, date) or (None, reason)."""
    base = os.path.join(restaurant_root, "app", "backup")
    try:
        days = sorted((d for d in os.listdir(base) if _DATE_DIR.match(d) and os.path.isfile(os.path.join(base, d, "menu_items.json"))), reverse=True)
    except OSError:
        days = []
    if not days:
        return None, f"no POS menu export under {base}, so entries are not linked to the app's menu ids"
    path = os.path.join(base, days[0], "menu_items.json")
    try:
        rows = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"the POS menu export {path} cannot be read ({e}), so entries are not linked to the app's menu ids"
    rows = [r for r in rows if isinstance(r, dict) and r.get("id") and str(r.get("pos_name_ar") or "").strip() and r.get("active") is not False]
    return (rows, days[0]), None


def _grams_equal(sheet, pos_row):
    """True/False when the sheet and the app agree on the meat weight, None when the sheet gives no weight to compare."""
    if not sheet:
        return None
    if pos_row.get("grams_expr"):
        total = _safe_total(pos_row["grams_expr"])
        return None if total is None else abs(total - sum(sheet.values())) < 1e-6
    pos = {"chicken": float(_dec(pos_row.get("chicken_g")) or 0), "beef": float(_dec(pos_row.get("beef_g")) or 0), "sojok": float(_dec(pos_row.get("sausage_g")) or 0)}
    return all(abs(pos[p] - float(sheet.get(p, 0))) < 1e-6 for p in PROTEINS)


def _build(ref_dir, restaurant_root):
    doc, rows, pdf_ok, warnings = _read_cheat(ref_dir)
    keys = {r["key"] for _, r in rows}
    up = doc.get("combo_upgrade")
    if isinstance(up, dict) and up.get("price_usd") is not None:
        keys.add("U01")
    unlisted, uw = _read_unlisted(ref_dir, keys, {normalize(r["name_ar"]) for _, r in rows if r.get("name_ar")})
    keys |= {u["item_id"] for u in unlisted}
    confirmed, proposed, aw = _read_aliases(ref_dir, keys)
    decisions, dw = _read_decisions(ref_dir, keys)
    ignored_rules, iw = _read_ignored(ref_dir)
    warnings += uw + aw + dw + iw
    items = []
    for kind, r in rows:
        grams = {p: float(r["grams"].get(p, 0)) for p in PROTEINS} if isinstance(r.get("grams"), dict) else None
        items.append({"item_id": r["key"], "kind": kind, "name_ar": r.get("name_ar") or None, "name_en": r.get("name_en") or None,
                      "names_en": [r["name_en"]] if r.get("name_en") else [], "match_names_ar": [], "category": r.get("category") or ("kitchen" if r.get("section") == "internal" else "sojok line"),
                      "price_usd": r.get("price_usd"), "was_price_usd": r.get("was_price_usd"), "weight_text": r.get("weight_text"), "weight_g": r.get("weight_g"),
                      "grams": grams, "grams_status": "ok" if grams is not None else "unspecified", "conflicts": [], "note": r.get("note"),
                      "pos_item_id": None, "pos_name_ar": None, "in_pos_menu": False, "active": True})
    if "U01" in keys:
        items.append({"item_id": "U01", "kind": "upgrade", "name_ar": None, "name_en": "Combo upgrade (fries + soft drink)", "names_en": ["Combo upgrade (fries + soft drink)", "Make it a combo"],
                      "match_names_ar": [], "category": "upgrade", "price_usd": up["price_usd"], "was_price_usd": None, "weight_text": None, "weight_g": 0,
                      "grams": {p: 0.0 for p in PROTEINS}, "grams_status": "ok", "conflicts": [], "note": up.get("note"),
                      "pos_item_id": None, "pos_name_ar": None, "in_pos_menu": False, "active": True})
    for u in unlisted:
        items.append({"item_id": u["item_id"], "kind": "unlisted", "name_ar": u["name_ar"].strip(), "name_en": u.get("name_en") or None, "names_en": [u["name_en"]] if u.get("name_en") else [],
                      "match_names_ar": [], "category": u.get("category") or "unlisted", "price_usd": u.get("price_usd"), "was_price_usd": None, "weight_text": None, "weight_g": 0,
                      "grams": {p: float(u["grams"][p]) for p in PROTEINS}, "grams_status": "ok", "conflicts": [], "note": u.get("evidence"),
                      "pos_item_id": None, "pos_name_ar": None, "in_pos_menu": False, "active": True})
    if unlisted:
        warnings.append(f"{len(unlisted)} items are not on the cheat sheet; the manager added them in {ALIASES_JSON} (kind 'unlisted'): " + ", ".join(f"{u['item_id']} {u['name_ar']}" for u in unlisted))
    by_key = {i["item_id"]: i for i in items}

    # names: the Arabic name on the sheet, a name plus its own weight ("صحن شاورما دجاج 200 غرام"), and the aliases the manager confirmed
    for i in items:
        names = []
        if i["name_ar"]:
            names.append(i["name_ar"])
            if i["weight_g"] and not any(ch.isdigit() for ch in i["name_ar"]) and float(i["weight_g"]).is_integer() and i["grams"] and sum(1 for p in PROTEINS if i["grams"][p]) == 1:
                names.append(f"{i['name_ar']} {int(i['weight_g'])} غرام")
        i["match_names_ar"] = names
    own = {i["item_id"]: list(i["match_names_ar"]) for i in items}        # the sheet's own names, before any alias
    for a in confirmed:
        by_key[a["item"]]["match_names_ar"].append(a["ar"].strip())

    # what the manager ruled about a bare POS line the sheet leaves ambiguous: the line is the decided item, never the item it was confused with,
    # and its weight is the manager's (the sheet's own figure is kept in the conflicts so the difference stays visible)
    decided = set()
    for d in decisions:
        key, tgt = normalize(d["ar"]), by_key[d["item"]]
        decided.add(key)
        for other in d.get("not_item") or []:
            by_key[other]["match_names_ar"] = [n for n in by_key[other]["match_names_ar"] if normalize(n) != key]
        want = {p: float(d["grams"].get(p, 0)) for p in PROTEINS}
        total = int(sum(want.values())) if float(sum(want.values())).is_integer() else sum(want.values())
        if tgt["grams"] != want:
            tgt["conflicts"].append({"field": "grams", "detail": f"the sheet says {tgt['weight_text']}" + (f" ({tgt['grams']})" if tgt["grams"] is not None else " and does not name the meat") + f"; the manager's ruling for the POS line '{d['ar']}' is {want} ({d.get('confirmed_on')}): {d.get('decision')}"})
        tgt["sheet_grams"], tgt["grams"], tgt["grams_status"], tgt["decision"] = tgt["grams"], want, "ok", d.get("decision")
        tgt["match_names_ar"] = [n for n in tgt["match_names_ar"] if not (normalize(n).startswith(key + " ") and normalize(n).endswith("غرام") and normalize(n) != key)] + [f"{d['ar']} {total} غرام"]

    # the same Arabic name on two entries with different recipes: the sheet itself says to confirm which is in use
    groups = {}
    for i in items:
        if i["name_ar"]:
            groups.setdefault(normalize(i["name_ar"]), []).append(i)
    for gkey, g in groups.items():
        if gkey in decided:
            continue
        if len(g) > 1 and len({tuple(sorted((x["grams"] or {}).items())) for x in g}) > 1:
            for x in g:
                others = [y for y in g if y is not x]
                x["grams_status"] = "ambiguous"
                x["conflicts"].append({"field": "name", "detail": f"the Arabic name is also used by {', '.join(y['item_id'] + ' (' + str(y['weight_text']) + ')' for y in others)}; "
                                       "the sheet says to confirm which format is in use before prepping"})
                warnings.append(f"{x['name_ar']} ({x['item_id']}, {x['weight_text']}) shares its name with {', '.join(y['item_id'] + ' (' + str(y['weight_text']) + ')' for y in others)}: ambiguous until the manager confirms")

    # the app's menu export: ids to link and disagreements to report; never a source of names, prices or grams
    pos, why = _read_pos(restaurant_root)
    uncovered, pos_info = [], None
    if pos is None:
        warnings.append(why)
    else:
        prow, pday = pos
        pos_info = {"date": pday, "rows": len(prow)}
        by_norm = {}
        for r in prow:
            by_norm.setdefault(normalize(r["pos_name_ar"]), []).append(r)
        covered = set()
        for i in items:
            for n in i["match_names_ar"]:
                covered.add(normalize(n))
        for i in items:
            hit = next((by_norm[normalize(n)] for n in own[i["item_id"]] if normalize(n) in by_norm), None)
            if hit and len(hit) == 1 and len(groups.get(normalize(i["name_ar"]), [])) == 1:
                r = hit[0]
                i["pos_item_id"], i["pos_name_ar"], i["in_pos_menu"] = r["id"], r["pos_name_ar"], True
                eq = _grams_equal(i["grams"], r)
                if eq is False:
                    i["conflicts"].append({"field": "grams", "detail": f"the app's menu has different meat weights for this item ({r.get('grams_expr') or {'chicken': r.get('chicken_g'), 'beef': r.get('beef_g'), 'sojok': r.get('sausage_g')}}); the sheet's weights are used"})
                    if i["grams_status"] == "ok":
                        i["grams_status"] = "differs_from_app"
                    warnings.append(f"{i['name_ar']} ({i['item_id']}): the sheet says {i['weight_text']}, the app's menu says {r.get('grams_expr') or {'chicken': r.get('chicken_g'), 'beef': r.get('beef_g'), 'sojok': r.get('sausage_g')}}")
            elif hit and len(hit) == 1 and i["grams_status"] == "ambiguous":
                i["conflicts"].append({"field": "pos_link", "detail": "the app has one item with this name; it is not linked because two sheet entries share the name"})
        uncovered = [{"pos_item_id": r["id"], "name_ar": r["pos_name_ar"]} for n, lst in by_norm.items() for r in lst if n not in covered]
        if uncovered:
            warnings.append(f"{len(uncovered)} items of the app's menu are not named on the cheat sheet, so receipt lines with those names go to a human (see uncovered_pos_items)")
    if proposed:
        warnings.append(f"{len(proposed)} receipt spellings are proposed but not confirmed in {ALIASES_JSON}; they are not used")
    cons = sum(1 for i in items if i["conflicts"])
    src = doc["source"]
    return {"tool": "get_menu_glossary", "ok": True, "status": "ok", "as_of": src.get("verified_on_sheet"),
            "source": {"file": src.get("file"), "sha256": src.get("sha256"), "pdf_verified": pdf_ok, "pages": src.get("pages"),
                       "extraction": os.path.join(ref_dir, CHEAT_JSON), "aliases": {"confirmed": len(confirmed), "proposed": len(proposed), "decisions": len(decisions), "unlisted_items": len(unlisted), "ignored_line_rules": len(ignored_rules)},
                       "pos_export": pos_info},
            "counts": {"items": len(items), "unlisted": len(unlisted), "customer_menu": sum(1 for i in items if i["kind"] == "customer"), "kitchen_portions": sum(1 for i in items if i["kind"] == "kitchen"),
                       "with_price": sum(1 for i in items if i["price_usd"] is not None), "ambiguous": sum(1 for i in items if i["grams_status"] == "ambiguous"),
                       "differs_from_app": sum(1 for i in items if i["grams_status"] == "differs_from_app"), "unspecified": sum(1 for i in items if i["grams_status"] == "unspecified"),
                       "uncovered_pos_items": len(uncovered)},
            "warnings": warnings, "items": items, "uncovered_pos_items": uncovered, "proposed_aliases": proposed, "ignored_line_rules": ignored_rules}


def _signature_of(ref_dir, restaurant_root):
    paths = [os.path.join(ref_dir, CHEAT_JSON), os.path.join(ref_dir, ALIASES_JSON)]
    try:
        doc = json.load(open(paths[0], encoding="utf-8"))
        paths.append(os.path.join(ref_dir, (doc.get("source") or {}).get("file") or ""))
    except (OSError, ValueError):
        pass
    base = os.path.join(restaurant_root, "app", "backup")
    try:
        days = sorted((d for d in os.listdir(base) if _DATE_DIR.match(d)), reverse=True)
        if days:
            paths.append(os.path.join(base, days[0], "menu_items.json"))
    except OSError:
        pass
    return tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size) if os.path.isfile(p) else (p, None, None) for p in paths)


def load_glossary(ref_dir=None, root=None):
    """The full glossary dict (or raises GlossaryError). Re-read automatically when any source file changes."""
    ref_dir, root = ref_dir or REF_DIR, root or RESTAURANT_ROOT
    sig = _signature_of(ref_dir, root)
    hit = _cache.get((ref_dir, root))
    if hit and hit[0] == sig:
        return hit[1]
    g = _build(ref_dir, root)
    _cache[(ref_dir, root)] = (sig, g)
    return g


def get_menu_glossary(category=None, search=None, pos_only=False, ref_dir=None, root=None):
    """The menu, from the staff cheat sheet (the definitive source). Returns {"ok", "status", "as_of", "source", "counts", "warnings",
    "items", "uncovered_pos_items", "proposed_aliases"}; never raises. category / search / pos_only only narrow the item list.
    ref_dir and root are for code and tests (where the cheat sheet extraction and the Demo Bistro folder are)."""
    try:
        g = load_glossary(ref_dir, root)
    except GlossaryError as e:
        return RT._fail("get_menu_glossary", [str(e)], status=e.status)
    except Exception as e:                                           # an unreadable file must not crash the agent
        return RT._fail("get_menu_glossary", [f"the menu could not be read: {e}"], status="no_menu")
    items = g["items"]
    if pos_only:
        items = [i for i in items if i["in_pos_menu"]]
    if category:
        c = normalize(category)
        items = [i for i in items if c == normalize(i.get("kind") or "") or (c and c in normalize(i.get("category") or ""))]
    if search:
        s = normalize(search)
        items = [i for i in items if s and any(s in normalize(x or "") for x in [*i["match_names_ar"], *i["names_en"]])]
    out = dict(g, items=[dict(i) for i in items])
    out["counts"] = dict(g["counts"], items=len(items), customer_menu=sum(1 for i in items if i["kind"] == "customer"), kitchen_portions=sum(1 for i in items if i["kind"] == "kitchen"),
                         with_price=sum(1 for i in items if i["price_usd"] is not None), ambiguous=sum(1 for i in items if i["grams_status"] == "ambiguous"),
                         differs_from_app=sum(1 for i in items if i["grams_status"] == "differs_from_app"), unspecified=sum(1 for i in items if i["grams_status"] == "unspecified"))
    return out


# ══ match_menu_item ═════════════════════════════════════════════════════════════════════════════════════════════════════════

def _thresholds(thresholds):
    th = dict(RT.DEFAULT_THRESHOLDS)
    for k, v in (thresholds or {}).items():
        if k not in ("suggested", "margin"):
            raise ValueError(f"threshold {k!r} cannot be changed: 100 always means an exact match (only 'suggested' and 'margin' can be set)")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"threshold {k} must be a number")
        th[k] = v
    if not 50 <= th["suggested"] <= 99:
        raise ValueError("threshold 'suggested' must be between 50 and 99")
    if not 0 <= th["margin"] <= 20:
        raise ValueError("threshold 'margin' must be between 0 and 20")
    return th


def _index(glossary):
    idx = []
    for it in glossary["items"]:
        variants = []
        for n, ar in enumerate(it.get("match_names_ar") or ([it["name_ar"]] if it.get("name_ar") else [])):
            variants.append((_prep(ar), "name_ar" if n == 0 else "alias", False))
        for en in it.get("names_en") or []:
            variants.append((_prep(en), "name_en", True))
        variants = [v for v in variants if v[0]["norm"]]
        if variants:
            idx.append((it, variants))
    return idx


def rank_item_name(text, glossary):
    """Every glossary item scored against `text`, best first: [(score, item, via, reasons)]. Deterministic."""
    q = _prep(text)
    if len(q["norm"]) < 2:
        return []
    ranked = []
    for it, variants in _index(glossary):
        best = max(((*_score_variant(q, v, english), src, v) for v, src, english in variants), key=lambda t: (t[0], {"name_ar": 2, "alias": 1}.get(t[2], 0)))
        # a weight in the text ("200 غرام") that the item's name carries too breaks a tie: the 200 g platter, not the plain small shawarma
        same_number = 1 if q["sig"]["number"] and best[3]["sig"]["number"] == q["sig"]["number"] else 0
        ranked.append((best[0], it, best[2], best[1], same_number))
    ranked.sort(key=lambda r: (-r[0], -r[4], r[1]["name_ar"] or "", r[1]["item_id"]))
    return [r[:4] for r in ranked]


def _cand(score, it, via):
    return {"item_id": it["item_id"], "name_ar": it["name_ar"], "name_en": it["name_en"], "score": score, "kind": it["kind"],
            "grams_status": it["grams_status"], "weight_text": it.get("weight_text"), "in_pos_menu": it["in_pos_menu"], "via": via}


def _match_one(text, glossary, th):
    if not isinstance(text, str):
        return {"input": text if isinstance(text, (int, float)) else None, "normalized": "", "status": "needs human", "score": 0,
                "match": None, "candidates": [], "reasons": ["the input is not text"], "package_match": {"item_id": None, "name_ar": "", "score": 0, "candidates": []}}
    res = {"input": text, "normalized": normalize(text), "status": "needs human", "score": 0, "match": None, "candidates": [], "reasons": []}
    if len(text) > MAX_TEXT_CHARS:
        res["reasons"].append(f"the text is longer than {MAX_TEXT_CHARS} characters; an item name is expected")
    elif len(res["normalized"]) < 2:
        res["reasons"].append("there is no readable text to match")
    else:
        ranked = rank_item_name(text, glossary)
        cands = [_cand(s, it, via) for s, it, via, _ in ranked if s >= CANDIDATE_FLOOR][:3]
        res["candidates"] = cands
        if not cands:
            res["reasons"].append("nothing in the menu is close to this text")
        else:
            best, reasons = cands[0], ranked[0][3]
            res["match"], res["score"] = best, best["score"]
            rivals = [c for c in cands[1:] if best["score"] - c["score"] <= th["margin"]]
            if rivals:
                res["reasons"] += [f"another item scored within {th['margin']} points: " + "; ".join(f"{c['name_ar'] or c['name_en']} ({c['item_id']}, {c['weight_text'] or 'no weight'}, {c['score']})" for c in rivals)]
            elif best["score"] >= th["matched"]:
                res["status"] = "matched"
            elif best["score"] >= th["suggested"]:
                res["status"] = "suggested"
                res["reasons"].append("confirm this match")
            else:
                res["reasons"].append(f"the best score {best['score']} is below {th['suggested']}")
            res["reasons"] += reasons
            if best["grams_status"] in ("ambiguous", "unspecified") and res["status"] != "needs human":
                res["reasons"].append("the cheat sheet leaves this item's meat weight open, so it is not counted in the meat total until a human settles it")
    res["package_match"] = {"item_id": res["match"]["item_id"] if res["match"] else None, "name_ar": (res["match"]["name_ar"] or "") if res["match"] else "",
                            "score": res["score"], "candidates": [{"item_id": c["item_id"], "name_ar": c["name_ar"], "score": c["score"]} for c in res["candidates"]]}
    return res


def match_menu_item(raw_text=None, raw_texts=None, thresholds=None, glossary=None, ref_dir=None, root=None):
    """Match OCR / vision text against the menu glossary. Give raw_text (one name) or raw_texts (a list of names).

    Each result has status matched (score 100: identical after normalisation), suggested (85-99, confirm) or needs human (everything
    else, including a close rival, a one-word difference such as chicken/beef, or only part of a name). `package_match` is the
    exact block the draft package carries for the line. Never raises."""
    tool = "match_menu_item"
    if raw_text is None and raw_texts is None:
        return RT._fail(tool, ["give raw_text (one item name) or raw_texts (a list of names)"])
    if raw_texts is not None and not isinstance(raw_texts, list):
        return RT._fail(tool, ["raw_texts must be a list of strings"])
    texts = ([raw_text] if raw_text is not None else []) + list(raw_texts or [])
    if len(texts) > MAX_TEXTS:
        return RT._fail(tool, [f"at most {MAX_TEXTS} names per call"])
    try:
        th = _thresholds(thresholds)
    except ValueError as e:
        return RT._fail(tool, [str(e)])
    if glossary is None:
        g = get_menu_glossary(ref_dir=ref_dir, root=root)
        if not g.get("ok"):
            return RT._fail(tool, g["errors"], status="no_menu")
        glossary = g
    results = [_match_one(t, glossary, th) for t in texts]
    counts = {"matched": 0, "suggested": 0, "needs human": 0}
    for r in results:
        counts[r["status"]] += 1
    return {"tool": tool, "ok": True, "thresholds": th, "glossary": {"as_of": glossary.get("as_of"), "items": len(glossary["items"])},
            "counts": {**counts, "lines": len(results)}, "results": results}


# ══ validate_draft_package ══════════════════════════════════════════════════════════════════════════════════════════════════

def _err(code, path, message):
    return {"code": code, "path": path, "message": message}


def _jtype(v):
    return "null" if v is None else "boolean" if isinstance(v, bool) else "number" if isinstance(v, (int, float)) else \
        "string" if isinstance(v, str) else "array" if isinstance(v, list) else "object" if isinstance(v, dict) else type(v).__name__


def _type_ok(v, t):
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "null":
        return v is None
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and (not isinstance(v, float) or v == v and abs(v) != float("inf"))
    if t == "integer":
        return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer())
    return True


def _schema_check(value, schema, path, out):
    """The subset of JSON Schema that draft_package.schema.json uses: type (or list of types), const, enum, pattern, required,
    properties, items. Anything else in the schema file (title, description, default) is documentation and is ignored."""
    t = schema.get("type")
    types = [t] if isinstance(t, str) else (t or [])
    if types and not any(_type_ok(value, x) for x in types):
        out.append(_err("SCHEMA_TYPE", path, f"must be {' or '.join(types)}, not {_jtype(value)}"))
        return
    if "const" in schema and value != schema["const"]:
        out.append(_err("SCHEMA_CONST", path, f"must be {schema['const']!r}, not {value!r}"))
    if "enum" in schema and value not in schema["enum"]:
        out.append(_err("SCHEMA_ENUM", path, f"must be one of {schema['enum']}, not {value!r}"))
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        out.append(_err("SCHEMA_PATTERN", path, f"{value!r} does not match the required form {schema['pattern']}"))
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                out.append(_err("SCHEMA_REQUIRED", f"{path}.{k}" if path else k, "is required and missing"))
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                _schema_check(value[k], sub, f"{path}.{k}" if path else k, out)
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, v in enumerate(value):
            _schema_check(v, schema["items"], f"{path}[{i}]", out)


def _d2(v):
    return RT.rnd(RT.to_dec(v, "value"), 2)


def _semantic(pkg, glossary, errors, warnings, checks):
    day = pkg.get("business_day")
    day_ok = False
    if isinstance(day, str):
        try:
            datetime.strptime(day, "%Y-%m-%d")
            day_ok = True
        except ValueError:
            errors.append(_err("BUSINESS_DAY_INVALID", "business_day", f"{day} is not a real calendar date"))
    did = pkg.get("draft_id")
    if isinstance(did, str) and not RT.DRAFT_ID.match(did):
        errors.append(_err("DRAFT_ID_INVALID", "draft_id", f"{did!r} must look like DRAFT-2026-09-20-001 (DRAFT-<business day>-<number>)"))
    if isinstance(did, str) and RT.DRAFT_ID.match(did) and day_ok and did[6:16] != day:
        errors.append(_err("DRAFT_ID_DAY_MISMATCH", "draft_id", f"the draft id names {did[6:16]} but business_day is {day}"))
    if pkg.get("day_status") == "ended" and not str(pkg.get("correction_reason") or "").strip():
        errors.append(_err("CORRECTION_REASON_MISSING", "correction_reason", "the day is ended, so the app's correction mode needs a reason"))
    rate = pkg.get("rate_lbp_per_usd")
    if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate <= 0:
        errors.append(_err("RATE_NOT_POSITIVE", "rate_lbp_per_usd", "the exchange rate must be above zero"))
    bs_rate = (pkg.get("branch_settings") or {}).get("rate_lbp_per_usd")
    if isinstance(rate, (int, float)) and isinstance(bs_rate, (int, float)) and rate != bs_rate:
        warnings.append(_err("RATE_DIFFERS_FROM_BRANCH", "rate_lbp_per_usd", f"the draft uses {rate:,.0f} but the branch setting is {bs_rate:,.0f}"))
    sources = pkg.get("sources")
    if isinstance(sources, list):
        if not sources:
            errors.append(_err("NO_SOURCES", "sources", "a draft must list the files it was read from"))
        for i, s in enumerate(sources):
            if isinstance(s, dict) and s.get("sha256") is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", str(s["sha256"])):
                errors.append(_err("SOURCE_HASH_INVALID", f"sources[{i}].sha256", "is not a SHA-256 (64 hex characters)"))
        if sources and all(isinstance(s, dict) and not s.get("sha256") for s in sources):
            warnings.append(_err("SOURCES_NOT_HASHED", "sources", "no source file has a SHA-256, so the draft cannot be tied to the exact files"))

    sbi = (pkg.get("extraction") or {}).get("summary_by_items")
    lines = (sbi or {}).get("lines") if isinstance(sbi, dict) else None
    if not isinstance(lines, list):
        errors.append(_err("NO_ITEM_LINES", "extraction.summary_by_items.lines", "the package has no item lines to review"))
        return
    if not lines:
        warnings.append(_err("NO_ITEM_LINES", "extraction.summary_by_items.lines", "the list of item lines is empty"))
    th = dict(RT.DEFAULT_THRESHOLDS)
    ids = {i["item_id"]: i for i in glossary["items"]}
    seen_nums, amounts, checked_matches = set(), [], 0
    for i, line in enumerate(lines):
        p = f"extraction.summary_by_items.lines[{i}]"
        if not isinstance(line, dict):
            continue
        n = line.get("line")
        if isinstance(n, (int, float)) and not isinstance(n, bool):
            if n < 1 or n != int(n):
                errors.append(_err("LINE_NUMBER_INVALID", f"{p}.line", "line numbers are whole numbers from 1"))
            elif n in seen_nums:
                errors.append(_err("LINE_NUMBER_DUPLICATE", f"{p}.line", f"line {int(n)} appears more than once"))
            seen_nums.add(n)
        name = line.get("name_ar")
        if isinstance(name, str) and not name.strip():
            errors.append(_err("LINE_NAME_EMPTY", f"{p}.name_ar", "the printed name is empty"))
        for k in ("qty", "amount", "name_ar"):
            alt = line.get(f"{k}_alt")
            if line.get("readings_agree") is True and f"{k}_alt" in line and alt is not None and not (normalize(alt) == normalize(line.get(k)) if k == "name_ar" else alt == line.get(k)):
                settled = (k == "name_ar" and line.get("name_settled_by_menu") is True and isinstance(line.get("match"), dict) and line["match"].get("score") == 100)
                if not settled:
                    errors.append(_err("READINGS_INCONSISTENT", p, f"readings_agree is true but {k} and {k}_alt differ" + ("; a name difference is only settled when the first reading is an exact menu name (score 100)" if k == "name_ar" else "")))
        if line.get("readings_agree") is False:
            if not any(f"{k}_alt" in line for k in ("qty", "amount", "name_ar")):
                errors.append(_err("SECOND_READING_MISSING", p, "the readings disagree but no second reading (qty_alt, amount_alt or name_ar_alt) is given"))
            else:
                warnings.append(_err("READINGS_DISAGREE", p, f"line {n}: the two readings disagree (the review sheet shows both)"))
        if isinstance(line.get("qty"), (int, float)) and not isinstance(line.get("qty"), bool) and line["qty"] < 0:
            warnings.append(_err("NEGATIVE_QTY", f"{p}.qty", "a negative quantity (a return?) needs a human"))
        amt = line.get("amount")
        amounts.append(RT.to_dec(amt, "amount") if isinstance(amt, (int, float)) and not isinstance(amt, bool) else None)

        # ── matched items ──
        if "match" not in line:
            errors.append(_err("MATCH_MISSING", f"{p}.match", "no match block: run match_menu_item for this line (item_id null is allowed when nothing matched)"))
            continue
        m = line["match"]
        if not isinstance(m, dict):
            continue
        checked_matches += 1
        mid, score = m.get("item_id"), m.get("score")
        if score is not None and (not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 100):
            errors.append(_err("MATCH_SCORE_INVALID", f"{p}.match.score", "a score is a number from 0 to 100"))
            score = None
        for j, c in enumerate(m.get("candidates") or []):
            if isinstance(c, dict) and c.get("item_id") not in ids:
                errors.append(_err("MATCH_CANDIDATE_UNKNOWN", f"{p}.match.candidates[{j}]", f"{c.get('item_id')!r} is not in the menu glossary"))
        if mid is None:
            if isinstance(score, (int, float)) and score >= th["suggested"]:
                errors.append(_err("MATCH_INCONSISTENT", f"{p}.match", f"no item is named but the score is {score}"))
            continue
        if mid not in ids:
            errors.append(_err("MATCH_ITEM_UNKNOWN", f"{p}.match.item_id", f"{mid!r} is not in the menu glossary (an item that does not exist)"))
            continue
        if m.get("name_ar") is not None and normalize(m["name_ar"]) not in {normalize(x) for x in (ids[mid]["match_names_ar"] or [""])} | {normalize(ids[mid]["name_ar"] or ""), ""}:
            errors.append(_err("MATCH_NAME_MISMATCH", f"{p}.match.name_ar", f"{m['name_ar']!r} is not the glossary name of {mid} ({ids[mid]['name_ar']!r})"))
        if not isinstance(score, (int, float)):
            errors.append(_err("MATCH_SCORE_MISSING", f"{p}.match.score", "an item is named but there is no score"))
            continue
        texts = [t for t in (name, line.get("name_ar_alt")) if isinstance(t, str) and t.strip()]
        verdicts = []                                    # per spelling: what the claimed item scores there, and the best any item scores there
        for t in texts:
            ranked = rank_item_name(t, glossary)
            by_id = {it["item_id"]: s_ for s_, it, _, _ in ranked}
            verdicts.append((by_id.get(mid, 0), ranked[0][0] if ranked else 0))
        if not any(claim >= score for claim, _ in verdicts):
            errors.append(_err("MATCH_SCORE_INFLATED", f"{p}.match.score", f"the draft says {score} but the matching code gives {max([c for c, _ in verdicts] + [0])} for that item and this name"))
        elif not any(claim >= score and top <= claim for claim, top in verdicts):
            best_top = max(top for claim, top in verdicts if claim >= score)
            errors.append(_err("MATCH_NOT_BEST", f"{p}.match.item_id", f"another item scores higher ({best_top}) than the one chosen on the spelling it was matched from"))
    checks["match_blocks_checked"] = checked_matches

    # ── the math: recomputed with the same code, never trusted ──
    comp = pkg.get("computed") if isinstance(pkg.get("computed"), dict) else {}
    amounts_printed = not (isinstance(sbi, dict) and sbi.get("amounts_printed") is False)
    sum_lines = sum(amounts, Decimal(0)) if amounts and all(a is not None for a in amounts) else (Decimal(0) if not amounts else None)
    printed = sbi.get("printed_total") if isinstance(sbi, dict) else None

    def compare(key, expected, label):
        got = comp.get(key)
        if got is None:
            errors.append(_err("MATH_MISSING", f"computed.{key}", f"{label} must be in the computed block ({_d2(expected)})"))
        elif isinstance(got, (int, float)) and not isinstance(got, bool) and _d2(got) != _d2(expected):
            errors.append(_err("COMPUTED_WRONG", f"computed.{key}", f"the draft says {_d2(got)} but the code gets {_d2(expected)} for {label}"))

    if not amounts_printed:
        warnings.append(_err("AMOUNTS_NOT_PRINTED", "extraction.summary_by_items", "the POS item list prints quantities only: there is no sum of lines to check"))
        if comp.get("sum_of_lines") is not None or comp.get("line_gap") is not None:
            errors.append(_err("COMPUTED_FROM_INCOMPLETE", "computed.sum_of_lines", "a sum of lines is given although the item list has no amounts"))
    elif sum_lines is None:
        warnings.append(_err("LINES_NOT_TOTALLED", "extraction.summary_by_items.lines", "a line has no amount, so the lines cannot be totalled"))
        if comp.get("sum_of_lines") is not None:
            errors.append(_err("COMPUTED_FROM_INCOMPLETE", "computed.sum_of_lines", "a sum is given although a line has no amount"))
    else:
        compare("sum_of_lines", sum_lines, "the sum of the lines")
        if isinstance(printed, (int, float)) and not isinstance(printed, bool):
            compare("printed_total", RT.to_dec(printed, "printed_total"), "the printed total")
            compare("line_gap", sum_lines - RT.to_dec(printed, "printed_total"), "the gap (sum of lines − printed total)")
            if _d2(sum_lines - RT.to_dec(printed, "printed_total")) != 0:
                warnings.append(_err("LINE_GAP", "computed.line_gap", f"the lines add up to {RT.money(sum_lines)} but the printed total is {RT.money(RT.to_dec(printed, 'printed_total'))}: the review sheet will block this"))
        else:
            warnings.append(_err("PRINTED_TOTAL_MISSING", "extraction.summary_by_items.printed_total", "no printed total was read"))
    checks["sum_of_lines"] = None if sum_lines is None else float(_d2(sum_lines))

    cash = pkg.get("cash") if isinstance(pkg.get("cash"), dict) else None
    if cash is not None:
        res = RT.reconcile_cash(carried_cash=cash.get("old_cash"), pos_sales=cash.get("pos_sales"), wallet=cash.get("wallet"), expenses=cash.get("expenses"),
                                transfer_to_admin=cash.get("transfer_to_admin"), actual_cash_count=cash.get("actual_cash_count"),
                                counted_usd=cash.get("counted_usd"), counted_lbp=cash.get("counted_lbp"),
                                rate_lbp_per_usd=rate if isinstance(rate, (int, float)) and not isinstance(rate, bool) else None,
                                branch_rate_lbp_per_usd=bs_rate if isinstance(bs_rate, (int, float)) and not isinstance(bs_rate, bool) else None)
        checks["cash_status"] = res.get("status")
        if res.get("status") == "invalid":
            errors.append(_err("CASH_INPUT_INVALID", "cash", "; ".join(res.get("errors") or [])))
        elif res.get("status") == "incomplete":
            warnings.append(_err("CASH_INCOMPLETE", "cash", "the cash check cannot be completed: " + "; ".join(res.get("errors") or res.get("missing") or ["something is missing"])))
            if res.get("ok"):
                compare("expected", Decimal(str(res["expected_usd"])), "the expected cash")
            for k in ("expected", "counted", "diff"):
                if not (k == "expected" and res.get("ok")) and comp.get(k) is not None:
                    errors.append(_err("COMPUTED_FROM_INCOMPLETE", f"computed.{k}", "a cash figure is given although the cash check is incomplete"))
        else:
            for f in res.get("flags") or []:
                warnings.append(_err(f["code"], "cash", f["message"]))
            compare("expected", Decimal(str(res["expected_usd"])), "the expected cash")
            compare("counted", Decimal(str(res["counted_usd"])), "the counted cash")
            compare("diff", Decimal(str(res["variance_usd"])), "the cash difference (counted − expected)")
    else:
        warnings.append(_err("CASH_BLOCK_MISSING", "cash", "no cash block: the cash check cannot be verified"))


def validate_draft_package(draft_id=None, package=None, glossary=None, ref_dir=None, root=None):
    """Check a compiled draft package before the review sheet is rendered. Give draft_id (a saved package) or package.

    Returns {"ok" (the check ran), "valid", "status": valid|invalid, "errors": [...], "warnings": [...], "checks": {...}}.
    valid means: schema v0 satisfied; draft id / day / sources sane; every line carries a match block whose items exist in the glossary
    with scores the matching code agrees with; and the computed block equals what the code computes from the package's own numbers.
    Warnings (disagreeing readings, a gap against the printed total, an incomplete cash count) do not make a package invalid: the review
    sheet is where they are shown. Never raises, never writes."""
    tool = "validate_draft_package"
    if package is None and draft_id:
        if not RT.DRAFT_ID.match(str(draft_id)):
            return RT._fail(tool, [f"draft_id must look like DRAFT-2026-09-20-001, not {draft_id!r}"])
        path = os.path.join(RT.DRAFTS_DIR, f"{draft_id}.json")
        if not os.path.exists(path):
            return RT._fail(tool, [f"no saved package for {draft_id}"])
        try:
            with open(path, encoding="utf-8") as f:
                package = json.load(f)
        except (OSError, ValueError) as e:
            return RT._fail(tool, [f"the saved package for {draft_id} cannot be read: {e}"])
    if isinstance(package, str):
        try:
            package = json.loads(package)
        except ValueError:
            return RT._fail(tool, ["the package is not valid JSON"])
    if not isinstance(package, dict):
        return RT._fail(tool, ["give a draft_id or a package"])
    try:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            schema = json.load(f)
    except (OSError, ValueError) as e:
        return RT._fail(tool, [f"the draft schema cannot be read ({SCHEMA_PATH}): {e}"], status="no_schema")

    errors, warnings, checks = [], [], {"schema": (schema.get("properties", {}).get("schema") or {}).get("const", "draft_package/v0")}
    _schema_check(package, schema, "", errors)
    checks["schema_errors"] = len(errors)
    if glossary is None:
        g = get_menu_glossary(ref_dir=ref_dir, root=root)
        if not g.get("ok"):
            errors.append(_err("GLOSSARY_UNAVAILABLE", "", "the menu glossary cannot be read, so matched items cannot be verified: " + "; ".join(g.get("errors") or [])))
            glossary = None
        else:
            glossary = g
    if glossary is not None:
        checks["glossary_as_of"] = glossary.get("as_of")
        checks["glossary_items"] = len(glossary["items"])
        if not errors:                                   # a structurally broken package would only produce noise below
            try:
                _semantic(package, glossary, errors, warnings, checks)
            except Exception as e:                       # a validator must report, never crash
                errors.append(_err("VALIDATOR_ERROR", "", f"the deeper checks could not run: {e}"))
        else:
            warnings.append(_err("DEEPER_CHECKS_SKIPPED", "", "item matches and math were not checked because the structure is wrong; fix the errors above first"))
    valid = not errors
    return {"tool": tool, "ok": True, "valid": valid, "status": "valid" if valid else "invalid", "draft_id": package.get("draft_id", draft_id),
            "errors": errors, "warnings": warnings, "checks": checks}


# ══ finished replies (what the HUD shows; no model rewords them) ═════════════════════════════════════════════════════════════

def _cell(v):
    return RT._cell("—" if v is None or v == "" else v)


def glossary_markdown(g):
    c = g["counts"]
    src = g["source"]
    out = [f"# Menu glossary (staff cheat sheet, verified {g['as_of']})",
           f"Source: {src['file']} (SHA-256 {src['sha256'][:12]}…, {'matches the extraction' if src['pdf_verified'] else 'PDF not found, extraction not re-checked'}).",
           f"{c['items']} entries: {c['customer_menu']} customer-menu items, {c['kitchen_portions']} kitchen portions" + (f", {c['unlisted']} not on the sheet (added by the manager)" if c.get("unlisted") else "") + f"; {c['with_price']} with a price; "
           f"{c['ambiguous']} ambiguous, {c['differs_from_app']} differ from the app's menu, {c['unspecified']} with no meat weight on the sheet.", ""]
    if g.get("warnings"):
        out += ["## Warnings", *(f"- {w}" for w in g["warnings"]), ""]
    groups = {}
    for i in g["items"]:
        groups.setdefault((i["kind"], i.get("category") or "other"), []).append(i)
    for (kind, cat), lst in groups.items():
        out += [f"## {cat}" + ("" if kind == "customer" else f" ({kind})"),
                "| Id | Arabic | English | Price USD | Meat grams | Flags |", "|---|---|---|---|---|---|"]
        for i in lst:
            gr = i.get("grams")
            parts = [f"{k} {v:g}" for k, v in (gr or {}).items() if v]
            meat = " + ".join(parts) if parts else (i.get("weight_text") if gr is None else None)
            price = f"{i['price_usd']:.2f}" if i["price_usd"] is not None else None
            flags = "; ".join(x for x in [i["grams_status"] if i["grams_status"] != "ok" else "", "in the app's menu" if i["in_pos_menu"] else "", "not on the cheat sheet" if i["kind"] == "unlisted" else ""] if x)
            out.append(f"| {_cell(i['item_id'])} | {_cell(i['name_ar'])} | {_cell(i['name_en'])} | {_cell(price)} | {_cell(meat)} | {_cell(flags)} |")
        out.append("")
    out.append(NOT_ASSERTED)
    return "\n".join(out)


def match_markdown(r):
    rows = []
    for n, x in enumerate(r["results"], 1):
        m = x["match"]
        best = "none" if not m else f"{m['name_ar'] or m['name_en']} ({m['item_id']})"
        alts = "; ".join(f"{c['name_ar'] or c['name_en']} ({c['score']})" for c in x["candidates"][1:])
        note = "; ".join(x["reasons"]) + (f"; also close: {alts}" if alts and x["status"] == "needs human" else "")
        rows.append(f"| {n} | {_cell(x['input'])} | {_cell(best)} | {x['score']} | {x['status']} | {_cell(note)} |")
    c, th = r["counts"], r["thresholds"]
    return "\n".join([f"# Menu matches ({c['lines']} name(s): {c['matched']} matched, {c['suggested']} suggested, {c['needs human']} needs human)",
                      f"Staff cheat sheet verified {r['glossary']['as_of']}, {r['glossary']['items']} entries.", "",
                      "| # | Text as given | Best match | Score | Status | Note |", "|---|---|---|---|---|---|", *rows, "",
                      f"Scores are computed by code: 100 = identical after Arabic normalisation; {th['suggested']}-99 = suggested, confirm it; "
                      f"below {th['suggested']}, or another item within {th['margin']} points = needs a human. A match is never forced.", NOT_ASSERTED])


def validation_markdown(r):
    did = r.get("draft_id") or "no draft id"
    head = ("**VALID**: the package fits the draft schema and its matches and math check out. Warnings below do not block the review sheet."
            if r["valid"] else f"**NOT VALID**: {len(r['errors'])} error(s). Fix them before the review sheet is rendered.")
    out = [f"# Draft package check: {did} ({r['checks'].get('schema', 'draft_package/v0')})", head, ""]
    if r["errors"]:
        out += [f"## Errors ({len(r['errors'])})", "| Where | Code | Problem |", "|---|---|---|"]
        out += [f"| {_cell(e['path'] or 'package')} | {e['code']} | {_cell(e['message'])} |" for e in r["errors"][:MAX_ERRORS_SHOWN]]
        if len(r["errors"]) > MAX_ERRORS_SHOWN:
            out.append(f"| … | | {len(r['errors']) - MAX_ERRORS_SHOWN} more not shown |")
        out.append("")
    if r["warnings"]:
        out += [f"## Warnings ({len(r['warnings'])})", *(f"- {_cell(w['path'] or 'package')}: {w['message']}" for w in r["warnings"][:MAX_ERRORS_SHOWN]), ""]
    ch = r["checks"]
    out += ["## What was checked",
            f"- Structure against the {ch.get('schema', 'draft_package/v0')} schema ({ch.get('schema_errors', 0)} error(s))",
            "- Draft id, business day, day status, rate, sources",
            f"- Item lines and {ch.get('match_blocks_checked', 0)} match block(s) against the menu glossary"
            + (f" dated {ch['glossary_as_of']} ({ch['glossary_items']} items)" if ch.get("glossary_as_of") else " (the glossary could not be read)"),
            "- The computed block against the same code that computes it (sum of lines, printed total, gap, expected / counted / difference cash)",
            "", "Nothing was booked or written. A valid package is not an approved package."]
    return "\n".join(out)


def verbatim_text(name, result):
    """The finished reply for a successful result, or None (errors take the normal path so a model can explain them)."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    if name == "get_menu_glossary":
        return glossary_markdown(result)
    if name == "match_menu_item":
        return match_markdown(result)
    if name == "validate_draft_package":
        return validation_markdown(result)
    return None


# ══ dispatch for tool_executor ══════════════════════════════════════════════════════════════════════════════════════════════

# What a model may pass. root, glossary and thresholds are code-only: a model cannot point the tools at another folder, hand them its
# own glossary, or loosen the scoring.
_MODEL_ARGS = {
    "get_menu_glossary": ("category", "search", "pos_only"),
    "match_menu_item": ("raw_text", "raw_texts"),
    "validate_draft_package": ("draft_id", "package"),
}
_FUNCS = {"get_menu_glossary": get_menu_glossary, "match_menu_item": match_menu_item, "validate_draft_package": validate_draft_package}


def call_tool(name, args):
    """Run one of the three tools from a model-written argument dict. Returns JSON text. Unknown or code-only arguments are
    ignored and reported, never acted on."""
    if name not in _FUNCS:
        return f"Unknown tool: {name}"
    args = dict(args or {})
    allowed = _MODEL_ARGS[name]
    ignored = sorted(k for k in args if k not in allowed)
    result = _FUNCS[name](**{k: v for k, v in args.items() if k in allowed})
    if ignored:
        result["ignored_args"] = ignored
    return json.dumps(result, ensure_ascii=False, indent=1)
