import sys
import time
import os
import io
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
            # Extract the exact tvg-name value
            import re
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
    Create (or overwrite) a wrapper .m3u8 file inside
    Channels/Mana-mana/{channel_name}/{channel_name}.m3u8 with a clean template.
    """
    # Folder path: Channels/Mana-mana/<channel_name>
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
    print(f"    Created / replaced subfolder file: {file_path}")


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

    print("Scraping fresh Mana2 URLs...")
    fresh_urls = scrape_mana2_urls()
    if not fresh_urls:
        print("No new URLs scraped – exiting.")
        sys.exit(0)

    print("\nApplying new tokens to Main.m3u8 and subfolder files ...")
    for channel, new_url in fresh_urls.items():
        replace_in_main_m3u8(main_path, channel, new_url)
        create_or_replace_subfolder(base_dir, channel, new_url)

    print("\nUpdate complete.")


if __name__ == '__main__':
    main()
