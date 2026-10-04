#!/usr/bin/env python3
"""LearnUs (ys.learnus.org) helper: pending assignments, unwatched videos, bulk file download.

Usage:
  python learnus.py login [--save]  # Yonsei portal SSO login; --save stores id/pw in credentials.json
  python learnus.py cookie          # pull login cookies from a Chrome started with --remote-debugging-port=9222
  python learnus.py todo            # pending assignments + unwatched videos, also writes report.html
  python learnus.py download [--videos] [--all-boards] [--out DIR]
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://ys.learnus.org"
HERE = Path(__file__).resolve().parent
COOKIE_FILE = HERE / "cookies.txt"
CRED_FILE = HERE / "credentials.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
RESOURCE_BOARD = re.compile(r"자료실|자료|class files|files|materials|resources", re.I)
SKIP_BOARD = re.compile(r"공지|질의|q\s*&\s*a|announcement", re.I)
STAFF_ONLY = re.compile(r"교직원|faculties|faculty|教职员|教職", re.I)


# ---------------------------------------------------------------- session

def cmd_cookie(args):
    import websocket

    ver = requests.get(f"http://127.0.0.1:{args.port}/json/version", timeout=5).json()
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
    ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            break
    ws.close()
    cookies = [c for c in msg["result"]["cookies"] if c["domain"] in ("ys.learnus.org", ".learnus.org", ".ys.learnus.org")]
    COOKIE_FILE.write_text("; ".join(f"{c['name']}={c['value']}" for c in cookies), encoding="utf-8")
    try:
        os.chmod(COOKIE_FILE, 0o600)
    except OSError:
        pass
    print(f"saved {len(cookies)} cookies -> {COOKIE_FILE}")


def save_cookies(s):
    cookies = [c for c in s.cookies if c.domain in ("ys.learnus.org", ".learnus.org", ".ys.learnus.org")]
    COOKIE_FILE.write_text("; ".join(f"{c.name}={c.value}" for c in cookies), encoding="utf-8")
    try:
        os.chmod(COOKIE_FILE, 0o600)
    except OSError:
        pass


def logged_in(s):
    r = s.get(BASE + "/", timeout=30)
    return "/login" not in r.url and "course/view.php" in r.text


def load_credentials():
    uid, pw = os.environ.get("LEARNUS_ID"), os.environ.get("LEARNUS_PW")
    if uid and pw:
        return uid, pw
    if CRED_FILE.exists():
        data = json.loads(CRED_FILE.read_text(encoding="utf-8"))
        return data["id"], data["pw"]
    return None


def rsa_encrypt_hex(message, modulus_hex, exponent_hex):
    """PKCS#1 v1.5 encryption, hex output, same as jsbn's RSAKey.encrypt."""
    n, e = int(modulus_hex, 16), int(exponent_hex, 16)
    k = (n.bit_length() + 7) // 8
    data = message.encode("utf-8")
    if len(data) > k - 11:
        raise ValueError("message too long for RSA key")
    pad = bytearray()
    while len(pad) < k - 3 - len(data):
        pad += bytes(b for b in os.urandom(k) if b)
    block = b"\x00\x02" + bytes(pad[: k - 3 - len(data)]) + b"\x00" + data
    h = format(pow(int.from_bytes(block, "big"), e, n), "x")
    return h if len(h) % 2 == 0 else "0" + h


def follow_auto_forms(s, r, limit=8):
    """Submit hidden-field forms that the SSO pages auto-post via JavaScript."""
    for _ in range(limit):
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.select_one("form")
        if form and not form.select_one("input[type=password]") and "submit()" in r.text:
            data = {i["name"]: i.get("value", "") for i in form.select("input[name]")}
            action = urljoin(r.url, form.get("action") or r.url)
            r = s.request((form.get("method") or "get").upper(), action, data=data, timeout=30,
                          headers={"Referer": r.url})
            continue
        m = re.search(r"location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", r.text)
        if m and len(r.text) < 5000 and "learnus" not in urlparse(r.url).netloc:
            r = s.get(urljoin(r.url, m.group(1)), timeout=30, headers={"Referer": r.url})
            continue
        break
    return r


