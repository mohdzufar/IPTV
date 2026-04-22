#!/usr/bin/env python3
"""
IPTV Playlist Flattener – Deep Validation & Full Entry Commenting (Verbose Logging)
- Tests candidates with media signature verification to prevent sync byte errors.
- Comments out both #EXTINF and URL if all candidates fail.
- Detailed logging shows each step of validation.
- Comprehensive media signature detection for all common video/audio containers.
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
DEEP_VALIDATION = True     # Enable media signature checks
CHUNK_SIZE = 262144        # 256 KB for validation
VERBOSE = True             # Show detailed validation steps

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': '*/*',
    'Accept-Encoding': 'identity',
    'Connection': 'keep-alive'
}

# -------------------------------------------------------------------
# COMPREHENSIVE MEDIA SIGNATURES
# Each entry: (offset, bytes_to_match, description)
# -------------------------------------------------------------------
MEDIA_SIGNATURES = [
    # --- MPEG Transport Stream (HLS) ---
    (0, b'\x47', 'MPEG-TS (sync byte)'),
    
    # --- MP4 / MOV / 3GP / fMP4 ---
    (4, b'ftyp', 'MP4/MOV/3GP (ftyp box)'),
    (4, b'moov', 'MP4 (moov box)'),
    (4, b'mdat', 'MP4 (mdat box)'),
    (4, b'free', 'MP4 (free box)'),
    (4, b'skip', 'MP4 (skip box)'),
    
    # --- WebM / Matroska ---
    (0, b'\x1a\x45\xdf\xa3', 'WebM/Matroska'),
    
    # --- FLV (Flash Video) ---
    (0, b'FLV', 'FLV'),
    
    # --- Ogg / OGM ---
    (0, b'OggS', 'Ogg'),
    
    # --- AVI / WAV (RIFF container) ---
    (0, b'RIFF', 'RIFF (AVI/WAV)'),
    
    # --- ASF / WMV / WMA ---
    (0, b'\x30\x26\xb2\x75\x8e\x66\xcf\x11', 'ASF/WMV'),
    
    # --- MPEG Program Stream ---
    (0, b'\x00\x00\x01\xba', 'MPEG-PS'),
    (0, b'\x00\x00\x01\xb3', 'MPEG Video'),
    
    # --- MP3 Audio ---
    (0, b'\xff\xfb', 'MP3 (MPEG-1 Layer 3)'),
    (0, b'\xff\xf3', 'MP3 (MPEG-2 Layer 3)'),
    (0, b'\xff\xf2', 'MP3 (MPEG-2 Layer 3)'),
    (0, b'ID3', 'MP3 with ID3v2 tag'),
    
    # --- AAC Audio (ADTS) ---
    (0, b'\xff\xf1', 'AAC (ADTS)'),
    (0, b'\xff\xf9', 'AAC (ADTS)'),
    
    # --- AC-3 / Dolby Digital ---
    (0, b'\x0b\x77', 'AC-3 / Dolby Digital'),
    
    # --- DTS Audio ---
    (0, b'\x7f\xfe\x80\x01', 'DTS'),
    
    # --- FLAC Audio ---
    (0, b'fLaC', 'FLAC'),
    
    # --- WAV (explicit) ---
    (8, b'WAVE', 'WAV'),
    
    # --- AIFF Audio ---
    (0, b'FORM', 'AIFF'),
    
    # --- RealMedia ---
    (0, b'.RMF', 'RealMedia'),
    
    # --- QuickTime (alternative) ---
    (4, b'wide', 'QuickTime'),
    (4, b'pnot', 'QuickTime'),
    
    # --- 3GPP ---
    (4, b'3gp', '3GPP'),
    
    # --- MKV (alternative) ---
    (0, b'\x1a\x45\xdf\xa3', 'Matroska'),
    
    # --- IVF (VP8/VP9) ---
    (0, b'DKIF', 'IVF (VP8/VP9)'),
    
    # --- Dirac / VC-2 ---
    (0, b'BBCD', 'Dirac'),
]

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

def is_valid_media_data(data):
    """Check if data contains any known media signature."""
    if not data or len(data) < 12:
        return False
    
    for offset, sig, desc in MEDIA_SIGNATURES:
        if len(data) > offset + len(sig) and data[offset:offset+len(sig)] == sig:
            log_detail(f"Found media signature: {desc}")
            return True
    
    # Additional heuristic: if data is mostly non-printable (binary), likely media
    try:
        sample = data[:200]
        printable = sum(32 <= b < 127 or b in (9,10,13) for b in sample)
        if printable / len(sample) < 0.3:  # less than 30% printable ASCII
            log_detail("Data appears to be binary (likely media)")
            return True
    except:
        pass
    
    log_detail("No known media signature found")
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

def extract_first_segment_url(content, base_url):
    try:
        lines = content.decode('utf-8', errors='ignore').splitlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            segment = safe_urljoin(base_url, line)
            log_detail(f"Extracted media segment: {segment[:80]}...")
            return segment
    except:
        pass
    return None

def test_stream_playable(url, depth=0):
    """Recursive test with optional deep media validation."""
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
            log_detail(f"Media playlist detected")
            segment_url = extract_first_segment_url(data, final_url)
            if segment_url:
                if DEEP_VALIDATION:
                    log_detail(f"Fetching segment for deep validation...")
                    seg_data, seg_ok, _ = fetch_url(segment_url, chunk_size=CHUNK_SIZE)
                    if seg_ok and not is_error_page(seg_data):
                        if is_valid_media_data(seg_data):
                            log_detail(f"Segment contains valid media data")
                            return True
                        else:
                            log_detail(f"Segment does NOT contain valid media signature")
                    else:
                        log_detail(f"Failed to fetch segment or segment is error page")
                    return False
                else:
                    log_detail(f"Checking segment reachability (HEAD)...")
                    _, seg_ok, _ = fetch_url(segment_url, method='HEAD')
                    return seg_ok
            else:
                log_detail(f"No segment URL found in media playlist")
                return False

    # Direct stream
    log_detail(f"Direct stream detected")
    if DEEP_VALIDATION:
        if is_valid_media_data(data):
            log_detail(f"Direct stream contains valid media signature")
            return True
        else:
            log_detail(f"Direct stream lacks media signature")
            return False
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
    print("IPTV Playlist Flattener – Deep Validation Mode (Verbose)")
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
