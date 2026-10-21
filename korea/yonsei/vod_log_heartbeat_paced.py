#!/usr/bin/env python3
"""LearnUs lightweight VOD progress via type=vod_log (no video decode)."""
import os
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import learnus  # noqa: E402

from parse_vod_progress import meta_from_args, parse_amd_progress  # noqa: E402

BASE = learnus.BASE


def vod_log(s, meta, state, pos_from, pos_to):
    data = {
        "courseid": meta["courseid"],
        "cmid": meta["cmid"],
        "type": "vod_log",
        "track": meta["trackid"],
        "attempt": meta["attempt"],
        "state": str(state),
        "positionfrom": pos_from,
        "positionto": pos_to,
        "logtime": meta["logtime"],
    }
    r = s.post(
        f"{BASE}/mod/vod/action.php",
        data=data,
        timeout=30,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE}/mod/vod/viewer.php?id={meta['cmid']}",
        },
    )
    return r.json()


def main():
    uid = os.environ.get("LEARNUS_ID")
    pw = os.environ.get("LEARNUS_PW")
    if not uid or not pw:
        print("set LEARNUS_ID LEARNUS_PW")
        sys.exit(1)
    vid = os.environ.get("LEARNUS_VOD")
    if not vid:
        print("set LEARNUS_VOD")
        sys.exit(1)

    s = learnus.sso_login(uid, pw)
    r0 = s.get(f"{BASE}/mod/vod/viewer.php?id={vid}", timeout=60)
    args = parse_amd_progress(r0.text, cmid=vid)
    if not args:
        sys.exit("no amd.progress")
    meta = meta_from_args(args)

    interval = float(os.environ.get("LEARNUS_INTERVAL", str(meta["interval_ms"] / 1000)))
    step = float(os.environ.get("LEARNUS_STEP", "60"))
    max_steps = int(os.environ.get("LEARNUS_MAX_STEPS", "0"))
    start = float(os.environ.get("LEARNUS_START", "0"))

    print(f"vod={vid} dur={meta['dur']}s interval={interval}s")
    vod_log(s, meta, "1", 0, 0)
    vod_log(s, meta, "3", start, start)
    cur = start
    n = 0
    while cur < meta["dur"] - 0.5:
        if max_steps and n >= max_steps:
            print("stopped: LEARNUS_MAX_STEPS")
            break
        time.sleep(interval)
        nxt = min(meta["dur"], cur + step)
        print(vod_log(s, meta, "8", cur, nxt))
        cur = nxt
        n += 1
    if cur >= meta["dur"] - 0.5:
        vod_log(s, meta, "10", meta["dur"], meta["dur"])
        print("ended")


if __name__ == "__main__":
    main()
