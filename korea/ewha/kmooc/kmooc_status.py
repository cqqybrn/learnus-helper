import asyncio
import os

async def main():
    from playwright.async_api import async_playwright

    pw = os.environ["KMOOC_PW"]
    uid = os.environ.get("KMOOC_ID", "ying11@ewha.ac.kr")
    cid = os.environ.get("KMOOC_COURSE", "20514")
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


asyncio.run(main())
