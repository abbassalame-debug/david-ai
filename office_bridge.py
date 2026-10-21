# office_bridge.py — David Microsoft Office Bridge
# Supports: Word, Excel, PowerPoint via Windows COM (win32com)
#
# Requires: pip install pywin32
#
# Usage:
#   py -3.11 office_bridge.py --app word        "create a project report with 3 sections"
#   py -3.11 office_bridge.py --app excel       "quarterly budget with Q1-Q4 columns"
#   py -3.11 office_bridge.py --app powerpoint  "5-slide dark pitch deck for Demo User User"
#   py -3.11 office_bridge.py --status
#   py -3.11 office_bridge.py --app word --default

from david_config import DAVID_DATA
import os
import sys
import json
import logging
import datetime
import urllib.request
from ollama_config import num_ctx_for, OLLAMA_HOST

sys.stdout.reconfigure(encoding="utf-8")
log = logging.getLogger("david.office")
logging.basicConfig(level=logging.INFO, format="  [Office] %(message)s")

OUTPUT_DIR = str(DAVID_DATA / "generated")
OLLAMA     = OLLAMA_HOST + "/api/chat"
MODEL      = "qwen3.5:9b"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Color helper ──────────────────────────────────────────────────────────────

def hex_to_com_color(hex_str: str) -> int:
    """#RRGGBB → COM RGB integer (r + g×256 + b×65536)."""
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r + g * 256 + b * 65536

# ── App config ────────────────────────────────────────────────────────────────

APP_CONFIG = {
    "word":        {"com_id": "Word.Application",        "label": "Word",        "ext": ".docx"},
    "excel":       {"com_id": "Excel.Application",       "label": "Excel",       "ext": ".xlsx"},
    "powerpoint":  {"com_id": "PowerPoint.Application",  "label": "PowerPoint",  "ext": ".pptx"},
}

# ── Per-app LLM prompts ───────────────────────────────────────────────────────

_PROMPTS = {
    "word": """\
Output a single COMPACT JSON object (no newlines, no spaces) for Microsoft Word automation.
Start with {{ and end with }}. Raw JSON only — no markdown, no code fences.

Keys: document_name(str), close_after(bool,default:true), export_pdf(bool,default:false),
content([
  {{type:"heading",text,level:1-3,color}},
  {{type:"paragraph",text,bold,color,size,align:"left"|"center"|"right"}},
  {{type:"table",header_row:bool,header_color,rows:[["col1","col2"],["val1","val2"]]}},
  {{type:"page_break"}}
])

Example: {{"document_name":"Project Report","close_after":true,"export_pdf":false,"content":[{{"type":"heading","text":"Project Report","level":1}},{{"type":"paragraph","text":"This report covers Q2 2026 activity.","size":12}},{{"type":"table","header_row":true,"header_color":"#1A1A2E","rows":[["Task","Owner","Status"],["Design","Ali","Done"],["Build","Sam","In Progress"]]}},{{"type":"page_break"}},{{"type":"heading","text":"Financials","level":2}},{{"type":"paragraph","text":"Revenue exceeded targets by 12%."}}]}}

Design request: {request}
JSON:""",

    "excel": """\
Output a single COMPACT JSON object (no newlines, no spaces) for Microsoft Excel automation.
Start with {{ and end with }}. Raw JSON only — no markdown, no code fences.

Keys: workbook_name(str), close_after(bool,default:true),
sheets([{{
  name, freeze_row(int,0=none), freeze_col(int,0=none),
  column_widths:{{"A":20,"B":15}},
  cells:[{{cell:"A1",value,bold,bg_color,font_color,format,align:"left"|"center"|"right",merge_to:"B1"}}]
}}])
Cell value: string, number, or formula starting with =

Example: {{"workbook_name":"Q2 Budget","close_after":true,"sheets":[{{"name":"Revenue","freeze_row":1,"column_widths":{{"A":22,"B":14,"C":14,"D":14}},"cells":[{{"cell":"A1","value":"Product","bold":true,"bg_color":"#1A1A2E","font_color":"#FFFFFF"}},{{"cell":"B1","value":"Q1","bold":true,"bg_color":"#1A1A2E","font_color":"#FFFFFF"}},{{"cell":"C1","value":"Q2","bold":true,"bg_color":"#1A1A2E","font_color":"#FFFFFF"}},{{"cell":"A2","value":"Product A"}},{{"cell":"B2","value":45000,"format":"#,##0"}},{{"cell":"C2","value":52000,"format":"#,##0"}},{{"cell":"B4","value":"=SUM(B2:B3)","bold":true,"format":"#,##0"}}]}}]}}

Design request: {request}
JSON:""",

    "powerpoint": """\
Output a single COMPACT JSON object (no newlines, no spaces) for Microsoft PowerPoint automation.
Start with {{ and end with }}. Raw JSON only — no markdown, no code fences.

Positions and sizes in POINTS (72pt = 1 inch). Default widescreen slide: 960×540pt.
Keys: presentation_name(str), slide_width(default:960), slide_height(default:540), close_after(bool,default:true),
slides([{{
  background_color,
  shapes:[{{type:"text_box"|"rectangle"|"ellipse",x,y,width,height,text,font_size,bold,color,fill_color,align:"left"|"center"|"right"}}]
}}])

Example: {{"presentation_name":"Demo User User Pitch","slide_width":960,"slide_height":540,"close_after":true,"slides":[{{"background_color":"#1A1A2E","shapes":[{{"type":"rectangle","x":0,"y":0,"width":960,"height":6,"fill_color":"#E94560"}},{{"type":"text_box","x":72,"y":160,"width":816,"height":160,"text":"Demo User User","font_size":60,"bold":true,"color":"#FFFFFF","align":"center"}},{{"type":"text_box","x":72,"y":330,"width":816,"height":60,"text":"Creative Direction & Brand Strategy","font_size":22,"color":"#AAAAAA","align":"center"}},{{"type":"rectangle","x":0,"y":534,"width":960,"height":6,"fill_color":"#E94560"}}]}},{{"background_color":"#0F3460","shapes":[{{"type":"text_box","x":72,"y":60,"width":816,"height":80,"text":"Our Services","font_size":40,"bold":true,"color":"#FFFFFF"}},{{"type":"text_box","x":72,"y":160,"width":816,"height":320,"text":"• Brand identity design\n• Motion graphics\n• Print & digital production","font_size":22,"color":"#CCCCCC"}}]}}]}}

Design request: {request}
JSON:""",
}