def sso_login(uid, pw):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})
    s.get(BASE + "/login.php", timeout=30)
    r = s.get(BASE + "/passni/sso/spLogin2.php", timeout=30, headers={"Referer": BASE + "/login.php"})
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.select_one("form#frmSSO")
    if not form:
        raise RuntimeError(f"unexpected SSO entry page ({r.status_code})")
    data = {i["name"]: i.get("value", "") for i in form.select("input[name]")}
    r = s.post(form["action"], data=data, timeout=30, headers={"Referer": r.url})

    challenge = re.search(r"ssoChallenge\s*=\s*'([0-9A-Fa-f]+)'", r.text)
    key = re.search(r"setPublic\(\s*'([0-9a-fA-F]+)'\s*,\s*'([0-9a-fA-F]+)'", r.text)
    login_form = BeautifulSoup(r.text, "html.parser").select_one("form#ssoLoginForm")
    if not (challenge and key and login_form):
        raise RuntimeError("could not read SSO login page (layout changed?)")
    fields = {i["name"]: i.get("value", "") for i in login_form.select("input[type=hidden][name]")}
    payload = json.dumps({"userid": uid, "userpw": pw, "ssoChallenge": challenge.group(1)}, ensure_ascii=False)
    fields["E2"] = rsa_encrypt_hex(payload, key.group(1), key.group(2))
    r = s.post(urljoin(r.url, login_form["action"]), data=fields, timeout=30, headers={"Referer": r.url})
    r = follow_auto_forms(s, r)

    if not logged_in(s):
        msg = re.search(r"returnMessage\s*=\s*'([^']+)'", r.text)
        raise RuntimeError("login failed" + (f": {msg.group(1)}" if msg else f" (ended at {r.url})") +
                           " -- not retrying; 3+ failures make the portal require a captcha")
    s.headers.update({"Referer": BASE + "/"})
    return s


def cmd_login(args):
    import getpass

    creds = load_credentials()
    if not creds:
        uid = input("학번 / student id: ").strip()
        creds = (uid, getpass.getpass("password: "))
    s = sso_login(*creds)
    save_cookies(s)
    print(f"login ok, cookies -> {COOKIE_FILE}")
    if args.save:
        CRED_FILE.write_text(json.dumps({"id": creds[0], "pw": creds[1]}), encoding="utf-8")
        try:
            os.chmod(CRED_FILE, 0o600)
        except OSError:
            pass
        print(f"credentials saved -> {CRED_FILE} (keep this file private)")


def make_session():
    raw = os.environ.get("LEARNUS_COOKIE") or (COOKIE_FILE.read_text(encoding="utf-8").strip() if COOKIE_FILE.exists() else "")
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": BASE + "/"})
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            s.cookies.set(k, v, domain="ys.learnus.org", path="/")
    if raw and logged_in(s):
        return s
    creds = load_credentials()
    if not creds:
        sys.exit("not logged in: run `learnus.py login` (or `learnus.py cookie`)")
    print("session expired, logging in again ...", flush=True)
    s = sso_login(*creds)
    save_cookies(s)
    return s


