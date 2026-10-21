#!/usr/bin/env python3
"""Play K-MOOC LMS videos below 100% progress at 2x (learnus-style)."""
import asyncio
import os
import re

LMS = "https://lms.kmooc.kr"
COURSE = os.environ.get("KMOOC_COURSE", "20514")  # Semiconductor I


async def login(page, uid, pw):
    page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
    await page.goto("https://www.kmooc.kr/login", wait_until="networkidle", timeout=60000)
    await page.fill("#userid", uid)
    await page.fill("#password", pw)
    await page.locator("button.button_login.btn_submit").click()
    await page.wait_for_timeout(4000)
    if "/login" in page.url and "로그아웃" not in await page.inner_text("body"):
        raise RuntimeError("login failed")


async def pending_vods(page, cid):
    await page.goto(f"{LMS}/local/ubonattend/report.php?id={cid}", wait_until="networkidle", timeout=90000)
    await page.wait_for_timeout(8000)
    try:
        await page.wait_for_selector('a[href*="mod/vod/view.php"]', timeout=60000)
    except Exception:
        pass
    for _ in range(4):
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1500)
    items = await page.evaluate(
        """() => {
        const out = [];
        document.querySelectorAll('table tr').forEach(tr => {
            const link = tr.querySelector('a[href*=\"mod/vod/view.php\"]');
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
                href: link.href,
            });
        });
        return out;
    }"""
    )
    n = await page.locator('a[href*="mod/vod/view.php"]').count()
    if n == 0:
        return None
    return items


async def find_video_frame(page):
    for fr in page.frames:
        try:
            if await fr.locator("video").count():
                return fr
        except Exception:
            continue
    return None


async def play_viewer(page, vod_id, speed=2.0, title="", fast=True, start_pct=0.0):
    """fast: last ~90s only when start_pct>=95; else play from 0 at 2x for 학습구간."""
    view_url = f"{LMS}/mod/vod/view.php?id={vod_id}"
    url = f"{LMS}/mod/vod/viewer.php?id={vod_id}"
    out(f"\n>> {title} ({vod_id})")
    await page.goto(view_url, wait_until="networkidle", timeout=90000)
    await page.wait_for_timeout(1500)
    for sel in ('a[href*="viewer.php"]', 'a.btn-primary', 'button:has-text("학습")'):
        loc = page.locator(sel)
        if await loc.count():
            try:
                await loc.first.click(timeout=3000)
                await page.wait_for_timeout(2000)
                break
            except Exception:
                pass
    if "viewer.php" not in page.url:
        await page.goto(url, wait_until="networkidle", timeout=90000)
    await page.wait_for_timeout(3000)
    for _ in range(12):
        frame = await find_video_frame(page)
        if frame:
            break
        await page.wait_for_timeout(2500)
    frame = await find_video_frame(page)
    if not frame:
        out("  ! no video")
        return False

    use_fast = fast and start_pct >= 95.0
    tail = 90 if use_fast else 0
    rate = float(speed)
    init = await frame.evaluate(
        f"""async () => {{
        const v = document.querySelector('video');
        if (!v) return null;
        v.muted = true;
        const tail = {tail};
        const rate = {rate};
        const startPct = {start_pct};
        if (v.duration && isFinite(v.duration)) {{
            if (tail > 0) v.currentTime = Math.max(0, v.duration - tail);
            else if (startPct > 1 && startPct < 95) v.currentTime = v.duration * (startPct / 100) * 0.97;
            else v.currentTime = 0;
        }}
        v.playbackRate = rate;
        await v.play().catch(() => {{}});
        return {{dur: v.duration, cur: v.currentTime, rate: v.playbackRate}};
    }}"""
    )
    if not init:
        return False
    dur = float(init.get("dur") or 0)
    out(
        f"  mode={'tail' if use_fast else 'full'} dur={dur:.0f}s "
        f"from={init.get('cur',0):.0f}s rate={init.get('rate')} start_pct={start_pct}"
    )

    remain = max(0.0, dur - float(init.get("cur") or 0))
    max_wait = 120 if use_fast else int(remain / max(rate, 0.5) + 120)
    max_wait = max(max_wait, int(dur / max(rate, 0.5) + 90))
    max_wait = min(max_wait, 7200)
    elapsed = 0
    last_cur = float(init.get("cur") or 0)
    while elapsed < max_wait:
        await asyncio.sleep(5)
        elapsed += 5
        frame = await find_video_frame(page)
        if not frame:
            out("  frame missing, stop this video (no chain to next)")
            return False
        try:
            st = await frame.evaluate(
                f"""() => {{
                const v = document.querySelector('video');
                if (!v) return null;
                if (v.paused) v.play().catch(()=>{{}});
                if (v.playbackRate !== {rate}) v.playbackRate = {rate};
                return {{cur: v.currentTime, dur: v.duration, ended: v.ended}};
            }}"""
            )
        except Exception as e:
            out(f"  frame error: {e}")
            continue
        if not st:
            break
        cur = float(st["cur"])
        last_cur = cur
        d = float(st["dur"] or dur)
        out(f"  {cur:.0f}/{d:.0f}s ended={st.get('ended')}")
        if st.get("ended") or (d > 0 and cur >= d - 3):
            await frame.evaluate("() => { const v=document.querySelector('video'); if(v) v.pause(); }")
            await asyncio.sleep(8)
            out("  done")
            return True
    out("  timeout (may still count if heartbeat sent)")
    return False


