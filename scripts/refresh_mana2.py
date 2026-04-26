"""
refresh_mana2.py - Refresh Mana-mana tokens directly in Main.m3u8 and subfolders.
Uses sync Playwright + stealth. Designed for self-hosted runner.
"""

import os
import re
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth        # <-- correct import for your version

# Configuration
PLAYLIST_FILE = "Main.m3u8"
MANA_PATTERN = re.compile(r"Mana-mana", re.IGNORECASE)

def fetch_wrapper(url):
    """Fetch a wrapper .m3u8 file from raw GitHub URL, return lines."""
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = resp.read().decode('utf-8', errors='ignore')
            return data.splitlines()
    except Exception as e:
        print(f"  [!] Failed to fetch wrapper {url}: {e}")
        return None

def extract_inner_url(lines):
    """Return the first http line from wrapper lines, or None."""
    for line in lines:
        line = line.strip()
        if line.startswith('http'):
            return line
    return None

def refresh_mana_token(inner_url):
    """
    Use sync Playwright + stealth to refresh the token.
    Returns a fresh direct Mana URL or None.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            stealth(page)                  # <-- apply stealth (sync)
            page.goto(inner_url, wait_until='domcontentloaded', timeout=20000)
            page.wait_for_timeout(3000)    # allow token to be set
            final_url = page.url
            # If the URL didn't change, try to extract from page content
            if final_url == inner_url:
                content = page.content()
                import re
                matches = re.findall(r'(https?://[^"\'\s]+\.(m3u8|mpd|ts)[^"\'\s]*)', content)
                if matches:
                    final_url = matches[0][0]
            browser.close()
            return final_url if final_url != inner_url else None
    except Exception as e:
        print(f"  [!] Playwright error: {e}")
        return None

def local_path_from_url(wrapper_url):
    """
    Convert a raw GitHub wrapper URL to a local file path.
    Example: https://raw.githubusercontent.com/.../Channels/Mana-mana/Al-Hijrah/Al-Hijrah.m3u8
    -> Channels/Mana-mana/Al-Hijrah/Al-Hijrah.m3u8
    """
    parts = wrapper_url.split('/')
    if 'main' in parts:
        idx = parts.index('main')
        return '/'.join(parts[idx+1:])
    # Fallback: just use path segments after 'Channels'
    return '/'.join(parts[parts.index('Channels'):])

def update_subfolder_file(wrapper_url, fresh_url):
    """Replace the inner stream URL in the local .m3u8 file with the fresh URL."""
    local_file = local_path_from_url(wrapper_url)
    if not os.path.exists(local_file):
        print(f"  [!] Local file not found: {local_file}")
        return False
    try:
        with open(local_file, 'r', encoding='utf-8') as f:
            content = f.read()
        lines = content.splitlines(True)
        for i, line in enumerate(lines):
            if line.strip().startswith('http'):
                lines[i] = fresh_url + '\n'
                break
        with open(local_file, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(lines)
        print(f"  [OK] Updated local file: {local_file}")
        return True
    except Exception as e:
        print(f"  [!] Failed to update local file {local_file}: {e}")
        return False

def main():
    if not os.path.exists(PLAYLIST_FILE):
        print(f"Playlist {PLAYLIST_FILE} not found.")
        return

    with open(PLAYLIST_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    updated = False
    new_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('http') and MANA_PATTERN.search(stripped):
            print(f"Refreshing Mana URL: {stripped}")
            wrapper_lines = fetch_wrapper(stripped)
            if not wrapper_lines:
                new_lines.append(line)
                continue
            inner_url = extract_inner_url(wrapper_lines)
            if not inner_url:
                print(f"  [!] No inner URL found, keeping original")
                new_lines.append(line)
                continue
            print(f"  Inner URL: {inner_url}")
            fresh_url = refresh_mana_token(inner_url)
            if fresh_url:
                print(f"  [OK] New URL: {fresh_url}")
                new_lines.append(fresh_url + '\n')
                update_subfolder_file(stripped, fresh_url)
                updated = True
            else:
                print(f"  [!] Token refresh failed, keeping original wrapper")
                new_lines.append(line)
        else:
            new_lines.append(line)

    if updated:
        with open(PLAYLIST_FILE, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(new_lines)
        print(f"Main.m3u8 updated with fresh Mana tokens.")
    else:
        print("No Mana tokens refreshed (or no updates).")

if __name__ == "__main__":
    main()
