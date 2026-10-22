# adobe_bridge.py — David Adobe Bridge (5-app edition)
# Supports: Photoshop, Illustrator, InDesign, After Effects, Premiere Pro
# Execution: Windows COM (win32com) for PS/AI/ID/AE — subprocess for Premiere.
#
# Requires: pip install pywin32
#
# Usage:
#   py -3.11 adobe_bridge.py --status
#   py -3.11 adobe_bridge.py --app photoshop   "create a 1920x1080 brand canvas"
#   py -3.11 adobe_bridge.py --app illustrator "vector logo with brand swatches"
#   py -3.11 adobe_bridge.py --app indesign    "A4 brochure layout with heading"
#   py -3.11 adobe_bridge.py --app aftereffects "10-second title comp dark background"
#   py -3.11 adobe_bridge.py --app premiere    "add markers at 0 5 10 seconds"

from david_config import DAVID_DATA, DAVID_HOME
import os
import sys
import json
import logging
import subprocess
import urllib.request
from ollama_config import num_ctx_for, OLLAMA_HOST
import datetime

sys.stdout.reconfigure(encoding="utf-8")
log = logging.getLogger("david.adobe")
logging.basicConfig(level=logging.INFO, format="  [Adobe] %(message)s")

SCRIPT_DIR = str(DAVID_HOME / "scripts")
OUTPUT_DIR = str(DAVID_DATA / "generated")
OLLAMA     = OLLAMA_HOST + "/api/chat"
MODEL      = "qwen3.5:9b"

