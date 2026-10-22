import asyncio
import json
import os
import sys

def get_config(key, default=""):
    val = os.environ.get(key)
    if val:
        return val
    for p in [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "credentials.json"),
    ]:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    if key in cfg and cfg[key]:
                        return cfg[key]
            except Exception:
                pass
    return default

async def main():
    from playwright.async_api import async_playwright

    uid = get_config("KMOOC_ID")
    pw = get_config("KMOOC_PW")
    cid = get_config("KMOOC_COURSE", "20515")
    if not uid or not pw:
        print("Error: KMOOC_ID / KMOOC_PW not set in environment or credentials.json")
        sys.exit(1)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await page.goto("https://www.kmooc.kr/login", wait_until="networkidle")
        await page.fill("#userid", uid)
        await page.fill("#password", pw)
        await page.locator("button.button_login.btn_submit").click()
        await page.wait_for_timeout(4000)
        await page.goto(
            f"https://lms.kmooc.kr/local/ubonattend/report.php?id={cid}",
            wait_until="networkidle",
            timeout=90000,
        )
        rows = await page.evaluate(
            """() => {
            const out=[];
            document.querySelectorAll('table tr').forEach(tr=>{
                const a=tr.querySelector('a[href*=\"mod/vod/view.php\"]');
                if(!a) return;
                const cells=[...tr.querySelectorAll('td')].map(c=>c.innerText.trim());
            const pct=cells.find(c=>/^\\d{1,3}(\\.\\d+)?%$/.test(c));
            const val=pct?parseFloat(pct):0;
            out.push({title:a.innerText.trim(), id:(a.href.match(/id=(\\d+)/)||[])[1], pct: pct||'0%', val});
            });
            return out;
        }"""
        )
        total = len(rows)
        done = sum(1 for r in rows if float(r.get("val") or 0) >= 99.99)
        pending = [r for r in rows if float(r.get("val") or 0) < 99.99]
        print(f"course {cid}: total_vod={total} at_100%={done} pending={len(pending)}")
        for r in pending:
            print(f"  {r['pct']} {r['title']} id={r['id']}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
