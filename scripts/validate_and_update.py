import sys
import io
import os
import re
import time
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SKIP_PATTERNS     = ['Mana-mana', 'tonton']
HLS_PROTOCOL_TAGS = ('#EXTM3U', '#EXT-X-', '#EXTINF')
PLAYER_HINT_PREFIXES = ('#EXTVLCOPT', '#KODIPROP', '#EXTHTTP', '#EXTATTRB')

MYT = timezone(timedelta(hours=8))


# ── Wrapper parsing ────────────────────────────────────────────────────────

def extract_wrapper_info(wrapper_content, base_url):
    player_hints, urls = [], []
    for raw in wrapper_content.splitlines():
        line = raw.strip()
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
            hname = '-'.join(p.capitalize() for p in key[5:].split('-'))
            headers[hname] = value
    return headers


def fetch_wrapper_hints(wrapper_url):
    try:
        resp = requests.get(wrapper_url.strip(), timeout=10,
                            headers={'User-Agent': 'VLC/3.0.20'})
        if resp.status_code != 200:
            return []
        hints, _ = extract_wrapper_info(resp.text, wrapper_url.strip())
        return hints
    except Exception:
        return []


# ── Stream validation ──────────────────────────────────────────────────────

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
    try:
        resp = requests.get(url, headers=headers, timeout=15, stream=True)
        chunk = resp.raw.read(2048, decode_content=True)
        ctype = resp.headers.get('Content-Type', '')
        kind  = classify_content(chunk.decode('utf-8', errors='ignore'),
                                 resp.status_code, ctype)
        good  = kind not in ('invalid', 'html', 'empty_ok', 'binary', 'wrapper')
        return kind, resp.status_code, good
    except Exception:
        return 'exception', 0, False


# ── Skip / Flatten logic ───────────────────────────────────────────────────

def is_skipped(channel_name, wrapper_url=''):
    for pat in SKIP_PATTERNS:
        if pat.lower() in channel_name.lower():
            return True
        if pat.lower() in wrapper_url.lower():
            return True
    return False


def parse_flatten(flatten_path):
    with open(flatten_path, 'r', encoding='utf-8') as f:
        raw = f.read()
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')
    lines = raw.splitlines()

    header = ''
    start  = 0
    if lines and lines[0].startswith('#EXTM3U'):
        header = lines[0].strip()
        start  = 1

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
                i += 1; continue
            if any(nxt.startswith(p) for p in PLAYER_HINT_PREFIXES):
                hint_lines.append(nxt); i += 1; continue
            break

        wrapper_url = ''
        if i < len(lines):
            nxt = lines[i].strip()
            if nxt.startswith('http') or nxt.startswith('/'):
                wrapper_url = nxt
                i += 1

        if not wrapper_url:
            continue

        name  = (re.search(r'tvg-name="([^"]*)"',   extinf) or re.search(r'$', '')).group(1) \
                if re.search(r'tvg-name="([^"]*)"',  extinf) else 'Unknown'
        group = re.search(r'group-title="([^"]*)"',  extinf)
        group = group.group(1) if group else ''

        blocks.append((extinf, hint_lines, wrapper_url, name, group))

    return header, blocks


# ── Main.m3u8 writers ─────────────────────────────────────────────────────

def build_main_entry(extinf, player_hints, url):
    return '\n'.join([extinf] + player_hints + [url]) + '\n'


def build_dead_entry(extinf, player_hints, url):
    return ''.join('## ' + l + '\n' for l in [extinf] + player_hints + [url])


def update_main_m3u8(main_path, header, blocks, results):
    with open(main_path, 'w', encoding='utf-8') as f:
        f.write((header or '#EXTM3U') + '\n')
        for idx, (extinf, hint_lines, url, name, group) in enumerate(blocks):
            if is_skipped(name, url):
                hints = fetch_wrapper_hints(url)
                f.write(build_main_entry(extinf, hints, url))
                continue
            result       = results[idx]
            player_hints = result.get('player_hints', [])
            if result['valid']:
                direct = result.get('direct')
                f.write(build_main_entry(extinf, player_hints,
                                         direct if direct else url))
            else:
                f.write(build_dead_entry(extinf, player_hints, url))


# ── Validation report ─────────────────────────────────────────────────────

