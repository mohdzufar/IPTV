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

# HLS/DASH protocol tags that are part of a stream manifest —
# present inside wrapper files but must NOT be copied into Main.m3u8.
HLS_PROTOCOL_TAGS = (
    '#EXTM3U',
    '#EXT-X-',
    '#EXTINF',
)

# Player-hint declaration prefixes that SHOULD be copied from the wrapper
# into Main.m3u8, placed between the channel's #EXTINF and its URL.
PLAYER_HINT_PREFIXES = (
    '#EXTVLCOPT',
    '#KODIPROP',
    '#EXTHTTP',
    '#EXTATTRB',
)


def extract_wrapper_info(wrapper_content, base_url):
    """
    Parse a wrapper .m3u8 file and return:
      - player_hints : list of player-hint declaration lines
      - urls         : list of candidate stream URLs (## excluded)
    """
    player_hints = []
    urls = []

    for raw_line in wrapper_content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith('##'):
            continue
        if any(line.startswith(prefix) for prefix in PLAYER_HINT_PREFIXES):
            player_hints.append(line)
            continue
        if any(line.startswith(tag) for tag in HLS_PROTOCOL_TAGS):
            continue
        if line.startswith('http'):
            urls.append(line)
            continue
        if line.startswith('/'):
            urls.append(urljoin(base_url, line))
            continue

    return player_hints, urls


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
    """Return True if this channel should be skipped."""
    for pat in SKIP_PATTERNS:
        if pat.lower() in channel_name.lower():
            return True
        if pat.lower() in wrapper_url.lower():
            return True
    return False


def parse_flatten(flatten_path):
    """
    Parse Flatten.m3u8 and return (header_line, list_of_blocks).

    Each block is a tuple:
      (extinf_line, hint_lines, wrapper_url, channel_name, group)

    hint_lines : list of #EXTVLCOPT / #KODIPROP lines between #EXTINF and URL
                 (these should be empty for Flatten.m3u8, but we handle them
                  gracefully so that stray entries never silently disappear)
    """
    with open(flatten_path, 'r', encoding='utf-8') as f:
        raw = f.read()

    # Normalise Windows line endings
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')
    lines = raw.splitlines()

    header = ''
    start = 0
    if lines and lines[0].startswith('#EXTM3U'):
        header = lines[0].strip()
        start = 1

    blocks = []
    i = start
    while i < len(lines):
        line = lines[i].strip()

        # Skip blank lines, ## comment lines, and non-EXTINF # lines
        if not line or line.startswith('##') or not line.startswith('#EXTINF'):
            i += 1
            continue

        extinf = line
        i += 1

        # Collect any player-hint lines between #EXTINF and the URL
        hint_lines = []
        while i < len(lines):
            next_line = lines[i].strip()
            if not next_line:
                i += 1
                continue
            if any(next_line.startswith(p) for p in PLAYER_HINT_PREFIXES):
                hint_lines.append(next_line)
                i += 1
                continue
            break  # next non-blank, non-hint line

        # The next non-blank, non-hint line should be the wrapper URL
        wrapper_url = ''
        if i < len(lines):
            next_line = lines[i].strip()
            if next_line.startswith('http') or next_line.startswith('/'):
                wrapper_url = next_line
                i += 1

        if not wrapper_url:
            # No URL found — skip this malformed block
            continue

        name_match = re.search(r'tvg-name="([^"]*)"', extinf)
        channel_name = name_match.group(1) if name_match else 'Unknown'
        group_match = re.search(r'group-title="([^"]*)"', extinf)
        group = group_match.group(1) if group_match else ''

        blocks.append((extinf, hint_lines, wrapper_url, channel_name, group))

    return header, blocks


def build_main_entry(extinf, player_hints, url, direct_url=None):
    """Build the block to write into Main.m3u8 for one active channel."""
    lines = [extinf]
    lines.extend(player_hints)
    lines.append(direct_url if direct_url else url)
    return '\n'.join(lines) + '\n'


