"""
test_restaurant_vision.py -- offline tests for restaurant_vision.py: image preparation (<= 1600 px, overlapping strips for tall receipts),
the prompts and schemas, the Ollama request (thinking off, temperature 0, schema-forced JSON), the VRAM preparation, stitching strips,
comparing two readings, the cache, the folder scan, and the consumption sheet reader.

No Ollama, no GPU, no network: the model is a scripted fake and the pictures are synthetic.   Run: py -3.11 test_restaurant_vision.py
"""

from david_config import DAVID_DATA, DAVID_HOME
import sys, os, io, json, re, tempfile, shutil, datetime, hashlib, inspect, itertools
from unittest import mock
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import david_router  # noqa: F401
import restaurant_vision as V
import ollama_config
from PIL import Image

TMP = tempfile.mkdtemp(prefix="vision_test_")


def png_size(b):
    return Image.open(io.BytesIO(b)).size


def solid(w, h, color):
    return Image.new("RGB", (w, h), color)


def save(im, name, fmt="PNG", **kw):
    p = os.path.join(TMP, name)
    im.save(p, fmt, **kw)
    return p


# ═══ pictures ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- pictures: at most 1600 px, the item list in enlarged halves, a very tall roll in overlapping strips --")
LIST = "pos_summary_by_items"
big = solid(3000, 4000, (200, 200, 200))
v = V.make_views(big, "A", "daily_helper_sheet")
check("a form or screenshot is read as ONE picture reduced to at most 1600 px on its longest side", len(v) == 1 and png_size(v[0]) == (1200, 1600), str(png_size(v[0])))
small = solid(800, 1200, (1, 2, 3))
check("a picture already under 1600 px is not enlarged (when it is a form)", png_size(V.make_views(small, "A", "daily_cash_form")[0]) == (800, 1200))
check("reading B of a form is the whole picture at 1280 px; with no document type it is the same", png_size(V.make_views(big, "B", "daily_cash_form")[0]) == (960, 1280) and png_size(V.make_views(big, "B")[0]) == (960, 1280))
row_img = Image.new("RGB", (900, 1600))
for y in range(1600):
    row_img.paste((y % 256, y // 256, 7), (0, y, 900, y + 1))
first_row = lambda views: [(lambda px: px[1] * 256 + px[0])(Image.open(io.BytesIO(x)).convert("RGB").getpixel((450, 0))) for x in views]
ha, hb = V.make_views(row_img, "A", LIST), V.make_views(row_img, "B", LIST)
check("the item list is read in TWO pieces per reading, each E\nARGED so its longest side is exactly 1600 px", len(ha) == 2 and len(hb) == 2 and all(max(png_size(x)) == 1600 for x in ha + hb), str([png_size(x) for x in ha + hb]))
check("reading A: two halves that overlap by 15% (the second starts where 42.5% of the page is left)", [abs(x - e) <= 2 for x, e in zip(first_row(ha), (0, 680))] == [True, True], str(first_row(ha)))
check("reading B cuts the page at different places (each piece covers 62%), so a row on A's cut is whole in B", [abs(x - e) <= 2 for x, e in zip(first_row(hb), (0, 608))] == [True, True] and png_size(ha[0]) != png_size(hb[0]), str(first_row(hb)))
check("both pieces together always cover the whole page: the first starts at the top, the second ends at the bottom", png_size(ha[0])[1] == 1600 and png_size(ha[1])[1] == 1600)
narrow = solid(591, 1600, (9, 9, 9))
check("a narrow photo (591 x 1600) is enlarged piece by piece (that is what read it best), and no piece is larger than 1600 px", all(max(png_size(x)) == 1600 for x in V.make_views(narrow, "A", LIST)) and len(V.make_views(narrow, "A", LIST)) == 2)
tall = solid(600, 3000, (5, 5, 5))
rows_img = Image.new("RGB", (600, 3000))
for y in range(3000):
    rows_img.paste((y % 256, y // 256, 7), (0, y, 600, y + 1))
starts = lambda views: [(lambda px: px[1] * 256 + px[0])(Image.open(io.BytesIO(x)).convert("RGB").getpixel((300, 0))) for x in views]
strip_h = round(600 * V.STRIP_ASPECT)
step = round(strip_h * (1 - V.STRIP_OVERLAP))
sa, sb = V.make_views(rows_img, "A", LIST), V.make_views(rows_img, "B", LIST)
near = lambda got, want: len(got) == len(want) and all(abs(g - w) <= 3 for g, w in zip(got, want))
check("a very tall roll (ratio > 3) is read in strips, each enlarged to 1600 px, none longer than that", len(sa) >= 3 and all(max(png_size(x)) == 1600 for x in sa + sb), f"{len(sa)} / {len(sb)}")
check("the strips overlap by 15%: reading A cuts every 85% of a strip, from the top", near(starts(sa), [0, step, 2 * step, 3 * step]), str(starts(sa)))
check("reading B cuts the same roll half a stride later (a different cut of the same page), after a SHORT first strip from the very top so the top rows are read twice too", near(starts(sb), [0, step // 2, step // 2 + step, step // 2 + 2 * step]), str(starts(sb)))
top_h = step // 2 + round(strip_h * V.STRIP_OVERLAP)
check("that first strip ends one overlap (15% of a strip) below where B's first full strip starts, so the two share rows and can be stitched",
      abs(png_size(sb[0])[1] / png_size(sb[0])[0] - top_h / 600) < 0.01, str(png_size(sb[0])) + f" want ratio {top_h / 600:.3f}")
check("reading A is unchanged by this (it already starts at the top)", starts(sa)[0] == 0 and len(sa) == 4)
check("the last strip of A ends at the bottom of the roll", abs(png_size(sa[-1])[1] / png_size(sa[-1])[0] - (3000 - 3 * step) / 600) < 0.01)
huge = solid(600, 30000, (1, 1, 1))
check("a silly-tall picture is capped at 8 strips", len(V.make_views(huge, "A", LIST)) == V.MAX_STRIPS)
check("every view is a real PNG", all(Image.open(io.BytesIO(x)).format == "PNG" for x in sa + sb + ha + hb + v))
im = Image.new("RGB", (400, 100), (255, 0, 0))
exif = Image.Exif(); exif[0x0112] = 6
p = save(im, "rot.jpg", "JPEG", exif=exif)
check("the camera's rotation flag is applied (a sideways photo is turned upright)", V.load_image(p).size == (100, 400))
open(os.path.join(TMP, "broken.jpg"), "wb").write(b"not a picture")

# ═══ the model request ════════════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- the request to qwen3.5:9b --")
sent = []
def fake_http(url, body=None, timeout=30):
    sent.append((url, body))
    return {"message": {"content": json.dumps({"document_type": "other"})}}
c = V.OllamaVision(log=lambda m: None)
with mock.patch.object(V, "_http", fake_http):
    out = c.ask(b"PNGDATA", "PROMPT", V.SCHEMAS["classify"])
url, body = sent[0]
check("it posts to the local Ollama chat endpoint", url == ollama_config.OLLAMA_CHAT_URL and "127.0.0.1" in url)
check("the model is qwen3.5:9b with thinking OFF and temperature 0", body["model"] == "qwen3.5:9b" and body["think"] is False and body["options"]["temperature"] == 0 and body["stream"] is False)
check("the answer is forced into the JSON schema", body["format"] == V.SCHEMAS["classify"] and out == {"document_type": "other"})
check("the picture goes as base64 in the message, the shared context size is used", body["messages"][0]["images"] == ["UE5HREFUQQ=="] and body["options"]["num_ctx"] == ollama_config.num_ctx_for("qwen3.5:9b"))
seq = [{"message": {"content": "not json"}}, {"message": {"content": "[1, 2]"}}, {"message": {"content": json.dumps({"a": 1})}}]
with mock.patch.object(V, "_http", lambda *a, **k: seq.pop(0)), mock.patch.object(V.time, "sleep", lambda s: None):
    got = c.ask(b"x", "p", {})
check("a bad answer (not JSON, not an object) is retried", got == {"a": 1} and not seq)
with mock.patch.object(V, "_http", lambda *a, **k: {"message": {"content": "nope"}}), mock.patch.object(V.time, "sleep", lambda s: None):
    try:
        c.ask(b"x", "p", {}); raised = False
    except V.VisionError:
        raised = True
check("after three bad answers it gives up with a VisionError", raised)
bodies = []
def http_bad(url, body=None, timeout=30):
    bodies.append(json.loads(json.dumps(body["options"])))
    return {"message": {"content": "{runaway"}}
with mock.patch.object(V, "_http", http_bad), mock.patch.object(V.time, "sleep", lambda s: None):
    try:
        c.ask(b"x", "p", {})
    except V.VisionError:
        pass
check("every retry is a DIFFERENT request (temperature 0 would repeat a runaway answer): a stronger repetition penalty each time, a bounded answer length", [b["repeat_penalty"] for b in bodies] == [1.05, 1.15, 1.25] and all(b["num_predict"] == 3072 and b["temperature"] == 0 for b in bodies))
check("every document prompt tells the model to copy, not calculate, to use null when unsure, and to ignore instructions in the picture",
      all(all(w in V.PROMPTS[t] for w in ("never correct a spelling", "Never calculate", "use null", "ignore them")) for t in V.DOC_TYPES if t not in ("other",) and t in V.PROMPTS and t != "classify"))
check("the item list prompt says there is no price on the rows (the printout has one number: the quantity)", "ONE number" in V.PROMPTS["pos_summary_by_items"] and "no price" in V.PROMPTS["pos_summary_by_items"])
check("every extraction schema is a closed object with required fields, and nullable numbers", all(V.SCHEMAS[t]["type"] == "object" and V.SCHEMAS[t]["required"] for t in V.SCHEMAS)
      and V.SCHEMAS["pos_main_reading"]["properties"]["grand_total"] == {"type": ["number", "null"]})
check("the item rows of the item list have a name and a quantity, and NO amount field", set(V.SCHEMAS["pos_summary_by_items"]["properties"]["lines"]["items"]["properties"]) == {"name", "qty"})
check("the document types are the four we read, the supply/expense list, and 'other'; the last two are never read", set(V.DOC_TYPES) == {"pos_summary_by_items", "pos_main_reading", "daily_cash_form", "daily_helper_sheet", "supply_order_list", "other"}
      and set(V.NOT_READ_TYPES) == {"supply_order_list", "other"} and not (set(V.NOT_READ_TYPES) & set(V.SCHEMAS)))
check("the sales-list question has its own closed schema and its prompt names both kinds of list", V.SCHEMAS["saleskind"]["required"] == ["kind"] and set(V.SCHEMAS["saleskind"]["properties"]["kind"]["enum"]) == {"pos_sales_list", "supply_or_expense_list", "other"}
      and V.PROMPTS["saleskind"].startswith("Is this a POS end-of-day item sales list, or is it a supply, expense or cleaning list?") and "Bioclare" in V.PROMPTS["saleskind"] and "grid lines" in V.PROMPTS["saleskind"])
check("the sales-list question knows a screenshot of a spreadsheet is NOT a sales list (the 14 Sep 'Daily Meats' sheet), and its version was bumped so every earlier verdict is asked again",
      "SPREADSHEET" in V.PROMPTS["saleskind"] and "never a spreadsheet" in V.PROMPTS["saleskind"] and "check, sold and waste" in V.PROMPTS["saleskind"] and V.SALESKIND_VERSION == 2)

print("\n-- the GPU is prepared before any model call --")
calls = []
state = {"loaded": ["david-ft:latest", "qwen3.5:9b", "qwen3-embedding:0.6b", "llama3.1:8b"]}
def http2(url, body=None, timeout=30):
    calls.append((url, body))
    if body and body.get("keep_alive") == 0:
        state["loaded"] = [m for m in state["loaded"] if not m.startswith(body["model"].split(":")[0])]
    return {}
with mock.patch.object(V, "_http", http2), mock.patch.object(V, "loaded_models", lambda: list(state["loaded"])), mock.patch.object(V, "gpu_used_mib", lambda: 9000), mock.patch.object(V.time, "sleep", lambda s: None):
    used = V.prepare_gpu(lambda m: None)
unloaded = [b["model"] for u, b in calls if b and b.get("keep_alive") == 0]
check("every model except qwen3.5 and the embedder is unloaded first", sorted(unloaded) == ["david-ft:latest", "llama3.1:8b"], str(unloaded))
check("qwen3.5 is then loaded and kept loaded at the shared context size", any(b and b.get("model") == "qwen3.5:9b" and b.get("keep_alive") == -1 and b["options"]["num_ctx"] == ollama_config.num_ctx_for("qwen3.5:9b") for u, b in calls) and used == 9000)
with mock.patch.object(V, "_http", http2), mock.patch.object(V, "loaded_models", lambda: ["qwen3.5:9b"]), mock.patch.object(V, "gpu_used_mib", lambda: 13500):
    try:
        V.prepare_gpu(lambda m: None); refused = False
    except V.VramError as e:
        refused = "14848" in str(e)
check("it REFUSES to start when the card is too full for the 14.5 GiB budget", refused)
with mock.patch.object(V, "_http", http2), mock.patch.object(V, "loaded_models", lambda: ["qwen3.5:9b"]), mock.patch.object(V, "gpu_used_mib", lambda: None):
    check("an unreadable GPU meter does not block the run", V.prepare_gpu(lambda m: None) is None)

# ═══ stitching strips ═════════════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- stitching overlapping strips --")
R = lambda *pairs: [{"name": n, "qty": q} for n, q in pairs]
rows, un = V.stitch([R(("ساندويش دجاج صغير", 29), ("ساندويش دجاج كبير", 16), ("بيبسي", 34)), R(("ساندويش دجاج كبير", 16), ("بيبسي", 34), ("لبن", 5))], "name")
check("rows that two strips share are kept once", [r["name"] for r in rows] == ["ساندويش دجاج صغير", "ساندويش دجاج كبير", "بيبسي", "لبن"] and un == 0)
rows, un = V.stitch([R(("بيبسي", 34)), R(("لبن", 5))], "name")
check("strips that share no row are joined but COUNTED as unverified (a row may be missing)", len(rows) == 2 and un == 1)
rows, un = V.stitch([R(("بيبسي", 34), ("لبن", 5)), R(("بيبسي", 7), ("ميراندا", 2))], "name")
check("the same name with a DIFFERENT quantity is a different row, never dropped as an overlap", len(rows) == 4 and un == 1)
rows, un = V.stitch([R(("ساندويش دجاج صغير", 29), ("ساندويش دجاج كبير", 16)), R(("ساند ويش دجاج كبير", 16), ("لبن", 5))], "name")
check("a slightly different spelling of the shared row still counts as the overlap (>= 80% alike)", [r["qty"] for r in rows] == [29, 16, 5])
rows, un = V.stitch([R(("a1", 1), ("ساندويش دجاج صغير", 29), ("ساندويش دجاج كبير", 16), ("بيب", None)), R(("اورما دجاج كبير", None), ("ساندويش دجاج كبير", 16), ("بيبسي", 34), ("لبن", 5))], "name")
check("rows cut by the boundary and read differently in each piece do not stop the join: only the shared whole rows count", [r["name"] for r in rows] == ["a1", "ساندويش دجاج صغير", "ساندويش دجاج كبير", "بيبسي", "لبن"] and un == 0, str(rows))
rows, un = V.stitch([R(("ساندويش دجاج صغير", 6), ("ساندويش لحمة صغير", 7)), R(("ساندويش دجاج كبير", 6), ("لبن", 5))], "name")
check("one similar-looking row with the same quantity is NOT enough to call it an overlap (87% alike is not identical)", len(rows) == 4 and un == 1)
rows, un = V.stitch([R(("a", 1), ("b", 2), ("c", 3)), R(("a", 1), ("b", 2), ("c", 3), ("d", 4)), R(("c", 3), ("d", 4), ("e", 5))], "name")
check("three pieces are joined in turn", [r["name"] for r in rows] == ["a", "b", "c", "d", "e"] and un == 0)
# a skewed photo: the second piece pairs every name with the number of the NEXT row (the Sep 13 receipt)
TRUE = [("وجبة موظف بطاطا", 16), ("وجبة موظف دجاج", 18), ("ديمو ناجتس دجاج ولحمه", 2), ("عرض 2 ساندويش دجاج تورتيلا", 5), ("عرض 2 ساندويش كبير", 6), ("عرض ديمو ناجتس", 6), ("عرض دجاج تورتيلا+صغير", 1),
        ("سلايدر 6 دجاج+18 قطعه تورتيلا", 19), ("سلايدر دجاج", 8), ("بيبسي", 46), ("بيبسي دايت", 6), ("سفن اب", 14), ("لبن عيران", 22), ("مياه معدنية", 24), ("ميراندا", 4), ("بطاطا صغير", 10),
        ("بطاطا كبير", 5), ("سندويش بطاطا كلاسيكي", 1), ("ستيك دجاج", 13), ("راب ساندويش دجاج", 5)]
def piece(lo, hi, shift=False):
    out = []
    for k in range(lo, hi):
        q = (TRUE[k + 1][1] if k + 1 < len(TRUE) else None) if shift else TRUE[k][1]
        out.append({"name": TRUE[k][0], "qty": q})
    return out
rep = []
rows, un = V.stitch([piece(0, 15), piece(6, 20, shift=True)], "name", report=rep)
check("a second piece whose quantities are all one row off is found by its NAMES (9 shared rows), not duplicated, and its quantities are moved back up: the list is the true list",
      [(r["name"], r["qty"]) for r in rows] == TRUE and un == 0 and rep == [{"realigned_rows": 5}], str([(r["name"], r["qty"]) for r in rows][14:]))
check("the rows of the overlap keep the FIRST piece's values (they were read right there)", rows[6:15] == piece(6, 15))
rows2, un2 = V.stitch([piece(0, 15), piece(6, 20, shift=True)], "name")
check("the report argument is optional (older callers get the same rows)", rows2 == rows and un2 == 0)
rep3 = []
rows3, un3 = V.stitch([piece(0, 10), piece(6, 20, shift=True)], "name", report=rep3)
check("with fewer than 6 shared rows the displacement is not believed: the join is unverified and the pieces are joined as they are, as before", un3 == 1 and rep3 == [] and len(rows3) == 10 + 14)
half = piece(6, 20, shift=True)
for k in range(0, 9, 2):
    half[k] = {"name": TRUE[6 + k][0], "qty": TRUE[6 + k][1]}
rep4 = []
rows4, un4 = V.stitch([piece(0, 15), half], "name", report=rep4)
check("when overlap rows agree row for row (same name AND quantity) the ordinary stitch applies and nothing is called displaced: no duplicates, no report", un4 == 0 and rep4 == [] and len(rows4) == 20 and [r["name"] for r in rows4] == [t[0] for t in TRUE])
rep5 = []
mixed = piece(6, 20, shift=True)
for k in (1, 3, 5, 7):
    mixed[k] = {"name": TRUE[6 + k][0], "qty": 999}
rows5, un5 = V.stitch([piece(0, 15), mixed], "name", report=rep5)
check("when fewer than 80% of the shared rows sit one row down it is NOT called displaced either", rep5 == [] and un5 == 1)
rep6 = []
rows6, un6 = V.stitch([piece(0, 15), piece(6, 20)], "name", report=rep6)
check("a normal overlap (same names, same quantities) is stitched exactly as before and reports nothing", [(r["name"], r["qty"]) for r in rows6] == TRUE and un6 == 0 and rep6 == [])
parts = [{"title_visible": True, "lines": piece(0, 15), "voids": []}, {"title_visible": True, "lines": piece(6, 20, shift=True), "voids": []}]
mg = V.merge_strips("pos_summary_by_items", parts)
check("merge_strips carries the count of realigned rows, and the reading compares equal to a clean one (no duplicates, no disagreements)", mg["_strip_quantities_realigned"] == 5 and "_strip_joins_without_overlap" not in mg
      and V.compare_readings("pos_summary_by_items", {"title_visible": True, "lines": piece(0, 20), "voids": []}, {k: v for k, v in mg.items() if not k.startswith("_")})["disagreements"] == 0)
cmp_ = V.compare_readings("pos_summary_by_items", {"title_visible": True, "lines": piece(0, 20), "voids": []}, mg)
check("compare_readings reports how many rows were realigned (for the draft's CHECK)", cmp_["strip_quantities_realigned"] == 5 and cmp_["strip_joins_without_overlap"] == 0)
check("stitching nothing is fine", V.stitch([], "name") == ([], 0) and V.stitch([[]], "name") == ([], 0))
doc = V.merge_strips("pos_main_reading", [{"grand_total": None, "vat": 378.26, "payments": R(("Cash Usd", 1))[:0]}, {"grand_total": 3816.8, "vat": 7}])
check("a single value is taken from the first strip that has one", doc["grand_total"] == 3816.8 and doc["vat"] == 378.26)
doc = V.merge_strips("pos_summary_by_items", [{"title_visible": True, "lines": R(("a", 1)), "voids": []}, {"title_visible": False, "lines": R(("a", 1), ("b", 2)), "voids": [{"name": "x", "amount": -1, "qty": 1}]}])
check("lists are stitched and 'title visible' is true if any strip saw it", doc["title_visible"] is True and [r["name"] for r in doc["lines"]] == ["a", "b"] and len(doc["voids"]) == 1)

# ═══ comparing two readings ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- comparing the two readings --")
A = {"title_visible": True, "lines": R(("ساندويش دجاج صغير", 29), ("بيبسي", 34), ("لبن عيران", 16)), "voids": []}
cmp1 = V.compare_readings("pos_summary_by_items", A, json.loads(json.dumps(A)))
check("identical readings: every row agrees, nothing is flagged", cmp1["disagreements"] == 0 and all(r["readings_agree"] for r in cmp1["lists"]["lines"]) and all("name_alt" not in r for r in cmp1["lists"]["lines"]))
B = {"title_visible": True, "lines": R(("ساندويش دجاج صغير", 29), ("بيبسي", 35), ("لين عيران", 16)), "voids": []}
cmp2 = V.compare_readings("pos_summary_by_items", A, B)
rows = cmp2["lists"]["lines"]
check("a different QUANTITY keeps both numbers (the first is primary, the second is qty_alt)", rows[1]["qty"] == 34 and rows[1]["qty_alt"] == 35 and rows[1]["readings_agree"] is False)
check("a different SPELLING keeps both names", rows[2]["name"] == "لبن عيران" and rows[2]["name_alt"] == "لين عيران" and rows[2]["readings_agree"] is False)
check("only the rows that differ are flagged", [r["readings_agree"] for r in rows] == [True, False, False] and cmp2["disagreements"] == 2)
check("spelling differences that normalise away (diacritics, alef, teh marbuta) are NOT disagreements", V.compare_readings("pos_summary_by_items", {"lines": R(("لحمة", 1)), "voids": [], "title_visible": True}, {"lines": R(("لَحْمَه", 1)), "voids": [], "title_visible": True})["disagreements"] == 0)
cmp3 = V.compare_readings("pos_summary_by_items", {"lines": R(("a b c", 1), ("ساندويش", 2)), "voids": [], "title_visible": True}, {"lines": R(("ساندويش", 2)), "voids": [], "title_visible": True})
check("a row only the first reading saw is kept, disagrees, and has no second reading", len(cmp3["lists"]["lines"]) == 2 and cmp3["lists"]["lines"][0]["readings_agree"] is False and not any(k.endswith("_alt") for k in cmp3["lists"]["lines"][0]))
cmp4 = V.compare_readings("pos_summary_by_items", {"lines": R(("ساندويش", 2)), "voids": [], "title_visible": True}, {"lines": R(("بيبسي", 3), ("ساندويش", 2)), "voids": [], "title_visible": True})
check("a row only the second reading saw is kept too, as disagreeing (never dropped)", [r["name"] for r in cmp4["lists"]["lines"]] == ["بيبسي", "ساندويش"] and cmp4["lists"]["lines"][0]["readings_agree"] is False and cmp4["lists"]["lines"][1]["readings_agree"] is True)
M1 = dict(grand_total=3816.8, vat=378.26, net_sales=3816.8, payments=[{"method": "Cash Usd", "amount": 3816.8}])
base = {k: None for k in V.SCHEMAS["pos_main_reading"]["properties"]}
mA, mB = {**base, **M1, "payments": M1["payments"]}, {**base, **M1, "grand_total": 3960.8, "payments": M1["payments"]}
cm = V.compare_readings("pos_main_reading", mA, mB)
check("a single figure that differs keeps both values and counts one disagreement", cm["fields"]["grand_total"] == {"value": 3816.8, "alt": 3960.8, "agree": False} and cm["fields"]["net_sales"]["agree"] and cm["disagreements"] == 1)
check("numbers are compared exactly (3816.8 vs 3816.80 agree; 3816.8 vs 3816.81 do not)", V._eq(3816.8, 3816.80) and not V._eq(3816.8, 3816.81) and V._eq("a", "a") and V._eq(None, None) and not V._eq(None, 0))
check("a boolean is never equal to a number", V._eq(True, True) and not V._eq(True, 1) and not V._eq(0, False))
al = V.align(R(("a", 1), ("ساندويش دجاج", 2), ("لبن", 3)), R(("ساندويش دجاج", 2), ("لبن", 3), ("ميراندا", 4)), "name")
check("alignment pairs the same rows in order and leaves gaps for rows only one side has", [(x and x["name"], y and y["name"]) for x, y in al] == [("a", None), ("ساندويش دجاج", "ساندويش دجاج"), ("لبن", "لبن"), (None, "ميراندا")])
check("alignment of nothing and of one empty side", V.align([], [], "name") == [] and [x[1] for x in V.align(R(("a", 1)), [], "name")] == [None])

# ═══ reading a file / a folder ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- reading files: classify, two readings, cache, duplicates --")
class Fake:
    """Scripted model: classification by the picture's colour, then readings in order."""
    def __init__(self, script):
        self.script, self.n, self.log = script, {}, []

    def ask(self, png, prompt, schema):
        color = Image.open(io.BytesIO(png)).convert("RGB").getpixel((3, 3))
        self.log.append((color, prompt[:20]))
        entry = self.script[color]
        if prompt.startswith("What kind of document"):
            return {"document_type": entry["type"]}
        if prompt.startswith("Is this a POS"):
            return {"kind": entry.get("kind", "pos_sales_list"), "why": entry.get("why", "")}
        k = self.n[color] = self.n.get(color, 0) + 1
        return entry["readings"][min(k, len(entry["readings"])) - 1]

CASH = {k: None for k in V.SCHEMAS["daily_helper_sheet"]["properties"]} | {"date": "10 Sept 2026", "sales": 3816.8, "old": 2361.98, "new": 2200.32, "rows": [{"subject": "Transfer", "amount": 3553.32}]}
SUM = {"title_visible": True, "lines": R(("ساندويش دجاج صغير", 29), ("بيبسي", 34)), "voids": []}
photos = {"helper.png": (10, 10, 10), "summary.png": (20, 20, 20), "other.png": (30, 30, 30)}
for name, col in photos.items():
    save(solid(300, 500, col), name)
fake = Fake({(10, 10, 10): {"type": "daily_helper_sheet", "readings": [CASH, CASH]},
             (20, 20, 20): {"type": "pos_summary_by_items", "readings": [SUM, {**SUM, "lines": []}, {**SUM, "lines": R(("ساندويش دجاج صغير", 29), ("بيبسي", 35))}, {**SUM, "lines": []}]},
             (30, 30, 30): {"type": "other", "readings": []}})
cache = tempfile.mkdtemp(dir=TMP)
with mock.patch.object(V, "CACHE_DIR", cache):
    r1 = V.read_image_file(fake, os.path.join(TMP, "helper.png"), True, lambda m: None)
    n_calls = len(fake.log)
    r1b = V.read_image_file(fake, os.path.join(TMP, "helper.png"), True, lambda m: None)
    again = len(fake.log) == n_calls
    rS = V.read_image_file(fake, os.path.join(TMP, "summary.png"), True, lambda m: None)
    rO = V.read_image_file(fake, os.path.join(TMP, "other.png"), True, lambda m: None)
    r_nc = V.read_image_file(fake, os.path.join(TMP, "helper.png"), False, lambda m: None)
check("a photo is classified once and read twice (readings A and B), and the two readings are compared", r1["document_type"] == "daily_helper_sheet" and n_calls == 3 and r1["comparison"]["disagreements"] == 0 and r1["sha256"] == V.file_sha256(os.path.join(TMP, "helper.png")))
check("the second time it costs NOTHING (cached by the file's hash)", again and r1b["comparison"] == r1["comparison"] and len(os.listdir(cache)) >= 3)
check("with the cache off it reads again", len(fake.log) > n_calls + 3)
check("the item list is read as A (two pieces) + B (two pieces): 4 model calls after classifying and the sales-list check, and B's first piece disagrees on a quantity",
      rS["document_type"] == "pos_summary_by_items" and rS["reading_a"]["_views"] == 2 and rS["reading_b"]["_views"] == 2 and rS["comparison"]["lists"]["lines"][1]["qty_alt"] == 35
      and rS["comparison"]["disagreements"] == 1 and len([1 for c, pr in fake.log if c == (20, 20, 20)]) == 6 and [pr for c, pr in fake.log if c == (20, 20, 20)][:2] == ["What kind of documen", "Is this a POS end-of"])
# a photo the first pass calls an item list but that is a supply / expense / cleaning list (the Bioclare order sheet) is never read as one
with mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP)):
    save(solid(300, 500, (50, 50, 50)), "supply.png")
    save(solid(300, 500, (51, 51, 51)), "elsewhere.png")
    save(solid(300, 500, (52, 52, 52)), "unsure.png")
    fk = Fake({(50, 50, 50): {"type": "pos_summary_by_items", "kind": "supply_or_expense_list", "why": "grid lines, cleaning products", "readings": []},
               (51, 51, 51): {"type": "pos_summary_by_items", "kind": "other", "why": "a schedule", "readings": []},
               (52, 52, 52): {"type": "supply_order_list", "readings": []}})
    r_sup = V.read_image_file(fk, os.path.join(TMP, "supply.png"), log=lambda m: None)
    r_oth = V.read_image_file(fk, os.path.join(TMP, "elsewhere.png"), log=lambda m: None)
    r_dir = V.read_image_file(fk, os.path.join(TMP, "unsure.png"), log=lambda m: None)
    logs, n_before = [], len(fk.log)
    V.read_image_file(fk, os.path.join(TMP, "supply.png"), log=logs.append)
    n_after = len(fk.log)
check("a supply/expense list that the first pass took for an item list becomes 'supply_order_list', says why, and NO item is read from it (only the two questions were asked)",
      r_sup["document_type"] == "supply_order_list" and r_sup["classified_first_as"] == "pos_summary_by_items" and r_sup["not_a_sales_list"] == {"kind": "supply_or_expense_list", "why": "grid lines, cleaning products"}
      and "reading_a" not in r_sup and "comparison" not in r_sup and [pr for c, pr in fk.log if c == (50, 50, 50)] == ["What kind of documen", "Is this a POS end-of"])
check("anything else that is not clearly a sales list becomes 'other', also unread", r_oth["document_type"] == "other" and r_oth["not_a_sales_list"]["kind"] == "other" and "reading_a" not in r_oth and [pr for c, pr in fk.log if c == (51, 51, 51)] == ["What kind of documen", "Is this a POS end-of"])
check("a photo the first pass already calls a supply list is not asked the second question and is not read", r_dir["document_type"] == "supply_order_list" and "not_a_sales_list" not in r_dir and [pr for c, pr in fk.log if c == (52, 52, 52)] == ["What kind of documen"])
check("the verdict is cached by the photo's hash: asking again costs nothing, and the log says why it was left alone", n_after == n_before and len([1 for c, pr in fk.log if c == (50, 50, 50)]) == 2 and any("first looked like an item list, but: grid lines, cleaning products" in m for m in logs))
check("a POS sales list passes the second question and is read as before", rS["document_type"] == "pos_summary_by_items" and "not_a_sales_list" not in rS)
class Muddled:
    def ask(self, png, prompt, schema):
        return {"kind": "something else", "why": "x" * 500}
sk = V.sales_kind(Muddled(), solid(300, 500, (1, 1, 1)))
check("an answer outside the allowed kinds is treated as 'other' (fail safe: never read as a sales list), and a long reason is cut", sk["kind"] == "other" and len(sk["why"]) == 200)

check("a document that is not one of ours ('other') is not read at all", rO["document_type"] == "other" and "reading_a" not in rO and [p for c, p in fake.log if c == (30, 30, 30)] == ["What kind of documen"])
broken = V.read_image_file(fake, os.path.join(TMP, "broken.jpg"), True, lambda m: None)
check("a file that is not a picture is reported, never a crash", broken["document_type"] is None and "cannot be opened" in broken["error"])

print("\n-- scanning a day folder --")
day = tempfile.mkdtemp(dir=TMP)
for nm, col in (("a.jpeg", (40, 40, 40)), ("b.jpeg", (50, 50, 50))):
    solid(200, 200, col).save(os.path.join(day, nm), "JPEG")
shutil.copy(os.path.join(day, "a.jpeg"), os.path.join(day, "a_copy.jpeg"))
open(os.path.join(day, "empty.jpeg"), "wb").close()
open(os.path.join(day, "bill.pdf"), "wb").write(b"%PDF")
open(os.path.join(day, "sheet.xlsx"), "wb").write(b"PK")
open(os.path.join(day, "notes.txt"), "w").write("x")
os.makedirs(os.path.join(day, "sub"))
sc = V.scan_day_folder(day)
check("identical photos (WhatsApp saves them twice) are read once", [os.path.basename(p) for p in sc["photos"]] == ["a.jpeg", "b.jpeg"] and sc["duplicates"] == [("a_copy.jpeg", "a.jpeg")])
check("zero-byte files, spreadsheets and other files are listed separately; sub-folders are ignored", sc["empty"] == ["empty.jpeg"] and [os.path.basename(p) for p in sc["sheets"]] == ["sheet.xlsx"] and sorted(sc["other"]) == ["bill.pdf", "notes.txt"])
class Boom:
    def __init__(self, bad): self.bad, self.calls = bad, 0
    def ask(self, png, prompt, schema):
        self.calls += 1
        if Image.open(io.BytesIO(png)).convert("RGB").getpixel((3, 3)) == self.bad:
            raise V.VisionError("no answer")
        return {"document_type": "other"}
with mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP)):
    rd = V.read_day(day, client=Boom((40, 40, 40)), log=lambda m: None)
check("one photo failing does not stop the day: it is listed under errors, the others are read", len(rd["errors"]) == 1 and rd["errors"][0]["file"] == "a.jpeg" and [d["file"] for d in rd["documents"]] == ["b.jpeg"])
class Down:
    def ask(self, *a): raise V.OllamaDown("gone")
with mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP)):
    try:
        V.read_day(day, client=Down(), log=lambda m: None); stopped = False
    except V.OllamaDown:
        stopped = True
check("if Ollama itself is gone the whole run stops (it does not pretend)", stopped)
with mock.patch.object(V, "prepare_gpu") as pg, mock.patch.object(V, "OllamaVision") as ov, mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP)):
    ov.return_value = Boom((0, 0, 0))
    V.read_day(day, log=lambda m: None)
