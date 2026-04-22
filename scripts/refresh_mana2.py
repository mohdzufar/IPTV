#!/usr/bin/env python3
"""
Mana-Mana Token Refresher - Advanced Stealth Edition
Uses playwright-stealth and advanced arguments to bypass bot detection.
"""

import asyncio
import sys
import io
from pathlib import Path
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Force UTF-8 output on Windows
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
WAIT_TIMEOUT_MS = 15000  # 15 seconds

# -------------------------------------------------------------------
# CORE LOGIC
# -------------------------------------------------------------------

async def extract_m3u8_url(page_url):
    async with Stealth().use_async(async_playwright()) as p:
        # Launch browser with a powerful set of anti-detection arguments
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor',
                '--disable-ipc-flooding-protection',
                '--disable-renderer-backgrounding',
                '--disable-backgrounding-occluded-windows',
                '--disable-field-trial-config',
                '--disable-back-forward-cache',
                '--disable-component-extensions-with-background-pages',
                '--disable-client-side-phishing-detection',
                '--disable-default-apps',
                '--disable-extensions',
                '--disable-hang-monitor',
                '--disable-popup-blocking',
                '--disable-prompt-on-repost',
                '--disable-sync',
                '--force-color-profile=srgb',
                '--metrics-recording-only',
                '--no-first-run',
                '--safebrowsing-disable-auto-update',
                '--enable-automation',
            ]
        )
        
        # Create a context that mimics a real user
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='en-MY',
            timezone_id='Asia/Kuala_Lumpur',
            permissions=['geolocation'],
            service_workers='block'
        )
        page = await context.new_page()

        m3u8_promise = asyncio.get_running_loop().create_future()

        def handle_request(request):
            if not m3u8_promise.done() and ".m3u8" in request.url:
                m3u8_promise.set_result(request.url)

        page.on('request', handle_request)

        print(f"  Navigating to {page_url}...")
        await page.goto(page_url, wait_until="networkidle")
        
        # Try to click any potential "Play" button if one exists
        try:
            play_button = page.locator('button:has-text("Play"), [aria-label="Play"], .play-button').first
            await play_button.click(timeout=3000)
            print("  Clicked Play button...")
        except:
            pass

        print(f"  Waiting for player to load and request m3u8...")
        try:
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
    print("Mana-Mana Token Refresher (Advanced Stealth)")
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
