#!/usr/bin/env python3
import io
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROFILE_DIR = Path(
    r"C:\Users\zufar\Downloads\IPTV_Project\GitHub_Runner_IPTV\actions-runner\auth\tonton-profile"
)
REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_FILE = REPO_ROOT / "Main.m3u8"
TONTON_ROOT = REPO_ROOT / "Channels" / "TONTON"
DEBUG_DIR = REPO_ROOT / "debug_tonton"

REFERER = "https://watch.tonton.com.my/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

INITIAL_SETTLE_SECONDS = 8
TOKEN_WAIT_SECONDS = 60
INTERACTION_INTERVAL = 3

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
    '[role="button"][aria-label="Play"]',
    'button[aria-label*="Play"]',
    '[role="button"][aria-label*="Play"]',
    ".jw-icon-display",
    ".jw-display-icon-container",
    ".jwplayer",
    ".vjs-big-play-button",
    '[data-testid="play-button"]',
    'button:has-text("Play")',
    'button:has-text("Watch")',
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

IGNORE_URL_KEYWORDS = (
    "jwpltx.com",
    "ping.gif",
    "doubleclick",
    "googleads",
    "googlesyndication",
    "adservice",
    "imasdk",
    "vast",
)

# Declaration lines that may appear between #EXTINF and the URL
# in Main.m3u8 (written there by validate_and_update.py from wrappers).
# replace_in_main_m3u8 must skip past these to find the URL line.
HINT_PREFIXES = (
    "#EXTVLCOPT",
    "#KODIPROP",
    "#EXTHTTP",
    "#EXTATTRB",
)


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


def is_ignored_stream(url):
    lower = url.lower()
    if ".m3u8" not in lower:
        return True
    return any(bad in lower for bad in IGNORE_URL_KEYWORDS)


