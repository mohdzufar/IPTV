#!/usr/bin/env python3
"""
IPTV Playlist Flattener - Universal Playability with MIME Type Validation
- Handles Unicode output safely on Windows.
"""

import urllib.request
import urllib.error
import sys
import time
import re
import io
from pathlib import Path
from urllib.parse import urljoin

# Force UTF-8 output to avoid UnicodeEncodeError on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SOURCE_FILE = "Channels/Flatten.m3u8"
OUTPUT_FILE = "Main.m3u8"

CHUNK_SIZE = 262144        # 256 KB
TIMEOUT = 15
MAX_RETRIES = 2
RETRY_DELAY = 2

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': '*/*',
    'Accept-Encoding': 'identity',
    'Connection': 'keep-alive'
}

VALID_HLS_MIME = {'application/vnd.apple.mpegurl', 'audio/mpegurl', 'application/x-mpegURL'}
VALID_DASH_MIME = {'application/dash+xml'}

# -------------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------------
def fetch_with_retry(url, timeout=TIMEOUT, max_retries=MAX_RETRIES, chunk_size=None):
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content_type = resp.headers.get('Content-Type', '').lower()
                if chunk_size:
                    data = resp.read(chunk_size)
                else:
                    data = resp.read()
                return data, True, content_type
        except Exception:
            if attempt < max_retries:
                time.sleep(RETRY_DELAY)
            else:
                return None, False, ''

def is_valid_media_data(data):
    if not data or len(data) < 100:
        return False
    if data[0] == 0x47:
        return True
    if len(data) > 8 and data[4:8] == b'ftyp':
        return True
    try:
        text = data[:200].decode('utf-8', errors='ignore').lower()
        if any(err in text for err in ['<!doctype', '<html', '404 not found', 'error', 'unauthorized']):
            return False
    except:
        pass
    return len(data) >= 1024

def is_hls_master_playlist(content):
    try:
        return '#EXT-X-STREAM-INF' in content.decode('utf-8', errors='ignore')
    except:
        return False

def is_dash_manifest(content):
    try:
        return content.decode('utf-8', errors='ignore').lstrip().startswith('<MPD')
    except:
        return False

def is_playlist_by_content(url, data):
    url_lower = url.lower()
    if any(url_lower.endswith(ext) for ext in ('.m3u', '.m3u8', '.mpd')):
        return True
    try:
        preview = data[:200].decode('utf-8', errors='ignore')
        return '#EXT' in preview or '<MPD' in preview
    except:
        return False

def extract_first_variant_url(content, base_url):
    try:
        text = content.decode('utf-8', errors='ignore')
    except:
        return None

    if '#EXT-X-STREAM-INF' in text:
        lines = text.splitlines()
        capture_next = False
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                if line.startswith('#EXT-X-STREAM-INF'):
                    capture_next = True
                continue
            if capture_next:
                return urljoin(base_url, line)
        return None

    if text.lstrip().startswith('<MPD'):
        base_match = re.search(r'<BaseURL>(.*?)</BaseURL>', text)
        dash_base = base_match.group(1) if base_match else base_url

        init_match = re.search(r'initialization="([^"]+)"', text)
        if init_match:
            return urljoin(dash_base, init_match.group(1))

        media_match = re.search(r'media="([^"]+)"', text)
        if media_match:
            template = media_match.group(1)
            test_url = template.replace('$Number%09d$', '000000001').replace('$Number$', '1')
            return urljoin(dash_base, test_url)

        return None

    return None

def test_candidate(url):
    print(f"      Testing: {url[:70]}...")

    data, success, content_type = fetch_with_retry(url, chunk_size=CHUNK_SIZE)
    if not success or not data:
        print(f"      Unreachable or no data")
        return False, url

    is_playlist = is_playlist_by_content(url, data)

    if is_playlist:
        print(f"      Playlist detected, fetching full content...")
        full_data, _, full_content_type = fetch_with_retry(url, chunk_size=None)
        if not full_data:
            return False, url

        master = is_hls_master_playlist(full_data)
        dash = is_dash_manifest(full_data)

        if master or dash:
            if master and full_content_type not in VALID_HLS_MIME:
                print(f"      Rejected: HLS master served as '{full_content_type}'")
                return False, url
            if dash and full_content_type not in VALID_DASH_MIME:
                print(f"      Rejected: DASH manifest served as '{full_content_type}'")
                return False, url

            print(f"      Master/DASH playlist with correct MIME - testing variant...")
            variant_url = extract_first_variant_url(full_data, url)
            if variant_url:
                variant_working, _ = test_candidate(variant_url)
                if variant_working:
                    print(f"      Variant playable, keeping original")
                    return True, url
                else:
                    print(f"      Variant not playable")
            else:
                print(f"      Could not extract variant URL")
            return False, url

        lines = full_data.decode('utf-8', errors='ignore').splitlines()
        stream_url = None
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                stream_url = line
                break

        if stream_url:
            print(f"      Extracted stream URL: {stream_url[:60]}...")
            sub_working, final_url = test_candidate(stream_url)
            return sub_working, final_url
        else:
            print(f"      No stream URL found in playlist")
            return False, url

    if is_valid_media_data(data):
        print(f"      Valid media stream ({len(data)} bytes)")
        return True, url
    else:
        print(f"      Invalid media content")
        return False, url

def process_channel(extinf_line, candidate_urls):
    # Safely truncate extinf_line for display
    safe_line = extinf_line[:50] + "..." if len(extinf_line) > 50 else extinf_line
    print(f"  Testing candidates for: {safe_line}")

    for idx, url in enumerate(candidate_urls, 1):
        print(f"    Candidate {idx}: {url[:70]}...")
        working, final_url = test_candidate(url)
        if working:
            print(f"    Selected candidate {idx}")
            return extinf_line, final_url

    print(f"    All candidates failed; using first URL as fallback")
    return extinf_line, candidate_urls[0]

def process_source_playlist(source_path):
    with open(source_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    flattened = []
    i = 0
    channel_count = 0

    while i < len(lines):
        line = lines[i].rstrip('\n\r')

        if line.startswith('#EXTM3U'):
            flattened.append(line)
            i += 1
            continue

        if line.startswith('#EXTINF:'):
            extinf_line = line
            i += 1

            while i < len(lines) and not lines[i].strip():
                i += 1

            candidates = []
            while i < len(lines):
                next_line = lines[i].rstrip('\n\r').strip()
                if not next_line:
                    i += 1
                    continue
                if next_line.startswith('#EXTINF:') or next_line.startswith('#EXTM3U'):
                    break
                candidates.append(next_line)
                i += 1

            if not candidates:
                flattened.append(extinf_line)
                continue

            channel_count += 1
            print(f"\nChannel {channel_count}:")
            final_extinf, final_url = process_channel(extinf_line, candidates)

            flattened.append(final_extinf)
            flattened.append(final_url)
        else:
            flattened.append(line)
            i += 1

    return flattened

def main():
    print("=" * 60)
    print("IPTV Playlist Flattener - MIME Type Validation")
    print("=" * 60)

    if not Path(SOURCE_FILE).exists():
        print(f"Error: {SOURCE_FILE} not found.")
        sys.exit(1)

    print(f"Reading {SOURCE_FILE}...")
    flattened_lines = process_source_playlist(SOURCE_FILE)

    output_path = Path(OUTPUT_FILE)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(flattened_lines))

    print("\n" + "=" * 60)
    print(f"Flattened playlist written to {OUTPUT_FILE}")
    print(f"   Total lines: {len(flattened_lines)}")
    print("=" * 60)

if __name__ == "__main__":
    main()