check("without a client given, the GPU is prepared (other models unloaded) before reading", pg.call_count == 1)

# ═══ the consumption sheet ════════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- the consumption sheet is read by cell, not by a model --")
import openpyxl
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["Daily Beef & Chicken Stock Consumption"]); ws.append(["Branch Name :", "downtown"]); ws.append([])
ws.append(["Date", "Item", "Opening", "Received", "Total", "Used", "Shrink", "Yield", "Sold", "Waste", "Remaining After Cooking", "Remaining Raw", "Notes"])
ws.append([datetime.datetime(2026, 9, 9), "Beef", 18.76, 10, 28.76, 12.12, 0.45, 6.666, 4.52, 1.6, 0.5, 16.64, None])
ws.append([datetime.datetime(2026, 9, 9), "Chicken", 151.666, 85, 236.666, 64.44, 0.45, 35.442, 30.65, 1.6, 3.0, 172.226, None])
ws.append([datetime.datetime(2026, 9, 10), "Beef", 16.64, 10, 26.64, 10.9, 0.45, 5.995, 4.91, 0.61, 0.4, 15.74, None])
ws.append([datetime.datetime(2026, 9, 10), "Chicken", 172.226, 68, 240.226, 72.88, 0.45, 40.084, 37, 4.9, "n/a", 167.346, None])
ws.append([0.45])
xl = os.path.join(TMP, "consumption.xlsx"); wb.save(xl)
rows = V.read_consumption_xlsx(xl, "2026-09-10")
check("the row of the asked day is read for each meat, in kilograms, by column", rows["beef"]["opening_raw_kg"] == 16.64 and rows["beef"]["received_raw_kg"] == 10.0 and rows["beef"]["sold_kg"] == 4.91
      and rows["beef"]["waste_kg"] == 0.61 and rows["beef"]["remaining_raw_kg"] == 15.74 and rows["beef"]["used_in_cooking_raw_kg"] == 10.9 and rows["chicken"]["opening_raw_kg"] == 172.226)
