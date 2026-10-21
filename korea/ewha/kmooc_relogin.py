import asyncio
import json
from pathlib import Path


async def main():
    from playwright.async_api import async_playwright

    uid = "ying11@ewha.ac.kr"
    pw = "huh_Hl12Huh*"
    print("pw repr", repr(pw), "len", len(pw))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        dialogs = []
        posts = []
        apis = []

        def on_dialog(d):
            dialogs.append(d.message)
            asyncio.ensure_future(d.accept())

        page.on("dialog", on_dialog)
        page.on(
            "request",
            lambda r: posts.append(r.post_data) if "loginProcess" in r.url else None,
        )

        async def on_resp(resp):
            if "loginProcess" in resp.url:
                apis.append(await resp.text())

        page.on("response", on_resp)
        await page.goto("https://www.kmooc.kr/login", wait_until="networkidle")
        await page.fill("#userid", uid)
        await page.fill("#password", pw)
        got = await page.evaluate(
            "() => ({u: document.querySelector('#userid').value, p: document.querySelector('#password').value})"
        )
        print("fields", got)
        await page.locator("button.button_login.btn_submit").click()
        await page.wait_for_timeout(4500)
        body = await page.inner_text("body")
        ok = "로그아웃" in body or "/login" not in page.url
        print("url", page.url)
        print("ok", ok)
        print("dialogs", dialogs)
        print("posts", posts)
        print("apis", apis)
        if ok:
            Path(".kmooc.storage.json").write_text(
                json.dumps({"cookies": await page.context.cookies()}, ensure_ascii=False),
                encoding="utf-8",
            )
            print("SAVED")
            await page.goto("https://www.kmooc.kr/view/mycampus/myCourse", timeout=90000)
            await page.wait_for_timeout(2500)
            print((await page.inner_text("body"))[:700].replace("\n", " | "))
        await browser.close()


asyncio.run(main())
