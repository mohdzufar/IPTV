import sys
import io
import os
import re
import requests
from urllib.parse import urljoin

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SKIP_PATTERNS = ['Mana-mana', 'tonton']

HLS_PROTOCOL_TAGS = ('#EXTM3U', '#EXT-X-', '#EXTINF')

PLAYER_HINT_PREFIXES = ('#EXTVLCOPT', '#KODIPROP', '#EXTHTTP', '#EXTATTRB')


def extract_wrapper_info(wrapper_content, base_url):
    """Parse wrapper .m3u8 — return (player_hints, urls)."""
    player_hints = []
    urls = []
    for raw_line in wrapper_content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('##'):
            continue
        if any(line.startswith(p) for p in PLAYER_HINT_PREFIXES):
            player_hints.append(line)
            continue
        if any(line.startswith(t) for t in HLS_PROTOCOL_TAGS):
            continue
        if line.startswith('http'):
            urls.append(line)
        elif line.startswith('/'):
            urls.append(urljoin(base_url, line))
    return player_hints, urls


def hints_to_headers(player_hints):
    """
    Convert #EXTVLCOPT player-hint lines into an HTTP headers dict.
    Ensures CDNs that require Referer/User-Agent (e.g. RTM CloudFront)
    receive the correct headers during stream validation.

      #EXTVLCOPT:http-referrer=https://...   ->  Referer: https://...
      #EXTVLCOPT:http-user-agent=Mozilla/5.0 ->  User-Agent: Mozilla/5.0
    """
    headers = {}
    for hint in player_hints:
        for prefix in PLAYER_HINT_PREFIXES:
            if hint.startswith(prefix + ':'):
                kv = hint[len(prefix) + 1:]
                break
        else:
            continue
        if '=' not in kv:
            continue
        key, _, value = kv.partition('=')
        key = key.strip().lower()
        value = value.strip()
        if key == 'http-referrer':
            headers['Referer'] = value
        elif key == 'http-user-agent':
            headers['User-Agent'] = value
        elif key == 'http-origin':
            headers['Origin'] = value
        elif key.startswith('http-'):
            header_name = '-'.join(
                part.capitalize() for part in key[5:].split('-')
            )
            headers[header_name] = value
    return headers


def classify_content(text, status, content_type):
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
        return 'mp4' if 'ftyp' in text else 'binary'
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
        good = kind not in ('invalid', 'html', 'empty_ok', 'binary', 'wrapper')
        return kind, resp.status_code, good
    except Exception:
        return 'exception', 0, False


def is_skipped(channel_name, wrapper_url=''):
    for pat in SKIP_PATTERNS:
        if pat.lower() in channel_name.lower():
            return True
        if pat.lower() in wrapper_url.lower():
            return True
    return False


def parse_flatten(flatten_path):
    """
    Parse Flatten.m3u8 line-by-line.
    Returns (header_line, blocks) where each block is:
        (extinf_line, hint_lines, wrapper_url, channel_name, group)
    """
    with open(flatten_path, 'r', encoding='utf-8') as f:
        raw = f.read()
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
        if not line or line.startswith('##') or not line.startswith('#EXTINF'):
            i += 1
            continue

        extinf = line
        i += 1

        hint_lines = []
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt:
                i += 1
                continue
            if any(nxt.startswith(p) for p in PLAYER_HINT_PREFIXES):
                hint_lines.append(nxt)
                i += 1
                continue
            break

        wrapper_url = ''
        if i < len(lines):
            nxt = lines[i].strip()
            if nxt.startswith('http') or nxt.startswith('/'):
                wrapper_url = nxt
                i += 1

        if not wrapper_url:
            continue

        name_match = re.search(r'tvg-name="([^"]*)"', extinf)
        channel_name = name_match.group(1) if name_match else 'Unknown'
        group_match = re.search(r'group-title="([^"]*)"', extinf)
        group = group_match.group(1) if group_match else ''

        blocks.append((extinf, hint_lines, wrapper_url, channel_name, group))

    return header, blocks


