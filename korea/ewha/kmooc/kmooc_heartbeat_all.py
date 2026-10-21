#!/usr/bin/env python3
"""Run paced trackDetail for every pending VOD in KMOOC_COURSE (no video decode)."""
import asyncio
import os
import re
import sys
import time
from datetime import datetime

import requests

LMS = "https://lms.kmooc.kr"
COURSE = os.environ.get("KMOOC_COURSE", "20515")
INTERVAL = float(os.environ.get("KMOOC_INTERVAL", "63"))
STEP = float(os.environ.get("KMOOC_STEP", "60"))
LOG_PATH = os.environ.get(
    "KMOOC_PACED_LOG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "kmooc_paced_all.log"),
)


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def login(browser, page, uid, pw):
    page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
    await page.goto("https://www.kmooc.kr/login", wait_until="networkidle", timeout=60000)
    await page.fill("#userid", uid)
    await page.fill("#password", pw)
    await page.locator("button.button_login.btn_submit").click()
    await page.wait_for_timeout(4000)


async def pending_vods(page):
    await page.goto(f"{LMS}/local/ubonattend/report.php?id={COURSE}", wait_until="networkidle", timeout=90000)
    for _ in range(3):
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(800)
    return await page.evaluate(
        """() => {
        const out = [];
        document.querySelectorAll('table tr').forEach(tr => {
            const link = tr.querySelector('a[href*="mod/vod/view.php"]');
            if (!link) return;
            const cells = [...tr.querySelectorAll('td')].map(c => c.innerText.trim());
            const pct = cells.find(c => /^\\d{1,3}(\\.\\d+)?%$/.test(c));
            const val = pct ? parseFloat(pct) : 0;
            if (val >= 99.99) return;
            out.push({
                title: link.innerText.trim(),
                id: (link.href.match(/id=(\\d+)/)||[])[1],
                pct: pct || "0%",
                val,
            });
        });
        return out;
    }"""
    )


async def video_duration(page):
    for _ in range(40):
        dur = await page.evaluate(
            """async () => {
            function pick() {
                const v = document.querySelector('video');
                if (v && v.duration && isFinite(v.duration) && v.duration > 0) return v.duration;
                for (const fr of document.querySelectorAll('iframe')) {
                    try {
                        const d = fr.contentDocument;
                        const w = d && d.querySelector('video');
                        if (w && w.duration && isFinite(w.duration)) return w.duration;
                    } catch (e) {}
                }
                return null;
            }
            return pick();
        }"""
        )
        if dur and dur > 30:
            return float(dur)
        await page.wait_for_timeout(1000)
    return float(os.environ.get("KMOOC_DUR_FALLBACK", "1327"))


def parse_viewer(html):
    m = re.search(r"amd\.progress\((.*?)\);", html)
    if not m:
        raise RuntimeError("no amd.progress")
    parts = re.findall(r'"(.*?)"|(-?\d+(?:\.\d+)?)|(true|false|null)', m.group(1))
    args = [a or b or c for a, b, c in parts]
    return args[5], args[6], args[2], re.search(r'"sesskey":"([^"]+)"', html).group(1)


def session_from_cookies(cookies):
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path") or "/")
    return s


def track(s, courseid, cmid, sesskey, attempt, state, start, end):
    r = s.post(
        f"{LMS}/mod/vod/action.php",
        data={
            "courseid": courseid,
            "cmid": cmid,
            "sesskey": sesskey,
            "attempt": attempt,
            "state": state,
            "positionfrom": f"{start:.3f}",
            "positionto": f"{end:.3f}",
            "unixtime": str(int(time.time())),
            "coursemostype": "trackDetail",
        },
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{LMS}/mod/vod/viewer.php?id={cmid}"},
        timeout=30,
    )
    return r.json()


async def refresh_track_session(page, context, s, vod_id):
    await page.goto(f"{LMS}/mod/vod/viewer.php?id={vod_id}", wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(1000)
    html = await page.content()
    courseid, cmid, attempt, sesskey = parse_viewer(html)
    for c in await context.cookies():
        s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path") or "/")
    return courseid, cmid, attempt, sesskey


async def paced_requests(page, context, s, vod_id, courseid, cmid, sesskey, attempt, dur, start_pct):
    start = dur * start_pct / 100.0
    log(f"  paced start={start:.0f}s dur={dur:.0f}s steps~={int((dur-start)/STEP)+1}")
    track(s, courseid, cmid, sesskey, attempt, "3", start, start)
    cur = start
    beat = 0
    while cur < dur - 0.5:
        await asyncio.sleep(INTERVAL)
        if beat and beat % 4 == 0:
            courseid, cmid, attempt, sesskey = await refresh_track_session(page, context, s, vod_id)
            log("  refreshed sesskey/cookies")
        nxt = min(dur, cur + STEP)
        data = track(s, courseid, cmid, sesskey, attempt, "8", cur, nxt)
        if data.get("code") == "399":
            courseid, cmid, attempt, sesskey = await refresh_track_session(page, context, s, vod_id)
            data = track(s, courseid, cmid, sesskey, attempt, "8", cur, nxt)
        if data.get("code") != "100":
            log(f"  ! track error: {data}")
        cur = nxt
        beat += 1
    track(s, courseid, cmid, sesskey, attempt, "10", dur, dur)
    log("  paced ended state=10")


async def process_one(page, context, item):
    vod_id = item["id"]
    title = item["title"]
    pct = float(item["val"])
    log(f">> {title} id={vod_id} report={pct}%")
    await page.goto(f"{LMS}/mod/vod/viewer.php?id={vod_id}", wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(2000)
    dur = await video_duration(page)
    html = await page.content()
    courseid, cmid, attempt, sesskey = parse_viewer(html)
    cookies = await context.cookies()
    s = session_from_cookies(cookies)
    await paced_requests(page, context, s, vod_id, courseid, cmid, sesskey, attempt, dur, pct)
    await page.goto(f"{LMS}/local/ubonattend/report.php?id={COURSE}", wait_until="networkidle", timeout=90000)
    new_pct = await page.evaluate(
        """(vod) => {
        let val = 0;
        document.querySelectorAll('table tr').forEach(tr => {
            const a = tr.querySelector('a[href*="mod/vod/view.php"]');
            if (!a) return;
            const id = (a.href.match(/id=(\\d+)/)||[])[1];
            if (id !== vod) return;
            const cells = [...tr.querySelectorAll('td')].map(c => c.innerText.trim());
            const p = cells.find(c => /^\\d{1,3}(\\.\\d+)?%$/.test(c));
            if (p) val = parseFloat(p);
        });
        return val;
    }""",
        vod_id,
    )
    log(f"  after report={new_pct}%")
    return new_pct >= 99.99


async def main():
    uid = os.environ.get("KMOOC_ID", "ying11@ewha.ac.kr")
    pw = os.environ.get("KMOOC_PW")
    if not pw:
        log("KMOOC_PW not set")
        sys.exit(1)
    from playwright.async_api import async_playwright

    log(f"course={COURSE} interval={INTERVAL}s step={STEP}s")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-gpu", "--mute-audio"])
        context = await browser.new_context()
        page = await context.new_page()
        await login(browser, page, uid, pw)
        while True:
            items = await pending_vods(page)
            if not items:
                log("all pending cleared")
                break
            log(f"pending count={len(items)}")
            ok = await process_one(page, context, items[0])
            if not ok:
                log("  ! still <100%, will retry same vod on next round")
        await browser.close()
    log("finished")


if __name__ == "__main__":
    asyncio.run(main())
