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
    Open a fresh browser context, navigate to the Mana2 channel,
    dismiss overlays, click the JW Player play button, and capture
    the first real .m3u8 stream request from live.mana2.my.
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
            # Capture only real stream .m3u8, not JW Player analytics pings
            if token is None and '.m3u8' in url and 'live.mana2.my' in url and 'jwpltx.com' not in url and 'ping.gif' not in url:
                token = url
                print(f"    Captured: {token}")

        page.on('request', handle_request)

        try:
            print(f"  Navigating to {page_url} ...")
            page.goto(page_url, wait_until='networkidle', timeout=30000)
            print("    Page loaded.")

            # --- Dismiss common overlays (cookies, age verification) ---
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
            # -----------------------------------------------------------

            # --- Wait for JW Player or a video element to appear ---
            try:
                page.wait_for_selector('.jwplayer, video', timeout=10000)
                print("    JW Player / video element found.")
            except Exception:
                print("    No JW Player element found – trying any video.")
            # ---------------------------------------------------------

            # --- Click play using JW Player specific selectors ---
            play_selectors = [
                '.jw-icon-playback',       # JW Player play button
                '.jw-button-play',         # alternative
                'button[aria-label="Play"]',
                '.vjs-big-play-button',
                'video',                   # fallback: click on the video itself
            ]
            clicked = False
            for sel in play_selectors:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        loc.first.click(timeout=5000)
                        print(f"    Clicked on '{sel}' to start playback.")
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                # Last resort: JavaScript play
                try:
                    page.evaluate("""
                        const v = document.querySelector('video');
                        if (v) { v.muted = true; v.play(); }
                    """)
                    print("    Forced video play via JavaScript.")
                    clicked = True
                except Exception as e:
                    print(f"    JS play failed: {e}")

            if not clicked:
                print("    No clickable play element found – hoping for autoplay...")
            # ----------------------------------------------------------------

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
