import re
import sys
import time
import os
import io
from playwright.sync_api import sync_playwright

# Fix Unicode output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ---------- CONFIGURATION (UPDATED URLs) ----------
CHANNELS = {
    "Al-Hijrah": "https://www.mana2.my/channel/live/tv-alhijrah",
    "Bernama": "https://www.mana2.my/channel/live/bernama",
    "Borneo TV": "https://www.mana2.my/channel/live/borneo-tv",
    "Enjoy TV": "https://www.mana2.my/channel/live/tv5",
    "Selangor TV": "https://www.mana2.my/channel/live/selangor-tv",
    "Sukan+": "https://www.mana2.my/channel/live/sukan-rtm",
    "Suke TV": "https://www.mana2.my/channel/live/suke-tv",
    "TVS": "https://www.mana2.my/channel/live/tvs",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.mana2.my/",
}
# ------------------------------------

MANA2_CONTAINER_PATH = "Channels/Mana-mana"


def replace_channel_url_in_file(filepath, channel_name, new_url):
    """Update the .m3u8 file for a specific channel with new URL and headers."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace the URL line (first http line after #EXTINF)
    pattern = re.compile(rf'(#EXTINF:.*{re.escape(channel_name)}.*\n)(https?://[^\n]+)', re.IGNORECASE)
    replacement = r'\1' + new_url
    content = pattern.sub(replacement, content)

    # Ensure EXTVLCOPT headers exist
    if '#EXTVLCOPT:http-user-agent' not in content:
        content = content.replace('#EXTINF:', '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)\n#EXTVLCOPT:http-referrer=https://www.mana2.my/\n#EXTINF:', 1)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)


def scrape_mana2_urls():
    """Scrape fresh m3u8 URLs from mana2.my using Playwright."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS["User-Agent"])
        page = context.new_page()
        urls = {}

        for channel, ch_url in CHANNELS.items():
            print(f"  Processing {channel} ({ch_url})")
            m3u8_url = None

            # Set up network listener BEFORE navigating
            def handle_request(request):
                nonlocal m3u8_url
                if '.m3u8' in request.url and 'live.mana2.my' in request.url:
                    m3u8_url = request.url
                    print(f"    Captured: {m3u8_url}")

            page.on('request', handle_request)

            try:
                # Navigate and wait for network to settle (video autoplays)
                page.goto(ch_url, wait_until='networkidle', timeout=30000)
                print("    Page loaded.")

                # Wait a bit more to be sure the request fires
                for _ in range(15):
                    if m3u8_url:
                        break
                    time.sleep(1)

                if m3u8_url:
                    urls[channel] = m3u8_url
                    print(f"    Success: {m3u8_url}")
                else:
                    print("    No .m3u8 request captured.")

            except Exception as e:
                print(f"    ERROR: {e}")

            page.remove_listener('request', handle_request)

        browser.close()
    return urls


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_m3u8_path = os.path.join(base_dir, 'Main.m3u8')

    print("Scraping fresh Mana2 URLs...")
    try:
        fresh_urls = scrape_mana2_urls()
    except Exception as e:
        print(f"ERROR during scraping: {e}")
        print("Using existing URLs (no changes made).")
        sys.exit(0)

    if not fresh_urls:
        print("No URLs scraped, exiting without changes.")
        sys.exit(0)

    # Update Main.m3u8
    with open(main_m3u8_path, 'r', encoding='utf-8') as f:
        main_content = f.read()

    for channel, new_url in fresh_urls.items():
        # Replace in Main.m3u8
        pattern = re.compile(rf'(#EXTINF:.*{re.escape(channel)}.*\n)(https?://[^\n]+)', re.IGNORECASE)
        main_content = pattern.sub(r'\1' + new_url, main_content)

        # Replace in subfolder file
        channel_safe = channel.replace(" ", "-")
        subfolder_file = os.path.join(base_dir, MANA2_CONTAINER_PATH, channel_safe, f"{channel}.m3u8")
        if os.path.exists(subfolder_file):
            replace_channel_url_in_file(subfolder_file, channel, new_url)
        else:
            print(f"Warning: subfolder file not found for {channel}: {subfolder_file}")

    with open(main_m3u8_path, 'w', encoding='utf-8') as f:
        f.write(main_content)

    print("Updated Main.m3u8 and subfolder files with fresh Mana2 URLs.")


if __name__ == '__main__':
    main()
