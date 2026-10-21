# code_guard.py — static validation of model-generated Python code
#
# Local 14B models write plausible-looking code that fails on contact:
# hallucinated stdlib functions (signal.start_loop_in_executor), parameters
# removed years ago (asyncio.wait_for(..., loop=...)), and plain syntax errors.
# This module catches those WITHOUT executing anything the model wrote:
#   1. ast.parse            — syntax
#   2. KNOWN_BAD patterns   — APIs that are deprecated/removed but still common
#                             in training data
#   3. attribute existence  — module.attr calls checked via hasattr() against
#                             the REAL installed module, for a fixed whitelist
#                             of stdlib modules only (importing a model-named
#                             third-party module would execute its top-level
#                             code — never do that)

import ast
import importlib
import re

# Modules we are willing to import to verify attribute existence. Stdlib only,
# no side effects on import. A module outside this set is simply not checked.
STDLIB_WHITELIST = {
    "asyncio", "signal", "os", "os.path", "sys", "json", "re", "time",
    "datetime", "pathlib", "subprocess", "threading", "sqlite3", "typing",
    "functools", "itertools", "collections", "math", "random", "logging",
    "shutil", "tempfile", "uuid", "hashlib", "base64", "string", "textwrap",
    "urllib", "urllib.request", "urllib.parse", "socket", "struct", "queue",
    "contextlib", "dataclasses", "enum", "abc", "io", "csv", "glob",
}

# (regex, message) — patterns that parse fine but fail at runtime on Py 3.10+.
KNOWN_BAD = [
    # Argument list may itself contain one level of nested parens (e.g. a call
    # expression as an argument: asyncio.wait_for(worker(), ..., loop=loop)) —
    # [^)]* alone stops at the first ')' and never sees a later loop= kwarg.
    (re.compile(r"\basyncio\.(?:wait_for|sleep|gather|wait|shield|Queue|Lock|Event|Condition|Semaphore)\("
                r"(?:[^()]|\([^()]*\))*\bloop\s*="),
     "the loop= parameter was removed from asyncio APIs in Python 3.10 — drop it"),
    (re.compile(r"\basyncio\.get_event_loop\(\)"),
     "asyncio.get_event_loop() is deprecated inside coroutines — use asyncio.get_running_loop()"),
    (re.compile(r"\basyncio\.async\("),
     "asyncio.async() was removed — use asyncio.ensure_future() or create_task()"),
    (re.compile(r"@asyncio\.coroutine"),
     "@asyncio.coroutine was removed in Python 3.11 — use `async def`"),
    (re.compile(r"\bcollections\.(?:Mapping|MutableMapping|Sequence|Iterable|Callable)\b"),
     "ABCs moved to collections.abc in Python 3.10 — collections.Mapping etc. no longer exist"),
]

_CODE_BLOCK_RE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_python_blocks(text: str) -> list:
    """Fenced ```python blocks from a chat response."""
    return [m.group(1) for m in _CODE_BLOCK_RE.finditer(text or "")]


def _collect_module_aliases(tree: ast.AST) -> dict:
    """{local alias -> real module name} for whitelisted stdlib imports."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in STDLIB_WHITELIST:
                    aliases[a.asname or a.name.split(".")[0]] = a.name
    return aliases


def validate_python(code: str) -> list:
    """Returns a list of human-readable issues; empty list == passes."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error at line {e.lineno}: {e.msg}"]

    issues = []
    for rx, msg in KNOWN_BAD:
        if rx.search(code):
            issues.append(msg)

    # Verify module.attr against the real installed module (whitelist only).
    aliases = _collect_module_aliases(tree)
    seen = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases
                and not node.attr.startswith("_")):
            modname, attr = aliases[node.value.id], node.attr
            if (modname, attr) in seen:
                continue
            seen.add((modname, attr))
            try:
                mod = importlib.import_module(modname)
            except ImportError:
                continue
            if not hasattr(mod, attr):
                issues.append(
                    f"{modname}.{attr} does not exist in the installed Python "
                    f"{'.'.join(map(str, __import__('sys').version_info[:2]))} "
                    f"— likely a hallucinated API"
                )
    return issues


def review(text: str) -> list:
    """Validate every python code block in a response. Returns all issues."""
    issues = []
    blocks = extract_python_blocks(text)
    for i, block in enumerate(blocks, 1):
        for issue in validate_python(block):
            prefix = f"code block {i}: " if len(blocks) > 1 else ""
            issues.append(prefix + issue)
    return issues