# ── Default specs ─────────────────────────────────────────────────────────────

def _default_spec(app_name: str) -> dict:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    defaults = {
        "word": {
            "document_name": f"David_Word_{ts}",
            "close_after": True,
            "export_pdf": False,
            "content": [
                {"type": "heading",   "text": "Document Title", "level": 1},
                {"type": "paragraph", "text": "Created by David AI.", "size": 12},
                {"type": "table", "header_row": True, "header_color": "#1A1A2E",
                 "rows": [["Column A", "Column B", "Column C"],
                          ["Value 1",  "Value 2",  "Value 3"],
                          ["Value 4",  "Value 5",  "Value 6"]]},
            ],
        },
        "excel": {
            "workbook_name": f"David_Excel_{ts}",
            "close_after": True,
            "sheets": [{
                "name": "Sheet1",
                "freeze_row": 1,
                "column_widths": {"A": 22, "B": 14, "C": 14},
                "cells": [
                    {"cell": "A1", "value": "Item",    "bold": True, "bg_color": "#1A1A2E", "font_color": "#FFFFFF"},
                    {"cell": "B1", "value": "Q1",      "bold": True, "bg_color": "#1A1A2E", "font_color": "#FFFFFF"},
                    {"cell": "C1", "value": "Q2",      "bold": True, "bg_color": "#1A1A2E", "font_color": "#FFFFFF"},
                    {"cell": "A2", "value": "Revenue"},
                    {"cell": "B2", "value": 50000, "format": "#,##0"},
                    {"cell": "C2", "value": 62000, "format": "#,##0"},
                    {"cell": "A3", "value": "Total",   "bold": True},
                    {"cell": "B3", "value": "=SUM(B2:B2)", "bold": True, "format": "#,##0"},
                    {"cell": "C3", "value": "=SUM(C2:C2)", "bold": True, "format": "#,##0"},
                ],
            }],
        },
        "powerpoint": {
            "presentation_name": f"David_PPT_{ts}",
            "slide_width": 960,
            "slide_height": 540,
            "close_after": True,
            "slides": [{
                "background_color": "#1A1A2E",
                "shapes": [
                    {"type": "rectangle", "x": 0,   "y": 0,   "width": 960, "height": 6,   "fill_color": "#E94560"},
                    {"type": "text_box",  "x": 72,  "y": 160, "width": 816, "height": 160,
                     "text": "Demo User User", "font_size": 60, "bold": True, "color": "#FFFFFF", "align": "center"},
                    {"type": "text_box",  "x": 72,  "y": 330, "width": 816, "height": 60,
                     "text": "Created by David AI", "font_size": 22, "color": "#AAAAAA", "align": "center"},
                    {"type": "rectangle", "x": 0,   "y": 534, "width": 960, "height": 6,   "fill_color": "#E94560"},
                ],
            }],
        },
    }
    return defaults.get(app_name, defaults["word"])