os.makedirs(SCRIPT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── App configuration table ───────────────────────────────────────────────────
# exec_method:
#   "DoJavaScriptFile" — PS / AI     : app.DoJavaScriptFile(path)  [COM]
#   "doScript"         — InDesign    : app.doScript(path, JS_LANG)  [COM]
#   "subprocess_r"     — After Effects: AfterFX.exe -r script.jsx  (no COM, -r supported)
#   "premiere_cep"     — Premiere Pro : no COM, no -r; CEP panel required for live execution

APP_CONFIG = {
    "photoshop": {
        "com_ids":    ["Photoshop.Application"],
        "exec_method": "DoJavaScriptFile",
        "template":   os.path.join(SCRIPT_DIR, "david_canvas.jsx"),
        "exe":        r"C:\Program Files\Adobe\Adobe Photoshop 2026\Photoshop.exe",
        "label":      "Photoshop",
    },
    "illustrator": {
        "com_ids":    ["Illustrator.Application"],
        "exec_method": "DoJavaScriptFile",
        "template":   os.path.join(SCRIPT_DIR, "david_illustrator.jsx"),
        "exe":        r"C:\Program Files\Adobe\Adobe Illustrator 2026\Support Files\Contents\Windows\Illustrator.exe",
        "label":      "Illustrator",
    },
    "indesign": {
        "com_ids":    ["{45112D7C-822C-400A-BA8C-D682F7FD0B8E}"],  # stable 2026 by CLSID: the ProgIDs now belong to the beta installs
        "exec_method": "doScript",
        "template":   os.path.join(SCRIPT_DIR, "david_indesign.jsx"),
        "exe":        r"C:\Program Files\Adobe\Adobe InDesign 2026\InDesign.exe",
        "label":      "InDesign",
    },
    "aftereffects": {
        "com_ids":    [],  # no COM — AfterFX.exe supports -r flag for script execution
        "exec_method": "subprocess_r",
        "template":   os.path.join(SCRIPT_DIR, "david_aftereffects.jsx"),
        "exe":        r"C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\AfterFX.exe",
        "label":      "After Effects",
    },
    "premiere": {
        "com_ids":    [],  # no COM, no -r flag — scripting requires a CEP panel
        "exec_method": "premiere_cep",
        "template":   os.path.join(SCRIPT_DIR, "david_premiere.jsx"),
        "exe":        r"C:\Program Files\Adobe\Adobe Premiere Pro 2026\Adobe Premiere Pro.exe",
        "label":      "Premiere Pro",
    },
}
# Discovered builds and COM ids replace the 2026 literals above when found (adobe_install.py, 2026-10-22); the literals are the fallback.
import adobe_install  # noqa: E402
for _app, _cfg in APP_CONFIG.items():
    _cfg["exe"] = adobe_install.exe_path(_app, _cfg["exe"])
    if _cfg["com_ids"]:
        _cfg["com_ids"] = adobe_install.com_ids(_app, fallback=_cfg["com_ids"])

# ── Per-app LLM prompts ───────────────────────────────────────────────────────

_PROMPTS = {
    "photoshop": """\
Output a single COMPACT JSON object (no newlines, no spaces) for Adobe Photoshop automation.
Start with {{ and end with }}. Raw JSON only — no markdown, no code fences.

Keys: canvas(width,height,dpi), document_name, background_color(hex), add_guides(bool),
native_save(bool,default:true — save as .psd), close_after(bool,default:true — close doc),
layer_groups([{{name, layers:[{{type:"solid_color",name,color,x,y,width,height}}
                              {{type:"text",name,content,color,size,x,y,bold}}]}}])

Example: {{"canvas":{{"width":1920,"height":1080,"dpi":72}},"document_name":"MyCanvas","background_color":"#1A1A2E","add_guides":true,"native_save":false,"close_after":false,"layer_groups":[{{"name":"Colors","layers":[{{"type":"solid_color","name":"Red","color":"#E94560","x":60,"y":60,"width":160,"height":160}}]}},{{"name":"Text","layers":[{{"type":"text","name":"Title","content":"Hello","color":"#FFFFFF","size":64,"x":60,"y":280}}]}}]}}

Design request: {request}
JSON:""",

    "illustrator": """\
Output a single COMPACT JSON object (no newlines, no spaces) for Adobe Illustrator automation.
Start with {{ and end with }}. Raw JSON only — no markdown, no code fences.

Keys: canvas(width,height), document_name, background_color(hex),
native_save(bool,default:true — save as .ai), close_after(bool,default:true — close doc),
layer_groups([{{name, layers:[{{type:"solid_color"|"text"|"shape",name,color,x,y,width,height,content,size,bold}}]}}])
Layer types: solid_color (rectangle shape with fill), text (point text), shape (same as solid_color)

Example: {{"canvas":{{"width":1920,"height":1080}},"document_name":"MyArt","background_color":"#1A1A2E","native_save":false,"close_after":false,"layer_groups":[{{"name":"Shapes","layers":[{{"type":"solid_color","name":"Swatch","color":"#E94560","x":60,"y":60,"width":200,"height":200}}]}},{{"name":"Labels","layers":[{{"type":"text","name":"Brand","content":"Demo User User","color":"#FFFFFF","size":72,"x":60,"y":320}}]}}]}}

Design request: {request}
JSON:""",

    "indesign": """\
Output a single COMPACT JSON object (no newlines, no spaces) for Adobe InDesign automation.
Start with {{ and end with }}. Raw JSON only — no markdown, no code fences.

All sizes in points (72pt = 1 inch). A4 = 595x842. Letter = 612x792.
Keys: canvas(width,height), document_name, background_color(hex),
native_save(bool,default:true — save as .indd), close_after(bool,default:true — close doc),
layer_groups([{{name, layers:[{{type:"solid_color"|"text",name,color,x,y,width,height,content,size,bold,italic}}]}}])

Example: {{"canvas":{{"width":595,"height":842}},"document_name":"Brochure","background_color":"#FFFFFF","native_save":false,"close_after":false,"layer_groups":[{{"name":"Content","layers":[{{"type":"solid_color","name":"Header Bar","color":"#1A1A2E","x":0,"y":0,"width":595,"height":120}},{{"type":"text","name":"Title","content":"Demo User User","color":"#FFFFFF","size":48,"x":40,"y":40,"width":520,"height":60}}]}}]}}

Design request: {request}
JSON:""",

    "aftereffects": """\
Output a single COMPACT JSON object (no newlines, no spaces) for Adobe After Effects automation.
Start with {{ and end with }}. Raw JSON only — no markdown, no code fences.

Keys: canvas(width,height), document_name, background_color(hex),
timing(duration_seconds,frame_rate),
native_save(bool,default:true — save as .aep), close_after(bool,default:true — quit AE),
layer_groups([{{name, layers:[{{type:"solid"|"solid_color"|"text"|"shape",name,color,x,y,width,height,content,size,bold,shape:"rect"|"ellipse",corner_radius}}]}}])

Example: {{"canvas":{{"width":1920,"height":1080}},"document_name":"Title Comp","background_color":"#0a0a0a","timing":{{"duration":10,"frame_rate":30}},"native_save":false,"close_after":false,"layer_groups":[{{"name":"Background","layers":[{{"type":"solid_color","name":"Dark BG","color":"#1A1A2E","width":1920,"height":1080}}]}},{{"name":"Title","layers":[{{"type":"text","name":"Main Title","content":"Demo User User","color":"#FFFFFF","size":96,"x":200,"y":400,"bold":true}}]}}]}}

Design request: {request}
JSON:""",

    "premiere": """\
Output a single COMPACT JSON object (no newlines, no spaces) for Adobe Premiere Pro scripting.
Start with {{ and end with }}. Raw JSON only — no markdown, no code fences.

Valid actions: "info"|"create_bin"|"import"|"add_markers"|"set_sequence_in_out"|"save"|"close_project"
Keys per action:
  info:                 {{"action":"info"}}
  create_bin:           {{"action":"create_bin","bin_name":"Assets"}}
  import:               {{"action":"import","file_paths":["C:/path/to/file.mp4"]}}
  add_markers:          {{"action":"add_markers","markers":[{{"time":0,"label":"Intro","comment":""}},{{"time":5,"label":"Main"}}]}}
  set_sequence_in_out:  {{"action":"set_sequence_in_out","in_point":0,"out_point":30}}
  save:                 {{"action":"save"}}
  close_project:        {{"action":"close_project"}}

Design request: {request}
JSON:""",
}


# ── COM helpers ───────────────────────────────────────────────────────────────

def _get_com_app(app_name: str):
    """Get or launch the COM object for an Adobe app. Raises on failure."""
    import win32com.client
    cfg = APP_CONFIG.get(app_name)
    if not cfg or not cfg["com_ids"]:
        raise RuntimeError(f"App '{app_name}' has no COM interface.")

    for com_id in cfg["com_ids"]:
        # Try GetActiveObject first (connect to running instance)
        try:
            return win32com.client.GetActiveObject(com_id)
        except Exception:
            pass

    # Nothing running — launch via Dispatch
    for com_id in cfg["com_ids"]:
        try:
            log.info(f"{cfg['label']} not running — launching via COM...")
            app = win32com.client.Dispatch(com_id)
            try:
                app.Visible = True
            except Exception:
                pass
            return app
        except Exception:
            pass

    raise RuntimeError(
        f"Cannot connect to {cfg['label']} via COM. "
        f"Is it installed? Tried: {cfg['com_ids']}"
    )


def is_app_running(app_name: str) -> bool:
    """Return True if the named Adobe app is reachable via COM."""
    if not APP_CONFIG.get(app_name, {}).get("com_ids"):
        return False  # premiere has no COM
    try:
        import win32com.client
        for com_id in APP_CONFIG[app_name]["com_ids"]:
            try:
                win32com.client.GetActiveObject(com_id)
                return True
            except Exception:
                pass
        return False
    except Exception:
        return False


# ── Premiere queue (CEP panel picks this up) ──────────────────────────────────

PREMIERE_QUEUE = str(DAVID_DATA / "premiere_queue.json")

def _premiere_queue_command(spec: dict) -> dict:
    ts     = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    cmd_id = f"cmd_{ts}"
    cmd    = {"id": cmd_id, "status": "pending",
              "created_at": datetime.datetime.now().isoformat(), **spec}
    try:
        if os.path.exists(PREMIERE_QUEUE):
            with open(PREMIERE_QUEUE, encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"commands": []}
        data["commands"].append(cmd)
        with open(PREMIERE_QUEUE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        log.info(f"Premiere: queued command {cmd_id} (action={spec.get('action','?')})")
        return {"ok": True,
                "message": f"Queued for Premiere CEP panel: {spec.get('action','?')} [{cmd_id}]",
                "result": "queued", "cmd_id": cmd_id}
    except Exception as e:
        return {"ok": False, "message": f"Queue write failed: {e}", "result": ""}


# ── JSX execution (all apps) ──────────────────────────────────────────────────

def execute_jsx(jsx_code: str, app_name: str = "photoshop") -> dict:
    """
    Execute JSX in the target Adobe app. Each app uses its own COM method:
      photoshop / illustrator → DoJavaScriptFile(path)
      indesign               → doScript(path, 1246973031)   [JS language constant]
      aftereffects           → DoScript(File_object)
      premiere               → subprocess -r flag (launches, does not control live)
    """
    cfg      = APP_CONFIG.get(app_name)
    method   = cfg["exec_method"] if cfg else "DoJavaScriptFile"
    ts       = datetime.datetime.now().strftime("%H%M%S%f")
    jsx_path = os.path.join(SCRIPT_DIR, f"_david_{app_name}_{ts}.jsx")

    try:
        with open(jsx_path, "w", encoding="utf-8") as f:
            f.write(jsx_code)
        log.info(f"Running JSX in {cfg['label'] if cfg else app_name}: {os.path.basename(jsx_path)}")

        if method == "subprocess_r":
            # After Effects only — AfterFX.exe -r runs script in the open (or new) instance
            exe   = cfg["exe"]
            label = cfg.get("label", app_name)
            if not os.path.exists(exe):
                return {"ok": False, "message": f"{label} exe not found: {exe}", "result": ""}
            proc = subprocess.run([exe, "-r", jsx_path], capture_output=True, timeout=60)
            return {"ok": proc.returncode == 0,
                    "message": f"{label}: script dispatched." if proc.returncode == 0 else proc.stderr.decode(errors="ignore"),
                    "result": "subprocess_r"}

        if method == "premiere_cep":
            return {"ok": False, "message": "Premiere requires design_request() — raw JSX unsupported via CEP.", "result": ""}

        adobe = _get_com_app(app_name)

        if method == "DoJavaScriptFile":
            ret = adobe.DoJavaScriptFile(jsx_path)

        elif method == "doScript":
            # InDesign: doScript(script, language=javascript, params=None, undo_mode, undo_name)
            JS_LANGUAGE = 1246973031  # ScriptLanguage.JAVASCRIPT
            ret = adobe.DoScript(jsx_path, JS_LANGUAGE)

        elif method == "DoScript":
            # After Effects — try DoScript(file) then fall back to executeScript(text)
            try:
                ret = adobe.DoScript(jsx_path)
            except Exception:
                with open(jsx_path, encoding="utf-8") as f:
                    content = f.read()
                try:
                    ret = adobe.executeScript(content)
                except Exception as e2:
                    return {"ok": False, "message": f"AE script execution failed: {e2}", "result": ""}
        else:
            return {"ok": False, "message": f"Unknown exec_method: {method}", "result": ""}

        ret_str = str(ret) if ret is not None else ""
        log.info(f"JSX result: {ret_str[:120]}")

        if ret_str.startswith("DAVID_ERR:"):
            return {"ok": False, "message": ret_str[10:], "result": ret_str}
        return {"ok": True, "message": "Executed successfully.", "result": ret_str}

    except Exception as e:
        log.error(f"execute_jsx failed: {e}")
        return {"ok": False, "message": str(e), "result": ""}
    finally:
        try:
            os.remove(jsx_path)
        except Exception:
            pass


# ── Spec → JSX ────────────────────────────────────────────────────────────────

def spec_to_jsx(spec: dict, app_name: str = "photoshop") -> str:
    """Load the app's JSX template and inject the spec JSON."""
    cfg      = APP_CONFIG.get(app_name, {})
    template = cfg.get("template", os.path.join(SCRIPT_DIR, "david_canvas.jsx"))
    with open(template, encoding="utf-8") as f:
        jsx = f.read()
    spec_json = json.dumps(spec, ensure_ascii=False, indent=2)
    return jsx.replace("__DAVID_SPEC__", spec_json)


def _default_spec(app_name: str) -> dict:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = {
        "photoshop": {
            "canvas": {"width": 1920, "height": 1080, "dpi": 72},
            "document_name": f"David_PS_{ts}",
            "background_color": "#1A1A2E", "add_guides": True,
            "layer_groups": [{"name": "Brand Colors", "layers": [
                {"type":"solid_color","name":"Primary","color":"#E94560","x":60,"y":60,"width":160,"height":160},
                {"type":"solid_color","name":"Secondary","color":"#0F3460","x":240,"y":60,"width":160,"height":160},
            ]}, {"name": "Typography", "layers": [
                {"type":"text","name":"Heading","content":"Demo User User","color":"#FFFFFF","size":64,"bold":True,"x":60,"y":280},
            ]}],
            "save_path": os.path.join(OUTPUT_DIR, f"canvas_{ts}.png"),
            "native_save": True, "close_after": True,
        },
        "illustrator": {
            "canvas": {"width": 1920, "height": 1080},
            "document_name": f"David_AI_{ts}",
            "background_color": "#1A1A2E",
            "layer_groups": [{"name": "Brand Colors", "layers": [
                {"type":"solid_color","name":"Red Swatch","color":"#E94560","x":60,"y":60,"width":160,"height":160},
                {"type":"solid_color","name":"Blue Swatch","color":"#0F3460","x":240,"y":60,"width":160,"height":160},
            ]}, {"name": "Typography", "layers": [
                {"type":"text","name":"Brand Name","content":"Demo User User","color":"#FFFFFF","size":72,"x":60,"y":320},
            ]}],
            "save_path": os.path.join(OUTPUT_DIR, f"illus_{ts}.png"),
            "native_save": True, "close_after": True,
        },
        "indesign": {
            "canvas": {"width": 595, "height": 842},
            "document_name": f"David_ID_{ts}",
            "background_color": "#FFFFFF",
            "layer_groups": [{"name": "Content", "layers": [
                {"type":"solid_color","name":"Header Bar","color":"#1A1A2E","x":0,"y":0,"width":595,"height":120},
                {"type":"text","name":"Title","content":"Demo User User","color":"#FFFFFF","size":48,"x":40,"y":35,"width":520,"height":60},
                {"type":"text","name":"Body","content":"Creative Direction & Brand Strategy","color":"#333333","size":18,"x":40,"y":160,"width":520,"height":200},
            ]}],
            "native_save": True, "close_after": True,
        },
        "aftereffects": {
            "canvas": {"width": 1920, "height": 1080},
            "document_name": f"David_AE_{ts}",
            "background_color": "#0a0a0a",
            "timing": {"duration": 10, "frame_rate": 30},
            "layer_groups": [
                {"name": "BG", "layers": [{"type":"solid_color","name":"Dark BG","color":"#1A1A2E","width":1920,"height":1080}]},
                {"name": "Title", "layers": [{"type":"text","name":"Main Title","content":"Demo User User","color":"#FFFFFF","size":96,"x":200,"y":440,"bold":True}]},
            ],
            "native_save": True, "close_after": True,
        },
        "premiere": {
            "action": "info",
        },
    }
    return base.get(app_name, base["photoshop"])


# ── LLM → Spec ────────────────────────────────────────────────────────────────

def request_to_spec(natural_language: str, app_name: str = "photoshop") -> dict:
    prompt_template = _PROMPTS.get(app_name, _PROMPTS["photoshop"])
    prompt          = prompt_template.format(request=natural_language)

    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"num_ctx": num_ctx_for(MODEL), "temperature": 0, "num_predict": 3000}
    }
    req = urllib.request.Request(
        OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = json.loads(r.read())["message"]["content"].strip()

    log.info(f"LLM raw (first 120): {repr(raw[:120])}")

    # Strip code fences
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                raw = part
                break

    # Extract first complete JSON object
    start = raw.find("{")
    if start == -1:
        raise ValueError("No JSON found in LLM response")
    depth, end = 0, -1
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        raise ValueError("Unterminated JSON in LLM response")

    spec = json.loads(raw[start:end])
    if "save_path" not in spec and app_name in ("photoshop", "illustrator"):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        spec["save_path"] = os.path.join(OUTPUT_DIR, f"{app_name}_{ts}.png")
    return spec


# ── Public API ────────────────────────────────────────────────────────────────

RETIRED_MESSAGE = ("The typed-sentence Adobe route was retired on 2026-10-10. Adobe work goes through the Design portal "
                   "(design_runner.py): fixed, tested operations, a plan first, and the designer's yes before anything runs.")


def design_request(text: str, app_name: str = "photoshop", dry_run: bool = False) -> dict:
    return {"ok": False, "retired": True, "message": RETIRED_MESSAGE, "app": app_name}


def execute_raw_jsx(jsx_code: str, app_name: str = "photoshop") -> dict:
    return {"ok": False, "retired": True, "message": RETIRED_MESSAGE, "app": app_name}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="David Adobe Bridge")
    parser.add_argument("request", nargs="*", help="Design request in natural language")
    parser.add_argument("--app",     default="photoshop",
                        choices=list(APP_CONFIG.keys()),
                        help="Target Adobe app (default: photoshop)")
    parser.add_argument("--status",  action="store_true", help="Check all app connections")
    parser.add_argument("--dry-run", action="store_true", help="Show spec/JSX without executing")
    args = parser.parse_args()

    if args.status:
        print("  Adobe app COM status:")
        for name, cfg in APP_CONFIG.items():
            if cfg["com_ids"]:
                ok = is_app_running(name)
                print(f"    {cfg['label']:16s} {'CONNECTED' if ok else 'not running'}")
            else:
                print(f"    {cfg['label']:16s} subprocess only (open it manually first)")
        sys.exit(0)

    if not args.request:
        parser.print_help()
        sys.exit(0)

    text   = " ".join(args.request)
    result = design_request(text, app_name=args.app, dry_run=args.dry_run)

    if result["ok"]:
        print(f"  OK — {result.get('app','')}: {result.get('message','')}")
        if result.get("save_path"):
            print(f"  Saved: {result['save_path']}")
        if args.dry_run:
            print(f"\n  Spec:\n{json.dumps(result['spec'], indent=2, ensure_ascii=False)[:800]}")
    else:
        print(f"  FAILED ({result.get('app',args.app)}): {result.get('message','')}")
