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


def get_subfolder_from_flatten(base_dir, channel_name):
    """
    Read Flatten.m3u8 line by line and find the wrapper .m3u8 URL for the
    given channel.  Return the local relative path (e.g.
    'Channels/Mana-mana/SukanPlus/SukanPlus.m3u8'), or None if not found.
    """
    flatten_path = os.path.join(base_dir, 'Channels', 'Flatten.m3u8')
    if not os.path.exists(flatten_path):
        print(f"    Error: Flatten.m3u8 not found at {flatten_path}")
        return None

    with open(flatten_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        # Look for an #EXTINF line that contains the channel name
        if line.startswith('#EXTINF') and channel_name in line:
            # The next non‑empty line should be the wrapper URL
            if i + 1 < len(lines):
                url_line = lines[i + 1].strip()
                if url_line.startswith('http'):
                    # Convert raw GitHub URL to local relative path
                    local_path = re.sub(
                        r'.*/(?:main|refs/heads/main)/(.+)',
                        r'\1',
                        url_line
                    )
                    # Make sure we didn't capture the whole URL
                    if local_path and not local_path.startswith('http'):
                        return local_path
                    else:
                        print(f"    Warning: Could not extract local path from {url_line}")
                else:
                    print(f"    Warning: Next line after #EXTINF is not a URL: {url_line}")
            break
    print(f"    Warning: Channel '{channel_name}' not found in Flatten.m3u8")
    return None


def replace_channel_url_in_subfolder(base_dir, rel_path, new_url):
    """Replace the first HTTP URL in the subfolder .m3u8 file with new_url."""
    full_path = os.path.join(base_dir, rel_path)
    if not os.path.exists(full_path):
        print(f"    Warning: Subfolder file not found: {full_path}")
        return

    with open(full_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace the first HTTP URL that appears after the #EXTINF line
    pattern = re.compile(r'(#EXTINF:[^\n]*\n)(https?://[^\n]+)')
    content = pattern.sub(r'\1' + new_url, content, count=1)

    # Ensure EXTVLCOPT headers are present
    if '#EXTVLCOPT:http-user-agent' not in content:
        content = content.replace(
            '#EXTINF:',
            '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)\n'
            '#EXTVLCOPT:http-referrer=https://www.mana2.my/\n#EXTINF:',
            1
        )

    with open(full_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"    Updated subfolder: {rel_path}")


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

            def handle_request(request):
                nonlocal m3u8_url
                if '.m3u8' in request.url and 'live.mana2.my' in request.url:
                    m3u8_url = request.url
                    print(f"    Captured: {m3u8_url}")

            page.on('request', handle_request)

            try:
                page.goto(ch_url, wait_until='networkidle', timeout=30000)
                print("    Page loaded.")
                # Wait up to 15 seconds for the m3u8 request
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

    # Read current Main.m3u8 for replacement
    with open(main_m3u8_path, 'r', encoding='utf-8') as f:
        main_content = f.read()

    for channel, new_url in fresh_urls.items():
        # Replace in Main.m3u8
        pattern = re.compile(
            rf'(#EXTINF:[^\n]*{re.escape(channel)}[^\n]*\n)(https?://[^\n]+)',
            re.IGNORECASE
        )
        main_content = pattern.sub(r'\1' + new_url, main_content)

        # Replace in subfolder file – extract path from Flatten.m3u8
        subfolder_rel = get_subfolder_from_flatten(base_dir, channel)
        if subfolder_rel:
            replace_channel_url_in_subfolder(base_dir, subfolder_rel, new_url)
        else:
            print(f"  Warning: Could not find subfolder path for {channel}")

    with open(main_m3u8_path, 'w', encoding='utf-8') as f:
        f.write(main_content)

    print("Updated Main.m3u8 and subfolder files with fresh Mana2 URLs.")


if __name__ == '__main__':
    main()
