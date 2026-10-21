#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ewha-helper (梨花女子大学 CyberCampus 智能助学知识库助手)

像一个管理得井井有条的学霸一样：
1. 登录 Eureka 统一身份认证（SSO + 邮箱验证码），列出课程与未交作业。
2. 自动建立规范学习知识库文件夹，按课程、按周次整理课件与资料室文件。
3. 课件文档自动转 Markdown：.docx/.doc/.pptx/.pdf → 排版清晰的 .md，
   方便 AI Harness (Claude Code, Cursor 等) 深入阅读。
4. 全自动索引树：为每个单元、每门课及整个工作区生成结构化 README.md。
5. **智能出勤 (watch)**：未达标 VOD 按顺序无头倍速播放，上报出勤心跳（与 LearnUs 助手同源逻辑）。

不含自动答题、自动签到等非视频类刷课。请遵守学校学术诚信规定与平台服务条款。
"""

import argparse
import asyncio
import datetime as dt
import getpass
import os
import html
import http.cookiejar
import json
import math
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from html import unescape as html_unescape
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("缺少依赖，请先运行: pip install -r requirements.txt")
    sys.exit(1)

BASE = "https://cyber.ewha.ac.kr"
NSSO = "https://nsso.ewha.ac.kr"
HERE = Path(__file__).parent
COOKIE_FILE = HERE / ".ewha.cookies"
STAFF_ONLY = re.compile(r"교수|강사|조교|staff|instructor", re.I)

_session_state = {"sid": "", "pwd": ""}


# ============================================================ 基础工具
def clean(t):
    return re.sub(r"\s+", " ", t).strip()


def soup_of(s, url):
    r = s.get(url, timeout=45)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def safe_name(name, limit=120):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", clean(name)).strip(" ._")
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot + 1:]) if dot > 0 else (name, "")
    if dot and 0 < len(ext) <= 8:
        return stem[: limit - len(ext) - 1].rstrip() + "." + ext
    return name[:limit]


def session_get(s, url, **kw):
    return s.get(url, timeout=45, **kw)


def session_post(s, url, data=None, **kw):
    kw.setdefault("timeout", 45)
    return s.post(url, data=data, **kw)


# ============================================================ 登录（Eureka SSO）
class _LWPCookieJar(http.cookiejar.LWPCookieJar):
    def __init__(self, filename=None):
        super().__init__(filename)


def _seed_encrypt(payload, key):
    """调用本地 node 执行学校登录页的 SEED 加密脚本（密码不落盘）。"""
    crypto = session_get_global(f"{NSSO}/sso/resources/user/js/crypto/seed.js")
    crypto += "\n" + session_get_global(
        f"{NSSO}/sso/resources/user/js/crypto/pad-ansix923-min.js"
    )
    js = (
        crypto
        + "\nconst key="
        + json.dumps(key)
        + "; const data="
        + json.dumps(json.dumps(payload, separators=(",", ":")))
        + "; console.log(CryptoJS.SEED.encrypt(data,CryptoJS.enc.Hex.parse("
        "key.substring(0,64)),{iv:CryptoJS.enc.Hex.parse(key.substring(64,96)),"
        "mode:CryptoJS.mode.CBC,padding:CryptoJS.pad.AnsiX923}).ciphertext.toString());"
    )
    return subprocess.check_output(["node"], input=js.encode()).decode().strip()


# 一个仅用于加密脚本抓取的临时全局 session（不带登录 cookie）
_global_s = None


def session_get_global(url):
    global _global_s
    if _global_s is None:
        _global_s = requests.Session()
        _global_s.headers["User-Agent"] = "Mozilla/5.0"
    return _global_s.get(url, timeout=45).text


def make_session():
    """构造带 cookie 持久化的 session。"""
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    if COOKIE_FILE.exists():
        try:
            s.cookies = _load_lwp_cookies()
        except Exception:
            pass
    return s


def _load_lwp_cookies():
    jar = _LWPCookieJar(str(COOKIE_FILE))
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def _save_lwp_cookies(s):
    jar = _LWPCookieJar(str(COOKIE_FILE))
    for c in s.cookies:
        jar.set_cookie(c)
    jar.save(ignore_discard=True, ignore_expires=True)


def logged_in(s):
    try:
        r = s.get(BASE + "/", timeout=45)
        home = r.text
        if "course/view.php?id=" not in home:
            return False
        low = home.lower()
        return ("logout" in low) or ("로그아웃" in home) or ("退出登录" in home)
    except requests.RequestException:
        return False


def sso_login(s, sid, pwd, get_code=input):
    """完成 Eureka SSO 登录（含邮箱验证码）。返回是否成功。"""
    # 1. 进入首页 + 跳转入口，拿到 SSO 会话
    s.get(BASE + "/", timeout=45)
    s.get(BASE + "/index_newsso.php", timeout=45)
    s.post(
        f"{NSSO}/sso/user/login/link",
        data={"agt_id": "ewha-prod-cyber", "agt_url": BASE, "ismobile": "0"},
        timeout=45,
    )
    # 2. 取加密 key
    key = json.loads(s.post(f"{NSSO}/sso/user/login/init", data={}).text)["result_data"]
    # 3. 提交账号密码
    enc = _seed_encrypt({"login_id": sid, "login_pwd": pwd}, key)
    resp = json.loads(
        s.post(
            f"{NSSO}/sso/user/ewha/login/auth", data={"user_data": enc}
        ).text
    )
    if resp.get("data") != "2ndauth":
        print("  登录响应:", resp.get("code"), resp.get("data"))
        return False
    # 4. 触发邮箱验证码
    sent = json.loads(
        s.post(
            f"{NSSO}/sso/user/api/mfa/request",
            data={"user_data": _seed_encrypt({"crtfc_type": "mail"}, key)},
        ).text
    )
    if not sent.get("result"):
        print("  验证码发送失败:", sent)
        return False
    print("  验证码已发送到绑定邮箱。")
    code = get_code("  请输入邮箱验证码: ").strip()
    verified = json.loads(
        s.post(
            f"{NSSO}/sso/user/api/mfa/verify",
            data={
                "user_data": _seed_encrypt(
                    {"crtfc_type": "mail", "crtfc_no": code}, key
                )
            },
        ).text
    )
    if not verified.get("result"):
        print("  验证码校验失败:", verified)
        return False
    # 5. 换取 sso_code 并回跳
    page = s.get(f"{NSSO}/sso/user/login/link", timeout=45).text
    m = re.search(r'name="sso_code" value="([^"]+)"', page)
    if not m:
        print("  未取得 sso_code")
        return False
    s.post(
        f"{BASE}/passni5/login_proc.php",
        data={"sso_code": m.group(1), "ismobile": "0"},
        timeout=45,
    )
    _save_lwp_cookies(s)
    return logged_in(s)


def ensure_login(s, get_code=input):
    if logged_in(s):
        print("  已复用保存的会话。")
        return True
    sid = (
        _session_state["sid"]
        or os.environ.get("EWHA_ID")
        or input("学号: ").strip()
    )
    pwd = (
        _session_state["pwd"]
        or os.environ.get("EWHA_PW")
        or getpass.getpass("密码: ").strip()
    )
    _session_state["sid"], _session_state["pwd"] = sid, pwd
    print("  正在登录 Eureka ...")
    ok = sso_login(s, sid, pwd, get_code)
    print("  登录成功。" if ok else "  登录失败。")
    return ok


def cmd_login(args):
    s = make_session()
    if ensure_login(s):
        print("登录状态有效。")


# ============================================================ 课程解析
def list_courses(s, args=None):
    home = soup_of(s, BASE + "/")
    courses = {}
    for a in home.select('a[href*="/course/view.php?id="]'):
        m = re.search(r"id=(\d+)", a["href"])
        if not m:
            continue
        cid = m.group(1)
        title = clean(a.get_text(" "))
        if title and (cid not in courses or len(title) > len(courses[cid])):
            courses[cid] = title
    if args is not None and getattr(args, "course", None):
        courses = {
            c: t
            for c, t in courses.items()
            if args.course == c or args.course.lower() in t.lower()
        }
    return courses


def short_course_name(title, cid):
    m = re.search(r"K-MOOC[:：]?\s*(.+?)\s*\(", title)
    return safe_name(m.group(1) if m else title, 60) or cid


def activity_name(li):
    el = li.select_one(".instancename")
    if el and el.contents and isinstance(el.contents[0], str):
        return clean(el.contents[0])
    return clean(el.get_text()) if el else "?"


def section_label(sec):
    el = sec.select_one(".sectionname")
    name = clean(el.get_text() if el else sec.get("aria-label", ""))
    m = re.search(r"(\d+)\s*주차|(\d+)\s*周次|week\s*(\d+)", name, re.I)
    if m:
        num = int(m.group(1) or m.group(2) or m.group(3))
        return f"{num:02d}주차", name
    return f"00_{safe_name(name, 30)}", name


def parse_course(s, cid):
    page = soup_of(s, f"{BASE}/course/view.php?id={cid}")
    sections, activities, seen = [], [], set()
    for sec in page.select("li.section.main"):
        folder, name = section_label(sec)
        sections.append((folder, name))
        for li in sec.select("li.activity"):
            mid = li.get("id", "").replace("module-", "")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            mod = next(
                (c[8:] for c in li.get("class", []) if c.startswith("modtype_")),
                "?",
            )
            icon = li.select_one(".autocompletion img, .togglecompletion img")
            done = None
            if icon:
                done = bool(
                    re.search(r"completion-(auto|manual)-(y|pass)", icon.get("src", ""))
                )
            period = li.select_one(".displayoptions .text-ubstrap")
            activities.append(
                {
                    "id": mid,
                    "mod": mod,
                    "name": activity_name(li),
                    "done": done,
                    "open": not li.select_one(".dimmed, .dimmed_text"),
                    "period": clean(period.get_text(" ")) if period else "",
                    "section": folder,
                }
            )
    return sections, activities


# ============================================================ 作业状态
def assignment_status(s, mid):
    soup = soup_of(s, f"{BASE}/mod/assign/view.php?id={mid}")
    info = {}
    for tr in soup.select("table tr"):
        cells = [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        if len(cells) >= 2:
            info[cells[0]] = cells[1]
    status = (
        info.get("作业状态")
        or info.get("제출 여부")
        or info.get("Submission status", "")
    )
    due = info.get("到期日期") or info.get("종료 일시") or info.get("Due date", "")
    remain = info.get("剩余时间") or info.get("마감까지 남은 기한") or info.get(
        "Time remaining", ""
    )
    submitted = bool(status) and not re.search(
        r"没有尝试|未提交|제출 안 함|No attempt|Nothing has been submitted", status
    )
    due_dt = None
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", due)
    if m:
        due_dt = dt.datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
    return submitted, status, due_dt, remain


def attendance_report(s, cid):
    """ubcompletion 视频出勤表 {活动名: (要求时长, 已看时长, 达标 O/X)}"""
    out = {}
    try:
        ps = soup_of(s, f"{BASE}/report/ubcompletion/progress.php?id={cid}")
    except requests.RequestException:
        return out
    for tr in ps.select("table tr"):
        cells = [clean(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
        if len(cells) >= 5 and cells[0].isdigit() and cells[1]:
            out[cells[1]] = (cells[2], cells[3], cells[4])
    return out


KMOOC_TITLE = re.compile(r"K-MOOC", re.I)


def is_kmooc_shell(title, acts):
    """梨花 K-MOOC 学分课：视频在 www.kmooc.kr，CyberCampus 仅壳+出勤同步。"""
    if not KMOOC_TITLE.search(title or ""):
        return False
    return not any(a["mod"] == "vod" for a in acts)


async def kmooc_progress_rows_async(s, cid):
    """Playwright 加载 Online-Attendance（K-MOOC 周次同步表）。"""
    from playwright.async_api import async_playwright

    url = f"{BASE}/report/ubcompletion/progress.php?id={cid}&viewtype=2"
    cookies_list = cookies_for_playwright(s)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        if cookies_list:
            await context.add_cookies(cookies_list)
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(3000)
        rows = await page.evaluate(
            """() => {
            const out = [];
            document.querySelectorAll('table.user_progress_table tr').forEach(tr => {
                const cells = [...tr.querySelectorAll('td,th')].map(c => c.innerText.trim());
                if (cells.some(x => x)) out.push(cells);
            });
            return out;
        }"""
        )
        await browser.close()
        return rows


def kmooc_progress_rows(s, cid):
    return asyncio.run(kmooc_progress_rows_async(s, cid))


def parse_mcfg_sesskey(html):
    m = re.search(r'"sesskey"\s*:\s*"([^"]+)"', html or "")
    return m.group(1) if m else ""


def cyber_academic_sync(s):
    """顶栏「학사 정보 동기화」：POST theme/coursemosv2/action.php type=user_update。"""
    if not logged_in(s):
        return False, "未登录 CyberCampus（.ewha.cookies 过期，需 python _mfa_login_once.py）"
    home = session_get(s, BASE + "/")
    home.raise_for_status()
    sk = parse_mcfg_sesskey(home.text)
    if not sk:
        return False, "首页未取得 sesskey"
    r = session_post(
        s,
        f"{BASE}/theme/coursemosv2/action.php",
        data={"type": "user_update", "sesskey": sk},
        timeout=300,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": BASE + "/",
        },
    )
    try:
        data = r.json()
    except json.JSONDecodeError:
        return False, (r.text or "")[:300]
    code = str(data.get("code", ""))
    msg = data.get("msg") or r.text
    return code == "100", msg


def kmooc_cyber_filled_weeks(rows):
    return sum(
        1
        for r in rows
        if r and r[0].isdigit() and any(x for x in r[2:8] if len(r) > 2 and str(x).strip())
    )


def kmooc_weeks_pending(rows):
    """从出勤表提取仍有学习记录但未达标的周次（有资源名或进度非空）。"""
    pending = []
    for cells in rows:
        if not cells or not cells[0].isdigit():
            continue
        week = cells[0]
        session = cells[1] if len(cells) > 1 else ""
        resource = cells[2] if len(cells) > 2 else ""
        progress = cells[6] if len(cells) > 6 else ""
        att = cells[7] if len(cells) > 7 else ""
        if resource or progress or att:
            if att != "O" and att.upper() != "O":
                pending.append(
                    {
                        "week": week,
                        "session": session,
                        "resource": resource,
                        "progress": progress,
                        "attendance": att,
                    }
                )
    return pending


def cmd_kmooc(args):
    """K-MOOC 学分课状态：CyberCampus 侧壳任务 + 出勤同步表（视频在 kmooc.kr）。"""
    s = make_session()
    if not ensure_login(s):
        return

    if getattr(args, "sync", False):
        print("== 학사 정보 동기화（K-MOOC → 梨花 Cyber）==")
        ok, msg = cyber_academic_sync(s)
        print("  结果:", "成功" if ok else "失败")
        print("  消息:", msg)
        cid = getattr(args, "course_id", None)
        if cid:
            time.sleep(3)
            rows = kmooc_progress_rows(s, str(cid))
            filled = kmooc_cyber_filled_weeks(rows)
            pending = kmooc_weeks_pending(rows)
            print(
                f"  Online-Attendance [{cid}]: "
                f"有数据的周次={filled}, 未达标={len(pending)}"
            )
            for p in pending[:6]:
                print(
                    f"    - {p['week']}주 {p['resource'] or '-'} "
                    f"progress={p['progress'] or '-'} att={p['attendance'] or '-'}"
                )
        if getattr(args, "sync_only", False):
            return
    courses = list_courses(s, args)
    kmooc = {}
    for cid, title in courses.items():
        _, acts = parse_course(s, cid)
        if is_kmooc_shell(title, acts):
            kmooc[cid] = (title, acts)

    if not kmooc:
        print("当前筛选下没有 K-MOOC 壳课程（标题含 K-MOOC 且无 mod/vod）。")
        return

    print("\n== K-MOOC 课程（视频在 https://www.kmooc.kr ，非 CyberCampus VOD）==")
    for cid, (title, acts) in kmooc.items():
        cname = short_course_name(title, cid)
        print(f"\n[{cid}] {title}")
        print(f"  CyberCampus 活动: {', '.join(a['mod']+':'+a['name'][:30] for a in acts)}")
        rows = kmooc_progress_rows(s, cid)
        pending = kmooc_weeks_pending(rows)
        filled = kmooc_cyber_filled_weeks(rows)
        if filled == 0:
            print("  Online-Attendance: （空）K-MOOC 学习进度尚未同步到梨花")
            print("    → kmooc 看完后: python ewha.py kmooc --sync --id", cid)
        elif pending:
            print(f"  未达标周次: {len(pending)}")
            for p in pending[:8]:
                print(
                    f"    - {p['week']}주 {p['resource'] or '(无标题)'} "
                    f"progress={p['progress'] or '-'} att={p['attendance'] or '-'}"
                )
        else:
            print("  Online-Attendance: 已同步且周次均已标记达标（或仅有空周）")

        for a in acts:
            if a["mod"] == "feedback" and a["done"] is False:
                print(f"  ⚠ 待办: {a['name']} → {BASE}/mod/feedback/view.php?id={a['id']}")
            if a["mod"] == "courseevaluation" and a["done"] is False:
                print(
                    f"  ⚠ 待办: {a['name']} → "
                    f"{BASE}/mod/courseevaluation/view.php?id={a['id']}"
                )

        print("  自动刷视频（learnus 同款）: python kmooc.py watch --course \"반도체\" --speed 2.0")
        print(f"  出勤页: {BASE}/report/ubcompletion/progress.php?id={cid}")


def period_end(period):
    dates = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?", period)
    if not dates:
        return None
    d = dates[1] if len(dates) > 1 and "~" in period else dates[-1]
    return dt.datetime.fromisoformat(d)


# ============================================================ todo
def cmd_todo(args):
    s = make_session()
    if not ensure_login(s):
        return
    now = dt.datetime.now()
    assignments, videos, others = [], [], []
    for cid, title in list_courses(s, args).items():
        cname = short_course_name(title, cid)
        _, acts = parse_course(s, cid)
        report = attendance_report(s, cid)
        for a in acts:
            url = f"{BASE}/mod/{a['mod']}/view.php?id={a['id']}"
            if a["mod"] == "assign":
                submitted, status, due, remain = assignment_status(s, a["id"])
                if not submitted:
                    assignments.append(
                        {
                            "course": cname,
                            "name": a["name"],
                            "due": due,
                            "remain": remain,
                            "overdue": bool(due and due < now),
                            "url": url,
                        }
                    )
            elif a["mod"] == "vod" and a["done"] is False and not STAFF_ONLY.search(a["name"]):
                req, watched, att = report.get(a["name"], ("", "", ""))
                if att == "O":
                    continue
                end = period_end(a["period"])
                videos.append(
                    {
                        "course": cname,
                        "name": a["name"],
                        "period": a["period"],
                        "required": req,
                        "watched": watched,
                        "open": a["open"],
                        "end": end,
                        "url": f"{BASE}/mod/vod/viewer.php?id={a['id']}",
                    }
                )
            elif (
                a["mod"] in ("feedback", "quiz")
                and a["done"] is False
                and a["open"]
                and not STAFF_ONLY.search(a["name"])
            ):
                others.append(
                    {"course": cname, "name": a["name"], "mod": a["mod"], "url": url}
                )

    assignments.sort(key=lambda x: (x["overdue"], x["due"] or dt.datetime.max))
    videos.sort(key=lambda x: (not x["open"], x["end"] or dt.datetime.max))

    print("\n== 未提交作业 (Pending Assignments) ==")
    if not assignments:
        print("  (无未提交作业)")
    for a in assignments:
        tag = "已过期" if a.get("overdue") else (a.get("remain") or "")
        due_s = a["due"].strftime("%Y-%m-%d %H:%M") if a.get("due") else "?"
        print(f"  - [{a['course']}] {a['name']}")
        print(f"      截止: {due_s}  ({tag})")
        print(f"      {a['url']}")
    print("\n== 未出席视频 (Pending VOD) ==")
    if not videos:
        print("  (无待出勤视频)")
    for x in videos:
        tag = "未开放" if not x["open"] else (
            "已过期" if x["end"] and x["end"] < now else ""
        )
        print(
            f"  - [{x['course']}] {x['name']}  {x['period']}  "
            f"要求 {x['required'] or '-'} 已看 {x['watched'] or '-'} {tag}\n      {x['url']}"
        )
    if others:
        print("\n== 其他未完成 (Quiz / Feedback) ==")
        for x in others:
            print(f"  - [{x['course']}] [{x['mod']}] {x['name']}\n      {x['url']}")

    out_path = getattr(args, "out", None)
    if out_path:
        path = Path(out_path)
        path.write_text(render_report(now, assignments, videos, others), encoding="utf-8")
        print(f"\n报告已导出: {path}")


def render_report(now, assignments, videos, others):
    e = html.escape

    def rows(items, cols):
        return (
            "".join(
                "<tr>" + "".join(f"<td>{c(x)}</td>" for c in cols) + "</tr>" for x in items
            )
            or f"<tr><td colspan={len(cols)}>无</td></tr>"
        )

    link = lambda x: f'<a href="{e(x["url"])}" target="_blank">{e(x["name"])}</a>'
    a_rows = rows(
        assignments,
        [
            lambda x: e(x["course"]),
            link,
            lambda x: e(
                x["due"].strftime("%Y-%m-%d %H:%M") if x.get("due") else "?"
            ),
            lambda x: '<b class="late">已过期</b>'
            if x["overdue"]
            else e(x.get("remain") or ""),
        ],
    )
    v_rows = rows(
        videos,
        [
            lambda x: e(x["course"]),
            link,
            lambda x: e(x["period"]),
            lambda x: e(x["required"] or "-"),
            lambda x: e(x["watched"] or "-"),
            lambda x: "未开放" if not x["open"] else "",
        ],
    )
    o_rows = rows(
        others,
        [lambda x: e(x["course"]), link, lambda x: e(x["mod"])],
    )
    return f"""<!doctype html><meta charset="utf-8"><title>CyberCampus 待办</title>
