import sys
import io
import os
import re
import base64
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ── Repo identity (for GitHub API wrapper fetch) ───────────────────────────
GITHUB_OWNER  = 'mohdzufar'
GITHUB_REPO   = 'IPTV'
GITHUB_BRANCH = 'main'
GITHUB_TOKEN  = os.environ.get('GITHUB_TOKEN', '')

HLS_PROTOCOL_TAGS    = ('#EXTM3U', '#EXT-X-', '#EXTINF')
PLAYER_HINT_PREFIXES = ('#EXTVLCOPT', '#KODIPROP', '#EXTHTTP', '#EXTATTRB')

# Stream types that are written as direct URL in Main.m3u8
DIRECT_URL_TYPES = ('mp4', 'dash', 'hls_master', 'mpeg_ts')

# Stream types that keep the wrapper chain in Main.m3u8
WRAPPER_KEEP_TYPES = ('hls_media',)

MYT = timezone(timedelta(hours=8))


# ── GitHub API wrapper fetch ────────────────────────────────────────────────

def wrapper_url_to_api_path(wrapper_url):
    """
    Convert a raw.githubusercontent.com wrapper URL into the repo-relative
    path needed for the GitHub Contents API.

    e.g. https://raw.githubusercontent.com/mohdzufar/IPTV/refs/heads/main/Channels/TONTON/TV3/TV3.m3u8
         -> Channels/TONTON/TV3/TV3.m3u8
    """
    parsed = urlparse(wrapper_url.strip())
    parts = parsed.path.lstrip('/').split('/')

    # parts: [owner, repo, 'refs', 'heads', branch, ...path] or [owner, repo, branch, ...path]
    if len(parts) < 3:
        return None

    if parts[2] == 'refs' and len(parts) >= 5 and parts[3] == 'heads':
        # /owner/repo/refs/heads/branch/path...
        repo_path = '/'.join(parts[5:])
    else:
        # /owner/repo/branch/path...
        repo_path = '/'.join(parts[3:])

    return repo_path or None


