#!/usr/bin/env python3
"""
refresh_tonton.py – robust token refresh for Tonton channels using Playwright + stealth.

Usage:
    python refresh_tonton.py <channel_name> <channel_id>  # used by GitHub workflow
    python refresh_tonton.py --setup                       # re-run manual login
    python refresh_tonton.py --debug <channel_name> <channel_id>  # visible browser
"""

import asyncio
import sys
import os
import json
import logging
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

# ---------- CONFIG ----------
# Adjust these paths to match your setup (or use environment variables)
USER_PROFILE = os.environ.get("TONTON_USER_PROFILE", r"C:\Users\zufar\Documents\GitHub\IPTV\.auth")
STATE_FILE = os.path.join(USER_PROFILE, "tonton-state.json")
CHANNELS_DIR = os.environ.get("CHANNELS_DIR", r"C:\Users\zufar\Documents\GitHub\IPTV\Channels")
MAIN_PLAYLIST = os.environ.get("MAIN_PLAYLIST", r"C:\Users\zufar\Documents\GitHub\IPTV\Main.m3u8")

# If you have Tonton credentials saved (optional), set these env vars
TONTON_EMAIL = os.environ.get("TONTON_EMAIL")
TONTON_PASSWORD = os.environ.get("TONTON_PASSWORD")

LOGIN_URL = "https://tonton.com.my/login"
BASE_URL = "https://tonton.com.my"

# ---------- LOGGING ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("refresh_tonton")

# ---------- HELPER: SAVE/LOAD STATE ----------
def state_exists():
    return os.path.isfile(STATE_FILE)

# ---------- MANUAL LOGIN (if called with --setup) ----------
async def manual_login():
    """Launch browser, login manually, save state."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-MY",
            timezone_id="Asia/Kuala_Lumpur"
        )
        page = await context.new_page()
        await stealth_async(page)

        logger.info("Opening Tonton login page. Please log in manually.")
        await page.goto(LOGIN_URL)
        logger.info("Waiting for you to complete login...")
        # Wait until the user is redirected to the home page or a known logged-in element
        try:
            await page.wait_for_url("**/tonton.com.my/", timeout=0)  # wait indefinitely
        except:
            pass

        # Ensure the user is really logged in (e.g., check for account menu)
        try:
            await page.wait_for_selector("button[aria-label='Profile']", timeout=10000)
            logger.info("Login detected.")
        except:
            logger.error("Could not confirm login. Try again.")
            await browser.close()
            return

        # Save state
        os.makedirs(USER_PROFILE, exist_ok=True)
        await context.storage_state(path=STATE_FILE)
        logger.info(f"Login state saved to {STATE_FILE}")
        await browser.close()

# ---------- MAIN REFRESH LOGIC ----------
async def fetch_stream_url(channel_id, debug=False):
    """
    Load Tonton channel page using saved state, click play, capture .m3u8 URL.
    Returns the URL string or None.
    """
    if not state_exists():
        logger.error("No saved login state found. Run setup_tonton_login.py first.")
        return None

    async with async_playwright() as p:
        launch_args = {"headless": not debug}
        browser = await p.chromium.launch(**launch_args)

        # Create context with stored state
        context = await browser.new_context(
            storage_state=STATE_FILE,
            viewport={"width": 1920, "height": 1080},
            locale="en-MY",
            timezone_id="Asia/Kuala_Lumpur",
            # Bypass automation flags (optional extra)
            bypass_csp=True,
        )
        page = await context.new_page()
        await stealth_async(page)

        # Container for the captured M3U8 URL
        captured_m3u8 = None

        async def intercept_response(response):
            nonlocal captured_m3u8
            # Look for any .m3u8 URL (master or variant)
            if not captured_m3u8 and ".m3u8" in response.url:
                logger.info(f"Captured .m3u8: {response.url}")
                captured_m3u8 = response.url

        page.on("response", intercept_response)

        channel_url = f"{BASE_URL}/{channel_id}"
        logger.info(f"Navigating to {channel_url}")
        await page.goto(channel_url, wait_until="domcontentloaded")

        # Check for login wall – if we're redirected to login page, session is dead
        if "login" in page.url:
            logger.error("Session expired! Please re-run setup_tonton_login.py to re-authenticate.")
            await browser.close()
            return None

        # Wait for the player to be ready – common selectors to try
        play_button_selectors = [
            "button[aria-label='Play']",
            ".play-button",
            "[data-testid='play-button']",
            "button:has-text('Play')"
        ]
        clicked = False
        for sel in play_button_selectors:
            try:
                await page.wait_for_selector(sel, timeout=5000)
                await page.click(sel)
                logger.info(f"Clicked play button (selector: {sel})")
                clicked = True
                break
            except:
                continue

        if not clicked:
            logger.warning("No play button found, maybe autoplay is active. Waiting for video...")

        # Now wait until a .m3u8 URL appears, or timeout after 25s
        try:
            # wait_for_function checks a JavaScript expression periodically
            await page.wait_for_function(
                "() => window.__captured_m3u8 !== undefined || document.querySelector('video')?.src?.includes('.m3u8')",
                timeout=25000
            )
        except Exception as e:
            logger.warning(f"Wait for video timed out: {e}")

        # If the intercept didn't catch it, try extracting from video element
        if not captured_m3u8:
            video_src = await page.evaluate("""() => {
                const video = document.querySelector('video');
                return video ? video.src : null;
            }""")
            if video_src and ".m3u8" in video_src:
                captured_m3u8 = video_src
                logger.info(f"Got M3U8 from video src: {captured_m3u8}")

        if not captured_m3u8:
            logger.error("Failed to capture any .m3u8 URL.")
            # Take screenshot for debugging
            try:
                await page.screenshot(path="tonton_error.png")
                logger.info("Screenshot saved as tonton_error.png")
            except:
                pass

        await browser.close()
        return captured_m3u8


def update_channel_file(channel_name, m3u8_url):
    """Write the stream URL to a channel wrapper (.m3u8 file) in Channels/"""
    if not m3u8_url:
        return
    channel_file = os.path.join(CHANNELS_DIR, f"{channel_name}.m3u8")
    content = f"#EXTM3U\n#EXTINF:-1,{channel_name}\n{m3u8_url}\n"
    with open(channel_file, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"Updated channel file: {channel_file}")

def update_main_playlist(channel_name, m3u8_url):
    """
    If you prefer to keep Main.m3u8 directly updated, this function replaces
    the line containing the channel wrapper reference with the fresh URL.
    This is optional – the workflow already flattens channels into Main.m3u8.
    """
    # Not strictly necessary because the workflow's flatten step uses channel wrappers.
    pass


async def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--setup":
        await manual_login()
        return

    debug = False
    args = sys.argv[1:]
    if "--debug" in args:
        debug = True
        args.remove("--debug")

    if len(args) != 2:
        print("Usage: refresh_tonton.py [--debug] <channel_name> <channel_id>")
        sys.exit(1)

    channel_name, channel_id = args[0], args[1]
    logger.info(f"Refreshing token for {channel_name} (ID: {channel_id})")
    m3u8_url = await fetch_stream_url(channel_id, debug=debug)

    if m3u8_url:
        update_channel_file(channel_name, m3u8_url)
        logger.info(f"Successfully updated {channel_name} with new URL.")
    else:
        logger.error(f"Could not fetch stream URL for {channel_name}.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
