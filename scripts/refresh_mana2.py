#!/usr/bin/env python3
"""
Mana-Mana Token Refresher (for mana2.my)
Uses Playwright to visit channel pages and extract fresh .m3u8 URLs.
"""

import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

# -------------------------------------------------------------------
# CONFIGURATION – VERIFIED URLs
# -------------------------------------------------------------------

CHANNELS = {
    "Al-Hijrah": "https://www.mana2.my/channel/live/tv-alhijrah",
    "Enjoy TV": "https://www.mana2.my/channel/live/tv5",
    "Borneo TV": "https://www.mana2.my/channel/live/borneo-tv",
    "Selangor TV": "https://www.mana2.my/channel/live/selangor-tv",
    "Suke TV": "https://www.mana2.my/channel/live/suke-tv",
    "TVS": "https://www.mana2.my/epg/play/1720414",
    "Sukan+": "https://www.mana2.my/channel/live/sukan-rtm",
    "Bernama": "https://www.mana2.my/channel/live/bernama",
}

# Where to save the updated playlist files
OUTPUT_BASE_DIR = Path("Channels/Mana-Mana")

# Set to False to see the browser window (useful for debugging)
HEADLESS = True

# -------------------------------------------------------------------
# CORE LOGIC
# -------------------------------------------------------------------

async def extract_m3u8_url(page_url):
    """
    Launches a headless browser, navigates to page_url,
    intercepts any .m3u8 request, and returns the first matching URL.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'
        )
        page = await context.new_page()

        captured_url = None

        async def handle_route(route, request):
            nonlocal captured_url
            if "monu3u8" in request.url and captured_url is None:
                captured_url = request.url
                print(f"  ✅ Captured: {captured_url[:80]}...")
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", handle_route)

        print(f"  Navigating to {page_url}...")
        await page.goto(page_url, wait_until="networkidle")
        await page.wait_for_timeout(5000)

        await browser.close()
        return captured_url


def update_playlist_file(channel_name, m3u8_url):
    """Writes the fresh .m3u8 URL to the channel's playlist file."""
    safe_name = channel_name.replace(" ", "_").replace("+", "Plus")
    file_path = OUTPUT_BASE_DIR / safe_name / f"{safe_name}.m3u8"
    file_path.parent.mkdir(parents=True, exist_ok=True)

    content = f"#EXTM3U\n{m3u8_url}\n"
    file_path.write_text(content, encoding='utf-8')
    print(f"  📁 Updated {file_path}")


async def main():
    print("=" * 50)
    print("Mana-Mana Token Refresher")
    print("=" * 50)

    for name, url in CHANNELS.items():
        print(f"\n🔄 Refreshing {name}...")
        try:
            fresh_url = await extract_m3u8_url(url)
            if fresh_url:
                update_playlist_file(name, fresh_url)
            else:
                print(f"  ❌ No .m3u8 URL captured for {name}")
        except Exception as e:
            print(f"  ❌ Failed: {e}")

    print("\n✅ Refresh complete.")


if __name__ == "__main__":
    asyncio.run(main())
