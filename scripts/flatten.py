#!/usr/bin/env python3
"""
IPTV Playlist Flattener – Playability-Based Candidate Selection
- Tests candidates in exact order.
- For master/DASH playlists: extracts a variant and tests it for actual video.
- For simple redirect playlists: extracts the embedded URL and tests recursively.
- For direct streams: validates video content signatures.
- Stops at first truly playable candidate; falls back to first URL if none work.
"""

import urllib.request
import urllib.error
import sys
import time
import re
from pathlib import Path
from urllib.parse import urljoin

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SOURCE_FILE = "Channels/Flatten.m3u8"   # Editable source file
OUTPUT_FILE = "Main.m3u8"               # Flattened output for users

CHUNK_SIZE = 262144        # 256 KB for direct stream validation
TIMEOUT = 15               # Seconds to wait for server response
MAX_RETRIES = 2            # Number of retry attempts
RETRY_DELAY = 2            # Seconds between retries

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': '*/*',
    'Accept-Encoding': 'identity',
    'Connection': 'keep-alive'
}

# -------------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------------
def fetch_with_retry(url, timeout=TIMEOUT, max_retries=MAX_RETRIES, chunk_size=None):
    """Fetch URL content with retry logic."""
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if chunk_size:
                    data = resp.read(chunk_size)
                else:
                    data = resp.read()
                return data, True
        except Exception:
            if attempt < max_retries:
                time.sleep(RETRY_DELAY)
            else:
                return None, False

def is_valid_stream_content(data):
    """
    Heuristic: check if downloaded data looks like video/audio.
    Detects MPEG-TS sync byte (0x47) or MP4 'ftyp' box.
    """
    if not data or len(data) < 100:
        return False
    # MPEG-TS sync byte
    if data[0] == 0x47:
        return True
    # MP4 'ftyp' box (usually at offset 4)
    if len(data) > 8 and data[4:8] == b'ftyp':
        return True
    # Check for common error strings
    try:
        text = data.decode('utf-8', errors='ignore')[:200].lower()
        if any(err in text for err in ['error', 'not found', 'unauthorized', 'forbidden']):
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
    """Return True if content starts with <MPD."""
    try:
        return content.decode('utf-8', errors='ignore').lstrip().startswith('<MPD')
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
    """
    Extract the first variant URI from a master playlist or DASH manifest.
    Returns an absolute URL or None.
    """
    try:
        text = content.decode('utf-8', errors='ignore')
    except:
        return None

    # HLS master playlist: find first non-comment line after #EXT-X-STREAM-INF
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

    # DASH manifest: look for BaseURL + SegmentTemplate or direct URL
    if text.lstrip().startswith('<MPD'):
        # Try to find BaseURL
        base_match = re.search(r'<BaseURL>(.*?)</BaseURL>', text)
        dash_base = base_match.group(1) if base_match else base_url

        # Try to find an initialization segment from the first Representation
        init_match = re.search(r'initialization="([^"]+)"', text)
        if init_match:
            return urljoin(dash_base, init_match.group(1))

        # Alternative: look for a media segment template
        media_match = re.search(r'media="([^"]+)"', text)
        if media_match:
            # Replace $Number$ with a small number (e.g., 1) to create a testable URL
            template = media_match.group(1)
            test_url = template.replace('$Number%09d$', '000000001').replace('$Number$', '1')
            return urljoin(dash_base, test_url)

        return None

    return None

def test_variant_playability(variant_url):
    """
    Test a variant URL by downloading a chunk and validating video content.
    Returns True if playable.
    """
    print(f"        🧪 Testing variant: {variant_url[:60]}...")
    data, success = fetch_with_retry(variant_url, chunk_size=CHUNK_SIZE)
    if not success or not data:
        return False
    return is_valid_stream_content(data)

def test_candidate(url):
    """
    Test a single candidate URL for true playability.
    Returns (working: bool, final_url: str)
    """
    print(f"      🔍 Testing: {url[:70]}...")

    # Step 1: Basic reachability (fetch a small chunk)
    data, success = fetch_with_retry(url, chunk_size=CHUNK_SIZE)
    if not success or not data:
        print(f"      ❌ Unreachable or no data")
        return False, url

    # Step 2: Determine if it's a playlist file
    is_playlist = is_playlist_by_content(url, data)

    if is_playlist:
        print(f"      📄 Playlist detected, fetching full content...")
        full_data, _ = fetch_with_retry(url, chunk_size=None)
        if not full_data:
            return False, url

        # Step 3: Check for master/DASH
        master = is_hls_master_playlist(full_data)
        dash = is_dash_manifest(full_data)

        if master or dash:
            print(f"      🎚️ Master/DASH playlist – testing variant playability...")
            variant_url = extract_first_variant_url(full_data, url)
            if variant_url and test_variant_playability(variant_url):
                print(f"      ✅ Master/DASH playlist – variant playable, keeping original")
                return True, url
            else:
                print(f"      ❌ Master/DASH playlist – variant not playable, skipping")
                return False, url

        # Step 4: Simple redirect playlist – extract embedded URL
        lines = full_data.decode('utf-8', errors='ignore').splitlines()
        stream_url = None
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                stream_url = line
                break

        if stream_url:
            print(f"      ➡️ Extracted stream URL: {stream_url[:60]}...")
            # Recursively test the extracted URL
            sub_working, final_url = test_candidate(stream_url)
            return sub_working, final_url
        else:
            print(f"      ❌ No stream URL found in playlist")
            return False, url

    # Step 5: Direct stream – validate content
    if is_valid_stream_content(data):
        print(f"      ✅ Valid stream content ({len(data)} bytes)")
        return True, url
    else:
        print(f"      ❌ Invalid stream content")
        return False, url

def process_channel(extinf_line, candidate_urls):
    """
    Test candidates sequentially. Use the first one that is truly playable.
    If none work, fallback to the first candidate's original URL.
    """
    print(f"  Testing candidates for: {extinf_line[:50]}...")

    for idx, url in enumerate(candidate_urls, 1):
        print(f"    Candidate {idx}: {url[:70]}...")
        working, final_url = test_candidate(url)
        if working:
            print(f"    ✅ Selected candidate {idx}")
            return extinf_line, final_url

    # All failed – fallback to first candidate
    print(f"    ❌ All candidates failed; using first URL as fallback")
    return extinf_line, candidate_urls[0]

def process_source_playlist(source_path):
    """Read the source file, test candidates, and build flattened output."""
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
    print("IPTV Playlist Flattener – Playability‑Based Testing")
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
