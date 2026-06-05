#!/usr/bin/env python3
import os
import requests
import sys
import re
from urllib.parse import urlparse

# Configuration
INPUT_PLAYLIST = os.environ.get("INPUT_PLAYLIST", "Flatten.m3u8")
OUTPUT_PLAYLIST = os.environ.get("OUTPUT_PLAYLIST", "Main.m3u8")
REPORT_FILE = os.environ.get("REPORT_FILE", "validation-report.txt")

WRAPPER_BASE_URL = os.environ.get("WRAPPER_BASE_URL", "http://your-iptv-server.com:8080")
WRAPPER_USER = os.environ.get("WRAPPER_USER", "your-user")
WRAPPER_PASS = os.environ.get("WRAPPER_PASS", "your-pass")

REQUEST_TIMEOUT = 10
MAX_RETRIES = 1  # retry once if first check fails

def get_stream_urls_from_wrapper(wrapper_url):
    """Fetch the wrapper M3U8 and extract stream URLs."""
    try:
        resp = requests.get(wrapper_url, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return []
        lines = resp.text.splitlines()
        urls = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                urls.append(line)
        return urls
    except:
        return []

def check_stream(url):
    """Check if a stream URL is reachable (returns True/False)."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT, stream=True)
            if r.status_code == 200:
                content_type = r.headers.get('Content-Type', '').lower()
                # Accept video/mp2t, application/x-mpegURL, etc.
                if 'html' not in content_type:
                    return True
            # if 404, 403, etc., return False
        except:
            pass
    return False

def main():
    if not os.path.exists(INPUT_PLAYLIST):
        print(f"ERROR: Input playlist '{INPUT_PLAYLIST}' not found.")
        sys.exit(1)

    with open(INPUT_PLAYLIST, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Parse entries: #EXTINF line followed by URL
    entries = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('#EXTINF:'):
            extinf = line
            # Next line should be the URL
            if i+1 < len(lines):
                url = lines[i+1].strip()
                entries.append((extinf, url))
                i += 2
            else:
                i += 1
        else:
            i += 1

    if not entries:
        print("No entries found in playlist.")
        sys.exit(1)

    valid_entries = []
    report = ["Validation Report", "=" * 50, ""]

    for idx, (extinf, wrapper_url) in enumerate(entries, 1):
        # Extract attributes from EXTINF
        # Example: #EXTINF:-1 tvg-id="TV1" tvg-name="TV1" tvg-logo="..." group-title="Malaysia",TV1
        attrs = dict(re.findall(r'(\S+)="(.*?)"', extinf))
        channel_name = attrs.get('tvg-name', f'channel-{idx}')
        group = attrs.get('group-title', 'Unknown')

        print(f"[{idx}/{len(entries)}] {channel_name}  (group: {group})")
        print(f"Wrapper : {wrapper_url}")

        inner_urls = get_stream_urls_from_wrapper(wrapper_url)
        if not inner_urls:
            print("  ❌ No inner URLs found")
            report.append(f"[INVALID] {wrapper_url} - {channel_name} (no streams)")
            continue

        print(f"  Inner URLs : {len(inner_urls)} found")
        valid_stream = None

        for si, stream_url in enumerate(inner_urls, 1):
            print(f"  [{si}/{len(inner_urls)}] {stream_url}")
            if check_stream(stream_url):
                print(f"  ✅ Valid")
                valid_stream = stream_url
                break
            else:
                print(f"  ❌ Failed")

        if valid_stream:
            # Construct proxy URL
            # Original logic: http://wrapper/live/user/pass/<encoded channel id>
            # Here we use the tvg-id as the identifier (you can change to something else)
            channel_id = attrs.get('tvg-id', str(idx))
            proxy_url = f"{WRAPPER_BASE_URL}/live/{WRAPPER_USER}/{WRAPPER_PASS}/{channel_id}"

            # Build final entry
            final_extinf = f'#EXTINF:-1 tvg-id="{attrs.get("tvg-id", "")}" tvg-name="{attrs.get("tvg-name", "")}" tvg-logo="{attrs.get("tvg-logo", "")}" group-title="{group}",{channel_name}'
            valid_entries.append(final_extinf)
            valid_entries.append(proxy_url)

            report.append(f"[VALID] {proxy_url} - {channel_name}")
        else:
            report.append(f"[INVALID] {wrapper_url} - {channel_name} (all inner URLs failed)")

    # Write final playlist
    with open(OUTPUT_PLAYLIST, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        f.write("\n".join(valid_entries) + "\n")

    # Write report
    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        f.write("\n".join(report) + "\n")

    print(f"\nDone. {len(valid_entries)//2} valid channels written to {OUTPUT_PLAYLIST}")

if __name__ == "__main__":
    main()