def soup_of(s, url):
    r = s.get(url, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def clean(text):
    return " ".join((text or "").split())


def safe_name(name, limit=120):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", clean(name)).strip(" .") or "untitled"
    if len(name) <= limit:
        return name
    stem, dot, ext = name.rpartition(".")
    if dot and 0 < len(ext) <= 8:
        return stem[: limit - len(ext) - 1].rstrip() + "." + ext
    return name[:limit]


def fix_mojibake(name):
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


# ---------------------------------------------------------------- course parsing

def list_courses(s, args=None):
    home = soup_of(s, BASE + "/")
    courses = {}
    for a in home.select('a[href*="/course/view.php?id="]'):
        cid = re.search(r"id=(\d+)", a["href"]).group(1)
        title = clean(a.get_text(" "))
        if title and len(title) > len(courses.get(cid, "")):
            courses[cid] = title
    if args is not None:
        if getattr(args, "course", None):
            courses = {c: t for c, t in courses.items() if args.course == c or args.course.lower() in t.lower()}
        elif not getattr(args, "include_noncredit", False):
            courses = {c: t for c, t in courses.items() if not t.startswith("비교과")}
    return courses


def short_course_name(title, cid):
    m = re.search(r"(?:비교과|교과)\s+(?:학부\s+|대학원\s+)?(.+?)\s*\(", title)
    return safe_name(m.group(1) if m else title, 60) or cid


def activity_name(li):
    el = li.select_one(".instancename")
    return clean(el.contents[0] if el and el.contents and isinstance(el.contents[0], str) else (el.get_text() if el else "?"))


def section_label(sec):
    el = sec.select_one(".sectionname")
    name = clean(el.get_text() if el else sec.get("aria-label", ""))
    m = re.search(r"(\d+)\s*주차|week\s*(\d+)", name, re.I)
    if m:
        return f"{int(m.group(1) or m.group(2)):02d}주차", name
    return f"00_{safe_name(name, 40)}", name


def section_range(name, year):
    m = re.search(r"(\d+)월\s*(\d+)일\s*-\s*(\d+)월\s*(\d+)일", name)
    if not m:
        return None
    a = dt.date(year, int(m.group(1)), int(m.group(2)))
    b = dt.date(year, int(m.group(3)), int(m.group(4)))
    if b < a:
        b = b.replace(year=year + 1)
    return a, b


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
            mod = next((c[8:] for c in li.get("class", []) if c.startswith("modtype_")), "?")
            icon = li.select_one(".autocompletion img, .togglecompletion img")
            done = None
            if icon:
                done = bool(re.search(r"completion-(auto|manual)-(y|pass)", icon.get("src", "")))
            period = li.select_one(".displayoptions .text-ubstrap")
            activities.append({
                "id": mid, "mod": mod, "name": activity_name(li), "done": done,
                "open": not li.select_one(".dimmed, .dimmed_text"),
                "period": clean(period.get_text(" ")) if period else "",
                "section": folder,
            })
    return sections, activities


def attendance_report(s, cid):
    """{video name: (required, watched, attended)} from the ubcompletion report."""
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


# ---------------------------------------------------------------- todo

def assignment_status(s, mid):
    soup = soup_of(s, f"{BASE}/mod/assign/view.php?id={mid}")
    info = {}
    for tr in soup.select("table tr"):
        cells = [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        if len(cells) >= 2:
            info[cells[0]] = cells[1]
    status = info.get("제출 여부") or info.get("Submission status", "")
    due = info.get("종료 일시") or info.get("Due date", "")
    remain = info.get("마감까지 남은 기한") or info.get("Time remaining", "")
    submitted = bool(status) and not re.search(r"제출 안 함|No attempt|Nothing has been submitted", status)
    due_dt = None
    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", due)
    if m:
        due_dt = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
    return submitted, status, due_dt, remain


def period_end(period):
    dates = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?", period)
    if not dates:
        return None
    d = dates[1] if len(dates) > 1 and "~" in period else dates[-1]
    return dt.datetime.fromisoformat(d)


def cmd_todo(args):
    s = make_session()
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
                    assignments.append({"course": cname, "name": a["name"], "due": due, "remain": remain,
                                        "overdue": bool(due and due < now), "url": url})
            elif a["mod"] == "vod" and a["done"] is False and not STAFF_ONLY.search(a["name"]):
                req, watched, att = report.get(a["name"], ("", "", ""))
                if att == "O":
                    continue
                end = period_end(a["period"])
                videos.append({"course": cname, "name": a["name"], "period": a["period"], "required": req,
                               "watched": watched, "open": a["open"], "end": end,
                               "url": f"{BASE}/mod/vod/viewer.php?id={a['id']}"})
            elif a["mod"] in ("feedback", "quiz") and a["done"] is False and a["open"] and not STAFF_ONLY.search(a["name"]):
                others.append({"course": cname, "name": a["name"], "mod": a["mod"], "url": url})

    assignments.sort(key=lambda x: (x["overdue"], x["due"] or dt.datetime.max))
    videos.sort(key=lambda x: (not x["open"], x["end"] or dt.datetime.max))

    print("\n== 未提交作业 ==")
    for x in assignments:
        tag = "已过期" if x["overdue"] else x["remain"]
        print(f"  [{x['course']}] {x['name']}  截止 {x['due'] or '?'}  ({tag})\n      {x['url']}")
    print("\n== 未出席视频 ==")
    for x in videos:
        tag = "未开放" if not x["open"] else ("已过期" if x["end"] and x["end"] < now else "")
        print(f"  [{x['course']}] {x['name']}  {x['period']}  要求 {x['required'] or '-'} 已看 {x['watched'] or '-'} {tag}\n      {x['url']}")
    if others:
        print("\n== 其他未完成（感想/测验）==")
        for x in others:
            print(f"  [{x['course']}] {x['name']} ({x['mod']})\n      {x['url']}")

    out = Path(args.out or HERE / "report.html")
    out.write_text(render_report(now, assignments, videos, others), encoding="utf-8")
    print(f"\nreport -> {out}")


def render_report(now, assignments, videos, others):
    e = html.escape

    def rows(items, cols):
        return "".join("<tr>" + "".join(f"<td>{c(x)}</td>" for c in cols) + "</tr>" for x in items) or \
            f"<tr><td colspan={len(cols)}>无</td></tr>"

    link = lambda x: f'<a href="{e(x["url"])}" target="_blank">{e(x["name"])}</a>'
    a_rows = rows(assignments, [lambda x: e(x["course"]), link, lambda x: e(str(x["due"] or "?")),
                                lambda x: '<b class="late">已过期</b>' if x["overdue"] else e(x["remain"])])
    v_rows = rows(videos, [lambda x: e(x["course"]), link, lambda x: e(x["period"]),
                           lambda x: e(x["required"] or "-"), lambda x: e(x["watched"] or "-"),
                           lambda x: "未开放" if not x["open"] else ""])
    o_rows = rows(others, [lambda x: e(x["course"]), link, lambda x: e(x["mod"])])
    return f"""<!doctype html><meta charset="utf-8"><title>LearnUs 待办</title>
<style>body{{font-family:sans-serif;margin:2em}}table{{border-collapse:collapse;margin-bottom:2em}}
td,th{{border:1px solid #ccc;padding:6px 10px}}th{{background:#f3f3f3}}.late{{color:#c00}}</style>
<h1>LearnUs 待办 <small>{now:%Y-%m-%d %H:%M}</small></h1>
<h2>未提交作业</h2><table><tr><th>课程</th><th>作业</th><th>截止</th><th>剩余</th></tr>{a_rows}</table>
<h2>未出席视频</h2><table><tr><th>课程</th><th>视频</th><th>期间</th><th>要求</th><th>已看</th><th></th></tr>{v_rows}</table>
<h2>其他未完成</h2><table><tr><th>课程</th><th>项目</th><th>类型</th></tr>{o_rows}</table>"""


# ---------------------------------------------------------------- download

def filename_from_url(url):
    return safe_name(unquote(urlparse(url).path.rsplit("/", 1)[-1]))


def fetch_file(s, url, dest_dir, name=None):
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not name and "pluginfile.php" in url:
        name = filename_from_url(url)
    if name and (dest_dir / name).exists() and (dest_dir / name).stat().st_size > 0:
        return dest_dir / name, False
    with s.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        if not name:
            cd = r.headers.get("Content-Disposition", "")
            if "pluginfile.php" in r.url:
                name = filename_from_url(r.url)
            elif m := re.search(r"filename\*=UTF-8''([^;]+)", cd):
                name = safe_name(unquote(m.group(1)))
            elif m := re.search(r'filename="?([^";]+)', cd):
                name = safe_name(fix_mojibake(m.group(1)))
            else:
                name = filename_from_url(r.url)
        dest = dest_dir / name
        size = 0 if r.headers.get("Content-Encoding") else int(r.headers.get("Content-Length") or 0)
        if dest.exists() and (not size or dest.stat().st_size == size):
            return dest, False
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        tmp.replace(dest)
    return dest, True


def module_files(s, a):
    """pluginfile URLs for ubfile/resource/folder activities."""
    url = f"{BASE}/mod/{a['mod']}/view.php?id={a['id']}"
    if a["mod"] in ("ubfile", "resource"):
        r = s.get(url, timeout=30, allow_redirects=False)
        if r.status_code in (301, 302, 303) and "pluginfile" in r.headers.get("Location", ""):
            return [r.headers["Location"]]
        soup = BeautifulSoup(r.text, "html.parser")
    else:
        soup = soup_of(s, url)
    return sorted({urljoin(BASE, x["href"]) for x in soup.select("a[href*='pluginfile.php']")
                   if "/mod_" in x["href"] or "/content/" in x["href"]})


def board_posts(s, board_id):
    posts, page = [], 1
    while True:
        soup = soup_of(s, f"{BASE}/mod/ubboard/view.php?id={board_id}&page={page}")
        found = [urljoin(BASE + "/mod/ubboard/", a["href"]) for a in soup.select("table a[href*='article.php']")]
        new = [p for p in found if p not in posts]
        if not new:
            break
        posts += new
        page += 1
    return posts


def post_details(s, url):
    soup = soup_of(s, url)
    text = clean(soup.get_text(" "))
    m = re.search(r"(?:작성일|Created Date)\s*:?\s*(\d{4}-\d{2}-\d{2})", text)
    date = dt.date.fromisoformat(m.group(1)) if m else None
    subj = soup.select_one(".subject")
    files = sorted({urljoin(BASE, a["href"]) for a in soup.select("a[href*='pluginfile.php']") if "attachment" in a["href"]})
    return clean(subj.get_text()) if subj else "", date, files


def week_for_date(ranges, date):
    for folder, (a, b) in ranges:
        if a <= date <= b:
            return folder
    return None


def download_video(s, vod_id, dest):
    if dest.exists():
        return False
    page = s.get(f"{BASE}/mod/vod/viewer.php?id={vod_id}", timeout=30).text
    m = re.search(r'src="(https?://[^"]+\.m3u8[^"]*)"', page)
    if not m:
        print(f"      ! no m3u8 for vod {vod_id} (maybe not open yet)")
        return False
    if not shutil.which("ffmpeg"):
        print("      ! ffmpeg not found, skip video")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-headers", f"Referer: {BASE}/\r\n",
                    "-i", m.group(1), "-c", "copy", "-bsf:a", "aac_adtstoasc", "-y", str(tmp)], check=True)
    tmp.replace(dest)
    return True


