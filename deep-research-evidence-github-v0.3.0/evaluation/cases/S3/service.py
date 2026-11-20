#!/usr/bin/env python3
"""Local synthetic retrieval service. This is not a network/PDF benchmark.

Use the public interface document. Corpus bytes are for service use only.
Each invocation records an actual call to the task's private state directory.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

BASE = Path(__file__).resolve().parent
CORPUS = BASE / 'corpus'
CATALOG = [
    {'source_id': 'S3-PROTOCOL', 'title': 'LSMF experiment protocol', 'media_type': 'text/plain'},
    {'source_id': 'S3-RUNS', 'title': 'LSMF metering table', 'media_type': 'application/x-mock-pdf'},
    {'source_id': 'S3-QUALITY', 'title': 'LSMF endpoint water quality', 'media_type': 'text/plain'},
    {'source_id': 'S3-LIMITS', 'title': 'LSMF scope limitations', 'media_type': 'text/plain'},
]


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def atomic_json(path, value):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def safe_output(raw):
    path = Path(raw).resolve()
    protected = [CORPUS, BASE / 'materials', BASE / 'case.json', BASE / 'task.md', BASE / 'service.py']
    if any(path == p or (p.is_dir() and within(path, p)) for p in protected):
        raise ValueError('Output must not overwrite task inputs or the service corpus.')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def perform(args, state):
    if args.command == 'search':
        query = args.query.strip()
        state['search_calls'] = state.get('search_calls', 0) + 1
        if state['search_calls'] == 1:
            state['empty_query'] = query.casefold()
            return {'status': 'empty', 'results': [], 'hint': 'The first index lookup is a simulated stale miss. Reformulate the query using project alias LSMF and retry once.'}, 0
        if query.casefold() == state.get('empty_query'):
            return {'status': 'empty', 'results': [], 'hint': 'This exact query remains cached as empty. Use a different query including LSMF.'}, 0
        if 'lsmf' not in query.casefold() and 'lanspray' not in query.casefold():
            return {'status': 'empty', 'results': [], 'hint': 'The index uses project alias LSMF or LanSpray-MF.'}, 0
        return {'status': 'ok', 'results': CATALOG, 'exhaustive_within_scope': True, 'scope': 'all four synthetic LSMF records in this closed case'}, 0
    if args.command == 'fetch':
        source = next((x for x in CATALOG if x['source_id'] == args.source_id), None)
        if source is None:
            return {'status': 'not_found', 'http_status': 404, 'retryable': False, 'error': 'Unknown source_id.'}, 4
        counts = state.setdefault('fetch_attempts', {})
        counts[args.source_id] = counts.get(args.source_id, 0) + 1
        if args.source_id == 'S3-RUNS' and counts[args.source_id] == 1:
            return {'status': 'rate_limited', 'http_status': 429, 'retryable': True, 'retry_after_seconds': 0, 'error': 'Synthetic one-time quota throttle; retry the same fetch.'}, 3
        suffix = '.mockpdf' if args.source_id == 'S3-RUNS' else '.txt'
        blob = (CORPUS / (args.source_id + suffix)).read_bytes()
        out = safe_output(args.output)
        out.write_bytes(blob)
        return {'status': 'ok', 'source_id': args.source_id, 'media_type': source['media_type'], 'output_path': str(out), 'sha256': digest(blob), 'bytes': len(blob)}, 0
    if args.command == 'parse':
        source_path = Path(args.input).resolve()
        if within(source_path, CORPUS):
            raise ValueError('Fetch a local artifact first; do not parse the protected corpus directly.')
        blob = source_path.read_bytes()
        data = json.loads(blob.decode('utf-8'))
        if data.get('format') != 'mock-pdf-v1' or data.get('source_id') != 'S3-RUNS':
            return {'status': 'parse_error', 'error_code': 'UNSUPPORTED_DOCUMENT', 'retryable': False}, 4
        if args.engine == 'native':
            return {'status': 'parse_error', 'error_code': 'MOCK_TABLE_LAYOUT_UNSUPPORTED', 'retryable': False, 'fallback_engine': 'fallback', 'input_sha256': digest(blob), 'hint': 'Call parse on the same downloaded artifact with --engine fallback.'}, 4
        lines = []
        mapping = []
        for page in data['pages']:
            start = len(lines) + 1
            lines.extend(page['lines'])
            mapping.append({'synthetic_physical_page': page['physical_page'], 'line_start': start, 'line_end': len(lines)})
        text = ('\n'.join(lines) + '\n').encode('utf-8')
        out = safe_output(args.output)
        out.write_bytes(text)
        return {'status': 'ok', 'source_id': data['source_id'], 'engine': args.engine, 'output_path': str(out), 'input_sha256': digest(blob), 'sha256': digest(text), 'line_count': len(lines), 'page_mapping': mapping, 'synthetic_notice': 'Mock document only; cite exported text lines, not real PDF performance.'}, 0
    raise ValueError('Unknown command.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', required=True, help='Private work/tool-state directory for this run; reuse it across calls.')
    commands = parser.add_subparsers(dest='command', required=True)
    search = commands.add_parser('search')
    search.add_argument('--query', required=True)
    fetch = commands.add_parser('fetch')
    fetch.add_argument('--source-id', required=True)
    fetch.add_argument('--output', required=True)
    parse = commands.add_parser('parse')
    parse.add_argument('--input', required=True)
    parse.add_argument('--engine', choices=['native', 'fallback'], required=True)
    parse.add_argument('--output', required=True)
    args = parser.parse_args()
    state_dir = Path(args.state_dir).resolve()
    if within(state_dir, CORPUS) or within(state_dir, BASE / 'materials'):
        parser.error('State directory must be in run work/, not in input corpus or materials.')
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / 'service-state.json'
    state = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {'service_schema': 1, 'call_count': 0}
    state['call_count'] += 1
    parameters = {k: v for k, v in vars(args).items() if k != 'state_dir'}
    started = utc()
    clock_start = time.monotonic()
    try:
        response, code = perform(args, state)
    except (ValueError, OSError, KeyError, TypeError) as error:
        response, code = {'status': 'invalid_request', 'error': str(error), 'retryable': False}, 4
    response['service_call_id'] = 'S3-CALL-%03d' % state['call_count']
    event = {'id': response['service_call_id'], 'started_at': started, 'finished_at': utc(), 'duration_seconds': round(time.monotonic() - clock_start, 6), 'command': args.command, 'parameters': parameters, 'response': response, 'exit_code': code, 'synthetic_tool': True}
    atomic_json(state_file, state)
    with (state_dir / 'events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + '\n')
    print(json.dumps(response, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
