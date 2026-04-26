"""
refresh_mana2.py – Refresh Mana‑mana tokens directly in Main.m3u8 and subfolders.
Uses the original async Playwright + Stealth approach (proven working).
"""

import asyncio
import os
import re
import sys
from pathlib import Path
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Fix Windows console encoding (if needed)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
OUTPUT_BASE_DIR = REPO_ROOT / "Channels" / "Mana-mana"

# Playlist file that will be updated
MAIN_PLAYLIST = REPO_ROOT / "Main.m3u8"

# Channel mapping: key -> (page_url, display_name)
CHANNELS = {
    "Al-Hijrah": ("https://www.mana2.my/channel/live/tv-alhijrah", "Al-Hijrah"),
    "Enjoy TV":  ("https://www.mana2.my/channel/live/tv5", "Enjoy TV"),
    "Borneo TV": ("https://www.mana2.my/channel/live/borneo-tv", "Borneo TV"),
    "Selangor TV": ("https://www.mana2.my/channel/live/selangor-tv", "Selangor TV"),
    "Suke TV":   ("https://www.mana2.my/channel/live/suke-tv", "Suke TV"),
    "TVS":       ("https://www.mana2.my/epg/play/1720414", "TVS"),
    "Sukan+":    ("https://www.mana2.my/channel/live/sukan-rtm", "Sukan+"),
    "Bernama":   ("https://www.mana2.my/channel/live/bernama", "Bernama"),
}

HEADLESS = True
WAIT_TIMEOUT_MS = 15000
USE_EXTVLCOPT = True
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)
REFERER = "https://mana2.my/"

# -------------------------------------------------------------------
# TOKEN REFRESH (original async logic)
# -------------------------------------------------------------------
async def extract_m3u8_url(page_url):
    """Navigate to the Mana‑mana channel page and capture the fresh .m3u8 URL."""
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
            user_agent=USER_AGENT,
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
            play_button = page.locator(
                'button:has-text("Play"), [aria-label="Play"], .play-button'
            ).first
            await play_button.click(timeout=3000)
            print("  Clicked Play button...")
        except:
            pass

        print(f"  Waiting for player to load and request m3u8...")
        try:
            captured_url = await asyncio.wait_for(
                m3u8_promise, timeout=WAIT_TIMEOUT_MS / 1000
            )
            print(f"  Captured: {captured_url[:80]}...")
        except asyncio.TimeoutError:
            captured_url = None
            print(f"  Timeout: No m3u8 request captured after {WAIT_TIMEOUT_MS / 1000} seconds.")
        finally:
            await browser.close()
        return captured_url


def update_subfolder_file(channel_key, display_name, m3u8_url):
    """Write/overwrite the individual .m3u8 file for a Mana‑mana channel."""
    safe_name = channel_key.replace(" ", "_").replace("+", "Plus")
    folder_path = OUTPUT_BASE_DIR / safe_name
    folder_path.mkdir(parents=True, exist_ok=True)
    file_path = folder_path / f"{safe_name}.m3u8"

    lines = ["#EXTM3U", f"#EXTINF:1,{display_name}"]
    if USE_EXTVLCOPT:
        lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        lines.append(f"#EXTVLCOPT:http-referrer={REFERER}")
    lines.append(m3u8_url)

    content = "\n".join(lines) + "\n"
    file_path.write_text(content, encoding='utf-8')
    print(f"  Updated {file_path}")


def extract_channel_key_from_wrapper_url(wrapper_url):
    """
    Given a wrapper URL like
    .../Channels/Mana-mana/Al-Hijrah/Al-Hijrah.m3u8
    return 'Al-Hijrah'.
    """
    # Split by '/Channels/Mana-mana/' to get the part after
    marker = "/Channels/Mana-mana/"
    idx = wrapper_url.find(marker)
    if idx != -1:
        after = wrapper_url[idx + len(marker):]
        parts = after.split('/')
        if parts:
            return parts[0]  # first folder name, e.g. 'Al-Hijrah'
    # Fallback: use last folder before filename
    parts = wrapper_url.rstrip('/').split('/')
    if len(parts) >= 2:
        return parts[-2]  # folder name
    return None


# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------
async def main_async():
    if not MAIN_PLAYLIST.exists():
        print(f"Main.m3u8 not found at {MAIN_PLAYLIST}")
        return

    # Read Main.m3u8
    with open(MAIN_PLAYLIST, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    updated = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        # Check if it's a Mana‑mana wrapper URL
        if stripped.startswith('http') and '/Channels/Mana-mana/' in stripped:
            print(f"Refreshing Mana URL: {stripped}")
            channel_key = extract_channel_key_from_wrapper_url(stripped)
            if channel_key is None or channel_key not in CHANNELS:
                print(f"  [!] Could not determine channel key, keeping original")
                new_lines.append(line)
                continue

            page_url, display_name = CHANNELS[channel_key]
            print(f"  Channel: {channel_key} -> {page_url}")
            try:
                fresh_url = await extract_m3u8_url(page_url)
                if fresh_url:
                    print(f"  [OK] New URL: {fresh_url[:80]}...")
                    # Replace wrapper URL in Main.m3u8
                    new_lines.append(fresh_url + '\n')
                    # Update the individual subfolder file
                    update_subfolder_file(channel_key, display_name, fresh_url)
                    updated = True
                else:
                    print(f"  [!] Failed to capture fresh URL, keeping original wrapper")
                    new_lines.append(line)
            except Exception as e:
                print(f"  [!] Error refreshing {channel_key}: {e}")
                new_lines.append(line)
        else:
            new_lines.append(line)

    if updated:
        with open(MAIN_PLAYLIST, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(new_lines)
        print(f"Main.m3u8 updated with fresh Mana tokens.")
    else:
        print("No Mana tokens refreshed (or no updates).")

if __name__ == "__main__":
    asyncio.run(main_async())
