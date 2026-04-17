#!/usr/bin/env python3
"""
IPTV Playlist Flattener (Multi-URL Support)
Reads Channels/Flatten.m3u8, resolves sub-playlists intelligently, and writes Main.m3u8.
- Preserves master playlists (HLS multi-bitrate).
- Flattens simple redirect playlists to direct stream URLs.
- Supports multiple URLs per channel (for automatic failover).
"""

import urllib.request
import urllib.error
import sys
from pathlib import Path

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SOURCE_FILE = "Channels/Flatten.m3u8"   # The file you edit
OUTPUT_FILE = "Main.m3u8"               # The flattened file for users

# -------------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------------
def fetch_url_content(url, timeout=15):
    """Fetch text content from a URL with a browser-like User-Agent."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_bytes = response.read()
            try:
                return content_bytes.decode('utf-8')
            except UnicodeDecodeError:
                return content_bytes.decode('latin-1')
    except Exception as e:
        print(f"  ⚠️ Failed to fetch {url}: {e}")
        return None

def is_master_playlist(content):
    """Return True if the playlist content contains #EXT-X-STREAM-INF."""
    if not content:
        return False
    return '#EXT-X-STREAM-INF' in content

def extract_stream_url_from_simple_playlist(content):
    """Extract the first usable stream URL from a SIMPLE playlist."""
    if not content:
        return None
    lines = content.splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        return line
    return None

def should_treat_as_playlist(url):
    """Heuristic to decide if a URL likely points to a playlist file."""
    url_lower = url.lower()
    playlist_extensions = ('.m3u', '.m3u8', '.m3u?', '.m3u8?')
    return any(url_lower.endswith(ext) or ext in url_lower for ext in playlist_extensions)

def process_source_playlist(source_path):
    """Read source, resolve sub-playlists, and return flattened lines with multi-URL support."""
    with open(source_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    flattened = []
    i = 0
    processed_count = 0
    master_count = 0
    flattened_count = 0
    skipped_count = 0

    while i < len(lines):
        line = lines[i].rstrip('\n\r')

        # Preserve #EXTM3U header
        if line.startswith('#EXTM3U'):
            flattened.append(line)
            i += 1
            continue

        # Channel entry: #EXTINF line
        if line.startswith('#EXTINF:'):
            extinf_line = line
            i += 1

            # Skip blank lines
            while i < len(lines) and not lines[i].strip():
                i += 1

            # Collect all consecutive URLs until next #EXTINF or EOF
            urls = []
            while i < len(lines):
                next_line = lines[i].rstrip('\n\r').strip()
                if not next_line:
                    i += 1
                    continue
                if next_line.startswith('#EXTINF:') or next_line.startswith('#EXTM3U'):
                    break
                # It's a URL
                urls.append(next_line)
                i += 1

            if not urls:
                # No URL found, just keep the #EXTINF
                flattened.append(extinf_line)
                continue

            # Process each URL
            resolved_urls = []
            for url in urls:
                if should_treat_as_playlist(url):
                    processed_count += 1
                    print(f"  Checking: {url[:70]}...")
                    content = fetch_url_content(url)
                    if content:
                        if is_master_playlist(content):
                            master_count += 1
                            print(f"    ✅ Master playlist detected, keeping original")
                            resolved_urls.append(url)
                        else:
                            stream_url = extract_stream_url_from_simple_playlist(content)
                            if stream_url:
                                flattened_count += 1
                                print(f"    ➡️ Flattened to: {stream_url[:60]}...")
                                resolved_urls.append(stream_url)
                            else:
                                skipped_count += 1
                                print(f"    ⚠️ Could not extract, keeping original")
                                resolved_urls.append(url)
                    else:
                        skipped_count += 1
                        print(f"    ❌ Failed to fetch, keeping original")
                        resolved_urls.append(url)
                else:
                    # Direct stream, keep as-is
                    resolved_urls.append(url)

            # Write the channel entry
            flattened.append(extinf_line)
            flattened.extend(resolved_urls)

        else:
            # Other lines (comments, blanks)
            flattened.append(line)
            i += 1

    print("\n" + "=" * 50)
    print("SUMMARY:")
    print(f"  Total sub-playlists processed: {processed_count}")
    print(f"  Master playlists (preserved):  {master_count}")
    print(f"  Simple playlists (flattened):   {flattened_count}")
    print(f"  Skipped/Failed:                 {skipped_count}")
    print("=" * 50)

    return flattened

def main():
    print("=" * 50)
    print("IPTV Playlist Flattener (Multi-URL)")
    print("=" * 50)

    if not Path(SOURCE_FILE).exists():
        print(f"❌ Error: {SOURCE_FILE} not found.")
        sys.exit(1)

    print(f"📂 Reading {SOURCE_FILE}...")
    flattened_lines = process_source_playlist(SOURCE_FILE)

    output_path = Path(OUTPUT_FILE)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(flattened_lines))

    print(f"\n✅ Flattened playlist written to {OUTPUT_FILE}")
    print(f"   Total lines: {len(flattened_lines)}")
    print("=" * 50)

if __name__ == "__main__":
    main()