def write_report(report_path, blocks, results, run_start):
    """
    Write a fixed-width, human-readable validation report.

    Column widths are calculated dynamically from actual data so the
    table never wraps or misaligns regardless of channel/group name length.
    """

    # ── collect rows ──────────────────────────────────────────────────────
    rows = []   # each entry: dict with display strings
    n_active = n_dead = n_skipped = 0

    for idx, (extinf, hint_lines, url, name, group) in enumerate(blocks):
        r = results[idx]

        if is_skipped(name, url):
            n_skipped += 1
            rows.append({
                'no'     : str(idx + 1),
                'channel': name,
                'group'  : group or '-',
                'status' : 'SKIP',
                'type'   : '-',
                'main'   : 'skipped',
                'http'   : '-',
            })
            continue

        valid  = r.get('valid', False)
        stype  = r.get('stream_type', '-')
        http   = str(r.get('http_status', '-'))
        direct = r.get('direct')

        if valid:
            n_active += 1
            status = 'OK'
            main   = 'direct_url' if direct else 'wrapper_kept'
        else:
            n_dead += 1
            status = 'DEAD'
            main   = 'dead'

        rows.append({
            'no'     : str(idx + 1),
            'channel': name,
            'group'  : group or '-',
            'status' : status,
            'type'   : stype,
            'main'   : main,
            'http'   : http,
        })

    # ── dynamic column widths ─────────────────────────────────────────────
    headers_labels = {
        'no'     : 'No',
        'channel': 'Channel',
        'group'  : 'Group',
        'status' : 'Status',
        'type'   : 'StreamType',
        'main'   : 'MainEntry',
        'http'   : 'HTTP',
    }
    fixed_cols = ['no', 'channel', 'group', 'status', 'type', 'main', 'http']

    widths = {}
    for col in fixed_cols:
        widths[col] = max(
            len(headers_labels[col]),
            max((len(r[col]) for r in rows), default=0)
        )

    def fmt_row(r):
        return '  '.join(r[c].ljust(widths[c]) for c in fixed_cols)

    def fmt_header():
        return '  '.join(headers_labels[c].ljust(widths[c]) for c in fixed_cols)

    def separator():
        return '  '.join('-' * widths[c] for c in fixed_cols)

    # ── totals & timing ───────────────────────────────────────────────────
    total        = len(blocks)
    non_skipped  = total - n_skipped
    success_pct  = (n_active / non_skipped * 100) if non_skipped else 0.0
    run_end      = datetime.now(MYT)
    elapsed      = int((run_end - run_start).total_seconds())
    mins, secs   = divmod(elapsed, 60)

    # ── write ─────────────────────────────────────────────────────────────
    W = len(separator())  # total line width for banner

    with open(report_path, 'w', encoding='utf-8') as f:

        # header banner
        f.write('=' * W + '\n')
        f.write(' IPTV Playlist Validation Report\n')
        f.write('=' * W + '\n')
        f.write(f' Generated : {run_end.strftime("%Y-%m-%d %H:%M:%S")} MYT\n')
        f.write(f' Source    : Channels/Flatten.m3u8\n')
        f.write(f' Run time  : {mins}m {secs}s\n')
        f.write('\n')

        # summary block
        f.write(f' Total     : {total}\n')
        f.write(f' Active    : {n_active}')
        n_wk = sum(1 for r in rows if r['main'] == 'wrapper_kept')
        n_du = sum(1 for r in rows if r['main'] == 'direct_url')
        f.write(f'  (wrapper_kept={n_wk}  direct_url={n_du})\n')
        f.write(f' Dead      : {n_dead}\n')
        f.write(f' Skipped   : {n_skipped}  (TONTON / Mana-mana — token refresh only)\n')
        f.write(f' Success   : {success_pct:.1f}%  (active / non-skipped)\n')
        f.write('\n')

        # legend
        f.write(' Status  : OK = active   DEAD = failed   SKIP = token-refresh channel\n')
        f.write(' Main    : wrapper_kept = TV app follows wrapper chain\n')
        f.write('           direct_url  = CDN URL written directly (hls_master/dash/mp4)\n')
        f.write('           dead        = block commented out in Main.m3u8\n')
        f.write('           skipped     = not validated; URL set by refresh script\n')
        f.write('\n')

        # ── group sections ────────────────────────────────────────────────
        # collect unique groups in order of first appearance
        seen_groups = []
        for r in rows:
            if r['group'] not in seen_groups:
                seen_groups.append(r['group'])

        for grp in seen_groups:
            grp_rows = [r for r in rows if r['group'] == grp]
            label = grp if grp != '-' else 'Ungrouped'

            f.write('=' * W + '\n')
            f.write(f' GROUP: {label}  '
                    f'({sum(1 for r in grp_rows if r["status"]=="OK")} active  '
                    f'{sum(1 for r in grp_rows if r["status"]=="DEAD")} dead  '
                    f'{sum(1 for r in grp_rows if r["status"]=="SKIP")} skipped)\n')
            f.write('=' * W + '\n')
            f.write(fmt_header() + '\n')
            f.write(separator() + '\n')
            for r in grp_rows:
                f.write(fmt_row(r) + '\n')
            f.write('\n')

        # footer
        f.write('=' * W + '\n')
        f.write(' END OF REPORT\n')
        f.write('=' * W + '\n')


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    run_start = datetime.now(MYT)

    base_dir     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    flatten_path = os.path.join(base_dir, 'Channels', 'Flatten.m3u8')
    main_path    = os.path.join(base_dir, 'Main.m3u8')
    report_path  = os.path.join(base_dir, 'validation-report.txt')

    print(f"Parsing {flatten_path}")
    header, blocks = parse_flatten(flatten_path)
    total = len(blocks)
    print(f"Header : {header or 'No EPG header found'}")
    print(f"Found  : {total} channels\n")

    results = [{} for _ in blocks]

    for i, (extinf, hint_lines, url, name, group) in enumerate(blocks):
        print("=" * 60)
        print(f"[{i+1}/{total}] {name}  (group: {group})")

        # ── skipped channels ──────────────────────────────────────────────
        if is_skipped(name, url):
            results[i] = {'valid': True, 'direct': None, 'player_hints': [],
                          'stream_type': '-', 'http_status': '-', 'winner_url': ''}
            print("Action  : ⏭️  Skipped (Mana-mana / tonton)")
            continue

        # ── validated channels ────────────────────────────────────────────
        print(f"Wrapper : {url}")
        try:
            wr = requests.get(url.strip(), timeout=10,
                              headers={'User-Agent': 'VLC/3.0.20'})
            print(f"Wrapper Status: {wr.status_code}")

            if wr.status_code != 200:
                results[i] = {'valid': False, 'direct': None, 'player_hints': [],
                              'stream_type': f'wrapper_http_{wr.status_code}',
                              'http_status': wr.status_code, 'winner_url': ''}
                print("Action  : ❌ Commented out (wrapper HTTP error)")
                continue

            player_hints, candidate_urls = extract_wrapper_info(wr.text, url.strip())
            stream_headers = hints_to_headers(player_hints)

            if player_hints:
                print(f"Hints   : {player_hints}")
            if stream_headers:
                print(f"Headers : {stream_headers}")

            if not candidate_urls:
                results[i] = {'valid': False, 'direct': None,
                              'player_hints': player_hints,
                              'stream_type': 'no_inner_url',
                              'http_status': '-', 'winner_url': ''}
                print("Action  : ❌ Commented out (no inner URL)")
                continue

            validated = False
            for inner_url in candidate_urls:
                print(f"Inner   : {inner_url}")
                stream_kind, status, good = validate_stream(
                    inner_url, headers=stream_headers
                )
                print(f"Stream  : {stream_kind} (HTTP {status})")

                if good:
                    validated = True
                    is_direct = stream_kind in ('mp4', 'dash', 'hls_master')
                    results[i] = {
                        'valid'      : True,
                        'direct'     : inner_url if is_direct else None,
                        'player_hints': player_hints,
                        'stream_type': stream_kind,
                        'http_status': status,
                        'winner_url' : inner_url,
                    }
                    print(f"Action  : {'🔄 Direct URL' if is_direct else '✅ Wrapper kept'}")
                    break

            if not validated:
                results[i] = {'valid': False, 'direct': None,
                              'player_hints': player_hints,
                              'stream_type': 'all_urls_failed',
                              'http_status': '-', 'winner_url': ''}
                print("Action  : ❌ Commented out (all URLs failed)")

        except Exception as e:
            results[i] = {'valid': False, 'direct': None, 'player_hints': [],
                          'stream_type': 'exception',
                          'http_status': '-', 'winner_url': str(e)[:80]}
            print(f"Exception: {e}")
            print("Action  : ❌ Commented out (exception)")

    print("\n" + "=" * 60)
    print("Writing Main.m3u8 ...")
    update_main_m3u8(main_path, header, blocks, results)

    print("Writing validation-report.txt ...")
    write_report(report_path, blocks, results, run_start)

    print("Done.")


if __name__ == '__main__':
    main()
