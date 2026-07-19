import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://app.outlier.bet/login")
        await page.wait_for_load_state("networkidle")
        await page.screenshot(path="scratch/dump_login.png")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
