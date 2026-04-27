import sys
import io
import os
import re
import requests
from urllib.parse import urljoin

# Fix Unicode output on Windows (avoid cp1252 errors)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Patterns of channels to skip validation (Mana-mana and tonton)
SKIP_PATTERNS = ['Mana-mana', 'tonton']


def extract_inner_url_from_wrapper(wrapper_content, base_url):
    """Extract the first HTTP URL from a wrapper .m3u8 file."""
    lines = wrapper_content.splitlines()
    for line in lines:
        line = line.strip()
        if line.startswith('http'):
            return line
        elif line.startswith('/'):
            return urljoin(base_url, line)
    return None


def classify_content(text, status, content_type):
    """Classify stream based on content snippet."""
    if text is None:
        return 'invalid'
    text = text.strip()
    if not text:
        return 'empty_ok'
    if text.startswith('#EXTM3U'):
        if '#EXT-X-STREAM-INF' in text:
            return 'hls_master'
        if '#EXTINF' in text:
            return 'hls_media'
        return 'wrapper'
    if text.startswith('\x00\x00\x00'):
        if 'ftyp' in text:
            return 'mp4'
        return 'binary'
    if '<html' in text.lower() or '<!doctype' in text.lower():
        return 'html'
    if '404' in text or 'not found' in text.lower():
        return 'invalid'
    if text.startswith('<?xml') or text.startswith('<MPD'):
        return 'dash'
    if content_type and 'video/mp4' in content_type:
        return 'mp4'
    return 'invalid'


def validate_stream(url, headers=None):
    """Fetch a stream URL and classify it. Returns (kind, status, good)."""
    try:
        resp = requests.get(url, headers=headers, timeout=15, stream=True)
        chunk = resp.raw.read(2048, decode_content=True)
        content_type = resp.headers.get('Content-Type', '')
        kind = classify_content(chunk.decode('utf-8', errors='ignore'),
                                resp.status_code, content_type)
        good = kind not in ('invalid', 'html', 'empty_ok')
        return kind, resp.status_code, good
    except Exception:
        return 'exception', 0, False


def is_skipped(channel_name, wrapper_url=''):
    """Return True if this channel should be skipped.
    Checks both the channel name and the wrapper URL path for skip patterns."""
    for pat in SKIP_PATTERNS:
        if pat.lower() in channel_name.lower():
            return True
        if pat.lower() in wrapper_url.lower():
            return True
    return False


def parse_flatten(flatten_path):
    with open(flatten_path, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = []
    pattern = re.compile(r'(#EXTINF:[^\n]*\n)((?:[^#\n][^\n]*\n?)+)')
    for m in pattern.finditer(content):
        extinf = m.group(1)
        url_lines = m.group(2).strip()
        name_match = re.search(r'tvg-name="([^"]*)"', extinf)
        channel_name = name_match.group(1) if name_match else 'Unknown'
        group_match = re.search(r'group-title="([^"]*)"', extinf)
        group = group_match.group(1) if group_match else ''
        blocks.append((m.group(0), extinf, url_lines, channel_name, group))
    return blocks


def update_main_m3u8(main_path, flattened, blocks, results):
    with open(main_path, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        for idx, (full, extinf, url, name, group) in enumerate(blocks):
            if is_skipped(name, url):
                f.write(full)
                continue
            result = results[idx]
            if result['valid']:
                if result['direct']:
                    f.write(extinf)
                    f.write(result['direct'] + '\n')
                else:
                    f.write(full)
            else:
                # FIXED: prefix every line with '## ' so both #EXTINF and URL are commented
                for line in full.splitlines(keepends=True):
                    f.write('## ' + line)


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    flatten_path = os.path.join(base_dir, 'Channels', 'Flatten.m3u8')
    main_path = os.path.join(base_dir, 'Main.m3u8')

    print(f"Parsing {flatten_path}")
    blocks = parse_flatten(flatten_path)
    total = len(blocks)
    print(f"Found {total} channels\n")

    results = [{} for _ in blocks]
    with open(os.path.join(base_dir, 'validation-report.txt'), 'w',
              encoding='utf-8') as report:
        report.write("Channel,Status,Type,DirectURL\n")
        for i, (full, extinf, url, name, group) in enumerate(blocks):
            print("=" * 60)
            print(f"[{i+1}/{total}] {name} (group: {group})")

            if is_skipped(name, url):
                results[i] = {'valid': True, 'direct': None}
                report.write(f"{name},Skipped,,\n")
                print("Action  : ⏭️  Skipped (Mana-mana / tonton)")
                continue

            print(f"Wrapper : {url.strip()}")
            try:
                wr = requests.get(url.strip(), timeout=10, headers={
                    'User-Agent': 'VLC/3.0.20'
                })
                print(f"Wrapper Status: {wr.status_code}")
                if wr.status_code != 200:
                    results[i] = {'valid': False, 'direct': None}
                    report.write(f"{name},WrapperHTTP{wr.status_code},,\n")
                    print("Action  : ❌ Commented out (wrapper HTTP error)")
                    continue

                wrapper_content = wr.text
                inner_url = extract_inner_url_from_wrapper(wrapper_content,
                                                           url.strip())
                if not inner_url:
                    results[i] = {'valid': False, 'direct': None}
                    report.write(f"{name},NoInnerURL,,\n")
                    print("Inner URL: NOT FOUND")
                    print("Action  : ❌ Commented out (no inner URL)")
                    continue

                print(f"Inner URL: {inner_url}")

                stream_kind, status, good = validate_stream(inner_url)
                results[i]['valid'] = good and (stream_kind not in ('empty_ok',))
                if results[i]['valid'] and stream_kind in ('mp4', 'dash'):
                    results[i]['direct'] = inner_url
                    print(f"Stream   : {stream_kind} (HTTP {status})")
                    print("Action  : 🔄 Replaced with direct URL")
                elif results[i]['valid']:
                    results[i]['direct'] = None
                    print(f"Stream   : {stream_kind} (HTTP {status})")
                    print("Action  : ✅ Kept (wrapper)")
                else:
                    results[i]['direct'] = None
                    print(f"Stream   : {stream_kind} (HTTP {status})")
                    print("Action  : ❌ Commented out (invalid stream)")
                report.write(f"{name},{stream_kind},{status},{inner_url}\n")
            except Exception as e:
                results[i] = {'valid': False, 'direct': None}
                report.write(f"{name},Exception,{str(e)},\n")
                print(f"Exception: {str(e)}")
                print("Action  : ❌ Commented out (exception)")

    print("\n" + "=" * 60)
    print("Writing Main.m3u8")
    update_main_m3u8(main_path, flatten_path, blocks, results)
    print("Done.")


if __name__ == '__main__':
    main()
