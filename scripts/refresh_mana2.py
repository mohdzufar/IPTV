#!/usr/bin/env python3
"""
Mana-Mana Token Refresher (for mana2.my) - Enhanced Version
Uses Playwright with anti-detection measures to extract fresh .m3u8 URLs.
"""

import asyncio
import sys
import io
from pathlib import Path
from playwright.async_api import async_playwright

# Force UTF-8 output on Windows to avoid encoding errors
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# -------------------------------------------------------------------
# CONFIGURATION
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

OUTPUT_BASE_DIR = Path("Channels/Mana-Mana")
HEADLESS = True
WAIT_TIMEOUT_MS = 15000  # Increased timeout to 15 seconds

# -------------------------------------------------------------------
# CORE LOGIC
# -------------------------------------------------------------------

async def extract_m3u8_url(page_url):
    async with async_playwright() as p:
        # Launch browser with anti-detection arguments
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=[
                '--disable-blink-features=AutomationControlled', # Hide automation
                '--disable-features=IsolateOrigins,site-per-process',
                '--no-sandbox',
                '--disable-setuid-sandbox',
            ]
        )
        
        # Create a context with a realistic viewport and disabled Service Workers
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            service_workers='block' # Prevents interference from Service Workers
        )
        page = await context.new_page()

        # Initialize with a null promise for the request we're waiting for
        m3u8_promise = asyncio.get_running_loop().create_future()

        def handle_request(request):
            """Callback function to check each request."""
            if not m3u8_promise.done():
                url = request.url
                # Look for any m3u8 file, not just 'monu3u8'
                if ".m3u8" in url:
                    m3u8_promise.set_result(url)

        # Listen for all requests
        page.on('request', handle_request)

        print(f"  Navigating to {page_url}...")
        await page.goto(page_url, wait_until="networkidle")
        
        print(f"  Waiting for player to load and request m3u8...")
        try:
            # Wait for the promise to be resolved
            captured_url = await asyncio.wait_for(m3u8_promise, timeout=WAIT_TIMEOUT_MS/1000)
            print(f"  Captured: {captured_url[:80]}...")
        except asyncio.TimeoutError:
            captured_url = None
            print(f"  Timeout: No m3u8 request captured after {WAIT_TIMEOUT_MS/1000} seconds.")
        finally:
            await browser.close()
            
        return captured_url


def update_playlist_file(channel_name, m3u8_url):
    """Writes the fresh .m3u8 URL to the channel's playlist file."""
    safe_name = channel_name.replace(" ", "_").replace("+", "Plus")
    file_path = OUTPUT_BASE_DIR / safe_name / f"{safe_name}.m3u8"
    file_path.parent.mkdir(parents=True, exist_ok=True)

    content = f"#EXTM3U\n{m3u8_url}\n"
    file_path.write_text(content, encoding='utf-8')
    print(f"  Updated {file_path}")


async def main():
    print("=" * 50)
    print("Mana-Mana Token Refresher (Enhanced)")
    print("=" * 50)

    for name, url in CHANNELS.items():
        print(f"\n[Refreshing] {name}...")
        try:
            fresh_url = await extract_m3u8_url(url)
            if fresh_url:
                update_playlist_file(name, fresh_url)
            else:
                print(f"  ERROR: No .m3u8 URL captured for {name}")
        except Exception as e:
            print(f"  ERROR: {e}")

    print("\nRefresh complete.")


if __name__ == "__main__":
    asyncio.run(main())
