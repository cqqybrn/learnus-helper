#!/usr/bin/env python3
"""Append K-MOOC course progress to log every N minutes while paced run is active."""
import os
import subprocess
import sys
import time
from datetime import datetime

INTERVAL_MIN = int(os.environ.get("KMOOC_MONITOR_MIN", "5"))
LOG = os.environ.get("KMOOC_MONITOR_LOG", "kmooc_monitor.log")
ROOT = os.path.dirname(os.path.abspath(__file__))


def tick():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n========== {ts} ==========\n")
        f.flush()
        r = subprocess.run(
            [sys.executable, "kmooc_status.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "KMOOC_COURSE": os.environ.get("KMOOC_COURSE", "20515")},
        )
        f.write(r.stdout or "")
        if r.stderr:
            f.write(r.stderr)
        f.flush()
    print(ts, r.stdout.split("\n")[0] if r.stdout else "no output", flush=True)


def main():
    print(f"monitor every {INTERVAL_MIN}m -> {LOG}", flush=True)
    while True:
        try:
            tick()
        except Exception as e:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"monitor error: {e}\n")
        time.sleep(INTERVAL_MIN * 60)


if __name__ == "__main__":
    main()