# ── COM app launcher ──────────────────────────────────────────────────────────

def _get_com_app(app_name: str):
    import win32com.client
    cfg = APP_CONFIG[app_name]
    try:
        app = win32com.client.GetActiveObject(cfg["com_id"])
        log.info(f"{cfg['label']}: connected to running instance")
        return app
    except Exception:
        log.info(f"{cfg['label']}: launching new instance")
        app = win32com.client.Dispatch(cfg["com_id"])
        app.Visible = True
        return app

# ── Word ──────────────────────────────────────────────────────────────────────

def _execute_word(spec: dict) -> str:
    ts        = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name      = spec.get("document_name", f"David_Word_{ts}")
    save_path = os.path.join(OUTPUT_DIR, name.replace(" ", "_") + ".docx")

    word = _get_com_app("word")
    doc  = word.Documents.Add()

    ALIGN_MAP = {"left": 0, "center": 1, "right": 2}

    for item in spec.get("content", []):
        t   = item.get("type", "")
        sel = word.Selection

        if t == "heading":
            level = max(1, min(9, item.get("level", 1)))
            sel.Style = doc.Styles(f"Heading {level}")
            if item.get("color"):
                sel.Font.Color = hex_to_com_color(item["color"])
            sel.TypeText(item.get("text", ""))
            sel.TypeParagraph()

        elif t == "paragraph":
            sel.Style = doc.Styles("Normal")
            if item.get("bold"):    sel.Font.Bold  = True
            if item.get("color"):   sel.Font.Color = hex_to_com_color(item["color"])
            if item.get("size"):    sel.Font.Size  = item["size"]
            align = item.get("align", "left")
            sel.ParagraphFormat.Alignment = ALIGN_MAP.get(align, 0)
            sel.TypeText(item.get("text", ""))
            sel.Font.Bold      = False
            sel.Font.Color     = -16777216  # wdColorAutomatic
            sel.Font.Size      = 12
            sel.ParagraphFormat.Alignment = 0
            sel.TypeParagraph()

        elif t == "page_break":
            sel.InsertBreak(7)  # wdPageBreak

        elif t == "table":
            rows_data = item.get("rows", [])
            if not rows_data:
                continue
            nr  = len(rows_data)
            nc  = max(len(r) for r in rows_data)
            tbl = doc.Tables.Add(sel.Range, nr, nc)
            try:
                tbl.Style = "Table Grid"
            except Exception:
                pass
            hdr_color = item.get("header_color")
            for ri, row_data in enumerate(rows_data):
                for ci, val in enumerate(row_data):
                    if ci >= nc:
                        continue
                    cell = tbl.Cell(ri + 1, ci + 1)
                    cell.Range.Text = str(val)
                    if ri == 0 and item.get("header_row"):
                        cell.Range.Font.Bold = True
                        if item.get("font_color"):
                            cell.Range.Font.Color = hex_to_com_color(item["font_color"])
                        if hdr_color:
                            cell.Shading.BackgroundPatternColor = hex_to_com_color(hdr_color)
            end = tbl.Range.End
            word.Selection.SetRange(end, end)
            word.Selection.TypeParagraph()

    doc.SaveAs2(save_path, FileFormat=16)  # wdFormatDocumentDefault = 16

    if spec.get("export_pdf"):
        pdf_path = save_path.replace(".docx", ".pdf")
        doc.ExportAsFixedFormat(pdf_path, ExportFormat=17)  # wdExportFormatPDF
        log.info(f"Word PDF: {pdf_path}")

    if spec.get("close_after", True):
        doc.Close(False)
        if not word.Documents.Count:
            word.Quit()

    log.info(f"Word: {save_path}")
    return save_path

# ── Excel ─────────────────────────────────────────────────────────────────────

