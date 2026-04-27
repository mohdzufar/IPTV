import re
import sys
import time
import os
import io
from playwright.sync_api import sync_playwright

# Fix Unicode output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ---------- CONFIGURATION (exact tvg‑name from Flatten.m3u8) ----------
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
# -----------------------------------------------------------------------


def build_flatten_lookup(base_dir):
    """
    Read Flatten.m3u8 and return a dict mapping the exact tvg-name to the local
    relative path of the wrapper .m3u8 file.
    Example: {"Sukan+": "Channels/Mana-mana/SukanPlus/SukanPlus.m3u8", ...}
    """
    flatten_path = os.path.join(base_dir, 'Channels', 'Flatten.m3u8')
    if not os.path.exists(flatten_path):
        print(f"    Error: Flatten.m3u8 not found at {flatten_path}")
        return {}

    lookup = {}
    with open(flatten_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        if line.startswith('#EXTINF'):
            # Extract tvg-name attribute value exactly
            m = re.search(r'tvg-name="([^"]*)"', line)
            if m:
                ch_name = m.group(1)
                # Next non‑empty line should be the wrapper URL
                if i + 1 < len(lines):
                    url_line = lines[i + 1].strip()
                    if url_line.startswith('http'):
                        # Convert raw GitHub URL to local path
                        local = re.sub(r'.*/(?:main|refs/heads/main)/(.+)', r'\1', url_line)
                        if local and not local.startswith('http'):
                            lookup[ch_name] = local
    return lookup


def replace_in_main_m3u8(main_path, channel_name, new_url):
    """
    Line‑by‑line replacement in Main.m3u8: find the #EXTINF line whose tvg-name
    attribute exactly matches 'channel_name', then replace the next HTTP URL line
    with 'new_url'.
    """
    with open(main_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    found = False
    for i, line in enumerate(lines):
        if line.startswith('#EXTINF'):
            # Check for exact tvg-name attribute match
            m = re.search(r'tvg-name="([^"]*)"', line)
            if m and m.group(1) == channel_name:
                # The URL is on the next line
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


def replace_in_subfolder(base_dir, rel_path, new_url):
    """Replace the first HTTP URL in the wrapper .m3u8 file with new_url."""
    full_path = os.path.join(base_dir, rel_path)
    if not os.path.exists(full_path):
        print(f"    Warning: Subfolder file not found: {full_path}")
        return

    with open(full_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace the first HTTP URL that follows the #EXTINF line
    pattern = re.compile(r'(#EXTINF:[^\n]*\n)(https?://[^\n]+)')
    content = pattern.sub(r'\1' + new_url, content, count=1)

    # Ensure EXTVLCOPT headers exist
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
    main_path = os.path.join(base_dir, 'Main.m3u8')

    # --- Step 1: Build lookup of folder paths from Flatten.m3u8 ---
    print("Building channel lookup from Flatten.m3u8 ...")
    flatten_lookup = build_flatten_lookup(base_dir)
    print(f"  Found {len(flatten_lookup)} channels in lookup.\n")

    # --- Step 2: Scrape fresh tokens ---
    print("Scraping fresh Mana2 URLs...")
    fresh_urls = scrape_mana2_urls()
    if not fresh_urls:
        print("No new URLs scraped – exiting.")
        sys.exit(0)

    # --- Step 3: Apply tokens to Main.m3u8 and subfolder files ---
    print("\nApplying tokens ...")
    for channel, new_url in fresh_urls.items():
        replace_in_main_m3u8(main_path, channel, new_url)

        sub_rel = flatten_lookup.get(channel)
        if sub_rel:
            replace_in_subfolder(base_dir, sub_rel, new_url)
        else:
            print(f"    Warning: No subfolder path for {channel}")

    print("\nUpdate complete.")


if __name__ == '__main__':
    main()