check("the other days' rows are not mixed in", rows["chicken"]["received_raw_kg"] == 68.0 and V.read_consumption_xlsx(xl, "2026-09-09")["chicken"]["received_raw_kg"] == 85.0)
check("a cell that is not a number comes back as None, never as a guess", rows["chicken"]["remaining_after_cooking_kg"] is None)
check("a day with no row gives None for each meat", V.read_consumption_xlsx(xl, "2026-09-20") == {"beef": None, "chicken": None})

# ═══ an item list photographed on its side ════════════════════════════════════════════════════════════════════════════════════
print("\n-- an item list photographed on its side (Sep 24): read in each turn, keep the one the menu knows --")
GOOD_NAMES = ["ساندويش دجاج صغير", "ساندويش دجاج كبير", "ساندويش لحمة صغير", "ساندويش لحمة كبير", "بيبسي", "سفن أب", "وجبة موظف بطاطا", "وجبة دجاج موظف"]


class Sideways:
    """The 'page' is right way up when it is PORTRAIT with the red marker in its bottom-left corner. Whole-picture reads (the first three item-list asks)
    return real menu names only then, junk otherwise; later asks are the readings proper and return a fixed list."""
    def __init__(self, upright_marker="bottom_left"):
        self.items_calls, self.log = 0, []

    def ask(self, png, prompt, schema):
        im = Image.open(io.BytesIO(png)).convert("RGB")
        w, h = im.size
        self.log.append((w, h, prompt[:20]))
        if prompt.startswith("What kind of document"):
            return {"document_type": "pos_summary_by_items"}
        if prompt.startswith("Is this a POS"):
            return {"kind": "pos_sales_list", "why": ""}
        self.items_calls += 1
        if self.items_calls <= 3:
            corner = {"top_left": (3, 3), "bottom_left": (3, h - 4), "top_right": (w - 4, 3), "bottom_right": (w - 4, h - 4)}
            at = [k for k, xy in corner.items() if im.getpixel(xy) == (255, 0, 0)]
            good = h > w and at == ["bottom_left"]
            return {"title_visible": True, "lines": [{"name": n if good else f"zq{i}x", "qty": i + 1} for i, n in enumerate(GOOD_NAMES)], "voids": []}
        return {"title_visible": True, "lines": [{"name": n, "qty": i + 1} for i, n in enumerate(GOOD_NAMES)], "voids": []}