def _execute_excel(spec: dict) -> str:
    ts        = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name      = spec.get("workbook_name", f"David_Excel_{ts}")
    save_path = os.path.join(OUTPUT_DIR, name.replace(" ", "_") + ".xlsx")

    ALIGN_MAP = {"left": -4131, "center": -4108, "right": -4152}  # xlLeft/Center/Right

    xl = _get_com_app("excel")
    xl.DisplayAlerts = False
    wb = xl.Workbooks.Add()

    sheets_spec = spec.get("sheets", [])

    # Resize sheet count to match spec
    while wb.Worksheets.Count < len(sheets_spec):
        wb.Worksheets.Add(After=wb.Worksheets(wb.Worksheets.Count))
    while wb.Worksheets.Count > max(len(sheets_spec), 1):
        wb.Worksheets(wb.Worksheets.Count).Delete()

    for si, sheet_spec in enumerate(sheets_spec):
        ws      = wb.Worksheets(si + 1)
        ws.Name = sheet_spec.get("name", f"Sheet{si + 1}")

        for c in sheet_spec.get("cells", []):
            addr = c.get("cell", "A1")
            cell = ws.Range(addr)
            cell.Value = c.get("value", "")
            if c.get("bold"):       cell.Font.Bold             = True
            if c.get("font_color"): cell.Font.Color            = hex_to_com_color(c["font_color"])
            if c.get("bg_color"):   cell.Interior.Color        = hex_to_com_color(c["bg_color"])
            if c.get("format"):     cell.NumberFormat          = c["format"]
            if c.get("merge_to"):   ws.Range(f"{addr}:{c['merge_to']}").Merge()
            if c.get("align"):      cell.HorizontalAlignment   = ALIGN_MAP.get(c["align"], -4131)

        for col_letter, col_width in sheet_spec.get("column_widths", {}).items():
            ws.Columns(col_letter).ColumnWidth = col_width

        freeze_row = sheet_spec.get("freeze_row", 0)
        freeze_col = sheet_spec.get("freeze_col", 0)
        if freeze_row or freeze_col:
            ws.Activate()
            ws.Cells(freeze_row + 1, freeze_col + 1).Select()
            xl.ActiveWindow.FreezePanes = True

    wb.SaveAs(save_path, FileFormat=51)  # xlOpenXMLWorkbook = 51

    if spec.get("close_after", True):
        wb.Close(False)
        if not xl.Workbooks.Count:
            xl.Quit()

    xl.DisplayAlerts = True
    log.info(f"Excel: {save_path}")
    return save_path

# ── PowerPoint ────────────────────────────────────────────────────────────────

def _execute_powerpoint(spec: dict) -> str:
    ts        = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name      = spec.get("presentation_name", f"David_PPT_{ts}")
    save_path = os.path.join(OUTPUT_DIR, name.replace(" ", "_") + ".pptx")

    ALIGN_MAP = {"left": 1, "center": 2, "right": 3}  # ppAlignLeft/Center/Right

    ppt = _get_com_app("powerpoint")
    prs = ppt.Presentations.Add(WithWindow=True)
    prs.PageSetup.SlideWidth  = spec.get("slide_width",  960)
    prs.PageSetup.SlideHeight = spec.get("slide_height", 540)

    for slide_idx, slide_spec in enumerate(spec.get("slides", [])):
        slide = prs.Slides.Add(slide_idx + 1, 12)  # ppLayoutBlank = 12

        bg_hex = slide_spec.get("background_color", "#FFFFFF")
        slide.Background.Fill.Solid()
        slide.Background.Fill.ForeColor.RGB = hex_to_com_color(bg_hex)
        slide.FollowMasterBackground = False

        for shp in slide_spec.get("shapes", []):
            x, y           = shp.get("x", 0), shp.get("y", 0)
            w, h           = shp.get("width", 200), shp.get("height", 100)
            shp_type       = shp.get("type", "text_box")
            fill_color_hex = shp.get("fill_color")

            if shp_type == "text_box":
                shape = slide.Shapes.AddTextbox(1, x, y, w, h)  # msoTextOrientationHorizontal=1
            elif shp_type == "rectangle":
                shape = slide.Shapes.AddShape(1, x, y, w, h)    # msoShapeRectangle=1
                shape.Fill.Solid()
                shape.Fill.ForeColor.RGB = hex_to_com_color(fill_color_hex or "#FFFFFF")
                shape.Line.Visible = False
            elif shp_type == "ellipse":
                shape = slide.Shapes.AddShape(9, x, y, w, h)    # msoShapeOval=9
                shape.Fill.Solid()
                shape.Fill.ForeColor.RGB = hex_to_com_color(fill_color_hex or "#FFFFFF")
                shape.Line.Visible = False
            else:
                continue

            text = shp.get("text", "")
            if text and shape.HasTextFrame:
                tf          = shape.TextFrame
                tf.WordWrap = True
                tf.AutoSize = 0  # ppAutoSizeNone
                tr          = tf.TextRange
                tr.Text     = text
                if shp.get("font_size"): tr.Font.Size      = shp["font_size"]
                if shp.get("bold"):      tr.Font.Bold       = True
                if shp.get("color"):     tr.Font.Color.RGB  = hex_to_com_color(shp["color"])
                align = shp.get("align", "left")
                tr.ParagraphFormat.Alignment = ALIGN_MAP.get(align, 1)

    prs.SaveAs(save_path, 24)  # ppSaveAsOpenXMLPresentation = 24

    if spec.get("close_after", True):
        prs.Close()
        if not ppt.Presentations.Count:
            ppt.Quit()

    log.info(f"PowerPoint: {save_path}")
    return save_path