def out(msg):
    print(msg, flush=True)
    with open("_kmooc_watch_log.txt", "a", encoding="utf-8") as f:
        f.write(str(msg) + "\n")


async def main():
    from playwright.async_api import async_playwright

    Path = __import__("pathlib").Path
    Path("_kmooc_watch_log.txt").write_text("", encoding="utf-8")
    uid = os.environ.get("KMOOC_ID", "ying11@ewha.ac.kr")
    pw = os.environ.get("KMOOC_PW", "")
    speed = float(os.environ.get("KMOOC_SPEED", "2.0"))
    limit = int(os.environ.get("KMOOC_LIMIT", "1"))
    fast_env = os.environ.get("KMOOC_FAST", "1") not in ("0", "false", "False")
    headless = os.environ.get("KMOOC_HEADLESS", "0") in ("1", "true", "True")
    if not pw:
        raise SystemExit("set KMOOC_PW")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--autoplay-policy=no-user-gesture-required",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-translate",
                "--mute-audio",
                "--no-first-run",
                "--renderer-process-limit=2",
            ],
        )
        page = await (await browser.new_context(locale="ko-KR")).new_page()
        await login(page, uid, pw)
        pending = await pending_vods(page, COURSE)
        if pending is None:
            out("attendance page has no vod links, stop")
            await browser.close()
            return
        if limit > 0:
            pending = pending[:limit]
        out(f"this run: {len(pending)} video(s), headless={headless}")
        for it in pending:
            out(f"  - {it['pct']} {it['title']}")
        ok = 0
        for it in pending:
            done = await play_viewer(
                page,
                it["id"],
                speed=speed,
                title=it["title"],
                fast=fast_env,
                start_pct=float(it.get("val") or 0),
            )
            if not done:
                out("stop: this video did not finish; next videos not started")
                break
            ok += 1
            await asyncio.sleep(2)
        pending2 = await pending_vods(page, COURSE)
        if pending2 is None:
            out("recheck failed (page empty), do not treat as finished")
        else:
            out(f"remaining pending: {len(pending2)}")
        out(f"played_ok={ok}")
        await browser.close()

    if os.environ.get("CYBER_SYNC", "0") not in ("0", "false", "False"):
        cyber_id = os.environ.get("EWHA_KMOOC_CYBER_ID", "146858")
        out(f"\n== Cyber 回填 (course {cyber_id}) ==")
        try:
            from ewha import (
                cyber_academic_sync,
                kmooc_cyber_filled_weeks,
                kmooc_progress_rows,
                logged_in,
                make_session,
            )

            cs = make_session()
            if not logged_in(cs):
                out("  skip: Cyber 未登录，请 MFA 后运行: python ewha.py kmooc --sync --id " + cyber_id)
            else:
                ok, msg = cyber_academic_sync(cs)
                out(f"  sync: {'OK' if ok else 'FAIL'} {msg}")
                if ok:
                    import time

                    time.sleep(5)
                    rows = kmooc_progress_rows(cs, cyber_id)
                    out(f"  cyber filled_weeks={kmooc_cyber_filled_weeks(rows)}")
        except Exception as e:
            out(f"  cyber sync error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
