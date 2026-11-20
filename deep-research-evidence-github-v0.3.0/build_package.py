#!/usr/bin/env python3
"""Build a deterministic text-only .skill archive; never publish or submit it."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent / 'skill')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'dist' / 'deep-research-evidence-0.3.0.skill')
    args = parser.parse_args()
    source = args.source.resolve()
    result = subprocess.run([sys.executable, str(source / 'scripts' / 'validate.py'),
                             'package', '--path', str(source)], capture_output=True, text=True)
    if result.returncode:
        sys.stderr.write(result.stdout + result.stderr)
        return result.returncode
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(args.output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_dir():
                continue
            if path.is_symlink():
                raise ValueError('Symlinks are not permitted: ' + str(path))
            data = path.read_bytes()
            data.decode('utf-8')
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo('deep-research-evidence/' + relative, (2026, 11, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
            manifest.append({'path': relative, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    receipt = {'archive': args.output.name, 'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
               'files': manifest, 'text_bytes': sum(item['bytes'] for item in manifest),
               'scope': 'Packaging integrity only; not a semantic or official benchmark result.'}
    args.output.with_suffix('.manifest.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'archive': str(args.output), 'files': len(manifest), 'text_bytes': receipt['text_bytes'], 'sha256': receipt['sha256']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