# ── LLM → Spec ────────────────────────────────────────────────────────────────

def request_to_spec(natural_language: str, app_name: str = "word") -> dict:
    prompt = _PROMPTS.get(app_name, _PROMPTS["word"]).format(request=natural_language)
    body   = {
        "model":    MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream":   False,
        "think":    False,
        "options":  {"num_ctx": num_ctx_for(MODEL), "temperature": 0, "num_predict": 4000},
    }
    req = urllib.request.Request(
        OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = json.loads(r.read())["message"]["content"].strip()

    log.info(f"LLM raw (first 120): {repr(raw[:120])}")

    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                raw = part
                break

    start = raw.find("{")
    if start == -1:
        raise ValueError("LLM returned no JSON")

    depth, end = 0, -1
    for i, ch in enumerate(raw[start:], start):
        if   ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        raise ValueError("LLM JSON truncated")

    return json.loads(raw[start:end])

# ── Main entry point (called from David) ─────────────────────────────────────

def design_request(natural_language: str, app_name: str = "word") -> str:
    if app_name not in APP_CONFIG:
        raise ValueError(f"Unknown app '{app_name}'. Valid: {list(APP_CONFIG)}")

    log.info(f"Office request → {app_name}: {natural_language[:80]}")

    try:
        spec = request_to_spec(natural_language, app_name)
        log.info(f"Spec keys: {list(spec.keys())}")
    except Exception as e:
        log.warning(f"LLM failed ({e}), using default spec")
        spec = _default_spec(app_name)

    if   app_name == "word":        path = _execute_word(spec)
    elif app_name == "excel":       path = _execute_excel(spec)
    elif app_name == "powerpoint":  path = _execute_powerpoint(spec)

    try:
        import generated_tracker
        generated_tracker.log_file(app_name, path, request=natural_language, spec=spec)
    except Exception as e:
        log.warning(f"Tracker: {e}")

    return path

# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="David Office Bridge")
    parser.add_argument("--app",     default="word", choices=list(APP_CONFIG),
                        help="Target Office app (default: word)")
    parser.add_argument("--status",  action="store_true", help="Show which apps are running")
    parser.add_argument("--default", action="store_true", help="Run default spec without LLM")
    parser.add_argument("request",   nargs="?", default="", help="Natural language request")
    args = parser.parse_args()

    if args.status:
        import win32com.client
        for name, cfg in APP_CONFIG.items():
            try:
                win32com.client.GetActiveObject(cfg["com_id"])
                print(f"  {cfg['label']:14s} RUNNING")
            except Exception:
                print(f"  {cfg['label']:14s} not running")
        sys.exit(0)

    if args.default:
        spec = _default_spec(args.app)
        if   args.app == "word":        path = _execute_word(spec)
        elif args.app == "excel":       path = _execute_excel(spec)
        elif args.app == "powerpoint":  path = _execute_powerpoint(spec)
        try:
            import generated_tracker
            generated_tracker.log_file(args.app, path, request="(default spec)", spec=spec)
        except Exception as e:
            log.warning(f"Tracker: {e}")
        print(f"Done: {path}")
        sys.exit(0)

    if not args.request:
        parser.print_help()
        sys.exit(1)

    result = design_request(args.request, args.app)
    print(f"Done: {result}")
