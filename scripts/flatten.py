#!/usr/bin/env python3
"""
IPTV Playlist Flattener – Final Version
- Tests candidates in the exact order they appear.
- Skips unreachable URLs.
- Preserves master playlists (#EXT-X-STREAM-INF) and DASH manifests (<MPD).
- Flattens simple redirect playlists to the embedded stream URL.
- Validates direct streams by checking for video signatures.
- Falls back to the first candidate if all fail.
"""

import urllib.request
import urllib.error
import sys
import time
from pathlib import Path

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SOURCE_FILE = "Channels/Flatten.m3u8"   # Your editable source file
OUTPUT_FILE = "Main.m3u8"               # Flattened output for users

CHUNK_SIZE = 262144        # 256 KB for stream validation
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
    """
    Determine if the URL points to a playlist file.
    Checks extension first, then content preview.
    """
    url_lower = url.lower()
    if any(url_lower.endswith(ext) for ext in ('.m3u', '.m3u8', '.mpd')):
        return True
    try:
        preview = data[:200].decode('utf-8', errors='ignore')
        return '#EXT' in preview or '<MPD' in preview
    except:
        return False

def test_candidate(url):
    """
    Test a single candidate URL.
    Returns (working: bool, final_url: str)
    - Recursively flattens simple playlists.
    - Preserves master/DASH playlists.
    - Validates direct streams.
    """
    print(f"      🔍 Testing: {url[:70]}...")

    # Step 1: Basic reachability check (fetch a small chunk)
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

        # Step 3: Check if it's a master playlist or DASH manifest
        master = is_hls_master_playlist(full_data)
        dash = is_dash_manifest(full_data)

        if master or dash:
            print(f"      ✅ Master/DASH playlist – keeping original")
            return True, url

        # Step 4: Simple redirect playlist – extract embedded stream URL
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
    Test candidates sequentially. Use the first one that works.
    If none work, fallback to the first candidate.
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

        # Preserve #EXTM3U header
        if line.startswith('#EXTM3U'):
            flattened.append(line)
            i += 1
            continue

        # Process channel entry
        if line.startswith('#EXTINF:'):
            extinf_line = line
            i += 1

            # Skip blank lines
            while i < len(lines) and not lines[i].strip():
                i += 1

            # Collect candidate URLs (until next #EXTINF or #EXTM3U)
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
            # Preserve comments and other lines
            flattened.append(line)
            i += 1

    return flattened

def main():
    print("=" * 60)
    print("IPTV Playlist Flattener – Final Version")
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