def fetch_wrapper_via_api(wrapper_url):
    """
    Fetch wrapper file content via the GitHub Contents API instead of
    raw.githubusercontent.com. This bypasses GitHub's raw-file CDN cache,
    which can serve stale content for some time after a push — critical
    here because TONTON/Mana-mana wrappers are refreshed and pushed
    earlier in the SAME workflow run, just before validation executes.

    Returns (text, status_code). status_code is a synthetic 200/404/000
    to stay compatible with the rest of the validation flow.
    """
    repo_path = wrapper_url_to_api_path(wrapper_url)
    if not repo_path:
        return None, 0

    api_url = (
        f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
        f'/contents/{repo_path}?ref={GITHUB_BRANCH}'
    )

    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'IPTV-Validator',
    }
    if GITHUB_TOKEN:
        headers['Authorization'] = f'Bearer {GITHUB_TOKEN}'

    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None, resp.status_code

        data = resp.json()
        content_b64 = data.get('content', '')
        encoding = data.get('encoding', 'base64')

        if encoding != 'base64':
            return None, resp.status_code

        # GitHub API returns base64 content with embedded newlines
        raw_bytes = base64.b64decode(content_b64)
        text = raw_bytes.decode('utf-8', errors='ignore')
        return text, 200

    except Exception:
        return None, 0


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
    """
    Convert #EXTVLCOPT player-hint lines into an HTTP headers dict.
    Ensures CDNs that require Referer/User-Agent receive the correct
    headers during stream validation.
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
        key   = key.strip().lower()
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


# ── Stream classification ──────────────────────────────────────────────────

def classify_content(raw_bytes, text, status, content_type):
    """
    Classify a stream from its raw bytes and decoded text.

    raw_bytes is checked first for binary formats (MPEG-TS, MP4).
    text is used for text-based formats (HLS, DASH, HTML error pages).
    """

    # ── MPEG-TS detection ─────────────────────────────────────────────────
    # Sync byte 0x47 appears every 188 bytes in a valid TS stream.
    # Checking positions 0 and 188 is sufficient to confirm.
    if (len(raw_bytes) >= 189
            and raw_bytes[0] == 0x47
            and raw_bytes[188] == 0x47):
        return 'mpeg_ts'

    # ── MP4 / binary detection ────────────────────────────────────────────
    if len(raw_bytes) >= 4 and raw_bytes[:4] == b'\x00\x00\x00\x00':
        if b'ftyp' in raw_bytes[:32]:
            return 'mp4'
        return 'binary'

    # ── Text-based formats ────────────────────────────────────────────────
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

    if '<html' in text.lower() or '<!doctype' in text.lower():
        return 'html'

    if '404' in text or 'not found' in text.lower():
        return 'invalid'

    if text.startswith('<?xml') or text.startswith('<MPD'):
        return 'dash'

    if content_type and 'video/mp4' in content_type:
        return 'mp4'

    if content_type and 'video/mp2t' in content_type:
        return 'mpeg_ts'

    return 'invalid'


def validate_stream(url, headers=None):
    """
    Fetch a stream URL and classify it.
    Returns (kind, http_status, good).

    Uses a short connect timeout (5s) and a longer read timeout (15s).
    This ensures dead servers on non-standard ports (e.g. 8080) fail
    fast instead of blocking the entire validation run.
    """
    try:
        resp = requests.get(
            url,
            headers=headers,
            timeout=(5, 15),   # (connect_timeout, read_timeout)
            stream=True
        )
        # Read enough bytes to detect MPEG-TS (needs at least 189 bytes)
        raw_bytes = resp.raw.read(512, decode_content=True)
        text      = raw_bytes.decode('utf-8', errors='ignore')
        ctype     = resp.headers.get('Content-Type', '')

        kind = classify_content(raw_bytes, text, resp.status_code, ctype)
        good = kind not in ('invalid', 'html', 'empty_ok', 'binary', 'wrapper')
        return kind, resp.status_code, good

    except requests.exceptions.ConnectTimeout:
        return 'connect_timeout', 0, False
    except requests.exceptions.ReadTimeout:
        return 'read_timeout', 0, False
    except Exception:
        return 'exception', 0, False


# ── Flatten.m3u8 parser ─────────────────────────────────────────────────────

def parse_flatten(flatten_path):
    """
    Parse Flatten.m3u8 line-by-line.
    Returns (header_line, blocks) where each block is:
        (extinf_line, hint_lines, wrapper_url, channel_name, group)
    """
    with open(flatten_path, 'r', encoding='utf-8') as f:
        raw = f.read()
    raw   = raw.replace('\r\n', '\n').replace('\r', '\n')
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

        name_m = re.search(r'tvg-name="([^"]*)"', extinf)
        name   = name_m.group(1) if name_m else 'Unknown'
        grp_m  = re.search(r'group-title="([^"]*)"', extinf)
        group  = grp_m.group(1) if grp_m else ''

        blocks.append((extinf, hint_lines, wrapper_url, name, group))

    return header, blocks


# ── Main.m3u8 writers ─────────────────────────────────────────────────────

def build_main_entry(extinf, player_hints, url):
    """Active channel — #EXTINF + hints + URL."""
    return '\n'.join([extinf] + player_hints + [url]) + '\n'


def build_dead_entry(extinf, player_hints, url):
    """Dead channel — everything prefixed with ## ."""
    return ''.join('## ' + l + '\n' for l in [extinf] + player_hints + [url])


