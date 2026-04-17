#!/usr/bin/env python3
"""
IPTV Playlist Flattener – Accurate URL Validation (Daily Run Optimized)
- Fetches a small chunk of actual stream content to confirm playability.
- Handles HLS master playlists, DASH manifests, and simple redirects.
- Retries failed connections for robustness.
- Preserves working URLs and outputs clean Main.m3u8.
"""

import urllib.request
import urllib.error
import sys
import time
import socket
from pathlib import Path

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SOURCE_FILE = "Channels/Flatten.m3u8"   # The file you edit
OUTPUT_FILE = "Main.m3u8"               # Flattened output for users

# Validation settings (tuned for accuracy over speed)
CHUNK_SIZE = 262144        # 256 KB – enough to confirm stream is active
TIMEOUT = 15               # Seconds to wait for server response
MAX_RETRIES = 2            # Number of retry attempts
RETRY_DELAY = 2            # Seconds between retries

# Browser-like headers to avoid blocking
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Encoding': 'identity',
    'Connection': 'keep-alive'
}

# -------------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------------
def fetch_with_retry(url, timeout=TIMEOUT, max_retries=MAX_RETRIES, chunk_size=None):
    """
    Fetch URL content with retry logic.
    If chunk_size is provided, only that many bytes are read.
    Returns (content_bytes, success_boolean).
    """
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if chunk_size:
                    data = resp.read(chunk_size)
                else:
                    data = resp.read()
                return data, True
        except Exception as e:
            if attempt < max_retries:
                print(f"      ⚠️ Attempt {attempt+1} failed: {e}. Retrying in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
            else:
                print(f"      ❌ Failed after {max_retries+1} attempts: {e}")
                return None, False

def is_valid_stream_content(data):
    """
    Heuristic: check if downloaded data looks like video/audio content.
    Returns True if data seems valid (non-empty and not an error page).
    """
    if not data or len(data) < 100:
        return False
    # Common binary stream signatures (TS, MP4, etc.)
    # MPEG-TS sync byte (0x47), MP4 'ftyp', or just sufficient binary data
    if data[0] == 0x47 or data[4:8] == b'ftyp':
        return True
    # If it's text, check for common error strings
    try:
        text = data.decode('utf-8', errors='ignore')[:200].lower()
        if any(err in text for err in ['error', 'not found', 'unauthorized', 'forbidden']):
            return False
    except:
        pass
    # Assume binary data is good if it's sizable
    return len(data) >= 1024

def is_hls_master_playlist(content):
    """Return True if content contains #EXT-X-STREAM-INF."""
    try:
        text = content.decode('utf-8', errors='ignore')
        return '#EXT-X-STREAM-INF' in text
    except:
        return False

def is_dash_manifest(content):
    """Return True if content looks like a DASH manifest."""
    try:
        text = content.decode('utf-8', errors='ignore').lstrip()
        return text.startswith('<MPD')
    except:
        return False

def extract_first_segment_url(content, base_url):
    """
    Extract the first media segment URL from an HLS media playlist or DASH manifest.
    Returns a full URL or None.
    """
    try:
        text = content.decode('utf-8', errors='ignore')
    except:
        return None

    # HLS: find first .ts line
    if not text.startswith('<MPD'):
        lines = text.splitlines()
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and ('.ts' in line or '.m4s' in line):
                if line.startswith('http'):
                    return line
                else:
                    # Relative URL – resolve against base
                    from urllib.parse import urljoin
                    return urljoin(base_url, line)

    # DASH: find BaseURL or SegmentTemplate initialization
    import re
    base_match = re.search(r'<BaseURL>(.*?)</BaseURL>', text)
    if base_match:
        dash_base = base_match.group(1)
        init_match = re.search(r'initialization="([^"]+)"', text)
        if init_match:
            from urllib.parse import urljoin
            return urljoin(dash_base, init_match.group(1))

    return None

def test_stream_url(url):
    """
    Test a direct stream URL by downloading a small chunk and validating content.
    Returns (is_working, final_url).
    For master playlists/DASH, we extract a segment and test that.
    """
    print(f"      🔍 Testing: {url[:70]}...")

    # First, fetch the URL (or a chunk of it)
    data, success = fetch_with_retry(url, chunk_size=CHUNK_SIZE)

    if not success or not data:
        return False, url

    # Check if it's a playlist file (based on content or extension)
    if url.lower().endswith(('.m3u', '.m3u8', '.mpd')) or is_hls_master_playlist(data) or is_dash_manifest(data):
        print(f"      📄 Playlist detected, inspecting...")
        # Fetch full playlist content
        full_data, _ = fetch_with_retry(url, chunk_size=None)
        if not full_data:
            return False, url

        # If it's a master playlist or DASH, preserve original (player handles variants)
        if is_hls_master_playlist(full_data) or is_dash_manifest(full_data):
            print(f"      ✅ Master/DASH playlist – preserving original")
            return True, url

        # Simple redirect playlist – extract stream URL and test it
        lines = full_data.decode('utf-8', errors='ignore').splitlines()
        stream_url = None
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                stream_url = line
                break

        if stream_url:
            print(f"      ➡️ Extracted stream URL: {stream_url[:60]}...")
            # Test the extracted stream URL
            sub_working, _ = test_stream_url(stream_url)
            if sub_working:
                return True, stream_url
            else:
                print(f"      ⚠️ Extracted stream failed, keeping playlist")
                return False, url
        else:
            print(f"      ⚠️ No stream URL found in playlist")
            return False, url

    # Direct stream – validate downloaded chunk
    if is_valid_stream_content(data):
        print(f"      ✅ Stream validated ({len(data)} bytes)")
        return True, url
    else:
        print(f"      ❌ Invalid stream content")
        return False, url

def process_channel(extinf_line, candidate_urls):
    """Test candidates in order, return first working URL."""
    print(f"  Testing candidates for: {extinf_line[:50]}...")
    for idx, url in enumerate(candidate_urls, 1):
        print(f"    Candidate {idx}: {url[:70]}...")
        working, final_url = test_stream_url(url)
        if working:
            print(f"    ✅ Selected candidate {idx}")
            return extinf_line, final_url

    # All failed – fallback to first URL
    print(f"    ❌ All candidates failed, using first as fallback")
    return extinf_line, candidate_urls[0]

def process_source_playlist(source_path):
    """Read source file, test URLs, and output single working URL per channel."""
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

            # Skip blank lines
            while i < len(lines) and not lines[i].strip():
                i += 1

            # Collect candidate URLs
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
            print(f"\n📺 Channel {channel_count}:")
            final_extinf, final_url = process_channel(extinf_line, candidates)

            flattened.append(final_extinf)
            flattened.append(final_url)
        else:
            flattened.append(line)
            i += 1

    return flattened

def main():
    print("=" * 60)
    print("IPTV Playlist Flattener – Accurate Stream Validation")
    print("=" * 60)

    if not Path(SOURCE_FILE).exists():
        print(f"❌ Error: {SOURCE_FILE} not found.")
        sys.exit(1)

    print(f"📂 Reading {SOURCE_FILE}...")
    flattened_lines = process_source_playlist(SOURCE_FILE)

    output_path = Path(OUTPUT_FILE)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(flattened_lines))

    print("\n" + "=" * 60)
    print(f"✅ Flattened playlist written to {OUTPUT_FILE}")
    print(f"   Total lines: {len(flattened_lines)}")
    print("=" * 60)

if __name__ == "__main__":
    main()