def build_dead_entry(extinf, player_hints, url):
    """Build a fully commented-out block for a dead channel."""
    lines = [extinf]
    lines.extend(player_hints)
    lines.append(url)
    return ''.join('## ' + line + '\n' for line in lines)


def update_main_m3u8(main_path, header, blocks, results):
    """Write Main.m3u8 using the original EPG header line."""
    with open(main_path, 'w', encoding='utf-8') as f:
        if header:
            f.write(header + '\n')
        else:
            f.write('#EXTM3U\n')

        for idx, (extinf, hint_lines, url, name, group) in enumerate(blocks):
            if is_skipped(name, url):
                # Write Flatten block as-is (without hints — Flatten should
                # not have #EXTVLCOPT; wrapper files carry those instead).
                f.write(extinf + '\n')
                f.write(url + '\n')
                continue

            result = results[idx]
            player_hints = result.get('player_hints', [])

            if result['valid']:
                direct = result.get('direct')
                f.write(build_main_entry(extinf, player_hints, url, direct))
            else:
                f.write(build_dead_entry(extinf, player_hints, url))


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    flatten_path = os.path.join(base_dir, 'Channels', 'Flatten.m3u8')
    main_path = os.path.join(base_dir, 'Main.m3u8')

    print(f"Parsing {flatten_path}")
    header, blocks = parse_flatten(flatten_path)
    total = len(blocks)
    print(f"Header: {header if header else 'No EPG header found'}")
    print(f"Found {total} channels\n")

    results = [{} for _ in blocks]
    report_path = os.path.join(base_dir, 'validation-report.txt')

    with open(report_path, 'w', encoding='utf-8') as report:
        report.write("Channel,Status,Type,DirectURL\n")

        for i, (extinf, hint_lines, url, name, group) in enumerate(blocks):
            print("=" * 60)
            print(f"[{i+1}/{total}] {name} (group: {group})")

            if is_skipped(name, url):
                results[i] = {'valid': True, 'direct': None, 'player_hints': []}
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
                    results[i] = {'valid': False, 'direct': None, 'player_hints': []}
                    report.write(f"{name},WrapperHTTP{wr.status_code},,\n")
                    print("Action  : ❌ Commented out (wrapper HTTP error)")
                    continue

                player_hints, candidate_urls = extract_wrapper_info(
                    wr.text, url.strip()
                )

                if player_hints:
                    print(f"Hints   : {player_hints}")

                if not candidate_urls:
                    results[i] = {'valid': False, 'direct': None, 'player_hints': player_hints}
                    report.write(f"{name},NoInnerURL,,\n")
                    print("Inner URL: NOT FOUND")
                    print("Action  : ❌ Commented out (no inner URL)")
                    continue

                validated = False
                for inner_url in candidate_urls:
                    print(f"Inner URL: {inner_url}")
                    stream_kind, status, good = validate_stream(inner_url)
                    print(f"Stream   : {stream_kind} (HTTP {status})")

                    if good:
                        validated = True
                        if stream_kind in ('mp4', 'dash', 'hls_master'):
                            results[i] = {
                                'valid': True,
                                'direct': inner_url,
                                'player_hints': player_hints,
                            }
                            print("Action  : 🔄 Replaced with direct URL")
                        else:
                            results[i] = {
                                'valid': True,
                                'direct': None,
                                'player_hints': player_hints,
                            }
                            print("Action  : ✅ Kept (wrapper)")
                        report.write(f"{name},{stream_kind},{status},{inner_url}\n")
                        break

                if not validated:
                    results[i] = {'valid': False, 'direct': None, 'player_hints': player_hints}
                    report.write(f"{name},all_urls_failed,,\n")
                    print("Action  : ❌ Commented out (all URLs invalid)")

            except Exception as e:
                results[i] = {'valid': False, 'direct': None, 'player_hints': []}
                report.write(f"{name},exception,{str(e)},\n")
                print(f"Exception: {str(e)}")
                print("Action  : ❌ Commented out (exception)")

    print("\n" + "=" * 60)
    print("Writing Main.m3u8")
    update_main_m3u8(main_path, header, blocks, results)
    print("Done.")


if __name__ == '__main__':
    main()