def update_main_m3u8(main_path, header, blocks, results):
    """Write Main.m3u8."""
    with open(main_path, 'w', encoding='utf-8') as f:
        f.write((header or '#EXTM3U') + '\n')

        for idx, (extinf, hint_lines, url, name, group) in enumerate(blocks):
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
    Column widths are calculated dynamically from actual data.
    """
    rows     = []
    n_active = n_dead = 0

    for idx, (extinf, hint_lines, url, name, group) in enumerate(blocks):
        r = results[idx]

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

    total       = len(blocks)
    success_pct = (n_active / total * 100) if total else 0.0
    run_end     = datetime.now(MYT)
    elapsed     = int((run_end - run_start).total_seconds())
    mins, secs  = divmod(elapsed, 60)

    W = len(separator())

    with open(report_path, 'w', encoding='utf-8') as f:

        f.write('=' * W + '\n')
        f.write(' IPTV Playlist Validation Report\n')
        f.write('=' * W + '\n')
        f.write(f' Generated : {run_end.strftime("%Y-%m-%d %H:%M:%S")} MYT\n')
        f.write(f' Source    : Channels/Flatten.m3u8\n')
        f.write(f' Run time  : {mins}m {secs}s\n')
        f.write('\n')

        f.write(f' Total     : {total}\n')
        n_wk = sum(1 for r in rows if r['main'] == 'wrapper_kept')
        n_du = sum(1 for r in rows if r['main'] == 'direct_url')
        f.write(f' Active    : {n_active}  (wrapper_kept={n_wk}  direct_url={n_du})\n')
        f.write(f' Dead      : {n_dead}\n')
        f.write(f' Success   : {success_pct:.1f}%\n')
        f.write('\n')

        f.write(' Status  : OK = active   DEAD = failed\n')
        f.write(' Type    : hls_media = HLS segment playlist\n')
        f.write('           hls_master = HLS multi-quality master\n')
        f.write('           mpeg_ts = MPEG-TS stream (Xtream Codes / direct TS)\n')
        f.write('           dash = MPEG-DASH\n')
        f.write('           mp4 = MP4 direct\n')
        f.write(' Main    : wrapper_kept = TV app follows wrapper chain (hls_media)\n')
        f.write('           direct_url  = CDN URL written directly (hls_master, dash, mp4, mpeg_ts)\n')
        f.write('           dead        = block commented out in Main.m3u8\n')
        f.write('\n')
        f.write(' Note    : All channels — including TONTON and Mana-mana — are now\n')
        f.write('           validated the same way. Their wrappers are fetched via the\n')
        f.write('           GitHub API to read the token refreshed earlier in this run.\n')
        f.write('\n')

        seen_groups = []
        for r in rows:
            if r['group'] not in seen_groups:
                seen_groups.append(r['group'])

        for grp in seen_groups:
            grp_rows = [r for r in rows if r['group'] == grp]
            label    = grp if grp != '-' else 'Ungrouped'

            f.write('=' * W + '\n')
            f.write(f' GROUP: {label}  '
                    f'({sum(1 for r in grp_rows if r["status"]=="OK")} active  '
                    f'{sum(1 for r in grp_rows if r["status"]=="DEAD")} dead)\n')
            f.write('=' * W + '\n')
            f.write(fmt_header() + '\n')
            f.write(separator() + '\n')
            for r in grp_rows:
                f.write(fmt_row(r) + '\n')
            f.write('\n')

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

    if not GITHUB_TOKEN:
        print("WARNING: GITHUB_TOKEN not set in environment. "
              "GitHub API calls will be unauthenticated and rate-limited.")

    print(f"Parsing {flatten_path}")
    header, blocks = parse_flatten(flatten_path)
    total = len(blocks)
    print(f"Header : {header or 'No EPG header found'}")
    print(f"Found  : {total} channels\n")

    results = [{} for _ in blocks]

    for i, (extinf, hint_lines, url, name, group) in enumerate(blocks):
        print("=" * 60)
        print(f"[{i+1}/{total}] {name}  (group: {group})")
        print(f"Wrapper : {url}")

        try:
            # Fetch wrapper via GitHub API — always current, no CDN cache
            # delay. Critical for TONTON/Mana-mana wrappers that were
            # refreshed and pushed earlier in this same workflow run.
            wrapper_text, api_status = fetch_wrapper_via_api(url)
            print(f"API Status: {api_status}")

            if wrapper_text is None:
                results[i] = {'valid': False, 'direct': None, 'player_hints': [],
                              'stream_type': f'wrapper_api_{api_status}',
                              'http_status': api_status}
                print("Action  : ❌ Commented out (wrapper API fetch failed)")
                continue

            player_hints, candidate_urls = extract_wrapper_info(wrapper_text, url.strip())
            stream_headers = hints_to_headers(player_hints)

            if player_hints:
                print(f"Hints   : {player_hints}")
            if stream_headers:
                print(f"Headers : {stream_headers}")

            if not candidate_urls:
                results[i] = {'valid': False, 'direct': None,
                              'player_hints': player_hints,
                              'stream_type': 'no_inner_url',
                              'http_status': '-'}
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
                    validated  = True
                    is_direct  = stream_kind in DIRECT_URL_TYPES
                    results[i] = {
                        'valid'       : True,
                        'direct'      : inner_url if is_direct else None,
                        'player_hints': player_hints,
                        'stream_type' : stream_kind,
                        'http_status' : status,
                    }
                    if is_direct:
                        print("Action  : 🔄 Direct URL")
                    else:
                        print("Action  : ✅ Wrapper kept")
                    break

            if not validated:
                results[i] = {'valid': False, 'direct': None,
                              'player_hints': player_hints,
                              'stream_type': 'all_urls_failed',
                              'http_status': '-'}
                print("Action  : ❌ Commented out (all URLs failed)")

        except Exception as e:
            results[i] = {'valid': False, 'direct': None, 'player_hints': [],
                          'stream_type': 'exception',
                          'http_status': '-'}
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
