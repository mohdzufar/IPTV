import sys
import time
import os
import io
from playwright.sync_api import sync_playwright

# Fix Unicode output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ---------- CONFIGURATION (exact tvg-name → Mana2 live page) ----------
CHANNELS = {
    "Al-Hijrah": "https://www.mana2.my/channel/live/tv-alhijrah",
    "Bernama":   "https://www.mana2.my/channel/live/bernama",
    "BorneoTV":  "https://www.mana2.my/channel/live/borneo-tv",
    "EnjoyTV":   "https://www.mana2.my/channel/live/tv5",
    "SelangorTV":"https://www.mana2.my/channel/live/selangor-tv",
    "SukanPlus": "https://www.mana2.my/channel/live/sukan-rtm",
    "SukeTV":    "https://www.mana2.my/channel/live/suke-tv",
    "TVS":       "https://www.mana2.my/channel/live/tvs",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://www.mana2.my/",
}
# --------------------------------------------------------------------


def fetch_token(channel_name, page_url):
    """
    Navigate to the Mana2 channel, click the JW Player play overlay,
    and capture the first real .m3u8 stream request from live.mana2.my.
    Returns the token URL or None.
    """
    token = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS["User-Agent"])
        page = context.new_page()

        def handle_request(request):
            nonlocal token
            url = request.url
            # Capture only real stream .m3u8 – ignore JW analytics pings
            if token is None and '.m3u8' in url and 'live.mana2.my' in url and 'jwpltx.com' not in url and 'ping.gif' not in url:
                token = url
                print(f"    Captured: {token}")

        page.on('request', handle_request)

        try:
            print(f"  Navigating to {page_url} ...")
            page.goto(page_url, wait_until='networkidle', timeout=30000)
            print("    Page loaded.")

            # Let the JW Player initialise
            time.sleep(3)

            # ---------- Dismiss any obvious overlays ----------
            overlay_selectors = [
                'button[aria-label="Close"]',
                '.cookie-consent button',
                '.modal button.close',
                '[data-dismiss="modal"]',
                '.mfp-close',
            ]
            for sel in overlay_selectors:
                try:
                    el = page.locator(sel)
                    if el.count() > 0:
                        el.first.click(timeout=2000)
                        print(f"    Dismissed overlay: {sel}")
                        time.sleep(0.5)
                except Exception:
                    pass

            # ---------- Click the JW Player play overlay ----------
            play_selectors = [
                'div[aria-label="Play"]',
                '.jw-icon-display',
                'button[aria-label="Play"]',
            ]
            clicked = False
            for sel in play_selectors:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        loc.first.click(timeout=5000)
                        print(f"    Clicked on '{sel}'.")
                        clicked = True
                        break
                except Exception as e:
                    print(f"    Selector '{sel}' failed: {e}")

            if not clicked:
                print("    No play button found – playback may not start.")

            # ---------- Wait for stream request ----------
            print("    Waiting for .m3u8 stream request ...")
            for _ in range(20):
                if token:
                    break
                time.sleep(1)

            if token:
                print(f"    Success: {token}")
            else:
                print("    No .m3u8 stream request captured within 20s.")
        except Exception as e:
            print(f"    ERROR during navigation/playback: {e}")

        context.close()
        browser.close()
    return token


def create_or_replace_subfolder(base_dir, channel_name, new_url):
    """
    Create (or overwrite) the wrapper .m3u8 file with the fresh token URL.
    This is the ONLY output of this script. validate_and_update.py reads
    this wrapper later via the GitHub API to decide live/dead.
    """
    folder = os.path.join(base_dir, 'Channels', 'Mana-mana', channel_name)
    os.makedirs(folder, exist_ok=True)

    file_path = os.path.join(folder, f"{channel_name}.m3u8")
    content = (
        "#EXTM3U\n"
        "#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)\n"
        "#EXTVLCOPT:http-referrer=https://www.mana2.my/\n"
        f"#EXTINF:1,{channel_name}\n"
        f"{new_url}\n"
    )
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"    Created / replaced subfolder: {file_path}")


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("Starting sequential Mana2 token refresh (wrapper files only)...")
    for channel, page_url in CHANNELS.items():
        print(f"\n--- Processing {channel} ---")
        token = fetch_token(channel, page_url)

        if token:
            create_or_replace_subfolder(base_dir, channel, token)
        else:
            print(f"  Skipping {channel} – no token received.")

    print("\nAll Mana-mana channels processed.")


if __name__ == '__main__':
    main()