def marked(w, h, corner):
    im = Image.new("RGB", (w, h), (200, 200, 200))
    xy = {"top_left": (0, 0), "bottom_left": (0, h - 8), "top_right": (w - 8, 0), "bottom_right": (w - 8, h - 8)}[corner]
    im.paste((255, 0, 0), (xy[0], xy[1], xy[0] + 8, xy[1] + 8))
    return im


# the page was turned so that its bottom-left corner went to the top-left of a landscape picture: turning it 270 degrees clockwise (90 counter-clockwise) puts it back
side = marked(600, 400, "top_left")
save(side, "sideways.png")
with mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP)):
    sw = Sideways()
    logs = []
    r_side = V.read_image_file(sw, os.path.join(TMP, "sideways.png"), True, logs.append)
    calls_first = len(sw.log)
    r_side2 = V.read_image_file(sw, os.path.join(TMP, "sideways.png"), True, lambda m: None)
check("a landscape item list is read whole in 3 turns (as it is, 90, 270) and the turn the menu knows (270) is kept", r_side["rotated_degrees"] == 270 and sw.items_calls == 3 + 4 and calls_first == 2 + 3 + 4,
      str((r_side.get("rotated_degrees"), sw.items_calls, calls_first)))
check("the readings are then made on the turned picture (portrait): both readings have the names and the log says what was done",
      all(c["name"] in GOOD_NAMES for c in r_side["reading_a"]["lines"]) and len(r_side["reading_a"]["lines"]) == 8 and r_side["reading_a"]["_views"] == 2 and any("photographed on its side" in m and "270" in m for m in logs))
