import sys
import time
import os
import io
import re
from playwright.sync_api import sync_playwright

# Fix Unicode output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ---------- CONFIGURATION (exact tvg‑name → Mana2 live page) ----------
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
    Navigate to the channel, click the play button (with iframe handling),
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

            # Give the page a moment to fully render and JW Player to initialise
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

            # ---------- Locate and click the play element ----------
            # JW Player often lives inside an iframe – look for one
            iframe = page.locator('iframe').first
            if iframe.count() > 0:
                try:
                    frame = iframe.content_frame
                    if frame:
                        print("    Found iframe containing player.")
                        # Try clicking inside the iframe
                        frame.locator('.jw-icon-playback, video').first.click(timeout=5000)
                        print("    Clicked inside iframe.")
                        token = _wait_for_token(page, token)
                        if token:
                            return token
                except Exception as e:
                    print(f"    Iframe click failed: {e}")

            # If no iframe, try clicking directly on the main page
            play_selectors = ['.jw-icon-playback', 'button[aria-label="Play"]', 'video']
            clicked = False
            for sel in play_selectors:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        loc.first.click(timeout=5000)
                        print(f"    Clicked on '{sel}'.")
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                # Absolute fallback: click the video element (works even without controls)
                try:
                    page.locator('video').first.click(timeout=5000)
                    print("    Clicked on <video> element.")
                    clicked = True
                except Exception as e:
                    print(f"    Fallback click failed: {e}")

            # ---------- Wait for stream request ----------
            token = _wait_for_token(page, token)

        except Exception as e:
            print(f"    ERROR during navigation/playback: {e}")

        context.close()
        browser.close()
    return token


def _wait_for_token(page, token):
    """Wait up to 20 seconds for the stream .m3u8 request."""
    print("    Waiting for .m3u8 stream request ...")
    for _ in range(20):
        if token:
            break
        time.sleep(1)
    if token:
        print(f"    Success: {token}")
    else:
        print("    No .m3u8 stream request captured within 20s.")
    return token


def replace_in_main_m3u8(main_path, channel_name, new_url):
    """
    Line‑by‑line replacement in Main.m3u8:
    Find the #EXTINF line whose tvg-name attribute exactly equals `channel_name`,
    then replace the next HTTP URL line with `new_url`.
    """
    with open(main_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    found = False
    for i, line in enumerate(lines):
        if line.startswith('#EXTINF'):
            m = re.search(r'tvg-name="([^"]*)"', line)
            if m and m.group(1) == channel_name:
                if i + 1 < len(lines) and lines[i + 1].strip().startswith('http'):
                    lines[i + 1] = new_url + '\n'
                    found = True
                    break

    if found:
        with open(main_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        print(f"    Updated Main.m3u8 for {channel_name}")
    else:
        print(f"    Warning: Channel '{channel_name}' not found in Main.m3u8")


def create_or_replace_subfolder(base_dir, channel_name, new_url):
    """
    Create (or overwrite) a wrapper .m3u8 file at
    Channels/Mana-mana/{channel_name}/{channel_name}.m3u8
    with a clean template and the fresh token URL.
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
    main_path = os.path.join(base_dir, 'Main.m3u8')

    print("Starting sequential Mana2 token refresh...")
    for channel, page_url in CHANNELS.items():
        print(f"\n--- Processing {channel} ---")
        token = fetch_token(channel, page_url)

        if token:
            replace_in_main_m3u8(main_path, channel, token)
            create_or_replace_subfolder(base_dir, channel, token)
        else:
            print(f"  Skipping {channel} – no token received.")

    print("\nAll Mana-mana channels processed.")


if __name__ == '__main__':
    main()
