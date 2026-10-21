import asyncio
import os
import re

LMS = "https://lms.kmooc.kr"
VIEW = f"{LMS}/mod/vod/view.php?id=2326465"


async def main():
    from playwright.async_api import async_playwright

    pw = os.environ.get("KMOOC_PW", "huh_HI12Huh*")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        page = await (await browser.new_context()).new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await page.goto("https://www.kmooc.kr/login", wait_until="networkidle")
        await page.fill("#userid", "ying11@ewha.ac.kr")
        await page.fill("#password", pw)
        await page.locator("button.button_login.btn_submit").click()
        await page.wait_for_timeout(4000)

        await page.goto(VIEW, wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(2000)
        # find viewer link or iframe
        hrefs = await page.evaluate(
            """() => [...document.querySelectorAll('a,iframe')].map(el => ({
            tag:el.tagName, h:el.href||el.src||'', t:(el.innerText||'').trim().slice(0,50)
        })).filter(x => /viewer|play|vod|video|m3u8|content/i.test(x.h+x.t))"""
        )
        print("media hrefs", hrefs)

        for label in ["학습하기", "강의 보기", "동영상 보기", "재생", "Play", "시청"]:
            loc = page.locator("a,button").filter(has_text=re.compile(label))
            if await loc.count():
                print("click", label)
                async with page.expect_navigation(timeout=30000):
                    await loc.first.click()
                break
        await page.wait_for_timeout(4000)
        print("after click url", page.url)
        print("frames", [f.url for f in page.frames])

        pg = page
        for fr in page.frames:
            if "viewer" in fr.url or await fr.locator("video").count():
                pg = fr if fr != page.main_frame else page
                break

        target = pg if hasattr(pg, "locator") else page
        if await target.locator("video").count():
            st = await target.evaluate(
                """async () => {
                const v=document.querySelector('video');
                v.muted=true; v.playbackRate=2;
                await v.play().catch(()=>{});
                return {dur:v.duration, cur:v.currentTime};
            }"""
            )
            print("PLAY", st)
            for _ in range(4):
                await asyncio.sleep(10)
                print(
                    await target.evaluate(
                        "() => { const v=document.querySelector('video'); return v?{cur:v.currentTime,dur:v.duration}:null; }"
                    )
                )
        else:
            html_snip = (await page.content())[:3000]
            if "viewer.php" in html_snip:
                m = re.search(r"viewer\.php\?id=\d+", html_snip)
                print("viewer in html", m.group(0) if m else None)
            # direct viewer with same id
            vid = VIEW.split("id=")[-1]
            vurl = f"{LMS}/mod/vod/viewer.php?id={vid}"
            print("try", vurl)
            await page.goto(vurl, wait_until="networkidle", timeout=90000)
            await page.wait_for_timeout(5000)
            print("viewer url", page.url, "video", await page.locator("video").count())
            for fr in page.frames:
                if await fr.locator("video").count():
                    st = await fr.evaluate(
                        """async () => {
                        const v=document.querySelector('video');
                        v.muted=true; v.playbackRate=2;
                        await v.play().catch(()=>{});
                        return {dur:v.duration, cur:v.currentTime};
                    }"""
                    )
                    print("viewer PLAY", st)
                    await asyncio.sleep(15)
                    print(
                        await fr.evaluate(
                            "() => { const v=document.querySelector('video'); return v?{cur:v.currentTime,dur:v.duration}:null; }"
                        )
                    )
                    break

        await browser.close()


asyncio.run(main())
