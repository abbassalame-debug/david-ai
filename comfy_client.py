# comfy_client.py — David v9.0 Stage 4 Mode E
# Talks to ComfyUI server at localhost:8188 via REST API.
# Run ComfyUI first:  python <DAVID_HOME>\ComfyUI\main.py --listen
# Then use:          python comfy_client.py generate "a cinematic sunset over Lisbon"

from david_config import COMFYUI_DIR, DAVID_DATA
import sys
import json
import uuid
import time
import os
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding="utf-8")

COMFY_URL   = "http://127.0.0.1:8188"
OUTPUT_DIR  = str(DAVID_DATA / "generated")
SDXL_MODEL  = "sd_xl_base_1.0.safetensors"
HTTP_TIMEOUT = 15

os.makedirs(OUTPUT_DIR, exist_ok=True)


def is_comfy_running():
    try:
        urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=3)
        return True
    except Exception:
        return False


def queue_prompt(workflow: dict) -> str:
    client_id = str(uuid.uuid4())
    payload   = json.dumps({"prompt": workflow, "client_id": client_id}).encode()
    req       = urllib.request.Request(
        f"{COMFY_URL}/prompt",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        data = json.loads(resp.read())
    return data["prompt_id"]


def wait_for_image(prompt_id: str, timeout: int = 300) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{COMFY_URL}/history/{prompt_id}", timeout=HTTP_TIMEOUT) as resp:
                history = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2)
            continue
        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})
            for node_id, node_out in outputs.items():
                images = node_out.get("images", [])
                if images:
                    img = images[0]
                    img_url = f"{COMFY_URL}/view?filename={img['filename']}&subfolder={img.get('subfolder','')}&type={img.get('type','output')}"
                    with urllib.request.urlopen(img_url, timeout=HTTP_TIMEOUT) as r:
                        img_data = r.read()
                    out_path = os.path.join(OUTPUT_DIR, img["filename"])
                    with open(out_path, "wb") as f:
                        f.write(img_data)
                    return out_path
        time.sleep(2)
    return None


def build_sdxl_workflow(prompt: str, negative: str = "", steps: int = 25,
                         cfg: float = 7.0, width: int = 1024, height: int = 1024,
                         seed: int = -1) -> dict:
    if seed == -1:
        import random
        seed = random.randint(0, 2**32 - 1)

    return {
        "4": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": SDXL_MODEL}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": negative or "blurry, low quality, watermark, text",
                         "clip": ["4", 1]}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                         "latent_image": ["5", 0], "seed": seed, "steps": steps,
                         "cfg": cfg, "sampler_name": "dpmpp_2m",
                         "scheduler": "karras", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["4", 2]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "david_gen"}},
    }


def generate(prompt: str, **kwargs) -> str | None:
    if not is_comfy_running():
        print("  [Mode E] ComfyUI is not running.")
        print("  Start it with:  python <DAVID_HOME>\\ComfyUI\\main.py --listen --lowvram")
        print("  Then retry your request.\n")
        return None

    print(f"  [Mode E] Generating: {prompt[:80]}...")
    workflow  = build_sdxl_workflow(prompt, **kwargs)
    prompt_id = queue_prompt(workflow)
    print(f"  Queued — ID: {prompt_id[:8]}... waiting for GPU...\n")

    out_path = wait_for_image(prompt_id)
    if out_path:
        print(f"  Image saved: {out_path}\n")
    else:
        print("  Generation timed out or failed.\n")
    return out_path


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python comfy_client.py generate <prompt>")
        print("       python comfy_client.py status")
        sys.exit(0)

    if args[0] == "status":
        if is_comfy_running():
            with urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=HTTP_TIMEOUT) as r:
                stats = json.loads(r.read())
            vram = stats.get("system", {}).get("vram_total", 0) / 1024**3
            free = stats.get("system", {}).get("vram_free", 0) / 1024**3
            print(f"  ComfyUI running  |  VRAM: {free:.1f} GB free / {vram:.1f} GB total")
        else:
            model_path = str(COMFYUI_DIR / "models" / "checkpoints" / "sd_xl_base_1.0.safetensors")
            model_ok   = os.path.exists(model_path) and os.path.getsize(model_path) > 6_000_000_000
            print(f"  ComfyUI not running.")
            print(f"  SDXL model: {'OK' if model_ok else 'MISSING — download to ' + model_path}")
            print(f"  Start server: run war room [7] or:")
            print(f"    python {COMFYUI_DIR / 'main.py'} --listen --lowvram")

    elif args[0] == "generate" and len(args) >= 2:
        prompt = " ".join(args[1:])
        generate(prompt)

    else:
        print("Usage: python comfy_client.py generate <prompt>")
        print("       python comfy_client.py status")
