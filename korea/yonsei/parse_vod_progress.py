"""Parse LearnUs viewer amd.progress(...) arguments."""
import ast
import re


def parse_amd_progress(html, cmid=None):
    """Return tuple of progress() args; prefer block containing cmid when given."""
    matches = list(re.finditer(r"amd\.progress\((.*?)\);", html, re.S))
    chosen = None
    for m in matches:
        inner = m.group(1)
        if cmid and str(cmid) not in inner:
            continue
        chosen = m
        break
    if not chosen and matches:
        chosen = max(matches, key=lambda x: len(x.group(1)))
    if not chosen:
        return None
    inner = chosen.group(1)
    inner = re.sub(r"\btrue\b", "True", inner)
    inner = re.sub(r"\bfalse\b", "False", inner)
    inner = re.sub(r"\bnull\b", "None", inner)
    return ast.literal_eval(f"({inner},)")


def meta_from_args(args):
    return {
        "courseid": args[6],
        "cmid": args[7],
        "trackid": args[8],
        "attempt": args[9],
        "dur": float(args[10]),
        "interval_ms": int(args[12]),
        "logtime": args[22],
    }
