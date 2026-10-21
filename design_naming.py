"""design_naming.py -- industry-standard names for design projects, folders and files. Pure functions, offline.

Rules (the common studio standard): ASCII letters, digits, "_" "-" and "." only; no spaces; ISO dates (YYYY-MM-DD) so folders sort by time; a two-digit
version suffix (_v01, _v02) instead of "final" or "copy"; Client_Project_Asset_Spec_vNN for files; one numbered folder skeleton per project.
"""
import re
import datetime

FOLDERS = ["01_Brief", "02_Assets", "03_Working", "04_Previews", "05_Exports", "06_Delivery", "07_Archive"]
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
BAD_WORDS = ("final", "copy", "new", "untitled", "latest", "temp", "test2")
WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
MAX_NAME = 100


class BadName(ValueError):
    pass


def slug(text, what="name"):
    s = re.sub(r"[^A-Za-z0-9]+", " ", str(text or "")).split()
    if not s:
        raise BadName(f"The {what} needs a short English (letters and digits) label, for example 'SpringMenu'. Arabic or empty names are kept in the brief, not in file names.")
    return "".join(w[:1].upper() + w[1:] for w in s)[:40]


def project_id(client, project, date=None):
    d = date or datetime.date.today().isoformat()
    try:
        datetime.date.fromisoformat(d)
    except ValueError:
        raise BadName(f"The date '{d}' is not a real date written YYYY-MM-DD.")
    return f"{d}_{slug(client, 'client')}_{slug(project, 'project')}"


def file_name(client, project, asset, spec, version, ext):
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= 99:
        raise BadName("The version must be a whole number from 1 to 99 (written v01, v02, ...).")
    e = str(ext).lstrip(".").lower()
    if not re.fullmatch(r"[a-z0-9]{1,5}", e):
        raise BadName(f"'{ext}' is not a file extension.")
    parts = [slug(client, "client"), slug(project, "project"), slug(asset, "asset")]
    if spec:
        parts.append(slug(spec, "spec"))
    return "_".join(parts) + f"_v{version:02d}.{e}"


def check_name(name):
    """Returns a list of sentences describing what breaks the standard (empty list = fine)."""
    n = str(name)
    out = []
    if not n or n != n.strip():
        out.append("The name is empty or has a space at the start or end.")
        return out
    if " " in n:
        out.append("The name has a space; use '_' or '-'.")
    if not n.isascii():
        out.append("The name has non-English letters; file and folder names stay in English letters and digits.")
    if not SAFE.match(n.replace(" ", "_")) and n.isascii():
        out.append("The name has a character outside letters, digits, '_', '-' and '.' or starts with a symbol.")
    if n.endswith("."):
        out.append("The name ends with a dot, which Windows does not allow.")
    if len(n) > MAX_NAME:
        out.append(f"The name is longer than {MAX_NAME} characters.")
    stem = n.rsplit(".", 1)[0] if "." in n else n
    if stem.lower() in WINDOWS_RESERVED:
        out.append(f"'{stem}' is a reserved Windows name.")
    low = re.split(r"[_\-. ]+", stem.lower())
    for w in BAD_WORDS:
        if w in low:
            out.append(f"The word '{w}' does not belong in a file name; use a version number (_v02) instead.")
    return out


def check_file_name(name):
    out = check_name(name)
    if "." not in str(name):
        out.append("The file name has no extension.")
    elif not re.search(r"_v\d{2}\.[A-Za-z0-9]{1,6}$", str(name)):
        out.append("The file name does not end with a two-digit version before the extension (for example _v01.psd).")
    return out
