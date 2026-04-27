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
    "BorneoTV": "https://www.mana2.my/channel/live/borneo-tv",
    "EnjoyTV": "https://www.mana2.my/channel/live/tv5",
    "SelangorTV": "https://www.mana2.my/channel/live/selangor-tv",
    "Sukan+": "https://www.mana2.my/channel/live/sukan-rtm",
    "SukeTV": "https://www.mana2.my/channel/live/suke-tv",
    "TVS": "https://www.mana2.my/channel/live/tvs",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.mana2.my/",
}
# ------------------------------------


def get_subfolder_from_flatten(base_dir, channel_name):
    """
    Read Flatten.m3u8 and find the wrapper .m3u8 URL for the
    channel whose **exact** tvg-name matches `channel_name`.
    Returns the local relative path (e.g.
    'Channels/Mana-mana/SukanPlus/SukanPlus.m3u8'), or None if not found.
    """
    flatten_path = os.path.join(base_dir, 'Channels', 'Flatten.m3u8')
    if not os.path.exists(flatten_path):
        print(f"    Error: Flatten.m3u8 not found at {flatten_path}")
        return None

    with open(flatten_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Match the exact tvg-name attribute value
    name_pattern = re.escape(channel_name)
    extinf_pattern = re.compile(
        rf'^#EXTINF:.*\btvg-name="{name_pattern}"',
        re.IGNORECASE | re.MULTILINE
    )
    match = extinf_pattern.search(content)
    if not match:
        print(f"    Channel '{channel_name}' not found in Flatten.m3u8")
        return None

    # The matching line
    line = match.group(0)
    # Now find the URL immediately after this line
    # We'll split the content from the match position onwards
    rest = content[match.end():]
    # The URL should be the first non-empty line that starts with http
    for url_line in rest.splitlines():
        url_line = url_line.strip()
        if url_line.startswith('http'):
            local_path = re.sub(
                r'.*/(?:main|refs/heads/main)/(.+)',
                r'\1',
                url_line
            )
            if local_path and not local_path.startswith('http'):
                return local_path
            else:
                print(f"    Warning: Could not extract local path from {url_line}")
            break
    print(f"    Warning: No URL found after #EXTINF for {channel_name}")
    return None


def replace_channel_url_in_subfolder(base_dir, rel_path, new_url):
    """Replace the first HTTP URL in the subfolder .m3u8 file with new_url."""
    full_path = os.path.join(base_dir, rel_path)
    if not os.path.exists(full_path):
        print(f"    Warning: Subfolder file not found: {full_path}")
        return

    with open(full_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace the first HTTP URL after #EXTINF
    pattern = re.compile(r'(#EXTINF:[^\n]*\n)(https?://[^\n]+)')
    content = pattern.sub(r'\1' + new_url, content, count=1)

    # Ensure EXTVLCOPT headers
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

    # Read current Main.m3u8 (already cleaned & validated)
    with open(main_m3u8_path, 'r', encoding='utf-8') as f:
        main_content = f.read()

    for channel, new_url in fresh_urls.items():
        # ---------- Replace in Main.m3u8 (exact tvg-name match) ----------
        name_pattern = re.escape(channel)
        pattern = re.compile(
            rf'^#EXTINF:.*\btvg-name="{name_pattern}".*\n(https?://[^\n]+)',
            re.IGNORECASE | re.MULTILINE
        )
        # Use subn to ensure only one replacement
        main_content, count = pattern.subn(
            lambda m: m.group(0).replace(m.group(1), new_url),
            main_content
        )
        if count == 0:
            print(f"  Warning: Channel '{channel}' not found in Main.m3u8")

        # ---------- Replace in subfolder file ----------
        subfolder_rel = get_subfolder_from_flatten(base_dir, channel)
        if subfolder_rel:
            replace_channel_url_in_subfolder(base_dir, subfolder_rel, new_url)
        else:
            print(f"  Warning: Could not update subfolder for {channel}")

    with open(main_m3u8_path, 'w', encoding='utf-8') as f:
        f.write(main_content)

    print("Updated Main.m3u8 and subfolder files with fresh Mana2 URLs.")


if __name__ == '__main__':
    main()