check("the turn is cached: the same photo again costs no model call, and the readings are cached under a name that carries the turn", len(sw.log) == calls_first and r_side2["rotated_degrees"] == 270)
# a photo already the right way up is read as before
up = marked(600, 400, "top_left")
upside = Image.new("RGB", (600, 400), (200, 200, 200))
class Upright(Sideways):
    def ask(self, png, prompt, schema):
        if not (prompt.startswith("What kind") or prompt.startswith("Is this a POS")) and self.items_calls < 3:
            self.items_calls += 1
            self.log.append((0, 0, prompt[:20]))
            return {"title_visible": True, "lines": [{"name": n, "qty": i + 1} for i, n in enumerate(GOOD_NAMES)], "voids": []}
        return super().ask(png, prompt, schema)
save(up, "landscape_ok.png")
with mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP)):
    r_up = V.read_image_file(Upright(), os.path.join(TMP, "landscape_ok.png"), True, lambda m: None)
check("a landscape photo that already reads well (every turn finds the same names) is NOT turned: 0 degrees, no 'rotated_degrees' entry", "rotated_degrees" not in r_up and "reading_a" in r_up)
# portrait photos never pay for the extra reads
with mock.patch.object(V, "CACHE_DIR", tempfile.mkdtemp(dir=TMP)):
    save(marked(400, 600, "bottom_left"), "portrait.png")
    sp = Sideways()
    r_port = V.read_image_file(sp, os.path.join(TMP, "portrait.png"), True, lambda m: None)