def cmd_download(args):
    s = make_session()
    root = Path(args.out or HERE / "downloads")
    total = 0
    for cid, title in list_courses(s, args).items():
        cname = short_course_name(title, cid)
        print(f"\n## {cname}", flush=True)
        sections, acts = parse_course(s, cid)
        year = dt.date.today().year
        ranges = [(f, r) for f, n in sections if (r := section_range(n, year))]

        for a in acts:
            try:
                wdir = root / cname / a["section"]
                if a["mod"] in ("ubfile", "resource", "folder"):
                    target = wdir / safe_name(a["name"]) if a["mod"] == "folder" else wdir
                    for url in module_files(s, a):
                        path, new = fetch_file(s, url, target)
                        total += new
                        print(f"   {'+' if new else '='} {path.relative_to(root)}")
                elif a["mod"] == "vod" and args.videos and a["open"] and not STAFF_ONLY.search(a["name"]):
                    dest = wdir / f"{safe_name(a['name'])}.mp4"
                    print(f"   ~ video {dest.relative_to(root)}")
                    total += download_video(s, a["id"], dest)
                elif a["mod"] == "ubboard":
                    if SKIP_BOARD.search(a["name"]):
                        continue
                    if not (args.all_boards or RESOURCE_BOARD.search(a["name"])):
                        continue
                    for post in board_posts(s, a["id"]):
                        subject, date, files = post_details(s, post)
                        if not files:
                            continue
                        week = (week_for_date(ranges, date) if date else None) or "00_날짜미상"
                        target = root / cname / week / safe_name(a["name"], 40) / safe_name(subject, 80)
                        for url in files:
                            path, new = fetch_file(s, url, target)
                            total += new
                            print(f"   {'+' if new else '='} {path.relative_to(root)}")
                        time.sleep(0.3)
            except Exception as ex:  # keep going on single failures
                print(f"   ! {a['mod']} {a['name']}: {ex}")
            time.sleep(0.2)
    print(f"\ndone, {total} new file(s) -> {root}")


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description="LearnUs helper")
    sub = p.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("login", help="log in through Yonsei portal SSO")
    lg.add_argument("--save", action="store_true", help="store id/pw in credentials.json for automatic re-login")
    c = sub.add_parser("cookie", help="grab cookies from Chrome remote debugging")
    c.add_argument("--port", type=int, default=9222)
    t = sub.add_parser("todo", help="pending assignments and unwatched videos")
    t.add_argument("--out", help="report html path")
    t.add_argument("--include-noncredit", action="store_true", help="include 비교과 courses")
    t.add_argument("--course", help="only this course (id or part of the title)")
    d = sub.add_parser("download", help="download course files by week")
    d.add_argument("--out", help="download root")
    d.add_argument("--videos", action="store_true", help="also download lecture videos (ffmpeg)")
    d.add_argument("--all-boards", action="store_true", help="download attachments from every board, not only 자료실")
    d.add_argument("--include-noncredit", action="store_true", help="include 비교과 courses")
    d.add_argument("--course", help="only this course (id or part of the title)")
    args = p.parse_args()
    {"login": cmd_login, "cookie": cmd_cookie, "todo": cmd_todo, "download": cmd_download}[args.cmd](args)


if __name__ == "__main__":
    main()
