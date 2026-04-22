#!/usr/bin/env python3
"""
Mana-Mana Token Refresher - Advanced Stealth Edition
Writes minimal M3U with #EXTINF:1,ChannelName and fresh URL.
Outputs to Channels/Mana-mana/ (exact GitHub folder name).
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

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
OUTPUT_BASE_DIR = REPO_ROOT / "Channels" / "Mana-mana"   # Exact folder name in GitHub

# Channel key -> (page_url, display_name)
CHANNELS = {
    "Al-Hijrah": ("https://www.mana2.my/channel/live/tv-alhijrah", "Al-Hijrah"),
    "Enjoy TV": ("https://www.mana2.my/channel/live/tv5", "Enjoy TV"),
    "Borneo TV": ("https://www.mana2.my/channel/live/borneo-tv", "Borneo TV"),
    "Selangor TV": ("https://www.mana2.my/channel/live/selangor-tv", "Selangor TV"),
    "Suke TV": ("https://www.mana2.my/channel/live/suke-tv", "Suke TV"),
    "TVS": ("https://www.mana2.my/epg/play/1720414", "TVS"),
    "Sukan+": ("https://www.mana2.my/channel/live/sukan-rtm", "Sukan+"),
    "Bernama": ("https://www.mana2.my/channel/live/bernama", "Bernama"),
}

HEADLESS = True
WAIT_TIMEOUT_MS = 15000

# -------------------------------------------------------------------
# CORE LOGIC
# -------------------------------------------------------------------

async def extract_m3u8_url(page_url):
    async with Stealth().use_async(async_playwright()) as p:
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
            if not m3u8_promise.done():
                url = request.url
                if ".m3u8" in url and "ping.gif" not in url:
                    m3u8_promise.set_result(url)

        page.on('request', handle_request)

        print(f"  Navigating to {page_url}...")
        await page.goto(page_url, wait_until="networkidle")
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


def update_playlist_file(channel_key, display_name, m3u8_url):
    """Writes minimal M3U with #EXTINF and URL."""
    safe_name = channel_key.replace(" ", "_").replace("+", "Plus")
    file_path = OUTPUT_BASE_DIR / safe_name / f"{safe_name}.m3u8"
    file_path.parent.mkdir(parents=True, exist_ok=True)

    content = f"#EXTM3U\n#EXTINF:1,{display_name}\n{m3u8_url}\n"
    file_path.write_text(content, encoding='utf-8')
    print(f"  Updated {file_path}")
    print(f"    #EXTINF:1,{display_name}")
    print(f"    URL: {m3u8_url[:80]}...")


async def main():
    print("=" * 50)
    print("Mana-Mana Token Refresher (Minimal EXTINF)")
    print("=" * 50)

    for channel_key, (url, display_name) in CHANNELS.items():
        print(f"\n[Refreshing] {channel_key}...")
        try:
            fresh_url = await extract_m3u8_url(url)
            if fresh_url:
                update_playlist_file(channel_key, display_name, fresh_url)
            else:
                print(f"  ERROR: No .m3u8 URL captured for {channel_key}")
        except Exception as e:
            print(f"  ERROR: {e}")

    print("\nRefresh complete.")


if __name__ == "__main__":
    asyncio.run(main())