check("a portrait item list is not asked the three whole-picture reads: only classify, the sales-list check and the 4 readings (2 + 2 pieces)", "rotated_degrees" not in r_port and len(sp.log) == 2 + 4 and sp.items_calls == 4)


class Stub:
    def __init__(self, hits):
        self.hits, self.n = hits, 0

    def ask(self, png, prompt, schema):
        self.n += 1
        w, h = Image.open(io.BytesIO(png)).size
        k = self.hits.pop(0)
        return {"title_visible": True, "lines": [{"name": GOOD_NAMES[i % len(GOOD_NAMES)] if i < k else f"zq{i}x", "qty": 1} for i in range(max(8, k + 2))], "voids": []}


im_ls = Image.new("RGB", (600, 400))
check("a turn must find at least 3 more names AND half as many again, else the photo stays as it is (as it is 4, turn 90: 6 -> stays 0)", V.best_rotation(Stub([4, 6, 2]), im_ls)["degrees"] == 0)
check("as it is 2, turn 90 finds 5 -> 90; as it is 2, turn 270 finds 8 -> 270", V.best_rotation(Stub([2, 5, 0]), im_ls)["degrees"] == 90 and V.best_rotation(Stub([2, 0, 8]), im_ls)["degrees"] == 270)
check("a sideways list the model half-reads still turns: as it is 24, turn 90 finds 35 (11 more, 46% more) -> 90 (the 2026-10-15 photo); 24 against 30 stays 0; 24 against 31 stays 0 (not 30% more)",
      V.best_rotation(Stub([24, 35, 5]), im_ls)["degrees"] == 90 and V.best_rotation(Stub([24, 30, 5]), im_ls)["degrees"] == 0 and V.best_rotation(Stub([24, 31, 5]), im_ls)["degrees"] == 0 and V.best_rotation(Stub([24, 5, 35]), im_ls)["degrees"] == 270)
