#!/usr/bin/env python3
import io
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

STATE_FILE = Path(
    r"C:\Users\zufar\Downloads\IPTV_Project\GitHub_Runner_IPTV\actions-runner\auth\tonton-state.json"
)
REPO_ROOT = Path(__file__).resolve().parents[1]
TONTON_ROOT = REPO_ROOT / "Channels" / "TONTON"

REFERER = "https://watch.tonton.com.my/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

TOKEN_WAIT_SECONDS = 25

CHANNELS = [
    {
        "display_name": "TV3",
        "folder_name": "TV3",
        "file_name": "TV3.m3u8",
        "page_url": "https://watch.tonton.com.my/live/tv3",
    },
    {
        "display_name": "Didik TV",
        "folder_name": "DidikTV",
        "file_name": "DidikTV.m3u8",
        "page_url": "https://watch.tonton.com.my/live/ntv7",
    },
    {
        "display_name": "TV9",
        "folder_name": "TV9",
        "file_name": "TV9.m3u8",
        "page_url": "https://watch.tonton.com.my/live/tv9",
    },
    {
        "display_name": "Drama Sangat",
        "folder_name": "Drama Sangat",
        "file_name": "Drama Sangat.m3u8",
        "page_url": "https://watch.tonton.com.my/live/ds",
    },
]

PLAY_SELECTORS = [
    'div[aria-label="Play"]',
    'button[aria-label="Play"]',
    ".jw-icon-display",
    ".jwplayer",
    "video",
]

LOGIN_HINT_SELECTORS = [
    'input[type="email"]',
    'input[type="password"]',
    'button[type="submit"]',
    'a[href*="login"]',
    'a[href*="signin"]',
]

OVERLAY_SELECTORS = [
    'button[aria-label="Close"]',
    ".mfp-close",
    '[data-dismiss="modal"]',
    ".cookie-consent button",
    ".modal button.close",
]


def log(message):
    print(message, flush=True)


def is_login_required(page):
    current_url = page.url.lower()
    if any(x in current_url for x in ("login", "signin", "sign-in", "auth")):
        return True

    for selector in LOGIN_HINT_SELECTORS:
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                return True
        except Exception:
            pass

    return False


def dismiss_overlays(page):
    for selector in OVERLAY_SELECTORS:
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                loc.first.click(timeout=1500)
                page.wait_for_timeout(500)
        except Exception:
            pass


def click_play(page):
    for selector in PLAY_SELECTORS:
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                loc.first.click(timeout=3000, force=True)
                page.wait_for_timeout(1200)
                return True
        except Exception:
            pass
    return False


def write_wrapper(channel, stream_url):
    folder = TONTON_ROOT / channel["folder_name"]
    folder.mkdir(parents=True, exist_ok=True)

    file_path = folder / channel["file_name"]
    content = (
        "#EXTM3U\n"
        f"#EXTVLCOPT:http-referrer={REFERER}\n"
        f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n"
        f"#EXTINF:1,{channel['display_name']}\n"
        f"{stream_url}\n"
    )

    file_path.write_text(content, encoding="utf-8", newline="\n")
    log(f"    Updated wrapper: {file_path}")


def capture_stream_url(browser, channel):
    token_url = None

    context = browser.new_context(
        storage_state=str(STATE_FILE),
        user_agent=USER_AGENT,
        extra_http_headers={"Referer": REFERER},
        locale="en-US",
        timezone_id="Asia/Kuala_Lumpur",
        viewport={"width": 1440, "height": 900},
    )

    page = context.new_page()

    def handle_request(request):
        nonlocal token_url
        if token_url:
            return

        url = request.url
        lower = url.lower()

        if ".m3u8" not in lower:
            return

        if any(bad in lower for bad in ("jwpltx.com", "ping.gif", "google", "doubleclick")):
            return

        token_url = url

    page.on("request", handle_request)

    try:
        try:
            page.goto(channel["page_url"], wait_until="domcontentloaded", timeout=45000)
        except PlaywrightTimeoutError:
            log("    Page navigation timed out, continuing to inspect page...")

        page.wait_for_timeout(3000)

        if is_login_required(page):
            return None, "login_required"

        dismiss_overlays(page)

        clicked = click_play(page)
        if not clicked:
            log("    Play button not found immediately, waiting for autoplay/request...")

        start = time.time()
        while time.time() - start < TOKEN_WAIT_SECONDS:
            if token_url:
                return token_url, "ok"

            dismiss_overlays(page)

            if int(time.time() - start) in (5, 10, 15):
                click_play(page)

            page.wait_for_timeout(1000)

        return None, "no_stream_captured"

    finally:
        context.close()


def main():
    if not STATE_FILE.exists():
        log("=" * 60)
        log("Tonton login state file not found.")
        log(f"Expected: {STATE_FILE}")
        log("Run setup_tonton_login.py once on the runner machine first.")
        log("=" * 60)
        return

    stealth = Stealth(
        navigator_user_agent_override=USER_AGENT,
        navigator_platform_override="Win32",
        navigator_languages_override=("en-US", "en"),
    )

    success_count = 0
    failed_count = 0
    login_invalid = False

    log("=" * 60)
    log("Refreshing TONTON channel tokens...")
    log(f"Using login state: {STATE_FILE}")
    log("=" * 60)

    with stealth.use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

        try:
            for index, channel in enumerate(CHANNELS, start=1):
                log(f"\n[{index}/{len(CHANNELS)}] {channel['display_name']}")
                log(f"    Page: {channel['page_url']}")

                token_url, status = capture_stream_url(browser, channel)

                if status == "login_required":
                    log("    Login session looks expired. Run setup_tonton_login.py again.")
                    login_invalid = True
                    failed_count += 1
                    break

                if token_url:
                    log(f"    Captured stream: {token_url[:150]}...")
                    write_wrapper(channel, token_url)
                    success_count += 1
                else:
                    log(f"    Failed to capture stream ({status}). Existing wrapper left unchanged.")
                    failed_count += 1

        finally:
            browser.close()

    log("\n" + "=" * 60)
    log(f"TONTON refresh finished. Success: {success_count} | Failed: {failed_count}")
    if login_invalid:
        log("Action needed: refresh Tonton login state.")
    log("=" * 60)


if __name__ == "__main__":
    main()