def build_main_entry(extinf, player_hints, url):
    """Active channel — #EXTINF + hints + URL."""
    lines = [extinf] + player_hints + [url]
    return '\n'.join(lines) + '\n'


def build_dead_entry(extinf, player_hints, url):
    """Dead channel — everything prefixed with ## ."""
    lines = [extinf] + player_hints + [url]
    return ''.join('## ' + l + '\n' for l in lines)


def fetch_wrapper_hints(wrapper_url):
    """Fetch wrapper file and return player_hints for skipped channels."""
    try:
        resp = requests.get(wrapper_url.strip(), timeout=10,
                            headers={'User-Agent': 'VLC/3.0.20'})
        if resp.status_code != 200:
            return []
        hints, _ = extract_wrapper_info(resp.text, wrapper_url.strip())
        return hints
    except Exception:
        return []


def update_main_m3u8(main_path, header, blocks, results):
    """Write Main.m3u8."""
    with open(main_path, 'w', encoding='utf-8') as f:
        f.write((header or '#EXTM3U') + '\n')

        for idx, (extinf, hint_lines, url, name, group) in enumerate(blocks):

            if is_skipped(name, url):
                hints = fetch_wrapper_hints(url)
                f.write(build_main_entry(extinf, hints, url))
                continue

            result = results[idx]
            player_hints = result.get('player_hints', [])

            if result['valid']:
                direct = result.get('direct')
                f.write(build_main_entry(extinf, player_hints,
                                         direct if direct else url))
            else:
                f.write(build_dead_entry(extinf, player_hints, url))


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    flatten_path = os.path.join(base_dir, 'Channels', 'Flatten.m3u8')
    main_path    = os.path.join(base_dir, 'Main.m3u8')
    report_path  = os.path.join(base_dir, 'validation-report.txt')

    print(f"Parsing {flatten_path}")
    header, blocks = parse_flatten(flatten_path)
    total = len(blocks)
    print(f"Header : {header or 'No EPG header found'}")
    print(f"Found  : {total} channels\n")

    results = [{} for _ in blocks]

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

            print(f"Wrapper : {url}")
            try:
                wr = requests.get(url.strip(), timeout=10,
                                  headers={'User-Agent': 'VLC/3.0.20'})
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

                # Convert hints to HTTP headers for stream validation
                stream_headers = hints_to_headers(player_hints)
                if stream_headers:
                    print(f"Headers : {stream_headers}")

                if not candidate_urls:
                    results[i] = {'valid': False, 'direct': None,
                                  'player_hints': player_hints}
                    report.write(f"{name},NoInnerURL,,\n")
                    print("Action  : ❌ Commented out (no inner URL)")
                    continue

                validated = False
                for inner_url in candidate_urls:
                    print(f"Inner   : {inner_url}")
                    # Pass stream_headers so CDNs enforcing Referer/UA return 200
                    stream_kind, status, good = validate_stream(
                        inner_url, headers=stream_headers
                    )
                    print(f"Stream  : {stream_kind} (HTTP {status})")

                    if good:
                        validated = True
                        if stream_kind in ('mp4', 'dash', 'hls_master'):
                            results[i] = {'valid': True, 'direct': inner_url,
                                          'player_hints': player_hints}
                            print("Action  : 🔄 Direct URL")
                        else:
                            results[i] = {'valid': True, 'direct': None,
                                          'player_hints': player_hints}
                            print("Action  : ✅ Wrapper kept")
                        report.write(f"{name},{stream_kind},{status},{inner_url}\n")
                        break

                if not validated:
                    results[i] = {'valid': False, 'direct': None,
                                  'player_hints': player_hints}
                    report.write(f"{name},all_urls_failed,,\n")
                    print("Action  : ❌ Commented out (all URLs failed)")

            except Exception as e:
                results[i] = {'valid': False, 'direct': None, 'player_hints': []}
                report.write(f"{name},exception,{str(e)},\n")
                print(f"Exception: {e}")
                print("Action  : ❌ Commented out (exception)")

    print("\n" + "=" * 60)
    print("Writing Main.m3u8 ...")
    update_main_m3u8(main_path, header, blocks, results)
    print("Done.")


if __name__ == '__main__':
    main()
