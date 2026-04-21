#!/usr/bin/env python3
"""
IPTV Playlist Flattener – IPTV App Style Validator (Fixed Timeout)
- Tests URLs exactly as an IPTV player would: fetch, check for error page,
  follow master playlists to first variant, verify media segment reachability.
- Includes proper timeout handling to prevent hanging.
- Handles relative URLs correctly.
- Outputs first working candidate; comments out if all fail.
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

def safe_urljoin(base, url):
    """Join base URL with a relative or protocol-relative URL."""
    if url.startswith('//'):
        parsed = urlparse(base)
        return f"{parsed.scheme}:{url}"
    return urljoin(base, url)

def fetch_url(url, timeout=TIMEOUT, max_retries=MAX_RETRIES, method='GET', head_only=False):
    """
    Fetch URL and return (data, success, final_url).
    If head_only is True, use HEAD request and return (None, success, final_url).
    """
    headers = HEADERS.copy()
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                final_url = resp.geturl()
                if head_only:
                    return None, True, final_url
                # Limit read to 256KB to prevent memory issues
                data = resp.read(262144)
                return data, True, final_url
        except Exception:
            if attempt < max_retries:
                time.sleep(RETRY_DELAY)
            else:
                return None, False, url

def is_error_page(data):
    """Heuristic to detect HTML error pages."""
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
    """Check if data looks like an HLS or DASH playlist."""
    try:
        preview = data[:500].decode('utf-8', errors='ignore')
        return '#EXTM3U' in preview or '<MPD' in preview
    except:
        return False

def is_master_playlist(content):
    """True if content contains #EXT-X-STREAM-INF."""
    try:
        return b'#EXT-X-STREAM-INF' in content
    except:
        return False

def extract_first_variant_url(content, base_url):
    """Extract first variant from master HLS or DASH manifest."""
    try:
        text = content.decode('utf-8', errors='ignore')
    except:
        return None

    # HLS Master
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
                return safe_urljoin(base_url, line)
        return None

    # DASH
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
                return safe_urljoin(dash_base, init)
            media = seg.get('media')
            if media:
                test = media.replace('$Number%09d$', '000000001').replace('$Number$', '1')
                return safe_urljoin(dash_base, test)
    return None

def extract_first_segment_url(content, base_url):
    """Extract first non-comment line from a media playlist."""
    try:
        lines = content.decode('utf-8', errors='ignore').splitlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            return safe_urljoin(base_url, line)
    except:
        pass
    return None

def is_url_reachable(url, head_only=True):
    """Check if URL is reachable (HEAD request) and not an error page."""
    data, success, final_url = fetch_url(url, method='HEAD' if head_only else 'GET',
                                         head_only=head_only)
    if not success:
        return False
    # For HEAD requests we can't check error page; assume success
    if head_only:
        return True
    return not is_error_page(data)

def test_stream_playable(url, depth=0):
    """
    Recursively test if a stream URL is playable.
    Returns True if reachable and not an error.
    """
    if depth > MAX_RECURSION_DEPTH:
        return False

    data, success, final_url = fetch_url(url)
    if not success or not data:
        return False

    if is_error_page(data):
        return False

    if is_playlist_content(data):
        if is_master_playlist(data):
            variant_url = extract_first_variant_url(data, final_url)
            if variant_url:
                return test_stream_playable(variant_url, depth + 1)
            return False
        else:
            # Media playlist: test first segment
            segment_url = extract_first_segment_url(data, final_url)
            if segment_url:
                return is_url_reachable(segment_url, head_only=False)  # GET to check error page
            return False

    # Direct stream – already passed error page check
    return True

def test_candidate(url):
    """Wrapper for parallel execution."""
    return test_stream_playable(url), url

def process_channel(extinf_line, candidates, channel_num, total_channels):
    safe = extinf_line[:50] + "..." if len(extinf_line) > 50 else extinf_line
    log_progress(channel_num, total_channels, f"Testing: {safe}")

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
            working, _ = test_candidate(url)
            if working:
                log_progress(channel_num, total_channels, f"✓ Candidate {idx} works")
                return extinf_line, url

    log_progress(channel_num, total_channels, "✗ All failed; commenting out")
    return extinf_line, f"##{candidates[0]}"

def process_source_playlist(source_path):
    with open(source_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Count channels for progress
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
    print("IPTV Playlist Flattener – IPTV App Style Validator")
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
