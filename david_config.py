"""
david_config.py -- the ONE place that says where David lives on disk and where its local services are.

Nothing else in the repository hard-codes a drive letter or a user name. Every setting has an environment variable and a default that works from a fresh clone:

  DAVID_HOME               the repository folder (default: the folder this file is in). Source code, agents/, ui/, scripts/ live under it.
  DAVID_DATA               runtime state: the LanceDB index, SQLite files, logs, generated files, queues (default: <DAVID_HOME>/data). Git-ignored.
  DAVID_VAULTS             the folder holding the note vaults, one sub-folder per vault (default: <DAVID_HOME>/vaults). Git-ignored.
  OLLAMA_HOST              the local Ollama server (default: http://127.0.0.1:11434). 127.0.0.1, never "localhost": on some Windows machines
                           "localhost" resolves IPv6-first and costs ~2 s per HTTP call.
  DAVID_AUDIO_PYTHON       a Python interpreter that has demucs/torch for stem separation (default: the interpreter running David).
  DAVID_RESTAURANT_ROOT    the folder that holds the restaurant day folders (<MM-YYYY>/<DD-MM-YYYY>/) for the restaurant vertical.
"""
import os
import sys
from pathlib import Path

try:                                     # an optional .env next to this file (see .env.example); real environment variables win over it
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass


def _env_path(name, default):
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser().resolve() if value else Path(default)


DAVID_HOME = _env_path("DAVID_HOME", Path(__file__).resolve().parent)
DAVID_DATA = _env_path("DAVID_DATA", DAVID_HOME / "data")
DAVID_VAULTS = _env_path("DAVID_VAULTS", DAVID_HOME / "vaults")

COMFYUI_DIR = _env_path("DAVID_COMFYUI_DIR", DAVID_HOME / "ComfyUI")     # a ComfyUI install, used by the generate.* operations (optional)

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "").strip() or "http://127.0.0.1:11434"
if not OLLAMA_HOST.startswith("http"):
    OLLAMA_HOST = "http://" + OLLAMA_HOST          # Ollama itself accepts a bare host:port in OLLAMA_HOST

DAVID_AUDIO_PYTHON = os.environ.get("DAVID_AUDIO_PYTHON", "").strip() or sys.executable

DAVID_RESTAURANT_ROOT = _env_path("DAVID_RESTAURANT_ROOT", DAVID_HOME / "examples" / "demo_bistro")


def ensure_data_dir():
    """Create DAVID_DATA if it does not exist yet (called by the modules that write there)."""
    DAVID_DATA.mkdir(parents=True, exist_ok=True)
    return DAVID_DATA


_policy_cache = {}


def load_policy():
    """config/vaults.toml if it exists, else config/vaults.example.toml: the vault list, the per-agent namespaces, the DLP patterns and the
    knowledge-privacy markers (see that file). Parsed once."""
    if "p" not in _policy_cache:
        import tomllib
        for name in ("vaults.toml", "vaults.example.toml"):
            path = DAVID_HOME / "config" / name
            if path.exists():
                with open(path, "rb") as f:
                    _policy_cache["p"] = tomllib.load(f)
                _policy_cache["path"] = str(path)
                break
        else:
            raise FileNotFoundError(f"no policy file: expected {DAVID_HOME / 'config' / 'vaults.example.toml'}")
    return _policy_cache["p"]


# ── Firewalled vaults: config/vaults.toml [boundaries].firewalled (2026-10-20, audit finding S-08) ──────────────────────────
# A firewalled vault is never imported into from outside folders, never written by the tool loop, left out of every scan that
# has no domain, and its folder path counts as private for the DLP pre-flight. Each of those gates reads these helpers, so
# renaming the vault or adding a second one in the policy file is enough. Which AGENTS may read a firewalled vault is a separate
# setting: [agent_namespaces].

def _norm_vault(name):
    return "".join(c for c in str(name).lower() if c.isalnum())


def firewalled_vaults():
    """The vault ids listed in [boundaries].firewalled (lower case). A policy without the key firewalls nothing."""
    return frozenset(str(v).lower() for v in load_policy().get("boundaries", {}).get("firewalled", []))


def is_firewalled_vault(name):
    """True for a firewalled vault however it is spelled: its id ("research") or its folder name ("Research")."""
    folders = load_policy().get("vaults", {})
    n = _norm_vault(name)
    return any(n in (_norm_vault(v), _norm_vault(folders.get(v, v))) for v in firewalled_vaults())


def firewalled_folders():
    """Folder names (under DAVID_VAULTS) of the firewalled vaults, e.g. ["Research"]."""
    folders = load_policy().get("vaults", {})
    return sorted(folders.get(v, v) for v in firewalled_vaults())


def default_scan_filter():
    """LanceDB filter for a scan over 'everything' (no domain given): memory episodes and every firewalled vault left out."""
    return " AND ".join(f"vault != '{v}'" for v in ["memory", *sorted(firewalled_vaults())])


def private_vault_folders():
    """[boundaries].private_vault_folders plus every firewalled vault's folder: paths inside them stay local (DLP)."""
    listed = list(load_policy().get("boundaries", {}).get("private_vault_folders", []))
    return listed + [f for f in firewalled_folders() if f not in listed]
