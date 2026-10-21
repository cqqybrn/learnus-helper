"""K-MOOC VOD progress without decoding: paced mod/vod/action.php trackDetail.

Server credits ~60s of progress per state=8 when wall-clock gap matches the player
interval (amd.progress last arg, usually 60000ms). Large position jumps return code 100
but do not increase attendance %.

Use ubonattend report % * duration as start position (not amd.progress resume pos).
"""
import asyncio
import os
import re
import sys
import time

import requests

LMS = "https://lms.kmooc.kr"
VOD = os.environ.get("KMOOC_VOD", "2326505")
COURSE = os.environ.get("KMOOC_COURSE", "20515")


async def login_page():
    from playwright.async_api import async_playwright

    uid = os.environ.get("KMOOC_ID", "ying11@ewha.ac.kr")
    pw = os.environ["KMOOC_PW"]
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-gpu", "--mute-audio"])
        context = await browser.new_context()
        page = await context.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await page.goto("https://www.kmooc.kr/login", wait_until="networkidle")
        await page.fill("#userid", uid)
        await page.fill("#password", pw)
        await page.locator("button.button_login.btn_submit").click()
        await page.wait_for_timeout(4000)
        return browser, context, page


async def report_pct(page, cmid):
    await page.goto(
        f"{LMS}/local/ubonattend/report.php?id={COURSE}",
        wait_until="networkidle",
        timeout=90000,
    )
    pct = await page.evaluate(
        """(cmid) => {
        let val = 0;
        document.querySelectorAll('table tr').forEach(tr => {
            const a = tr.querySelector('a[href*="mod/vod/view.php"]');
            if (!a) return;
            const id = (a.href.match(/id=(\\d+)/) || [])[1];
            if (id !== cmid) return;
            const cells = [...tr.querySelectorAll('td')].map(c => c.innerText.trim());
            const p = cells.find(c => /^\\d{1,3}(\\.\\d+)?%$/.test(c));
            if (p) val = parseFloat(p);
        });
        return val;
    }""",
        cmid,
    )
    return float(pct)


def parse_viewer(html):
    m = re.search(r"amd\.progress\((.*?)\);", html)
    if not m:
        raise RuntimeError("no amd.progress on viewer")
    parts = re.findall(r'"(.*?)"|(-?\d+(?:\.\d+)?)|(true|false|null)', m.group(1))
    args = [a or b or c for a, b, c in parts]
    attempt = args[2]
    courseid = args[5]
    cmid = args[6]
    interval_ms = int(args[9]) if str(args[9]).isdigit() else 60000
    sess = re.search(r'"sesskey":"([^"]+)"', html).group(1)
    return courseid, cmid, attempt, sess, interval_ms


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
    data = r.json()
    print(f"state={state} {start:.1f}->{end:.1f} {data.get('msg', data)}")
    return data


async def run():
    browser, context, page = await login_page()
    pct = await report_pct(page, VOD)
    await page.goto(f"{LMS}/mod/vod/viewer.php?id={VOD}", wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(1500)
    html = await page.content()
    cookies = await context.cookies()
    await browser.close()

    courseid, cmid, attempt, sesskey, interval_ms = parse_viewer(html)
    dur = float(os.environ.get("KMOOC_DUR", "1327"))
    interval = float(os.environ.get("KMOOC_INTERVAL", str(interval_ms / 1000)))
    step = float(os.environ.get("KMOOC_STEP", str(min(60, interval))))
    max_steps = int(os.environ.get("KMOOC_MAX_STEPS", "0"))
    start = float(os.environ.get("KMOOC_START", str(dur * pct / 100.0)))

    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path") or "/")

    print(f"vod={VOD} report={pct:.2f}% start={start:.1f}s dur={dur:.0f}s interval={interval}s step={step}s")
    track(s, courseid, cmid, sesskey, attempt, "3", start, start)
    cur = start
    n = 0
    while cur < dur - 0.5:
        if max_steps and n >= max_steps:
            print("stopped: KMOOC_MAX_STEPS")
            break
        time.sleep(interval)
        nxt = min(dur, cur + step)
        track(s, courseid, cmid, sesskey, attempt, "8", cur, nxt)
        cur = nxt
        n += 1
    if cur >= dur - 0.5:
        track(s, courseid, cmid, sesskey, attempt, "10", dur, dur)
        print("done: sent ended")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
