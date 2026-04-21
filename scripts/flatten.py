#!/usr/bin/env python3
"""
IPTV Playlist Flattener – Robust Timeout Edition
- Tests all candidates in Flatten.m3u8 with enforced per-candidate timeout.
- Uses subprocess isolation to prevent hanging requests from stalling the entire run.
- Handles Master HLS, DASH, and direct streams.
- Outputs the first working candidate URL; if none, comments out the first candidate.
"""

import urllib.request
import urllib.error
import urllib.parse
import sys
import time
import re
import io
import socket
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
import multiprocessing as mp

# Force UTF-8 output to avoid UnicodeEncodeError on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SOURCE_FILE = "Channels/Flatten.m3u8"
OUTPUT_FILE = "Main.m3u8"

CHUNK_SIZE = 262144        # 256 KB
TIMEOUT = 20               # Per-request timeout (seconds)
MAX_RETRIES = 1            # Only one retry to avoid wasting time
RETRY_DELAY = 1
MAX_RECURSION_DEPTH = 5    # Prevent infinite loops in nested playlists
PARALLEL_WORKERS = 2       # Reduced to prevent network congestion
CANDIDATE_TIMEOUT = 45     # Maximum seconds allowed per candidate (including recursion)

# Global socket timeout as a last-resort safety net
socket.setdefaulttimeout(TIMEOUT + 5)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': '*/*',
    'Accept-Encoding': 'identity',
    'Connection': 'keep-alive'
}

VALID_HLS_MIME = {'application/vnd.apple.mpegurl', 'audio/mpegurl', 'application/x-mpegURL'}
VALID_DASH_MIME = {'application/dash+xml'}

# Media file signatures
MEDIA_SIGNATURES = [
    (0, b'\x47'),                # MPEG-TS
    (4, b'ftyp'),                # MP4/MOV
    (0, b'\x1a\x45\xdf\xa3'),    # WebM / Matroska
    (0, b'FLV'),                 # FLV
]

# -------------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------------
def log_progress(channel_num, total_channels, message):
    """Print a timestamped progress message."""
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] Ch {channel_num}/{total_channels}: {message}")

def safe_urljoin(base, url):
    """Join a base URL with a relative URL, handling protocol‑relative URLs."""
    if url.startswith('//'):
        parsed_base = urlparse(base)
        return f"{parsed_base.scheme}:{url}"
    return urljoin(base, url)

def fetch_with_retry(url, timeout=TIMEOUT, max_retries=MAX_RETRIES, chunk_size=None, method='GET'):
    """Fetch URL content with retries. Returns (data, success, content_type)."""
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content_type = resp.headers.get('Content-Type', '').lower()
                if chunk_size:
                    data = resp.read(chunk_size)
                else:
                    data = resp.read()
                return data, True, content_type
        except Exception as e:
            if attempt < max_retries:
                time.sleep(RETRY_DELAY)
            else:
                return None, False, ''

def is_valid_media_data(data):
    """Check if downloaded data looks like actual video/audio."""
    if not data or len(data) < 100:
        return False
    for offset, signature in MEDIA_SIGNATURES:
        if len(data) > offset + len(signature) and data[offset:offset+len(signature)] == signature:
            return True
    # Reject obvious HTML error pages
    try:
        text = data[:200].decode('utf-8', errors='ignore').lower()
        if any(err in text for err in ['<!doctype', '<html', '404 not found', 'error', 'unauthorized']):
            return False
    except:
        pass
    return len(data) >= 1024

def is_hls_master_playlist(content):
    """Return True if content contains #EXT-X-STREAM-INF."""
    try:
        return '#EXT-X-STREAM-INF' in content.decode('utf-8', errors='ignore')
    except:
        return False

def is_dash_manifest(content):
    """Return True if content is a DASH manifest."""
    try:
        text = content.decode('utf-8', errors='ignore').lstrip()
        if text.startswith('<?xml'):
            end_idx = text.find('?>')
            if end_idx != -1:
                text = text[end_idx + 2:].lstrip()
        return text.startswith('<MPD')
    except:
        return False

def is_playlist_by_content(url, data):
    """Determine if the URL points to a playlist file."""
    url_lower = url.lower()
    if any(url_lower.endswith(ext) for ext in ('.m3u', '.m3u8', '.mpd')):
        return True
    try:
        preview = data[:200].decode('utf-8', errors='ignore')
        return '#EXT' in preview or '<MPD' in preview
    except:
        return False

def extract_first_variant_url(content, base_url):
    """Extract the first variant URI from a master HLS or DASH manifest."""
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
                return safe_urljoin(base_url, line)
        return None

    if '<MPD' in text:
        try:
            clean_text = text.lstrip()
            if clean_text.startswith('<?xml'):
                end_idx = clean_text.find('?>')
                if end_idx != -1:
                    clean_text = clean_text[end_idx + 2:].lstrip()
            root = ET.fromstring(clean_text)
        except ET.ParseError:
            return None

        ns = {'mpd': 'urn:mpeg:dash:schema:mpd:2011'}
        base_url_elem = root.find('.//mpd:BaseURL', ns)
        dash_base = base_url_elem.text if base_url_elem is not None else base_url

        segment_template = root.find('.//mpd:SegmentTemplate', ns)
        if segment_template is not None:
            init = segment_template.get('initialization')
            if init:
                return safe_urljoin(dash_base, init)
            media = segment_template.get('media')
            if media:
                test_url = media.replace('$Number%09d$', '000000001').replace('$Number$', '1')
                return safe_urljoin(dash_base, test_url)
        return None
    return None

def test_stream_playability(url, depth=0):
    """
    Core test for any stream URL. Returns (working: bool).
    """
    if depth > MAX_RECURSION_DEPTH:
        return False

    data, success, content_type = fetch_with_retry(url, chunk_size=CHUNK_SIZE)
    if not success or not data:
        return False

    is_playlist = is_playlist_by_content(url, data)

    if is_playlist:
        full_data, _, _ = fetch_with_retry(url, chunk_size=None)
        if not full_data:
            return False

        master = is_hls_master_playlist(full_data)
        dash = is_dash_manifest(full_data)

        if master or dash:
            variant_url = extract_first_variant_url(full_data, url)
            if variant_url:
                return test_stream_playability(variant_url, depth + 1)
            else:
                return False

        # Simple redirect playlist – test internal URLs sequentially
        lines = full_data.decode('utf-8', errors='ignore').splitlines()
        internal_urls = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('<?xml') or line.startswith('<MPD') or line.startswith('<Period'):
                continue
            if not (line.startswith('http://') or line.startswith('https://')):
                continue
            try:
                line.encode('ascii')
            except UnicodeEncodeError:
                continue
            internal_urls.append(line)

        for internal_url in internal_urls:
            if test_stream_playability(internal_url, depth + 1):
                return True
        return False

    # Direct stream
    return is_valid_media_data(data)

def test_candidate_with_timeout(url):
    """
    Wrapper that runs test_candidate in a separate process with a timeout.
    Returns (working: bool, final_url: str).
    """
    def target(queue, url):
        try:
            result = test_stream_playability(url)
            queue.put((result, url))
        except Exception:
            queue.put((False, url))

    queue = mp.Queue()
    proc = mp.Process(target=target, args=(queue, url))
    proc.start()
    proc.join(CANDIDATE_TIMEOUT)

    if proc.is_alive():
        proc.terminate()
        proc.join()
        return False, url
    else:
        try:
            working, final_url = queue.get_nowait()
            return working, final_url
        except:
            return False, url

def test_candidate(url):
    """Alias for the process‑based timeout version."""
    return test_candidate_with_timeout(url)

def process_channel(extinf_line, candidate_urls, channel_num, total_channels):
    """
    Test candidates for a channel with timeout protection.
    Returns the #EXTINF line and the URL to output (or commented URL).
    """
    safe_line = extinf_line[:50] + "..." if len(extinf_line) > 50 else extinf_line
    log_progress(channel_num, total_channels, f"Testing: {safe_line}")

    if PARALLEL_WORKERS > 1:
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
            future_to_url = {executor.submit(test_candidate, url): url for url in candidate_urls}
            for future in as_completed(future_to_url):
                try:
                    working, final_url = future.result(timeout=CANDIDATE_TIMEOUT + 5)
                except FuturesTimeoutError:
                    working, final_url = False, future_to_url[future]
                if working:
                    for f in future_to_url:
                        f.cancel()
                    log_progress(channel_num, total_channels, f"✓ Working: {final_url[:60]}...")
                    return extinf_line, final_url
    else:
        for idx, url in enumerate(candidate_urls, 1):
            working, final_url = test_candidate(url)
            if working:
                log_progress(channel_num, total_channels, f"✓ Candidate {idx} works")
                return extinf_line, final_url

    # All failed
    log_progress(channel_num, total_channels, "✗ All candidates failed; commenting out")
    commented_url = f"##{candidate_urls[0]}"
    return extinf_line, commented_url

def process_source_playlist(source_path):
    """Read the source file, test candidates, and build flattened output."""
    with open(source_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Pre-scan to count channels for progress
    channel_count = 0
    i = 0
    while i < len(lines):
        if lines[i].startswith('#EXTINF:'):
            channel_count += 1
            i += 1
            while i < len(lines) and not lines[i].startswith('#EXTINF:') and not lines[i].startswith('#EXTM3U'):
                i += 1
        else:
            i += 1

    flattened = []
    i = 0
    current_channel = 0

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
                if next_line.startswith('#'):
                    i += 1
                    continue
                if next_line.startswith('http://') or next_line.startswith('https://'):
                    candidates.append(next_line)
                i += 1

            if not candidates:
                flattened.append(extinf_line)
                continue

            current_channel += 1
            final_extinf, final_url = process_channel(extinf_line, candidates, current_channel, channel_count)

            flattened.append(final_extinf)
            flattened.append(final_url)
        else:
            flattened.append(line)
            i += 1

    return flattened

def main():
    print("=" * 60)
    print("IPTV Playlist Flattener – Robust Timeout Edition")
    print("=" * 60)

    if not Path(SOURCE_FILE).exists():
        print(f"Error: {SOURCE_FILE} not found.")
        sys.exit(1)

    print(f"Reading {SOURCE_FILE}...")
    start_time = time.time()
    flattened_lines = process_source_playlist(SOURCE_FILE)
    elapsed = time.time() - start_time

    output_path = Path(OUTPUT_FILE)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(flattened_lines))

    print("\n" + "=" * 60)
    print(f"Flattened playlist written to {OUTPUT_FILE}")
    print(f"   Total lines: {len(flattened_lines)}")
    print(f"   Time taken: {elapsed:.1f} seconds")
    print("=" * 60)

if __name__ == "__main__":
    mp.freeze_support()   # Required for multiprocessing on Windows
    main()
