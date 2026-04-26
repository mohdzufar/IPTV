"""
refresh_mana2.py - Refresh Mana-mana tokens directly in Main.m3u8 and subfolders.
Uses Playwright + stealth. Designed for self-hosted runner.
Replace the dummy token refresh function with your actual Playwright logic.
"""

import os
import re
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

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
    Use Playwright stealth to refresh the token and return a fresh direct Mana URL.
    *** REPLACE THIS DUMMY IMPLEMENTATION WITH YOUR EXISTING LOGIC ***
    """
    # Example: launch browser, navigate to inner_url, capture new URL
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            stealth_sync(page)
            # The actual token refresh mechanism – adjust as needed.
            # This dummy just fetches the page and returns the final redirected URL.
            page.goto(inner_url, wait_until='domcontentloaded', timeout=20000)
            # Wait a moment for any token generation
            page.wait_for_timeout(3000)
            final_url = page.url
            browser.close()
            # If final_url hasn't changed, attempt to extract from page content
            if final_url == inner_url:
                content = page.content()
                import re
                matches = re.findall(r'(https?://[^"\'\s]+\.(m3u8|mpd|ts)[^"\'\s]*)', content)
                if matches:
                    final_url = matches[0][0]
            return final_url if final_url != inner_url else None
    except Exception as e:
        print(f"  [!] Playwright refresh error: {e}")
        return None

def local_path_from_url(wrapper_url):
    """
    Convert a raw GitHub wrapper URL to a local file path.
    Example: https://raw.githubusercontent.com/.../Channels/Mana-mana/Al-Hijrah/Al-Hijrah.m3u8
    -> Channels/Mana-mana/Al-Hijrah/Al-Hijrah.m3u8
    """
    # Find the part after 'main/' or 'refs/heads/main/' – typical raw URL structure
    parts = wrapper_url.split('/')
    if 'main' in parts:
        idx = parts.index('main')
        return '/'.join(parts[idx+1:])
    # Fallback: just use last path segments
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
        # Replace the first http line with fresh_url (the old one is the inner URL)
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
            # Step 1: fetch wrapper
            wrapper_lines = fetch_wrapper(stripped)
            if not wrapper_lines:
                new_lines.append(line)
                continue
            # Step 2: extract inner URL
            inner_url = extract_inner_url(wrapper_lines)
            if not inner_url:
                print(f"  [!] No inner URL found, keeping original")
                new_lines.append(line)
                continue
            print(f"  Inner URL: {inner_url}")
            # Step 3: refresh token
            fresh_url = refresh_mana_token(inner_url)
            if fresh_url:
                print(f"  [OK] New URL: {fresh_url}")
                # Update Main.m3u8
                new_lines.append(fresh_url + '\n')
                # Update subfolder file
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