<style>body{{font-family:sans-serif;margin:2em}}table{{border-collapse:collapse;margin-bottom:2em}}
td,th{{border:1px solid #ccc;padding:6px 10px}}th{{background:#f3f3f3}}.late{{color:#c00}}</style>
<h1>梨花 CyberCampus 待办 <small>{now:%Y-%m-%d %H:%M}</small></h1>
<h2>未提交作业</h2><table><tr><th>课程</th><th>作业</th><th>截止</th><th>剩余</th></tr>{a_rows}</table>
<h2>未出席视频</h2><table><tr><th>课程</th><th>视频</th><th>期间</th><th>要求</th><th>已看</th><th></th></tr>{v_rows}</table>
<h2>其他未完成</h2><table><tr><th>课程</th><th>项目</th><th>类型</th></tr>{o_rows}</table>"""


# ============================================================ 资料下载
def filename_from_url(url):
    return safe_name(urllib.parse.unquote(urlparse(url).path.rsplit("/", 1)[-1]))


def urlparse(u):
    return urllib.parse.urlparse(u)


def fetch_file(s, url, dest_dir, name=None):
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not name and "pluginfile.php" in url:
        # pluginfile 通常带 ?file=... 或路径尾部文件名
        qs = urllib.parse.parse_qs(urlparse(url).query)
        if "file" in qs:
            name = safe_name(qs["file"][0])
    if not name:
        name = filename_from_url(url) or "file.bin"
    dest = dest_dir / name
    if dest.exists():
        return dest
    try:
        r = s.get(url, timeout=90, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return dest
    except requests.RequestException as e:
        print(f"      ! 下载失败 {name}: {e}")
        return None


def module_files(s, a):
    """进入活动页，抽取可下载的附件链接。"""
    url = f"{BASE}/mod/{a['mod']}/view.php?id={a['id']}"
    try:
        soup = soup_of(s, url)
    except requests.RequestException:
        return []
    files = []
    for link in soup.select('a[href*="pluginfile.php"], a[href*="file.php"]'):
        href = link.get("href", "")
        if href and href not in [f[0] for f in files]:
            files.append((href, clean(link.get_text(" "))))
    return files


def board_posts(s, board_id):
    """公告板帖子列表 [(date,title,url)]"""
    try:
        soup = soup_of(s, f"{BASE}/mod/ubboard/view.php?id={board_id}")
    except requests.RequestException:
        return []
    out = []
    for a in soup.select('a[href*="article.php"]'):
        href = a.get("href", "")
        date_el = a.select_one(".date")
        h5 = a.select_one("h5")
        if h5:
            out.append(
                (
                    clean(date_el.get_text()) if date_el else "",
                    clean(h5.get_text()),
                    href,
                )
            )
    return out


def cmd_download(args):
    s = make_session()
    if not ensure_login(s):
        return
    out_root = Path(args.out or "./workspace")
    for cid, title in list_courses(s, args).items():
        cname = short_course_name(title, cid)
        cdir = out_root / cname
        print(f"\n[{cname}] 正在下载课件 ...")
        sections, acts = parse_course(s, cid)
        for a in acts:
            unit = cdir / a["section"]
            for href, label in module_files(s, a):
                if href.startswith("/"):
                    href = BASE + href
                dest = fetch_file(s, href, unit, label or None)
                if dest:
                    print(f"      + {dest.relative_to(out_root)}")
                    if args.convert_docs:
                        convert_doc_file(dest)
        if args.all_boards:
            for board in [x for x in acts if x["mod"] == "ubboard"]:
                for date, title_p, url in board_posts(s, board["id"]):
                    if url.startswith("/"):
                        url = BASE + url
                    try:
                        ps = soup_of(s, url)
                    except requests.RequestException:
                        continue
                    for link in ps.select('a[href*="pluginfile.php"]'):
                        href = link.get("href", "")
                        if href.startswith("/"):
                            href = BASE + href
                        dest = fetch_file(s, href, cdir / "公告附件", None)
                        if dest:
                            print(f"      + {dest.relative_to(out_root)}")
                            if args.convert_docs:
                                convert_doc_file(dest)
    print("\n下载完成。")


# ============================================================ 文档转 Markdown
def docx_to_markdown(docx_path: Path):
    md_path = docx_path.with_suffix(".md")
    if md_path.exists() and md_path.stat().st_mtime >= docx_path.stat().st_mtime:
        return md_path
    try:
        import docx
        doc = docx.Document(docx_path)
    except Exception as e:
        print(f"      ! docx 解析失败 {docx_path.name}: {e}")
        return None
    lines = [f"# {docx_path.stem}\n", f"> *[自动化提取] 原始文件: `{docx_path.name}`*\n"]
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style_name = (p.style.name if p.style else "").lower()
        if "heading 1" in style_name:
            lines.append(f"\n# {text}\n")
        elif "heading 2" in style_name:
            lines.append(f"\n## {text}\n")
        elif "heading 3" in style_name:
            lines.append(f"\n### {text}\n")
        elif "bullet" in style_name or "list" in style_name:
            lines.append(f"- {text}")
        else:
            lines.append(f"{text}\n")
    for tbl_idx, table in enumerate(doc.tables):
        lines.append(f"\n### 表格 {tbl_idx + 1}\n")
        rows_data = [
            [clean(cell.text).replace("|", "\\|") for cell in row.cells]
            for row in table.rows
        ]
        if rows_data:
            header = rows_data[0]
            lines.append("| " + " | ".join(header) + " |")
            lines.append("| " + " | ".join(["---"] * len(header)) + " |")
            for row in rows_data[1:]:
                lines.append("| " + " | ".join(row) + " |")
            lines.append("\n")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def pptx_to_markdown(pptx_path: Path):
    md_path = pptx_path.with_suffix(".md")
    if md_path.exists() and md_path.stat().st_mtime >= pptx_path.stat().st_mtime:
        return md_path
    try:
        from pptx import Presentation
        prs = Presentation(pptx_path)
    except Exception as e:
        print(f"      ! pptx 解析失败 {pptx_path.name}: {e}")
        return None
    lines = [f"# {pptx_path.stem}\n", f"> *[自动化提取幻灯片] 原始文件: `{pptx_path.name}`*\n"]
    for idx, slide in enumerate(prs.slides, start=1):
        slide_lines = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    t = paragraph.text.strip()
                    if t:
                        slide_lines.append(f"- {t}")
            elif shape.has_table:
                rows = list(shape.table.rows)
                if rows:
                    slide_lines.append(
                        "\n| "
                        + " | ".join(clean(c.text).replace("|", "\\|") for c in rows[0].cells)
                        + " |"
                    )
                    slide_lines.append("| " + " | ".join("---" for _ in rows[0].cells) + " |")
                    for row in rows[1:]:
                        slide_lines.append(
                            "| "
                            + " | ".join(clean(c.text).replace("|", "\\|") for c in row.cells)
                            + " |"
                        )
        if slide_lines:
            lines.append(f"\n## Slide {idx}\n")
            lines.extend(slide_lines)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def pdf_to_markdown(pdf_path: Path):
    md_path = pdf_path.with_suffix(".md")
    if md_path.exists() and md_path.stat().st_mtime >= pdf_path.stat().st_mtime:
        return md_path
    try:
        import pypdf
        reader = pypdf.PdfReader(str(pdf_path))
    except Exception as e:
        print(f"      ! pdf 解析失败 {pdf_path.name}: {e}")
        return None
    lines = [f"# {pdf_path.stem}\n", f"> *[自动化提取课件] 原始文件: `{pdf_path.name}`*\n"]
    for i, page in enumerate(reader.pages, start=1):
        t = (page.extract_text() or "").strip()
        if t:
            lines.append(f"\n## Page {i}\n")
            lines.append(t)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def convert_doc_file(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return docx_to_markdown(path)
    if suffix == ".pptx":
        return pptx_to_markdown(path)
    if suffix == ".pdf":
        return pdf_to_markdown(path)
    return None


# ============================================================ 知识库索引
def build_unit_readme(unit_dir: Path, unit_name: str, activities=None, assignments=None):
    lines = [
        f"# {unit_name}",
        f"> 单元目录：`{unit_dir.name}` | 自动规整学习单元\n",
        "## 课件与阅读材料 (Course Materials)",
    ]
    materials = [
        f
        for f in unit_dir.iterdir()
        if f.is_file() and f.name != "README.md" and f.suffix.lower() != ".md"
    ]
    md_only = [
        f for f in unit_dir.iterdir() if f.is_file() and f.suffix.lower() == ".md"
    ]
    if materials or md_only:
        for m in sorted(materials, key=lambda x: x.name):
            md_ver = m.with_suffix(".md")
            if m.suffix.lower() in [".doc", ".docx", ".pdf", ".pptx"] and md_ver.exists():
                lines.append(
                    f"- **{m.stem}**: [阅读 Markdown]({md_ver.name}) *(原始: [{m.name}]({m.name}))*"
                )
            else:
                lines.append(f"- [{m.name}]({m.name})")
        for m in sorted(md_only, key=lambda x: x.name):
            orig = any(
                m.with_suffix(s).exists() for s in [".docx", ".doc", ".pdf", ".pptx"]
            )
            if not orig:
                lines.append(f"- [{m.name}]({m.name})")
    else:
        lines.append("- *(本单元暂无附件资料)*")
    if assignments:
        lines.append("\n## 本单元任务与作业 (Assignments)")
        for a in assignments:
            lines.append(
                f"- **{a['name']}**: 截止 `{a.get('due') or '未知'}` ({a.get('remain') or ''})"
            )
    (unit_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def build_course_readme(course_dir: Path, cname, cid, sections, assignments=None):
    lines = [
        f"# {cname}",
        f"> 课程编号：`{cid}` | 梨花女子大学 CyberCampus 在修课程知识库\n",
        "## 单元学习导航 (Course Units)",
        "| 单元编号 | 单元名称 | 课件数 | 单元索引入口 |",
        "| :--- | :--- | :---: | :--- |",
    ]
    for folder, sec_name in sections:
        sec_dir = course_dir / folder
        if not sec_dir.exists():
            continue
        mat_count = len(
            [f for f in sec_dir.iterdir() if f.is_file() and f.name != "README.md"]
        )
        lines.append(
            f"| **{folder}** | {sec_name} | {mat_count} | [进入单元]({folder}/README.md) |"
        )
    if assignments:
        lines.append("\n## 课程未完成待办事项 (Pending Tasks)")
        for a in assignments:
            lines.append(
                f"- [{' ' if not a.get('overdue') else 'x'}] **{a['name']}** "
                f"- 截止: `{a.get('due') or '?'}` ({a.get('remain') or ''}) [去提交]({a.get('url')})"
            )
    (course_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def build_workspace_readme(root: Path, course_summaries, all_pending=None):
    lines = [
        "# 梨花女子大学个人课程学习知识库 (CyberCampus Study Workspace)",
        "> 本知识库由 `ewha-helper` 自动构建与同步。专为 AI Agent Harness（Claude Code, Cursor 等）打造。\n",
        "## AI Harness / Agent 使用指引",
        "1. **结构说明**：每个课程目录代表一门学科，子目录对应各个单元/周次。",
        "2. **Markdown 课件优先阅读**：原始 .pdf/.pptx/.docx 课件已自动转换为结构化 .md，请优先阅读 .md。",
        "3. **复习与辅导**：检索各单元 README.md 获取该周大纲、课件，进行针对性复习或考前划重点。\n",
        "## 本学期在修课程清单",
        "| 课程名称 | 单元数 | 课程大纲索引 |",
        "| :--- | :---: | :--- |",
    ]
    for c in course_summaries:
        lines.append(
            f"| **{c['name']}** | {c['units']} | [进入课程目录]({c['dir']}/README.md) |"
        )
    if all_pending:
        lines.append("\n## 全局待办作业看板 (Upcoming Deadlines)")
        for a in all_pending:
            tag = "已过期" if a.get("overdue") else f"{a.get('remain')}"
            lines.append(
                f"- **[{a['course']}]** [{a['name']}]({a['url']}) | 截止: `{a['due'] or '?'}` ({tag})"
            )
    (root / "README.md").write_text("\n".join(lines), encoding="utf-8")


# ============================================================ sync / reindex
def cmd_sync(args):
    s = make_session()
    if not ensure_login(s):
        return
    out_root = Path(args.out or "./workspace")
    out_root.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now()
    course_summaries, all_pending = [], []
    for cid, title in list_courses(s, args).items():
        cname = short_course_name(title, cid)
        cdir = out_root / cname
        print(f"\n[{cname}] 同步中 ...")
        sections, acts = parse_course(s, cid)
        # 建单元目录并下载课件
        for a in acts:
            unit = cdir / a["section"]
            unit.mkdir(parents=True, exist_ok=True)
            if not args.no_docs:
                for href, label in module_files(s, a):
                    if href.startswith("/"):
                        href = BASE + href
                    dest = fetch_file(s, href, unit, label or None)
                    if dest:
                        print(f"      + {dest.relative_to(out_root)}")
                        convert_doc_file(dest)
        # 作业
        assignments = []
        for a in acts:
            if a["mod"] != "assign":
                continue
            submitted, status, due, remain = assignment_status(s, a["id"])
            if not submitted:
                assignments.append(
                    {
                        "course": cname,
                        "name": a["name"],
                        "due": due.strftime("%Y-%m-%d %H:%M") if due else "",
                        "remain": remain,
                        "overdue": bool(due and due < now),
                        "url": f"{BASE}/mod/assign/view.php?id={a['id']}",
                    }
                )
        # 生成单元 README
        for folder, sec_name in sections:
            unit = cdir / folder
            if unit.exists():
                build_unit_readme(unit, sec_name)
        build_course_readme(cdir, cname, cid, sections, assignments)
        course_summaries.append({"name": cname, "units": len(sections), "dir": cname})
        all_pending.extend(assignments)
        print(f"      完成，单元数 {len(sections)}，未交作业 {len(assignments)}")
    build_workspace_readme(out_root, course_summaries, all_pending)
    print(f"\n知识库已生成: {out_root}")


def cmd_reindex(args):
    root = Path(args.out or "./workspace")
    if not root.exists():
        print("工作区不存在:", root)
        return
    for md in root.rglob("*"):
        if md.is_file() and md.suffix.lower() in [".docx", ".doc", ".pptx", ".pdf"]:
            convert_doc_file(md)
    # 重建单元/课程 README
    for course_dir in [p for p in root.iterdir() if p.is_dir()]:
        for unit_dir in [p for p in course_dir.iterdir() if p.is_dir()]:
            build_unit_readme(unit_dir, unit_dir.name)
        build_course_readme(
            course_dir, course_dir.name, "", [(d.name, d.name) for d in course_dir.iterdir() if d.is_dir()]
        )
    print("重建完成。")


def cmd_courses(args):
    s = make_session()
    if not ensure_login(s):
        return
    for cid, title in list_courses(s, args).items():
        print(f"  [{cid}] {title}")


# ============================================================ watch (VOD 倍速出勤)
def cookies_for_playwright(s):
    """将 requests 会话 cookie 转为 Playwright 可用格式。"""
    out, seen = [], set()
    for c in s.cookies:
        dom = c.domain or "cyber.ewha.ac.kr"
        key = (c.name, c.value, dom)
        if key in seen:
            continue
        seen.add(key)
        path = c.path or "/"
        domains = {dom, "cyber.ewha.ac.kr", ".ewha.ac.kr"}
        if dom.startswith("."):
            domains.add(dom)
        else:
            domains.add("." + dom.split(".", 1)[-1] if "." in dom else ".ewha.ac.kr")
        for d in domains:
            out.append({"name": c.name, "value": c.value, "domain": d, "path": path})
    return out


def parse_time_seconds(time_str: str) -> int:
    if not time_str or ":" not in str(time_str):
        return 0
    parts = str(time_str).strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


async def _play_vod_session(s, vod_id: str, title: str, speed: float = 2.0, target_seconds: int = None):
    from playwright.async_api import async_playwright

    cookies_list = cookies_for_playwright(s)
    url = f"{BASE}/mod/vod/viewer.php?id={vod_id}"
    print(f"\n   🎬 正在播放: [{title}] (ID: {vod_id})")
    print(f"      - 倍速: {speed}x | 无头模式")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
            )
        )
        if cookies_list:
            await context.add_cookies(cookies_list)
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="load", timeout=60000)
            await page.wait_for_selector("video, iframe", timeout=20000)
        except Exception as ex:
            print(f"      ! 播放器加载失败: {ex}")
            await browser.close()
            return False

        inject_js = f"""
        () => {{
            const v = document.querySelector('video');
            if (v) {{
                v.muted = true;
                v.playbackRate = {speed};
                v.play().catch(() => {{}});
                return {{ found: true, duration: v.duration, current: v.currentTime }};
            }}
            return {{ found: false }};
        }}
        """
        init_st = await page.evaluate(inject_js)
        if not init_st or not init_st.get("found"):
            print("      ! 未找到 video 元素（iframe 内嵌播放器需另行适配）")
            await browser.close()
            return False

        raw_dur = init_st.get("duration")
        dur_sec = (
            int(raw_dur)
            if raw_dur is not None
            and not (isinstance(raw_dur, float) and math.isnan(raw_dur))
            else 0
        )
        if target_seconds:
            req_sec = target_seconds + 60
            if dur_sec > 0 and req_sec > dur_sec:
                req_sec = dur_sec
        else:
            req_sec = dur_sec

        print(
            f"      ▶️ 总长约 {dur_sec // 60}分{dur_sec % 60:02d}秒 | "
            f"目标 {req_sec // 60}分{req_sec % 60:02d}秒"
        )

        completed = False
        while True:
            await asyncio.sleep(10)
            st = await page.evaluate(
                f"""
            () => {{
                const v = document.querySelector('video');
                if (v) {{
                    if (v.paused) v.play().catch(() => {{}});
                    if (v.playbackRate !== {speed}) v.playbackRate = {speed};
                    return {{
                        current: v.currentTime,
                        duration: v.duration,
                        ended: v.ended,
                        paused: v.paused
                    }};
                }}
                return null;
            }}
            """
            )
            if not st:
                break

            cur_raw = st.get("current")
            cur = (
                int(cur_raw)
                if cur_raw is not None
                and not (isinstance(cur_raw, float) and math.isnan(cur_raw))
                else 0
            )
            d_raw = st.get("duration")
            if d_raw is not None and not (
                isinstance(d_raw, float) and math.isnan(d_raw)
            ) and int(d_raw) > 0:
                dur = int(d_raw)
            else:
                dur = dur_sec or req_sec or 1
            pct = int(cur / dur * 100) if dur else 0
            bar = "█" * (pct // 5) + "░" * (20 - (pct // 5))
            print(
                f"\r      ⏱️ [{bar}] {pct:3d}% | "
                f"{cur // 60:02d}:{cur % 60:02d} / {dur // 60:02d}:{dur % 60:02d} "
                f"(×{speed})",
                end="",
                flush=True,
            )

            if (
                st.get("ended")
                or (req_sec > 0 and cur >= req_sec)
                or (dur > 0 and cur >= dur - 2)
            ):
                completed = True
                print("\n      ✅ 已达目标观看时长")
                await page.evaluate(
                    "() => { const v = document.querySelector('video'); if (v) v.pause(); }"
                )
                await asyncio.sleep(8)
                break

        await browser.close()
        return completed


def cmd_watch(args):
    s = make_session()
    if not ensure_login(s):
        return
    courses = list_courses(s, args)
    if courses:
        profiles = []
        for cid, title in courses.items():
            _, acts = parse_course(s, cid)
            profiles.append(is_kmooc_shell(title, acts))
        if profiles and all(profiles):
            print(
                "\n所选课程均为 K-MOOC 外壳课，CyberCampus 上无 mod/vod，"
                "learnus 式 watch 无法使用。\n"
            )
            cmd_kmooc(args)
            return

    speed = float(getattr(args, "speed", 2.0))
    limit_count = int(getattr(args, "limit", 0) or 0)

    print("\n=======================================================")
    print(" 🚀 梨花 CyberCampus 智能 VOD 出勤")
    print(f"    - 倍速: {speed}x")
    print(f"    - 扫描课程: {len(courses)} 门")
    print("=======================================================\n")

    unwatched_tasks = []
    for cid, title in courses.items():
        cname = short_course_name(title, cid)
        _, acts = parse_course(s, cid)
        report = attendance_report(s, cid)
        for a in acts:
            if a["mod"] != "vod" or STAFF_ONLY.search(a["name"]):
                continue
            req_str, watched_str, att = report.get(a["name"], ("-", "-", "-"))
            if att == "O":
                continue
            if not a.get("open", True):
                continue
            req_sec = parse_time_seconds(req_str)
            unwatched_tasks.append(
                {
                    "course": cname,
                    "section": a["section"],
                    "name": a["name"],
                    "id": a["id"],
                    "req_sec": req_sec,
                    "req_str": req_str,
                    "watched_str": watched_str,
                }
            )

    if not unwatched_tasks:
        print("🎉 当前开放课程的视频出勤均已达标，无需挂机。\n")
        return

    print(f"📌 待出勤视频 {len(unwatched_tasks)} 个：")
    for idx, t in enumerate(unwatched_tasks):
        print(
            f"  [{idx + 1}] 【{t['course']}】({t['section']}) {t['name']} | "
            f"要求: {t['req_str']} (已看: {t['watched_str']})"
        )

    if limit_count > 0:
        unwatched_tasks = unwatched_tasks[:limit_count]

    print("\n-------------------------------------------------------")
    print(" 🎬 开始按序播放...")
    print("-------------------------------------------------------")

    for idx, task in enumerate(unwatched_tasks):
        print(f"\n>> ({idx + 1}/{len(unwatched_tasks)}) 【{task['course']}】{task['name']}")
        asyncio.run(
            _play_vod_session(
                s,
                task["id"],
                task["name"],
                speed=speed,
                target_seconds=task["req_sec"],
            )
        )
        time.sleep(2)

    print("\n=======================================================")
    print(" 🎉 挂机任务结束，刷新待办看板...")
    print("=======================================================\n")
    cmd_todo(args)


# ============================================================ CLI
def main():
    p = argparse.ArgumentParser(
        description="梨花 CyberCampus 智能助学工作区 & 知识库自动化系统"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sy = sub.add_parser("sync", help="【推荐】同步：下载课件 + doc转md + 构建知识库")
    sy.add_argument("--out", help="知识库输出根目录 (默认: ./workspace)")
    sy.add_argument("--course", help="仅同步指定课程 (课程ID或名称关键字)")
    sy.add_argument("--no-docs", action="store_true", help="跳过课件下载，仅构建索引")

    lg = sub.add_parser("login", help="Eureka SSO 登录并保存会话")

    t = sub.add_parser("todo", help="查看未提交作业与未出席视频")
    t.add_argument("--out", help="导出报告 HTML 路径")
    t.add_argument("--course", help="仅指定课程")

    w = sub.add_parser("watch", help="【出勤】未达标 VOD 按顺序倍速无头播放")
    w.add_argument("--course", help="仅指定课程 (ID 或名称关键字)")
    w.add_argument("--speed", type=float, default=2.0, help="播放倍速 (默认 2.0)")
    w.add_argument("--limit", type=int, default=0, help="最多处理 N 个视频 (0=全部)")

    km = sub.add_parser("kmooc", help="K-MOOC 学分课：出勤同步与 kmooc.kr 刷课指引")
    km.add_argument("--course", help="仅指定课程 (ID 或名称关键字)")
    km.add_argument(
        "--sync",
        action="store_true",
        help="触发 Cyber「학사 정보 동기화」，把 K-MOOC 进度写入梨花出勤表",
    )
    km.add_argument(
        "--id",
        type=int,
        dest="course_id",
        help="同步后检查的 Cyber 课程 ID（如半导体 146858）",
    )
    km.add_argument(
        "--sync-only",
        action="store_true",
        help="仅执行 --sync，不列出全部 K-MOOC 课",
    )

    d = sub.add_parser("download", help="按周次分类下载课程资料")
    d.add_argument("--out", help="下载根目录")
    d.add_argument("--convert-docs", action="store_true", help="自动将文档转 Markdown")
    d.add_argument("--all-boards", action="store_true", help="下载所有公告板附件")
    d.add_argument("--course", help="仅指定课程")

    rx = sub.add_parser("reindex", help="扫描已有目录，补全文档转换并重建索引")
    rx.add_argument("--out", help="工作区根目录 (默认: ./workspace)")

    c = sub.add_parser("courses", help="列出我的课程")

    args = p.parse_args()
    cmd_map = {
        "sync": cmd_sync,
        "login": cmd_login,
        "todo": cmd_todo,
        "watch": cmd_watch,
        "kmooc": cmd_kmooc,
        "download": cmd_download,
        "reindex": cmd_reindex,
        "courses": cmd_courses,
    }
    cmd_map[args.cmd](args)


if __name__ == "__main__":
    main()