def replace_in_main_m3u8(main_path, channel_name, new_url):
    with open(main_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    found = False

    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF"):
            continue

        match = re.search(r'tvg-name="([^"]*)"', line)
        if not match or match.group(1) != channel_name:
            continue

        # Advance past any blank lines or player-hint declarations
        # (#EXTVLCOPT, #KODIPROP, etc.) to reach the actual URL line.
        j = i + 1
        while j < len(lines):
            stripped = lines[j].strip()
            if not stripped:
                j += 1  # skip blank lines
                continue
            if any(stripped.startswith(p) for p in HINT_PREFIXES):
                j += 1  # skip hint declarations
                continue
            break  # first non-blank, non-hint line

        if j < len(lines):
            stripped = lines[j].strip()
            if stripped.startswith("http") or stripped.startswith("## http"):
                lines[j] = new_url + "\n"
                found = True
                break

    if found:
        with open(main_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        log(f"    Updated Main.m3u8 for {channel_name}")
    else:
        log(f"    Warning: Channel '{channel_name}' not found in Main.m3u8")


def create_or_replace_subfolder(channel, new_url):
    folder = TONTON_ROOT / channel["folder_name"]
    folder.mkdir(parents=True, exist_ok=True)

    file_path = folder / channel["file_name"]
    content = (
        "#EXTM3U\n"
        f"#EXTVLCOPT:http-referrer={REFERER}\n"
        f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n"
        f"#EXTINF:1,{channel['display_name']}\n"
        f"{new_url}\n"
    )

    with open(file_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

    log(f"    Updated wrapper: {file_path}")


def save_debug_artifacts(page, channel_name, tag):
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = channel_name.replace(" ", "_").replace("!", "")
    screenshot_path = DEBUG_DIR / f"{safe_name}_{tag}.png"
    info_path = DEBUG_DIR / f"{safe_name}_{tag}.txt"
    html_path = DEBUG_DIR / f"{safe_name}_{tag}.html"

    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception:
        pass

    try:
        title = page.title()
    except Exception:
        title = "<title unavailable>"

    try:
        frame_urls = [frame.url for frame in page.frames]
    except Exception:
        frame_urls = []

    try:
        info_path.write_text(
            "\n".join(
                [
                    f"URL: {page.url}",
                    f"Title: {title}",
                    f"Frames: {len(frame_urls)}",
                    *frame_urls,
                ]
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception:
        pass


def get_targets(page):
    targets = [("page", page)]
    for idx, frame in enumerate(page.frames):
        if frame == page.main_frame:
            continue
        targets.append((f"frame[{idx}]", frame))
    return targets


def try_play_interactions(page, debug=False):
    targets = get_targets(page)

    for label, target in targets:
        for selector in PLAY_SELECTORS:
            try:
                loc = target.locator(selector)
                count = loc.count()
                if debug:
                    log(f"    Debug: {label} selector {selector} count={count}")
                if count > 0:
                    try:
                        loc.first.click(timeout=2000, force=True)
                        log(f"    Clicked play target via {label}: {selector}")
                        page.wait_for_timeout(1200)
                        return True
                    except Exception as e:
                        if debug:
                            log(f"    Debug: click failed for {label} {selector}: {e}")
            except Exception as e:
                if debug:
                    log(f"    Debug: selector failed for {label} {selector}: {e}")

    try:
        page.keyboard.press("Space")
        page.wait_for_timeout(800)
        if debug:
            log("    Debug: pressed Space")
    except Exception:
        pass

    try:
        page.keyboard.press("k")
        page.wait_for_timeout(800)
        if debug:
            log("    Debug: pressed k")
    except Exception:
        pass

    try:
        page.mouse.click(720, 405)
        page.wait_for_timeout(800)
        if debug:
            log("    Debug: clicked center of viewport")
    except Exception:
        pass

    return False


def capture_stream_url(context, channel, debug=False):
    token_url = None
    page = context.new_page()

    def handle_request(request):
        nonlocal token_url
        if token_url:
            return
        url = request.url
        if is_ignored_stream(url):
            return
        token_url = url

    def handle_response(response):
        nonlocal token_url
        if token_url:
            return
        url = response.url
        if is_ignored_stream(url):
            return
        token_url = url

    page.on("request", handle_request)
    page.on("response", handle_response)

    try:
        try:
            page.goto(channel["page_url"], wait_until="domcontentloaded", timeout=45000)
        except PlaywrightTimeoutError:
            log("    Page navigation timed out, continuing to inspect page...")

        page.wait_for_timeout(3000)

        if is_login_required(page):
            log("    Login/session check: FAILED (redirected to login or login form detected)")
            if debug:
                save_debug_artifacts(page, channel["display_name"], "login_failed")
            return None, "login_required"

        log("    Login/session check: OK")

        try:
            log(f"    Page title: {page.title()}")
        except Exception:
            pass

        log(f"    Frame count: {len(page.frames)}")
        log(f"    Waiting {INITIAL_SETTLE_SECONDS}s for ads/player bootstrap before interaction...")

        if debug:
            save_debug_artifacts(page, channel["display_name"], "loaded")

        settle_start = time.time()
        while time.time() - settle_start < INITIAL_SETTLE_SECONDS:
            if token_url:
                return token_url, "ok"

            try:
                video_src = page.evaluate("document.querySelector('video')?.src")
                if video_src and not is_ignored_stream(video_src):
                    return video_src, "ok"
            except Exception:
                pass

            page.wait_for_timeout(1000)

        dismiss_overlays(page)

        if debug:
            save_debug_artifacts(page, channel["display_name"], "after_settle")

        clicked = try_play_interactions(page, debug=debug)
        if clicked:
            log("    Play interaction succeeded after settle delay.")
        else:
            log("    No play control found after settle delay, continuing retry loop...")

        capture_start = time.time()
        last_interaction = -999

        while time.time() - capture_start < TOKEN_WAIT_SECONDS:
            if token_url:
                return token_url, "ok"

            dismiss_overlays(page)

            elapsed = int(time.time() - capture_start)
            if elapsed - last_interaction >= INTERACTION_INTERVAL:
                try_play_interactions(page, debug=debug)
                last_interaction = elapsed

            try:
                video_src = page.evaluate("document.querySelector('video')?.src")
                if video_src and not is_ignored_stream(video_src):
                    return video_src, "ok"
            except Exception:
                pass

            page.wait_for_timeout(1000)

        if debug:
            save_debug_artifacts(page, channel["display_name"], "capture_failed")

        return None, "no_stream_captured"

    finally:
        page.close()


def main():
    debug = "--debug" in sys.argv

    if not PROFILE_DIR.exists():
        log("=" * 60)
        log("Tonton persistent profile folder not found.")
        log(f"Expected: {PROFILE_DIR}")
        log("Run setup_tonton_login.py once on the runner machine first.")
        log("=" * 60)
        sys.exit(1)

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
    log(f"Using profile folder: {PROFILE_DIR}")
    log(f"Debug mode: {'ON' if debug else 'OFF'}")
    log("=" * 60)

    with stealth.use_sync(sync_playwright()) as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=not debug,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--autoplay-policy=no-user-gesture-required",
            ],
            user_agent=USER_AGENT,
            extra_http_headers={"Referer": REFERER},
            locale="en-US",
            timezone_id="Asia/Kuala_Lumpur",
            viewport={"width": 1440, "height": 900},
        )

        try:
            for existing_page in context.pages[:-1]:
                try:
                    existing_page.close()
                except Exception:
                    pass

            for index, channel in enumerate(CHANNELS, start=1):
                log(f"\n[{index}/{len(CHANNELS)}] {channel['display_name']}")
                log(f"    Page: {channel['page_url']}")

                token_url, status = capture_stream_url(context, channel, debug=debug)

                if status == "login_required":
                    log("    Login session looks expired. Run setup_tonton_login.py again.")
                    login_invalid = True
                    failed_count += 1
                    break

                if token_url:
                    log(f"    Captured stream: {token_url[:150]}...")
                    replace_in_main_m3u8(MAIN_FILE, channel["display_name"], token_url)
                    create_or_replace_subfolder(channel, token_url)
                    success_count += 1
                else:
                    log(f"    Failed to capture stream ({status}). Existing Main.m3u8 and wrapper left unchanged.")
                    failed_count += 1

        finally:
            context.close()

    log("\n" + "=" * 60)
    log(f"TONTON refresh finished. Success: {success_count} | Failed: {failed_count}")
    if login_invalid:
        log("Action needed: refresh Tonton login setup/profile.")
    if debug:
        log(f"Debug artifacts folder: {DEBUG_DIR}")
    log("=" * 60)

    sys.exit(0)


if __name__ == "__main__":
    main()