check("as it is 0 and the turns find 0, 0: nothing to gain, stays 0; the hits are reported per turn", V.best_rotation(Stub([0, 0, 0]), im_ls) == {"degrees": 0, "menu_hits": {"0": 0, "90": 0, "270": 0}})

# ═══ boundaries ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- boundaries --")
code = "\n".join(l for l in inspect.getsource(V).splitlines() if not l.lstrip().startswith("#"))
check("it talks only to the local Ollama (127.0.0.1) and imports no other network library", "127.0.0.1" in ollama_config.OLLAMA_HOST and not re.search(r"^\s*(import|from)\s+(requests|httpx|socket|aiohttp|smtplib|ftplib)", code, re.M))
check("the only files it writes are its own cache entries (atomic, under jobs_state)", len(re.findall(r"open\([^)]*['\"]w", code)) == 1 and V.CACHE_DIR.startswith(str(DAVID_DATA / "jobs_state")) and "os.replace(tmp, path)" in code)
check("it never deletes, moves or edits an input file", not re.search(r"os\.(remove|rename|unlink|makedirs\(os\.path\.dirname)|shutil\.|\.unlink\(", code.replace("os.makedirs(CACHE_DIR", "")))
check("thinking is off and the temperature is 0 in the request code", '"think": False' in code and '"temperature": 0' in code)
check("the budget constants are the shared ones: 14.5 GiB, qwen3.5:9b, the embedder kept", V.VRAM_BUDGET_MIB == 14848 and V.MODEL == "qwen3.5:9b" and "qwen3-embedding" in V.KEEP_LOADED)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n  {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
