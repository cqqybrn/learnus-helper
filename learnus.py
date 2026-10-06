#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LearnUs (ys.learnus.org) 智能学习助手 & 知识库自动化管理系统

像一个井井有条的学霸一样：
1. 自动登录延世大学 LearnUs 并扫描学期所有课程与教学单元。
2. 按课程、按单元分类下载所有课件、资料附件与视频录播。
3. 自动将 .doc / .docx 转换为排版清晰的 .md 文件，方便 AI Agent (Harness) 深度阅读。
4. 本地调用阿里 SenseVoice 极速提取课堂视频的外语原生发言讲义（.transcript.md，带时间标记）。
5. 自动构建三级索引 (README.md 知识树)，让任意 Harness (Claude Code, Cursor 等) 随时调阅辅导。

Usage:
  python learnus.py sync [--course ID/NAME] [--no-videos] [--lang ko] [--out DIR]
  python learnus.py login [--save]
  python learnus.py todo [--out report.html]
  python learnus.py download [--videos] [--transcribe] [--convert-docs] [--out DIR]
  python learnus.py reindex [--out DIR]
"""

import argparse
import asyncio
import datetime as dt
import html
import json
import math
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
DEFAULT_WORKSPACE = HERE / "workspace"

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
    try:
        r = s.get(BASE + "/", timeout=30)
        return "/login" not in r.url and "course/view.php" in r.text
    except requests.RequestException:
        return False


def load_credentials():
    uid, pw = os.environ.get("LEARNUS_ID"), os.environ.get("LEARNUS_PW")
    if uid and pw:
        return uid, pw
    if CRED_FILE.exists():
        data = json.loads(CRED_FILE.read_text(encoding="utf-8"))
        return data.get("id"), data.get("pw")
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
    r = follow_auto_forms(s, r)

    # 支持新版/老版 SSO 表单结构
    m_chal = re.search(r"var\s+ssoChallenge\s*=\s*'([^']+)'", r.text)
    m_rsa = re.search(r"rsa\.setPublic\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", r.text)
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.select_one("form#ssoLoginForm") or soup.select_one("form#loginFrm") or soup.select_one("form[name='loginFrm']")
    if not form:
        raise RuntimeError("could not find portal login form")
    action = urljoin(r.url, form.get("action", ""))

    if m_chal and m_rsa:
        sso_challenge = m_chal.group(1)
        modulus, exponent = m_rsa.group(1), m_rsa.group(2)
        payload = {"userid": uid, "userpw": pw, "ssoChallenge": sso_challenge}
        json_str = json.dumps(payload, separators=(',', ':'))
        enc_e2 = rsa_encrypt_hex(json_str, modulus, exponent)

        data = {inp.get("name"): inp.get("value", "") for inp in form.find_all("input") if inp.get("name")}
        data["E2"] = enc_e2
        data["loginId"] = ""
        data["loginPasswd"] = ""
    else:
        keys = s.get("https://infra.yonsei.ac.kr/sso/getE2EKey.do", timeout=30).json()
        enc_id = rsa_encrypt_hex(uid, keys["publicKeyModulus"], keys["publicKeyExponent"])
        enc_pw = rsa_encrypt_hex(pw, keys["publicKeyModulus"], keys["publicKeyExponent"])
        data = {
            "loginType": "sso",
            "returnUrl": (form.select_one("input[name='returnUrl']") or {}).get("value", ""),
            "challenge": (form.select_one("input[name='challenge']") or {}).get("value", ""),
            "response": (form.select_one("input[name='response']") or {}).get("value", ""),
            "tId": (form.select_one("input[name='tId']") or {}).get("value", ""),
            "e2e_id": enc_id,
            "e2e_pw": enc_pw,
            "userId": "",
            "userPassword": "",
        }

    r = s.post(action, data=data, timeout=30, headers={"Referer": r.url})
    r = follow_auto_forms(s, r)
    if "spLoginData.php" in r.url:
        r = s.get(BASE + "/passni/spLoginProcess.php", timeout=30, headers={"Referer": r.url})
        r = follow_auto_forms(s, r)

    if "비밀번호" in r.text and ("오류" in r.text or "불일치" in r.text):
        raise RuntimeError("SSO 认证失败：学号或密码错误")
    if not logged_in(s):
        raise RuntimeError("SSO 登录未完成，请检查账号密码或验证码拦截")
    return s


def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})
    if COOKIE_FILE.exists():
        for pair in COOKIE_FILE.read_text(encoding="utf-8").split(";"):
            if "=" in pair:
                k, v = pair.strip().split("=", 1)
                s.cookies.set(k, v, domain="ys.learnus.org")
        if logged_in(s):
            return s
        print("[*] cookies.txt 已失效，尝试通过凭据自动重新登录...")

    cred = load_credentials()
    if cred:
        uid, pw = cred
        print(f"[*] 正在通过延世门户 SSO 登录 (账号: {uid})...")
        s = sso_login(uid, pw)
        save_cookies(s)
        print("[+] 登录成功，已自动续期 cookies.txt")
        return s

    print("[!] 尚未登录。请输入学号与密码：")
    uid = input("学号 (Student ID): ").strip()
    import getpass
    pw = getpass.getpass("密码 (Portal Password): ")
    save = input("是否记住密码供自动登录? [Y/n]: ").strip().lower() != "n"
    s = sso_login(uid, pw)
    save_cookies(s)
    if save:
        CRED_FILE.write_text(json.dumps({"id": uid, "pw": pw}), encoding="utf-8")
        try:
            os.chmod(CRED_FILE, 0o600)
        except OSError:
            pass
        print(f"[+] 凭据已加密保存至 {CRED_FILE}")
    print("[+] 登录成功！")
    return s


def cmd_login(args):
    cred = load_credentials()
    uid = (cred[0] if cred else None) or input("学号 (Student ID): ").strip()
    import getpass
    pw = (cred[1] if cred else None) or getpass.getpass("密码 (Portal Password): ")
    s = sso_login(uid, pw)
    save_cookies(s)
    if args.save:
        CRED_FILE.write_text(json.dumps({"id": uid, "pw": pw}), encoding="utf-8")
        try:
            os.chmod(CRED_FILE, 0o600)
        except OSError:
            pass
        print(f"[+] 凭据已保存到 {CRED_FILE}")
    print(f"[+] 登录成功，Cookie 已保存至 {COOKIE_FILE}")


# ---------------------------------------------------------------- utils

def clean(t):
    return re.sub(r"\s+", " ", t).strip()


def soup_of(s, url):
    r = s.get(url, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def safe_name(name, limit=120):
    name = re.sub(r'[\/:*?"<>|\r\n\t]', "_", clean(name)).strip(" ._")
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot + 1:]) if dot > 0 else (name, "")
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
        m = re.search(r"id=(\d+)", a["href"])
        if not m:
            continue
        cid = m.group(1)
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
        num = int(m.group(1) or m.group(2))
        return f"{num:02d}주차", name
    return f"00_{safe_name(name, 30)}", name


def section_range(name, year):
    m = re.search(r"(\d+)월\s*(\\d+)일\s*-\s*(\d+)월\s*(\\d+)일", name)
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
    """ubcompletion 出席情况"""
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

    out = Path(getattr(args, "out", None) or HERE / "report.html")
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


# ---------------------------------------------------------------- doc2md 模块

def docx_to_markdown(docx_path: Path) -> Path:
    """将 .docx 文件转换为结构清晰、方便 Harness 检索阅读的 Markdown 文件"""
    md_path = docx_path.with_suffix(".md")
    if md_path.exists() and md_path.stat().st_mtime >= docx_path.stat().st_mtime:
        return md_path

    try:
        import docx
        doc = docx.Document(docx_path)
    except Exception as e:
        print(f"      ! docx 解析失败 {docx_path.name}: {e}")
        return None

    lines = [
        f"# {docx_path.stem}\n",
        f"> *[自动化提取] 原始文件: `{docx_path.name}`*\n"
    ]

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
        rows_data = []
        for row in table.rows:
            rows_data.append([clean(cell.text).replace("|", "\\|") for cell in row.cells])
        if rows_data:
            header = rows_data[0]
            lines.append("| " + " | ".join(header) + " |")
            lines.append("| " + " | ".join(["---"] * len(header)) + " |")
            for row in rows_data[1:]:
                lines.append("| " + " | ".join(row) + " |")
            lines.append("\n")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def doc_to_markdown(doc_path: Path) -> Path:
    """老版二进制 .doc 文件纯文本提取转 Markdown"""
    md_path = doc_path.with_suffix(".md")
    if md_path.exists() and md_path.stat().st_mtime >= doc_path.stat().st_mtime:
        return md_path

    try:
        raw_bytes = doc_path.read_bytes()
        extracted = []
        # Word 97-2003 常用 UTF-16LE 提取
        for chunk in re.findall(rb'(?:[\x20-\x7e\r\n\t]|\x00)+', raw_bytes):
            try:
                txt = chunk.decode('utf-16le').strip()
                if len(txt) > 3 and not all(c.isascii() and not c.isalnum() for c in txt):
                    extracted.append(txt)
            except Exception:
                pass
        if not extracted:
            for enc in ('utf-8', 'cp949', 'euc-kr', 'latin-1'):
                try:
                    txt = raw_bytes.decode(enc, errors='ignore')
                    valid_lines = [line.strip() for line in txt.splitlines() if len(line.strip()) > 3]
                    if len(valid_lines) > 5:
                        extracted = valid_lines
                        break
                except Exception:
                    pass

        lines = [
            f"# {doc_path.stem}\n",
            f"> *[自动化提取] 原始文件: `{doc_path.name}`*\n"
        ]
        lines.extend(extracted)
        md_path.write_text("\n\n".join(lines), encoding="utf-8")
        return md_path
    except Exception as e:
        print(f"      ! doc 提取失败 {doc_path.name}: {e}")
        return None


def convert_doc_file(path: Path) -> Path:
    """自动将 docx, doc, pptx, pdf 转换为清晰易读的 Markdown"""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return docx_to_markdown(path)
    elif suffix == ".doc":
        return doc_to_markdown(path)
    elif suffix == ".pptx":
        return pptx_to_markdown(path)
    elif suffix == ".pdf":
        return pdf_to_markdown(path)
    return None


def pptx_to_markdown(pptx_path: Path) -> Path:
    """提取 PPTX 课件幻灯片内容并转换为 Markdown"""
    md_path = pptx_path.with_suffix(".md")
    if md_path.exists() and md_path.stat().st_mtime >= pptx_path.stat().st_mtime:
        return md_path
    try:
        from pptx import Presentation
        prs = Presentation(pptx_path)
    except Exception as e:
        print(f"      ! pptx 解析失败 {pptx_path.name}: {e}")
        return None

    lines = [
        f"# {pptx_path.stem}\n",
        f"> *[自动化提取幻灯片课件] 原始文件: `{pptx_path.name}`*\n"
    ]
    for idx, slide in enumerate(prs.slides, start=1):
        slide_lines = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    t = paragraph.text.strip()
                    if t:
                        slide_lines.append(f"- {t}")
            elif shape.has_table:
                table = shape.table
                rows = list(table.rows)
                if len(rows) > 0:
                    slide_lines.append("\n| " + " | ".join(clean(c.text).replace("|", "\\|") for c in rows[0].cells) + " |")
                    slide_lines.append("| " + " | ".join("---" for _ in rows[0].cells) + " |")
                    for row in rows[1:]:
                        slide_lines.append("| " + " | ".join(clean(c.text).replace("|", "\\|") for c in row.cells) + " |")
        if slide_lines:
            lines.append(f"\n## Slide {idx}\n")
            lines.extend(slide_lines)

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def pdf_to_markdown(pdf_path: Path) -> Path:
    """提取 PDF 课件文档内容并转换为 Markdown"""
    md_path = pdf_path.with_suffix(".md")
    if md_path.exists() and md_path.stat().st_mtime >= pdf_path.stat().st_mtime:
        return md_path
    try:
        import pypdf
        reader = pypdf.PdfReader(str(pdf_path))
    except Exception as e:
        print(f"      ! pdf 解析失败 {pdf_path.name}: {e}")
        return None

    lines = [
        f"# {pdf_path.stem}\n",
        f"> *[自动化提取课件文档] 原始文件: `{pdf_path.name}`*\n"
    ]
    for idx, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            lines.append(f"\n## 第 {idx} 页\n")
            lines.append(text + "\n")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def convert_all_docs_in_dir(target_dir: Path):
    """递归将目录中的所有 doc/docx/pptx/pdf 转换为 md"""
    converted = 0
    for ext in ("*.docx", "*.doc", "*.pptx", "*.pdf"):
        for doc_file in target_dir.rglob(ext):
            if convert_doc_file(doc_file):
                converted += 1
    return converted


# ---------------------------------------------------------------- transcribe 模块

def transcribe_video_raw(video_path: Path, lang: str = "ko") -> Path:
    """调用本地阿里 SenseVoice 模型，提取课堂录播外语纯原文讲义（带时间标记），无需 LLM 翻译，秒级完成"""
    transcript_md = video_path.with_suffix(".transcript.md")
    if transcript_md.exists() and transcript_md.stat().st_size > 0:
        return transcript_md

    # 路径转换
    if os.name != "nt":
        wsl_path = str(video_path.resolve())
        cmd = [
            "/home/miner/subtitle_pipeline/.venv/bin/python",
            "/home/miner/subtitle_pipeline/transcribe_translate.py",
            "--input", wsl_path,
            "--lang", lang,
            "--mode", "raw_txt"
        ]
    else:
        win_str = str(video_path.resolve())
        m = re.match(r"^([a-zA-Z]):[\\/](.*)", win_str)
        if m:
            drive = m.group(1).lower()
            sub = m.group(2).replace("\\", "/")
            wsl_path = f"/mnt/{drive}/{sub}"
        else:
            wsl_path = win_str
        cmd = [
            "wsl.exe", "-d", "Ubuntu-Agent", "-u", "miner",
            "--cd", "/home/miner/subtitle_pipeline",
            "/home/miner/subtitle_pipeline/.venv/bin/python",
            "transcribe_translate.py",
            "--input", wsl_path,
            "--lang", lang,
            "--mode", "raw_txt"
        ]

    try:
        print(f"      🎙️ 正在提取课堂实录原文: {video_path.name} ...", flush=True)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if res.returncode == 0 and transcript_md.exists():
            print(f"      ✅ 纯原文讲义生成完成: {transcript_md.name}")
            return transcript_md
        else:
            print(f"      ! 转写异常返回 (code {res.returncode}): {res.stderr[:200] or res.stdout[:200]}")
    except Exception as e:
        print(f"      ! 转写调用失败 {video_path.name}: {e}")
    return None


# ---------------------------------------------------------------- indexer 模块 (像学霸一样构建知识库索引)

def build_unit_readme(unit_dir: Path, unit_name: str, activities: list = None, assignments: list = None):
    """为各个单元（周次）生成本周学习指南 README.md"""
    lines = [
        f"# {unit_name}",
        f"> 单元目录：`{unit_dir.name}` | 自动规整学习单元\n",
        "## 📚 本单元课件与阅读材料 (Course Materials)"
    ]

    # 扫描课件与转换的 Markdown
    materials = [f for f in unit_dir.iterdir() if f.is_file() and not f.name.endswith(".mp4") and not f.name.endswith(".transcript.md") and not f.name.endswith(".raw.srt") and not f.name.endswith(".raw.txt") and f.name != "README.md"]
    if materials:
        for m in sorted(materials, key=lambda x: x.name):
            if m.suffix.lower() in [".doc", ".docx", ".pdf", ".pptx"]:
                md_ver = m.with_suffix(".md")
                if md_ver.exists():
                    lines.append(f"- 📄 **{m.stem}**: [阅读 Markdown 格式]({md_ver.name}) *(原始文件: [{m.name}]({m.name}))*")
                else:
                    lines.append(f"- 📄 [{m.name}]({m.name})")
            elif m.suffix.lower() == ".md":
                # 若对应原始课件文件已存在，则上文已一并索引，此处跳过避免重复
                orig_exists = any(m.with_suffix(s).exists() for s in [".docx", ".doc", ".pdf", ".pptx"])
                if not orig_exists:
                    lines.append(f"- 📝 [{m.name}]({m.name})")
            else:
                lines.append(f"- 📑 [{m.name}]({m.name})")
    else:
        lines.append("- *(本单元暂无附件资料)*")

    lines.append("\n## 🎥 课堂录播与讲义原文 (Lectures & Transcripts)")
    videos = list(unit_dir.glob("*.mp4"))
    if videos:
        for v in sorted(videos, key=lambda x: x.name):
            t_md = v.with_suffix(".transcript.md")
            lines.append(f"### 🎬 {v.stem}")
            lines.append(f"- 视频源文件：[{v.name}]({v.name})")
            if t_md.exists():
                lines.append(f"- 📝 **课堂讲义原文**：[查看外语发言实录]({t_md.name})")
                # 提取前 3 行预览
                try:
                    t_lines = [l.strip() for l in t_md.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#") and not l.startswith(">")]
                    if t_lines:
                        preview = "\n> ".join(t_lines[:3])
                        lines.append(f"> 💬 **讲义内容预览**：\n> {preview}\n")
                except Exception:
                    pass
            else:
                lines.append("- *(讲义尚未提取)*")
    else:
        lines.append("- *(本单元无录播视频)*")

    if assignments:
        lines.append("\n## 📌 本单元任务与作业 (Assignments)")
        for a in assignments:
            lines.append(f"- 📝 **{a['name']}**: 截止时间 `{a.get('due') or '未知'}` ({a.get('remain') or ''})")

    (unit_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def build_course_readme(course_dir: Path, cname: str, cid: str, sections: list, assignments: list = None):
    """为整门课程生成课程大纲导航 README.md"""
    lines = [
        f"# {cname}",
        f"> 课程编号：`{cid}` | 延世大学 LearnUs 在修课程知识库\n",
        "## 📅 单元学习导航 (Course Units)",
        "| 单元编号 | 单元名称 | 课件数 | 视频数 | 单元索引入口 |",
        "| :--- | :--- | :---: | :---: | :--- |"
    ]

    for folder, sec_name in sections:
        sec_dir = course_dir / folder
        if not sec_dir.exists():
            continue
        mat_count = len([f for f in sec_dir.iterdir() if f.is_file() and not f.name.endswith(".mp4") and not f.name.endswith(".transcript.md") and f.name != "README.md"])
        vod_count = len(list(sec_dir.glob("*.mp4")))
        lines.append(f"| **{folder}** | {sec_name} | {mat_count} | {vod_count} | [进入单元学习]({folder}/README.md) |")

    if assignments:
        lines.append("\n## ⚠️ 课程未完成待办事项 (Pending Tasks)")
        for a in assignments:
            lines.append(f"- [{' ' if not a.get('overdue') else 'x'}] **{a['name']}** - 截止: `{a.get('due') or '?'}` ({a.get('remain') or ''}) [去提交]({a.get('url')})")

    (course_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def build_workspace_readme(root: Path, course_summaries: list, all_pending: list = None):
    """为工作区顶层生成整学期知识库总索引 README.md，专为 Harness 设计"""
    lines = [
        "# 🎓 延世大学个人课程学习知识库 (LearnUs Study Workspace)",
        "> 本知识库由 `learnus-helper` 自动构建与同步。专为 AI Agent Harness（Claude Code, Cursor 等）打造。\n",
        "## 🤖 AI Harness / Agent 使用指引",
        "1. **结构说明**：每个课程目录代表一门学科，子目录对应各个单元/周次（`01주차`, `02주차` 等）。",
        "2. **Markdown 课件优先阅读**：所有原始 `.docx` / `.doc` 课件已被自动转换为排版结构化的 `.md` 文件，包含标题层级与表格，请优先阅读对应 `.md`。",
        "3. **课堂发言实录**：所有课程视频已由阿里 SenseVoice 本地 GPU 模型提取为纯外语原文讲义（`*.transcript.md`），包含精确时间点标记。",
        "4. **复习与辅导**：可直接检索各个单元的 `README.md` 获取该周的大纲、课件与视频讲义，进行针对性复习、知识点问答或考前划重点。\n",
        "## 📖 本学期在修课程清单",
        "| 课程名称 | 单元数 | 课程大纲索引 |",
        "| :--- | :---: | :--- |"
    ]

    for c in course_summaries:
        lines.append(f"| **{c['name']}** | {c['units']} | [进入课程目录]({c['dir']}/README.md) |")

    if all_pending:
        lines.append("\n## 🚨 全局待办作业看板 (Upcoming Deadlines)")
        for a in all_pending:
            tag = "🔴 已过期" if a.get("overdue") else f"⏰ {a.get('remain')}"
            lines.append(f"- **[{a['course']}]** [{a['name']}]({a['url']}) | 截止: `{a['due'] or '?'}` ({tag})")

    (root / "README.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- download & sync

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
    if dest.exists() and dest.stat().st_size > 0:
        return False
    page = s.get(f"{BASE}/mod/vod/viewer.php?id={vod_id}", timeout=30).text
    m = re.search(r'src="(https?://[^"]+\.m3u8[^"]*)"', page)
    if not m:
        print(f"      ! 暂未获取到 m3u8 (可能未开放或无需录播) vod {vod_id}")
        return False
    if not shutil.which("ffmpeg"):
        print("      ! 系统未找到 ffmpeg，跳过视频下载")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-headers", f"Referer: {BASE}/\r\n",
                    "-i", m.group(1), "-c", "copy", "-bsf:a", "aac_adtstoasc", "-y", str(tmp)], check=True)
    tmp.replace(dest)
    return True


def cmd_download(args):
    s = make_session()
    root = Path(getattr(args, "out", None) or HERE / "downloads")
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
                        if getattr(args, "convert_docs", False) and path.suffix.lower() in [".doc", ".docx"]:
                            docx_to_markdown(path) if path.suffix.lower() == ".docx" else doc_to_markdown(path)
                elif a["mod"] == "vod" and getattr(args, "videos", False) and not STAFF_ONLY.search(a["name"]):
                    dest = wdir / f"{safe_name(a['name'])}.mp4"
                    print(f"   ~ video {dest.relative_to(root)}")
                    new_vod = download_video(s, a["id"], dest)
                    total += new_vod
                    if getattr(args, "transcribe", False) and dest.exists():
                        transcribe_video_raw(dest, lang=getattr(args, "lang", "ko"))
                elif a["mod"] == "ubboard":
                    if SKIP_BOARD.search(a["name"]):
                        continue
                    if not (getattr(args, "all_boards", False) or RESOURCE_BOARD.search(a["name"])):
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
                            if getattr(args, "convert_docs", False) and path.suffix.lower() in [".doc", ".docx"]:
                                docx_to_markdown(path) if path.suffix.lower() == ".docx" else doc_to_markdown(path)
                        time.sleep(0.3)
            except Exception as ex:
                print(f"   ! {a['mod']} {a['name']}: {ex}")
            time.sleep(0.2)
    print(f"\ndone, {total} new file(s) -> {root}")


def cmd_sync(args):
    """【学霸全自动同步】
    1. 自动登录延世门户
    2. 按课程、按单元分类下载所有课件、资料室文件与录播视频
    3. 自动将 .doc / .docx 转换为排版清晰的 .md 供 Harness 检索
    4. 本地调用阿里 SenseVoice 极速提取课堂外语原生讲义
    5. 自动生成各级 README.md 知识树
    """
    s = make_session()
    root = Path(getattr(args, "out", None) or DEFAULT_WORKSPACE)
    root.mkdir(parents=True, exist_ok=True)
    lang = getattr(args, "lang", "ko")
    download_videos = not getattr(args, "no_videos", False)

    print(f"\n=======================================================")
    print(f" 🚀 正在同步延世大学课程知识库 -> {root}")
    print(f"    - 下载视频录播: {'是' if download_videos else '否'}")
    print(f"    - 课堂讲义转写: 是 (阿里 SenseVoice 本地模型 | 纯外语原文)")
    print(f"    - 文档格式转换: 是 (.doc/.docx -> .md)")
    print(f"=======================================================\n")

    course_summaries = []
    all_pending = []

    courses = list_courses(s, args)
    now = dt.datetime.now()

    for cid, title in courses.items():
        cname = short_course_name(title, cid)
        course_dir = root / cname
        course_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n📁 【课程】{cname} (ID: {cid})", flush=True)

        sections, acts = parse_course(s, cid)
        year = dt.date.today().year
        ranges = [(f, r) for f, n in sections if (r := section_range(n, year))]

        # 整理本门课作业
        course_assignments = []
        for a in acts:
            if a["mod"] == "assign":
                submitted, status, due, remain = assignment_status(s, a["id"])
                info = {
                    "course": cname, "name": a["name"], "due": str(due) if due else "?",
                    "remain": remain, "overdue": bool(due and due < now), "submitted": submitted,
                    "url": f"{BASE}/mod/assign/view.php?id={a['id']}",
                    "section": a["section"]
                }
                if not submitted:
                    course_assignments.append(info)
                    all_pending.append(info)

        # 下载课件、视频、公告板
        for a in acts:
            try:
                wdir = course_dir / a["section"]
                wdir.mkdir(parents=True, exist_ok=True)

                if a["mod"] in ("ubfile", "resource", "folder"):
                    target = wdir / safe_name(a["name"]) if a["mod"] == "folder" else wdir
                    for url in module_files(s, a):
                        path, new = fetch_file(s, url, target)
                        status_sym = "+" if new else "="
                        print(f"   {status_sym} 课件: {path.name}")
                        if path.suffix.lower() in [".doc", ".docx", ".pptx", ".pdf"]:
                            md_f = convert_doc_file(path)
                            if md_f:
                                print(f"      📄 已转换为 Markdown: {md_f.name}")
                        elif path.suffix.lower() == ".mp4" and download_videos:
                            transcribe_video_raw(path, lang=lang)

                elif a["mod"] == "vod" and download_videos and not STAFF_ONLY.search(a["name"]):
                    dest = wdir / f"{safe_name(a['name'])}.mp4"
                    new_vod = download_video(s, a["id"], dest)
                    if new_vod:
                        print(f"   + 视频已下载: {dest.name}")
                    if dest.exists():
                        transcribe_video_raw(dest, lang=lang)

                elif a["mod"] == "ubboard":
                    if SKIP_BOARD.search(a["name"]):
                        continue
                    if not (getattr(args, "all_boards", False) or RESOURCE_BOARD.search(a["name"])):
                        continue
                    for post in board_posts(s, a["id"]):
                        subject, date, files = post_details(s, post)
                        if not files:
                            continue
                        week = (week_for_date(ranges, date) if date else None) or "00_날짜미상"
                        target = course_dir / week / safe_name(a["name"], 40) / safe_name(subject, 80)
                        for url in files:
                            path, new = fetch_file(s, url, target)
                            status_sym = "+" if new else "="
                            print(f"   {status_sym} 资料室附件: {path.name}")
                            if path.suffix.lower() in [".doc", ".docx", ".pptx", ".pdf"]:
                                md_f = convert_doc_file(path)
                                if md_f:
                                    print(f"      📄 已转换为 Markdown: {md_f.name}")
                            elif path.suffix.lower() == ".mp4" and download_videos:
                                transcribe_video_raw(path, lang=lang)
                        time.sleep(0.3)
            except Exception as ex:
                print(f"   ! {a['mod']} {a['name']}: {ex}")
            time.sleep(0.1)

        # 构建本课程各个单元的 README.md
        for folder, sec_name in sections:
            sec_dir = course_dir / folder
            if sec_dir.exists():
                sec_assigns = [x for x in course_assignments if x["section"] == folder]
                build_unit_readme(sec_dir, f"{cname} - {sec_name}", assignments=sec_assigns)

        # 构建课程级 README.md
        build_course_readme(course_dir, cname, cid, sections, assignments=course_assignments)
        course_summaries.append({
            "name": cname,
            "units": len(sections),
            "dir": cname
        })

    # 构建全局工作区 README.md
    build_workspace_readme(root, course_summaries, all_pending=all_pending)
    print(f"\n=======================================================")
    print(f" ✨ 知识库同步与整理全部完成！")
    print(f" 📂 工作区根目录: {root.resolve()}")
    print(f" 📖 总索引文件:   {(root / 'README.md').resolve()}")
    print(f" 🤖 任何 AI Agent / Harness 可直接读取上述目录开启学习辅导！")
    print(f"=======================================================\n")


def cmd_reindex(args):
    """对已存在的下载工作区，重新跑 doc 转 md、讲义转写补充以及重建 README 索引树"""
    root = Path(getattr(args, "out", None) or DEFAULT_WORKSPACE)
    if not root.exists():
        print(f"[!] 目录不存在: {root}")
        return
    print(f"[*] 正在重构工作区知识库索引: {root} ...")
    convert_count = convert_all_docs_in_dir(root)
    print(f"[+] 已完成 {convert_count} 个 doc/docx 的 Markdown 转换")

    course_summaries = []
    for c_dir in sorted([d for d in root.iterdir() if d.is_dir()]):
        units = [u for u in c_dir.iterdir() if u.is_dir()]
        for u in units:
            build_unit_readme(u, f"{c_dir.name} - {u.name}")
        dummy_sections = [(u.name, u.name) for u in units]
        build_course_readme(c_dir, c_dir.name, "LOCAL", dummy_sections)
        course_summaries.append({
            "name": c_dir.name,
            "units": len(units),
            "dir": c_dir.name
        })

    build_workspace_readme(root, course_summaries)
    print(f"[+] 索引重构完成！总目录: {root / 'README.md'}")


# ---------------------------------------------------------------- watch (自动巡检与 2 倍速挂机浏览出勤)

async def _play_vod_session(vod_id: str, title: str, speed: float = 2.0, target_seconds: int = None):
    from playwright.async_api import async_playwright

    cookies_list = []
    if COOKIE_FILE.exists():
        for pair in COOKIE_FILE.read_text(encoding="utf-8").split(";"):
            if "=" in pair:
                k, v = pair.strip().split("=", 1)
                cookies_list.append({"name": k, "value": v, "domain": ".learnus.org", "path": "/"})
                cookies_list.append({"name": k, "value": v, "domain": "ys.learnus.org", "path": "/"})

    url = f"{BASE}/mod/vod/viewer.php?id={vod_id}"
    print(f"\n   🎬 正在接入视频播放: [{title}] (ID: {vod_id})")
    print(f"      - 设定倍速: {speed}x | 无头安全环境运行")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--autoplay-policy=no-user-gesture-required"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
        )
        await context.add_cookies(cookies_list)
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="load", timeout=60000)
            await page.wait_for_selector("video, iframe", timeout=20000)
        except Exception as e:
            print(f"      ! 播放器页面加载超时: {e}")
            await browser.close()
            return False

        # 启动视频播放与倍速
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
            print("      ! 未能定位到原生 video 元素，可能播放器结构异常")
            await browser.close()
            return False

        raw_dur = init_st.get("duration")
        dur_sec = int(raw_dur) if (raw_dur is not None and not (isinstance(raw_dur, float) and math.isnan(raw_dur))) else 0
        # 出勤保护：为防止最后一次心跳时间戳导致服务器少计几秒，要求时长增加 60 秒安全缓冲（不超过视频总时长）
        if target_seconds:
            req_sec = target_seconds + 60
            if dur_sec > 0 and req_sec > dur_sec:
                req_sec = dur_sec
        else:
            req_sec = dur_sec

        print(f"      ▶️ 视频总长约 {dur_sec//60}分{dur_sec%60:02d}秒 | 目标达成: {req_sec//60}分{req_sec%60:02d}秒")

        completed = False
        while True:
            await asyncio.sleep(10)
            st = await page.evaluate(f"""
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
            """)
            if not st:
                break

            cur_raw = st.get("current")
            cur = int(cur_raw) if (cur_raw is not None and not (isinstance(cur_raw, float) and math.isnan(cur_raw))) else 0
            d_raw = st.get("duration")
            if d_raw is not None and not (isinstance(d_raw, float) and math.isnan(d_raw)) and int(d_raw) > 0:
                dur = int(d_raw)
            else:
                dur = dur_sec or req_sec or 1
            pct = int(cur / dur * 100) if dur else 0
            bar = "█" * (pct // 5) + "░" * (20 - (pct // 5))

            print(f"\r      ⏱️ [{bar}] {pct:3d}% | {cur//60:02d}:{cur%60:02d} / {dur//60:02d}:{dur%60:02d} (速度 {speed}x)", end="", flush=True)

            if st.get("ended") or (req_sec > 0 and cur >= req_sec) or (dur > 0 and cur >= dur - 2):
                completed = True
                print(f"\n      ✅ 已达到目标出勤要求！")
                # 触发暂停促使 learningChecker 立即刷出最后一段出勤心跳包
                await page.evaluate("() => { const v = document.querySelector('video'); if (v) v.pause(); }")
                await asyncio.sleep(8)
                break

        await browser.close()
        return completed


def parse_time_seconds(time_str: str) -> int:
    """解析 MM:SS 格式为总秒数"""
    if not time_str or ":" not in time_str:
        return 0
    parts = time_str.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


def cmd_watch(args):
    """【自动化网课浏览与出勤】扫描未出勤的视频，按顺序 2 倍速安全挂机达标"""
    import asyncio
    s = make_session()
    courses = list_courses(s, args)
    speed = float(getattr(args, "speed", 2.0))
    limit_count = int(getattr(args, "limit", 0))

    print(f"\n=======================================================")
    print(f" 🚀 正在启动 LearnUs 智能出勤与课程浏览机器人")
    print(f"    - 播放倍速: {speed}x (合规安全心跳模式)")
    print(f"    - 扫描目标: {len(courses)} 门课程")
    print(f"=======================================================\n")

    unwatched_tasks = []

    for cid, title in courses.items():
        cname = short_course_name(title, cid)
        sections, acts = parse_course(s, cid)
        report = attendance_report(s, cid)

        for a in acts:
            if a["mod"] == "vod" and not STAFF_ONLY.search(a["name"]):
                req_str, watched_str, att = report.get(a["name"], ("-", "-", "-"))
                # 如果已经达标标记为 O，直接跳过
                if att == "O":
                    continue

                # 检查是否开放
                if not a.get("open", True):
                    continue

                req_sec = parse_time_seconds(req_str)
                unwatched_tasks.append({
                    "cid": cid,
                    "course": cname,
                    "section": a["section"],
                    "name": a["name"],
                    "id": a["id"],
                    "req_sec": req_sec,
                    "req_str": req_str,
                    "watched_str": watched_str,
                    "status": att
                })

    if not unwatched_tasks:
        print("🎉 太棒了！您当前所有开放课程的视频出勤全部已达标（100%），无需挂机！\n")
        return

    print(f"📌 发现以下 {len(unwatched_tasks)} 个待出勤视频任务：")
    for idx, t in enumerate(unwatched_tasks):
        print(f"  [{idx+1}] 【{t['course']}】({t['section']}) {t['name']} | 要求: {t['req_str']} (已看: {t['watched_str']})")

    if limit_count > 0:
        unwatched_tasks = unwatched_tasks[:limit_count]

    print("\n-------------------------------------------------------")
    print(" 🎬 开始按序自动播放与出勤上报...")
    print("-------------------------------------------------------")

    for idx, task in enumerate(unwatched_tasks):
        print(f"\n>> 正在处理 ({idx+1}/{len(unwatched_tasks)}): 【{task['course']}】{task['name']}")
        asyncio.run(_play_vod_session(task["id"], task["name"], speed=speed, target_seconds=task["req_sec"]))
        time.sleep(2)

    print("\n=======================================================")
    print(" 🎉 所有视频挂机任务已处理完毕！正在刷新最新出勤看板...")
    print("=======================================================\n")
    cmd_todo(args)


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description="LearnUs 智能助学工作区 & 知识库自动化系统")
    sub = p.add_subparsers(dest="cmd", required=True)

    # 1. sync (学霸模式全自动同步)
    sy = sub.add_parser("sync", help="【推荐】全自动同步：下载课件/视频 + doc转md + 提取讲义 + 构建知识库")
    sy.add_argument("--out", help="知识库工作区输出根目录 (默认: ./workspace)")
    sy.add_argument("--course", help="仅同步指定课程 (课程ID或名称关键字)")
    sy.add_argument("--lang", default="ko", choices=["ko", "en", "zh", "ja", "auto"], help="课堂实录语种 (默认: ko)")
    sy.add_argument("--no-videos", action="store_true", help="跳过视频下载与转写，仅同步课件文档")
    sy.add_argument("--include-noncredit", action="store_true", help="包含非学分 (비교과) 课程")
    sy.add_argument("--all-boards", action="store_true", help="包含所有公告板附件")

    # 2. login
    lg = sub.add_parser("login", help="延世门户 SSO 登录")
    lg.add_argument("--save", action="store_true", help="记住账号密码至 credentials.json，过期自动免密登录")

    # 3. cookie
    c = sub.add_parser("cookie", help="从开了远程调试端口的 Chrome 抓取 Cookie")
    c.add_argument("--port", type=int, default=9222)

    # 4. todo
    t = sub.add_parser("todo", help="查看未提交作业与未出席视频")
    t.add_argument("--out", help="导出报告 HTML 路径")
    t.add_argument("--include-noncredit", action="store_true", help="包含非学分课程")
    t.add_argument("--course", help="仅指定课程")

    # 5. download
    d = sub.add_parser("download", help="按周次分类下载课程资料与视频")
    d.add_argument("--out", help="下载根目录")
    d.add_argument("--videos", action="store_true", help="同时下载录播视频")
    d.add_argument("--transcribe", "-t", action="store_true", help="视频下载后自动调用 SenseVoice 提取外语讲义")
    d.add_argument("--convert-docs", action="store_true", help="自动将 doc/docx 转换为 Markdown")
    d.add_argument("--lang", default="ko", help="讲义语种 (默认: ko)")
    d.add_argument("--all-boards", action="store_true", help="下载所有公告板附件")
    d.add_argument("--include-noncredit", action="store_true", help="包含非学分课程")
    d.add_argument("--course", help="仅指定课程")

    # 6. reindex
    rx = sub.add_parser("reindex", help="扫描已有目录，补全 doc 转 md 并重建知识库 README 索引")
    rx.add_argument("--out", help="工作区根目录 (默认: ./workspace)")

    # 7. watch (自动出勤巡检与 2 倍速网课浏览)
    w = sub.add_parser("watch", help="【智能出勤】未看视频按顺序 2 倍速安全挂机达标")
    w.add_argument("--course", help="仅指定课程 (ID 或名称关键字)")
    w.add_argument("--speed", type=float, default=2.0, help="播放倍速 (默认: 2.0)")
    w.add_argument("--limit", type=int, default=0, help="本次最多处理几个视频 (0 表示全部)")
    w.add_argument("--include-noncredit", action="store_true", help="包含比较科非学分培训")

    args = p.parse_args()
    cmd_map = {
        "sync": cmd_sync,
        "login": cmd_login,
        "cookie": cmd_cookie,
        "todo": cmd_todo,
        "download": cmd_download,
        "reindex": cmd_reindex,
        "watch": cmd_watch
    }
    cmd_map[args.cmd](args)


if __name__ == "__main__":
    main()
