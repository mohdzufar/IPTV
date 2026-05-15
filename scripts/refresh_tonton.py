#!/usr/bin/env python3
"""
refresh_tonton.py – robust token refresh for Tonton channels using Playwright + stealth.
Replaces the old brittle selector-based approach with network interception.

Usage:
    python refresh_tonton.py              # refresh all channels
    python refresh_tonton.py --setup      # manual login to save state
    python refresh_tonton.py --debug      # run with visible browser
"""

import asyncio
import sys
import os
import logging
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

# ==================== CONFIG ====================
# Adjust these paths to match your setup (the ones used by your self-hosted runner)
BASE_DIR = os.environ.get(
    "IPTV_BASE_DIR",
    r"C:\Users\zufar\Downloads\IPTV_Project\GitHub_Runner_IPTV"
)
USER_PROFILE = os.environ.get("TONTON_USER_PROFILE", os.path.join(BASE_DIR, "auth"))
STATE_FILE = os.path.join(USER_PROFILE, "tonton-state.json")
CHANNELS_DIR = os.environ.get("CHANNELS_DIR", os.path.join(BASE_DIR, "Channels"))

# Login URL (if session is dead)
LOGIN_URL = "https://www.tonton.com.my/login"
WATCH_BASE = "https://watch.tonton.com.my/live"

# Channel list (same as in your original script)
TONTON_CHANNELS = {
    "TV3":           "tv3",
    "Didik TV":      "ntv7",
    "TV9":           "tv9",
    "Drama Sangat":  "ds"
}

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("refresh_tonton")


def state_exists():
    return os.path.isfile(STATE_FILE)


# ==================== MANUAL LOGIN SETUP ====================
async def manual_login():
    """Launch browser, let the user log in manually, then save the browser state."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-MY",
            timezone_id="Asia/Kuala_Lumpur"
        )
        page = await context.new_page()
        await stealth_async(page)

        logger.info("Opening Tonton login page. Please log in manually now.")
        await page.goto(LOGIN_URL)
        logger.info("Waiting for login to complete (you’ll be redirected to the home page)...")

        # Wait for redirect to home (or a known logged-in element)
        try:
            # Wait until URL no longer contains "login"
            await page.wait_for_function("() => !window.location.href.includes('login')", timeout=0)
        except:
            pass

        # Double-check we see a profile icon
        try:
            await page.wait_for_selector("button[aria-label='Profile']", timeout=10000)
            logger.info("Login successful.")
        except:
            logger.error("Could not confirm login. Please try again.")
            await browser.close()
            return

        os.makedirs(USER_PROFILE, exist_ok=True)
        await context.storage_state(path=STATE_FILE)
        logger.info(f"Login state saved to {STATE_FILE}")
        await browser.close()


# ==================== STREAM CAPTURE ====================
async def capture_stream(channel_name, channel_id, debug=False):
    """
    Load the channel page, wait for any .m3u8 network response,
    and return the URL. Handles autoplay and manual play buttons.
    """
    if not state_exists():
        logger.error("No login state found. Run with --setup first.")
        return None

    async with async_playwright() as p:
        launch_options = {"headless": not debug}
        browser = await p.chromium.launch(**launch_options)

        context = await browser.new_context(
            storage_state=STATE_FILE,
            viewport={"width": 1920, "height": 1080},
            locale="en-MY",
            timezone_id="Asia/Kuala_Lumpur",
            bypass_csp=True
        )
        page = await context.new_page()
        await stealth_async(page)

        captured_url = None

        # Intercept every response – look for .m3u8
        async def on_response(response):
            nonlocal captured_url
            if not captured_url and ".m3u8" in response.url:
                logger.info(f"[{channel_name}] .m3u8 found: {response.url}")
                captured_url = response.url

        page.on("response", on_response)

        url = f"{WATCH_BASE}/{channel_id}"
        logger.info(f"[{channel_name}] Loading {url}")
        await page.goto(url, wait_until="domcontentloaded")

        # Check for session expiry (redirect to login)
        if "login" in page.url:
            logger.error(f"[{channel_name}] Session expired! Re-run with --setup.")
            await browser.close()
            return None

        # Try to click a play button if one exists (otherwise rely on autoplay)
        play_selectors = [
            "button[aria-label='Play']",
            ".play-button",
            "[data-testid='play-button']",
            "button:has-text('Play')"
        ]
        for sel in play_selectors:
            try:
                btn = await page.wait_for_selector(sel, timeout=3000)
                if btn:
                    await btn.click()
                    logger.info(f"[{channel_name}] Clicked play button.")
                    break
            except:
                continue

        # Wait for the .m3u8 to appear (timeout after 20s)
        try:
            await page.wait_for_function(
                "() => window.__captured_m3u8 !== undefined || document.querySelector('video')?.src?.includes('.m3u8')",
                timeout=20000
            )
        except:
            pass

        # Fallback: check video element src
        if not captured_url:
            try:
                video_src = await page.evaluate("document.querySelector('video')?.src")
                if video_src and ".m3u8" in video_src:
                    captured_url = video_src
                    logger.info(f"[{channel_name}] Got M3U8 from video src: {captured_url}")
            except:
                pass

        if not captured_url:
            logger.error(f"[{channel_name}] Failed to capture .m3u8 URL.")
            try:
                screenshot_path = f"tonton_error_{channel_name}.png"
                await page.screenshot(path=screenshot_path)
                logger.info(f"Screenshot saved: {screenshot_path}")
            except:
                pass

        await browser.close()
        return captured_url


def update_channel_wrapper(channel_name, m3u8_url):
    """Write the stream URL to the channel wrapper file in Channels/"""
    if not m3u8_url:
        return False
    channel_file = os.path.join(CHANNELS_DIR, f"{channel_name}.m3u8")
    content = f"#EXTM3U\n#EXTINF:-1,{channel_name}\n{m3u8_url}\n"
    with open(channel_file, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"[{channel_name}] Channel file updated: {channel_file}")
    return True


async def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--setup":
        await manual_login()
        return

    debug = "--debug" in sys.argv

    print("=" * 60)
    print("Refreshing TONTON channel tokens...")
    print(f"Using login state: {STATE_FILE}")
    print("=" * 60)

    success = 0
    fail = 0

    for idx, (name, channel_id) in enumerate(TONTON_CHANNELS.items(), 1):
        print(f"\n[{idx}/{len(TONTON_CHANNELS)}] {name}")
        m3u8_url = await capture_stream(name, channel_id, debug=debug)
        if m3u8_url:
            if update_channel_wrapper(name, m3u8_url):
                success += 1
        else:
            fail += 1

    print("\n" + "=" * 60)
    print(f"TONTON refresh finished. Success: {success} | Failed: {fail}")
    print("=" * 60)

    if fail:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
