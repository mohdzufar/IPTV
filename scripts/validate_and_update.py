import os
import re
import sys
import requests
from urllib.parse import urljoin

# Patterns of channels to skip validation (Mana-mana and tonton)
SKIP_PATTERNS = ['Mana-mana', 'tonton']

def extract_inner_url_from_wrapper(wrapper_content, base_url):
    """Extract the first HTTP URL from a wrapper .m3u8 file."""
    lines = wrapper_content.splitlines()
    for line in lines:
        line = line.strip()
        if line.startswith('http'):
            # It's a direct URL
            return line
        elif line.startswith('/'):
            # Relative path, join with base
            return urljoin(base_url, line)
    return None

def classify_content(text, status, content_type):
    """Classify stream based on content snippet."""
    if text is None:
        return 'invalid'
    text = text.strip()
    if not text:
        # Empty body, but HTTP OK might happen for some streams
        return 'empty_ok'
    if text.startswith('#EXTM3U'):
        if '#EXT-X-STREAM-INF' in text:
            return 'hls_master'
        if '#EXTINF' in text:
            return 'hls_media'
        return 'wrapper'  # unknown M3U8
    if text.startswith('\x00\x00\x00'):
        if 'ftyp' in text:
            return 'mp4'
        return 'binary'
    if '<html' in text.lower() or '<!doctype' in text.lower():
        return 'html'
    if '404' in text or 'not found' in text.lower():
        return 'invalid'
    # DASH: xml starting with <MPD
    if text.startswith('<?xml') or text.startswith('<MPD'):
        return 'dash'
    if content_type and 'video/mp4' in content_type:
        return 'mp4'
    # Default: unknown, treat as invalid for safety
    return 'invalid'

def validate_stream(url, headers=None):
    """Fetch a stream URL and classify it. Returns (kind, status, good)."""
    try:
        resp = requests.get(url, headers=headers, timeout=15, stream=True)
        # Read first 2KB for classification
        chunk = resp.raw.read(2048, decode_content=True)
        content_type = resp.headers.get('Content-Type', '')
        kind = classify_content(chunk.decode('utf-8', errors='ignore'), resp.status_code, content_type)
        good = kind not in ('invalid', 'html', 'empty_ok')
        return kind, resp.status_code, good
    except Exception:
        return 'exception', 0, False

def is_skipped(channel_name):
    """Return True if this channel should be skipped."""
    for pat in SKIP_PATTERNS:
        if pat.lower() in channel_name.lower():
            return True
    return False

def parse_flatten(flatten_path):
    """Parse Flatten.m3u8 into list of channel blocks.
    Each block: (full_text, extinf_line, url_line, channel_name, group)
    extinf_line and url_line are strings with trailing newline.
    """
    with open(flatten_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Split by #EXTINF
    blocks = []
    # Simple regex to capture #EXTINF line followed by one or more URL lines
    pattern = re.compile(r'(#EXTINF:[^\n]*\n)((?:[^#\n][^\n]*\n?)+)')
    for m in pattern.finditer(content):
        extinf = m.group(1)
        url_lines = m.group(2).strip()
        # Get channel name from tvg-name attribute
        name_match = re.search(r'tvg-name="([^"]*)"', extinf)
        channel_name = name_match.group(1) if name_match else 'Unknown'
        group_match = re.search(r'group-title="([^"]*)"', extinf)
        group = group_match.group(1) if group_match else ''
        blocks.append((m.group(0), extinf, url_lines, channel_name, group))
    return blocks

def update_main_m3u8(main_path, flattened, blocks, results):
    """Write Main.m3u8 based on validation results."""
    with open(main_path, 'w', encoding='utf-8') as f:
        # Header
        f.write('#EXTM3U\n')
        for idx, (full, extinf, url, name, group) in enumerate(blocks):
            if is_skipped(name):
                # Keep as is (Mana/tonton)
                f.write(full)
                continue
            result = results[idx]
            if result['valid']:
                if result['direct']:
                    # Replace wrapper URL with direct URL
                    f.write(extinf)
                    f.write(result['direct'] + '\n')
                else:
                    # Keep wrapper URL
                    f.write(full)
            else:
                # Comment out
                f.write('## ' + full)

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    flatten_path = os.path.join(base_dir, 'Channels', 'Flatten.m3u8')
    main_path = os.path.join(base_dir, 'Main.m3u8')

    print(f"Parsing {flatten_path}")
    blocks = parse_flatten(flatten_path)
    print(f"Found {len(blocks)} channels")

    results = [{} for _ in blocks]
    with open(os.path.join(base_dir, 'validation-report.txt'), 'w', encoding='utf-8') as report:
        report.write("Channel,Status,Type,DirectURL\n")
        for i, (full, extinf, url, name, group) in enumerate(blocks):
            if is_skipped(name):
                results[i] = {'valid': True, 'direct': None}
                report.write(f"{name},Skipped,,\n")
                print(f"{i+1}/{len(blocks)} {name}: skipped")
                continue

            print(f"{i+1}/{len(blocks)} {name}: validating...", end=' ')
            # Fetch wrapper
            try:
                wr = requests.get(url.strip(), timeout=10, headers={
                    'User-Agent': 'VLC/3.0.20'
                })
                if wr.status_code != 200:
                    results[i] = {'valid': False, 'direct': None}
                    report.write(f"{name},WrapperHTTP{wr.status_code},,\n")
                    print(f"wrapper status {wr.status_code}")
                    continue
                wrapper_content = wr.text
                inner_url = extract_inner_url_from_wrapper(wrapper_content, url.strip())
                if not inner_url:
                    results[i] = {'valid': False, 'direct': None}
                    report.write(f"{name},NoInnerURL,,\n")
                    print("no inner URL")
                    continue

                # Validate inner stream
                stream_kind, status, good = validate_stream(inner_url)
                results[i]['valid'] = good and (stream_kind not in ('empty_ok',))
                if results[i]['valid'] and stream_kind in ('mp4', 'dash'):
                    results[i]['direct'] = inner_url
                else:
                    results[i]['direct'] = None
                report.write(f"{name},{stream_kind},{status},{inner_url}\n")
                print(f"{stream_kind} (valid={results[i]['valid']})")
            except Exception as e:
                results[i] = {'valid': False, 'direct': None}
                report.write(f"{name},Exception,{str(e)},\n")
                print(f"exception: {e}")

    print("Writing Main.m3u8")
    update_main_m3u8(main_path, flatten_path, blocks, results)
    print("Done.")

if __name__ == '__main__':
    main()
