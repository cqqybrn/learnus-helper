#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K-MOOC (www.kmooc.kr) 视频出勤 — 对应梨花 K-MOOC 学分课的真实刷课面。

CyberCampus 上的「半导体」等课只有公告/反馈/评价，视频在 K-MOOC 官网。
本脚本用 Playwright 在 kmooc.kr 登录后按课程单元倍速播放（对齐 learnus watch 思路）。

用法:
  set KMOOC_ID=你的kmooc邮箱或ID
  set KMOOC_PW=密码
  python kmooc.py search --q "반도체"
  python kmooc.py watch --course "반도체" --speed 2.0
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import math
import os
import re
import sys
import time

KMOOC = "https://www.kmooc.kr"
HERE = __import__("pathlib").Path(__file__).resolve().parent
STATE = HERE / ".kmooc.storage.json"

DEFAULT_SEARCH = {
    "semiconductor": "AI시대 진로를 위한 반도체 이해하기",
    "반도체": "AI시대 진로를 위한 반도체 이해하기",
}


def credentials():
    uid = os.environ.get("KMOOC_ID") or os.environ.get("EWHA_ID")
    pw = os.environ.get("KMOOC_PW") or os.environ.get("EWHA_PW")
    if not uid:
        uid = input("K-MOOC 账号 (建议 Ewha 邮箱): ").strip()
    if not pw:
        pw = getpass.getpass("K-MOOC 密码: ").strip()
    return uid, pw


async def _browser_context(headless=True):
    from playwright.async_api import async_playwright

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=headless,
        args=["--autoplay-policy=no-user-gesture-required"],
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
        )
    )
    if STATE.exists():
        try:
            import json

            state = json.loads(STATE.read_text(encoding="utf-8"))
            await context.add_cookies(state.get("cookies", []))
        except (OSError, ValueError, KeyError):
            pass
    return p, browser, context


async def _save_state(context):
    import json

    cookies = await context.cookies()
    STATE.write_text(json.dumps({"cookies": cookies}, ensure_ascii=False), encoding="utf-8")


async def cmd_search(args):
    p, browser, context = await _browser_context(headless=not args.show)
    page = await context.new_page()
    q = args.q or DEFAULT_SEARCH.get("반도체", "반도체")
    await page.goto(f"{KMOOC}/", timeout=60000)
    # 站点搜索框结构可能变化，先尝试常见选择器
    for sel in ['input[name="searchKeyword"]', "#searchKeyword", 'input[type="search"]']:
        if await page.locator(sel).count():
            await page.fill(sel, q)
            await page.keyboard.press("Enter")
            break
    else:
        await page.goto(f"{KMOOC}/view/course/courseList?searchKeyword={q}", timeout=60000)
    await page.wait_for_timeout(3000)
    links = await page.evaluate(
        """() => [...document.querySelectorAll('a[href*="course"]')].slice(0, 20)
        .map(a => ({href: a.href, text: a.innerText.trim().slice(0, 80)}))
        .filter(x => x.text)"""
    )
    print(f"搜索: {q}")
    for item in links:
        print(f"  - {item['text']}\n    {item['href']}")
    await _save_state(context)
    await browser.close()
    await p.stop()


async def _play_video_page(page, speed: float, target_sec: int | None):
    await page.wait_for_selector("video, iframe", timeout=30000)
    # 若 video 在 iframe 内，进入第一个含 video 的 frame
    frame = page
    for fr in page.frames:
        try:
            if await fr.locator("video").count():
                frame = fr
                break
        except Exception:
            continue

    init = await frame.evaluate(
        f"""() => {{
        const v = document.querySelector('video');
        if (!v) return {{found: false}};
        v.muted = true;
        v.playbackRate = {speed};
        v.play().catch(() => {{}});
        return {{found: true, duration: v.duration, current: v.currentTime}};
    }}"""
    )
    if not init or not init.get("found"):
        return False

    dur = int(init.get("duration") or 0)
    req = target_sec + 60 if target_sec else dur
    if dur and req > dur:
        req = dur

    while True:
        await asyncio.sleep(8)
        st = await frame.evaluate(
            f"""() => {{
            const v = document.querySelector('video');
            if (!v) return null;
            if (v.paused) v.play().catch(() => {{}});
            v.playbackRate = {speed};
            return {{current: v.currentTime, duration: v.duration, ended: v.ended}};
        }}"""
        )
        if not st:
            break
        cur = int(st.get("current") or 0)
        dur = int(st.get("duration") or dur or 1)
        pct = min(100, int(cur / dur * 100)) if dur else 0
        print(f"\r      ⏱️ {pct:3d}% {cur//60:02d}:{cur%60:02d}/{dur//60:02d}:{dur%60:02d}", end="", flush=True)
        if st.get("ended") or (req and cur >= req) or cur >= dur - 2:
            print("\n      ✅ 播放完成")
            await frame.evaluate("() => { const v=document.querySelector('video'); if(v) v.pause(); }")
            await asyncio.sleep(5)
            return True
    return False


async def cmd_watch(args):
    """登录 kmooc.kr 后打开匹配课程并倍速播放（实验性，需站点登录页适配）。"""
    uid, pw = credentials()
    speed = float(args.speed)
    keyword = args.course or DEFAULT_SEARCH["반도체"]
    p, browser, context = await _browser_context(headless=not args.show)
    page = await context.new_page()
    page.on("dialog", lambda d: print(f"  [K-MOOC] {d.message}"))
    await page.goto(f"{KMOOC}/login", timeout=60000)
    await page.fill("#userid", uid)
    await page.fill("#password", pw)
    await page.locator("button.button_login.btn_submit").click()
    await page.wait_for_timeout(4000)
    if "/login" in page.url:
        print("  K-MOOC 登录失败：请确认 KMOOC_ID 为注册邮箱，KMOOC_PW 为 K-MOOC 密码（非 Eureka 学号）。")
        await _save_state(context)
        await browser.close()
        await p.stop()
        return

    await page.goto(f"{KMOOC}/view/mycampus/myCourse", timeout=60000)
    await page.wait_for_timeout(3000)
    print(f"  已登录，当前: {page.url}")
    await cmd_search(argparse.Namespace(q=keyword, show=args.show))
    print(
        "\n⚠ K-MOOC 刷课需进入「我的课程 → 单元 → 视频」逐条播放。"
        "当前为实验骨架：已搜索并尝试登录，请用 --show 观察页面并反馈登录/播放器 DOM。"
    )
    await _save_state(context)
    await browser.close()
    await p.stop()


def main():
    parser = argparse.ArgumentParser(description="K-MOOC 视频出勤（梨花 K-MOOC 学分课）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="搜索课程")
    s.add_argument("--q", help="关键词")
    s.add_argument("--show", action="store_true", help="有界面浏览器")
    w = sub.add_parser("watch", help="登录并刷课（实验性）")
    w.add_argument("--course", help="课程名关键字，默认 반도체")
    w.add_argument("--speed", type=float, default=2.0)
    w.add_argument("--show", action="store_true")
    args = parser.parse_args()
    if args.cmd == "search":
        asyncio.run(cmd_search(args))
    else:
        asyncio.run(cmd_watch(args))


if __name__ == "__main__":
    main()
