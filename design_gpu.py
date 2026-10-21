"""design_gpu.py -- hands the graphics card over to a heavy local job (video, music) and gives it back.

David already has a VRAM controller: the modes of david_router (Mode E evicts every language model for maximum headroom, Mode A re-warms the daily pair). A heavy job asks
for Mode E before it starts ComfyUI and puts the previous mode back when it ends, even when it failed. If the controller cannot be loaded, the loaded Ollama models are
unloaded directly (keep_alive 0) and nothing is restored: they load again on the next question.
"""
from ollama_config import OLLAMA_HOST
import json
import time
import urllib.request

OLLAMA = OLLAMA_HOST


def _ollama(path, body=None, timeout=20):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8") or "{}")


def _unload_all():
    names = []
    try:
        names = [m["name"] for m in _ollama("/api/ps").get("models", [])]
    except Exception:
        return []
    for n in names:
        try:
            _ollama("/api/generate", {"model": n, "keep_alive": 0})
        except Exception:
            pass
    return names


def _wait_loaded(limit=45, sleep=time.sleep):
    """David's warm-up of the daily models runs in a background thread; a short-lived process would end before it finished, so give it up to `limit` seconds to show a loaded model."""
    waited = 0
    while waited < limit:
        try:
            if _ollama("/api/ps").get("models"):
                return True
        except Exception:
            pass
        sleep(2)
        waited += 2
    return False


class heavy_job:
    """with heavy_job(): ... the card is cleared on entry and the previous David mode is restored on exit. `controller` is injectable for tests."""

    def __init__(self, controller=None):
        self.controller, self.previous, self.via = controller, None, None

    def __enter__(self):
        try:
            dr = self.controller
            if dr is None:
                import david_router as dr
            self.previous = dr.get_mode()
            dr.set_mode("E")
            self.controller, self.via = dr, "mode E"
        except Exception:
            self.controller, self.via = None, "unload"
            _unload_all()
        return self

    def __exit__(self, *a):
        if self.controller is not None and self.previous:
            try:
                self.controller.set_mode(self.previous)
                if self.previous != "E":
                    _wait_loaded()
            except Exception:
                pass
        return False
