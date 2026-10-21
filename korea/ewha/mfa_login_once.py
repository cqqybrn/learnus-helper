#!/usr/bin/env python3
"""MFA login: start (send email + save session) | finish CODE (verify only)."""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from ewha import (
    BASE,
    HERE,
    NSSO,
    _save_lwp_cookies,
    _seed_encrypt,
    list_courses,
    logged_in,
    make_session,
)

PENDING = HERE / ".ewha.mfa-pending.json"


def _sso_to_2ndauth(s, sid, pwd):
    s.get(BASE + "/", timeout=45)
    s.get(BASE + "/index_newsso.php", timeout=45)
    s.post(
        f"{NSSO}/sso/user/login/link",
        data={"agt_id": "ewha-prod-cyber", "agt_url": BASE, "ismobile": "0"},
        timeout=45,
    )
    key = json.loads(s.post(f"{NSSO}/sso/user/login/init", data={}).text)["result_data"]
    enc = _seed_encrypt({"login_id": sid, "login_pwd": pwd}, key)
    resp = json.loads(
        s.post(f"{NSSO}/sso/user/ewha/login/auth", data={"user_data": enc}).text
    )
    return key, resp


def _save_pending(s, key):
    cookies = []
    for c in s.cookies:
        cookies.append(
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path or "/",
            }
        )
    PENDING.write_text(
        json.dumps({"key": key, "cookies": cookies}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        os.chmod(PENDING, 0o600)
    except OSError:
        pass


def _load_pending(s):
    from requests.cookies import RequestsCookieJar

    data = json.loads(PENDING.read_text(encoding="utf-8"))
    jar = RequestsCookieJar()
    for c in data["cookies"]:
        jar.set(
            c["name"],
            c["value"],
            domain=c.get("domain") or "",
            path=c.get("path") or "/",
        )
    s.cookies = jar
    return data["key"]


def cmd_start():
    sid = os.environ.get("EWHA_ID")
    pwd = os.environ.get("EWHA_PW")
    if not sid or not pwd:
        print("set EWHA_ID and EWHA_PW", file=sys.stderr)
        sys.exit(1)
    s = make_session()
    if logged_in(s):
        print("ALREADY_LOGGED_IN")
        PENDING.unlink(missing_ok=True)
        return
    key, resp = _sso_to_2ndauth(s, sid, pwd)
    print("auth:", resp.get("data"), resp.get("code"))
    if resp.get("data") != "2ndauth":
        sys.exit(1)
    sent = json.loads(
        s.post(
            f"{NSSO}/sso/user/api/mfa/request",
            data={"user_data": _seed_encrypt({"crtfc_type": "mail"}, key)},
        ).text
    )
    print("mfa_request:", sent.get("result"), sent.get("code"))
    if not sent.get("result"):
        sys.exit(1)
    _save_pending(s, key)
    print("PENDING_SAVED: reply with email code (use: finish CODE)")


def cmd_finish(code: str):
    if not PENDING.exists():
        print("no pending MFA session; run: start", file=sys.stderr)
        sys.exit(1)
    s = make_session()
    key = _load_pending(s)
    verified = json.loads(
        s.post(
            f"{NSSO}/sso/user/api/mfa/verify",
            data={
                "user_data": _seed_encrypt(
                    {"crtfc_type": "mail", "crtfc_no": code.strip()}, key
                )
            },
        ).text
    )
    print("verify:", verified)
    if not verified.get("result"):
        sys.exit(2)
    page = s.get(f"{NSSO}/sso/user/login/link", timeout=45).text
    m = re.search(r'name="sso_code" value="([^"]+)"', page)
    if not m:
        print("no sso_code")
        sys.exit(3)
    s.post(
        f"{BASE}/passni5/login_proc.php",
        data={"sso_code": m.group(1), "ismobile": "0"},
        timeout=45,
    )
    _save_lwp_cookies(s)
    PENDING.unlink(missing_ok=True)
    ok = logged_in(s)
    print("logged_in:", ok)
    courses = list_courses(s, argparse.Namespace(course=None))
    print("courses:", len(courses))
    for cid, title in list(courses.items())[:5]:
        print(f"  {cid} {title[:70]}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start", help="send MFA email and save SSO session")
    fin = sub.add_parser("finish", help="verify code against saved session")
    fin.add_argument("code")
    args = p.parse_args()
    if args.cmd == "start":
        cmd_start()
    else:
        cmd_finish(args.code)


if __name__ == "__main__":
    main()
