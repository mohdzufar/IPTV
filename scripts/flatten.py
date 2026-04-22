#!/usr/bin/env python3
"""
IPTV Playlist Flattener – Player‑Like Validation (No HEAD/Deep Checks)
- Tests candidates by fetching playlists and checking for error pages.
- Media playlists are accepted immediately after successful fetch (no segment testing).
- Master playlists are followed to the first variant, which is then tested.
- Comments out both #EXTINF and URL if all candidates fail.
- Handles HLS master/variant, DASH, and direct streams.
"""

import urllib.request
import urllib.error
import urllib.parse
import sys
import time
import io
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SOURCE_FILE = "Channels/Flatten.m3u8"
OUTPUT_FILE = "Main.m3u8"

TIMEOUT = 15               # Seconds per request
MAX_RETRIES = 1
RETRY_DELAY = 2
MAX_RECURSION_DEPTH = 5    # Prevent infinite loops
PARALLEL_WORKERS = 4       # Concurrent candidate tests per channel
VERBOSE = True             # Show detailed validation steps

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': '*/*',
    'Accept-Encoding': 'identity',
    'Connection': 'keep-alive'
}

# -------------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------------
def log_progress(channel_num, total_channels, message):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] Ch {channel_num}/{total_channels}: {message}")

def log_detail(message):
    if VERBOSE:
        timestamp = time.strftime("%H:%M:%S")
        print(f"      [{timestamp}] {message}")

def safe_urljoin(base, url):
    if url.startswith('//'):
        parsed = urlparse(base)
        return f"{parsed.scheme}:{url}"
    return urljoin(base, url)

def fetch_url(url, timeout=TIMEOUT, max_retries=MAX_RETRIES, method='GET', head_only=False, chunk_size=None):
    headers = HEADERS.copy()
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                final_url = resp.geturl()
                if head_only:
                    return None, True, final_url
                if chunk_size:
                    data = resp.read(chunk_size)
                else:
                    data = resp.read()
                return data, True, final_url
        except Exception as e:
            log_detail(f"Fetch attempt {attempt+1} failed: {str(e)[:50]}")
            if attempt < max_retries:
                time.sleep(RETRY_DELAY)
            else:
                return None, False, url

def is_error_page(data):
    if not data:
        return False
    try:
        text = data[:1000].decode('utf-8', errors='ignore').lower()
        indicators = ['<html', '<!doctype', '404 not found', '403 forbidden',
                      'access denied', 'error', 'unauthorized']
        return any(ind in text for ind in indicators)
    except:
        return False

def is_playlist_content(data):
    try:
        preview = data[:500].decode('utf-8', errors='ignore')
        return '#EXTM3U' in preview or '<MPD' in preview
    except:
        return False

def is_master_playlist(content):
    try:
        return b'#EXT-X-STREAM-INF' in content
    except:
        return False

def extract_first_variant_url(content, base_url):
    try:
        text = content.decode('utf-8', errors='ignore')
    except:
        return None

    if '#EXT-X-STREAM-INF' in text:
        lines = text.splitlines()
        capture = False
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                if line.startswith('#EXT-X-STREAM-INF'):
                    capture = True
                continue
            if capture:
                variant = safe_urljoin(base_url, line)
                log_detail(f"Extracted HLS variant: {variant[:80]}...")
                return variant
        return None

    if '<MPD' in text:
        try:
            clean = text.lstrip()
            if clean.startswith('<?xml'):
                end = clean.find('?>')
                if end != -1:
                    clean = clean[end+2:].lstrip()
            root = ET.fromstring(clean)
        except:
            return None
        ns = {'mpd': 'urn:mpeg:dash:schema:mpd:2011'}
        base_elem = root.find('.//mpd:BaseURL', ns)
        dash_base = base_elem.text if base_elem is not None else base_url
        seg = root.find('.//mpd:SegmentTemplate', ns)
        if seg is not None:
            init = seg.get('initialization')
            if init:
                variant = safe_urljoin(dash_base, init)
                log_detail(f"Extracted DASH init: {variant[:80]}...")
                return variant
            media = seg.get('media')
            if media:
                test = media.replace('$Number%09d$', '000000001').replace('$Number$', '1')
                variant = safe_urljoin(dash_base, test)
                log_detail(f"Extracted DASH segment: {variant[:80]}...")
                return variant
    return None

def test_stream_playable(url, depth=0):
    """
    Recursive test that mimics a real player:
    - Fetches URL, checks for error page.
    - For master playlists, follows first variant.
    - For media playlists, accepts immediately (no segment testing).
    - For direct streams, accepts if not an error page.
    """
    if depth > MAX_RECURSION_DEPTH:
        log_detail(f"Max recursion depth reached")
        return False

    log_detail(f"Testing URL: {url[:100]}...")
    data, success, final_url = fetch_url(url)
    if not success or not data:
        log_detail(f"Failed to fetch URL")
        return False

    if is_error_page(data):
        log_detail(f"Response appears to be an error page (HTML)")
        return False

    if is_playlist_content(data):
        log_detail(f"Detected playlist content")
        if is_master_playlist(data):
            log_detail(f"Master playlist detected")
            variant_url = extract_first_variant_url(data, final_url)
            if variant_url:
                log_detail(f"Testing variant stream...")
                result = test_stream_playable(variant_url, depth + 1)
                log_detail(f"Variant test result: {'PASS' if result else 'FAIL'}")
                return result
            else:
                log_detail(f"No variant URL found in master playlist")
                return False
        else:
            # Media playlist – accept immediately (like VLC does)
            log_detail(f"Media playlist detected – accepting as playable (no segment check)")
            return True

    # Direct stream – accept if not an error page
    log_detail(f"Direct stream detected – accepting as playable")
    return True

def test_candidate(url):
    log_detail(f"--- Testing candidate: {url[:80]}...")
    result = test_stream_playable(url)
    log_detail(f"Candidate result: {'PASS' if result else 'FAIL'}")
    return result, url

def process_channel(extinf_line, candidates, channel_num, total_channels):
    safe = extinf_line[:50] + "..." if len(extinf_line) > 50 else extinf_line
    log_progress(channel_num, total_channels, f"Testing: {safe}")
    log_detail(f"Channel has {len(candidates)} candidate(s)")

    if PARALLEL_WORKERS > 1:
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
            futures = {executor.submit(test_candidate, url): url for url in candidates}
            for future in as_completed(futures):
                working, final_url = future.result()
                if working:
                    for f in futures:
                        f.cancel()
                    log_progress(channel_num, total_channels, f"✓ Working: {final_url[:60]}...")
                    return extinf_line, final_url
    else:
        for idx, url in enumerate(candidates, 1):
            log_detail(f"Testing candidate {idx}/{len(candidates)}")
            working, _ = test_candidate(url)
            if working:
                log_progress(channel_num, total_channels, f"✓ Candidate {idx} works")
                return extinf_line, url

    log_progress(channel_num, total_channels, "✗ All failed; commenting out entire entry")
    commented_extinf = f"##{extinf_line}"
    commented_url = f"##{candidates[0]}"
    return commented_extinf, commented_url

def process_source_playlist(source_path):
    with open(source_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    channel_count = sum(1 for line in lines if line.startswith('#EXTINF:'))

    flattened = []
    i = 0
    current = 0

    while i < len(lines):
        line = lines[i].rstrip('\n\r')

        if line.startswith('#EXTM3U'):
            flattened.append(line)
            i += 1
            continue

        if line.startswith('#EXTINF:'):
            extinf = line
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1

            candidates = []
            while i < len(lines):
                nxt = lines[i].rstrip('\n\r').strip()
                if not nxt:
                    i += 1
                    continue
                if nxt.startswith('#EXTINF:') or nxt.startswith('#EXTM3U'):
                    break
                if nxt.startswith('#'):
                    i += 1
                    continue
                if nxt.startswith(('http://', 'https://')):
                    candidates.append(nxt)
                i += 1

            if candidates:
                current += 1
                final_extinf, final_url = process_channel(extinf, candidates, current, channel_count)
                flattened.append(final_extinf)
                flattened.append(final_url)
            else:
                flattened.append(extinf)
        else:
            flattened.append(line)
            i += 1

    return flattened

def main():
    print("=" * 60)
    print("IPTV Playlist Flattener – Player‑Like Validation (No HEAD Checks)")
    print("=" * 60)

    if not Path(SOURCE_FILE).exists():
        print(f"Error: {SOURCE_FILE} not found.")
        sys.exit(1)

    print(f"Reading {SOURCE_FILE}...")
    start = time.time()
    result = process_source_playlist(SOURCE_FILE)
    elapsed = time.time() - start

    with open(OUTPUT_FILE, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(result))

    print("\n" + "=" * 60)
    print(f"Flattened playlist written to {OUTPUT_FILE}")
    print(f"   Total lines: {len(result)}")
    print(f"   Time taken: {elapsed:.1f} seconds")
    print("=" * 60)

if __name__ == "__main__":
    main()
