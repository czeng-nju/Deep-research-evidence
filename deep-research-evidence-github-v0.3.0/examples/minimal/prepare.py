#!/usr/bin/env python3
"""Prepare actual example inputs; never generate or copy a research answer."""
from pathlib import Path
import shutil
import sys

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]


def main():
    if Path.cwd().resolve() != ROOT:
        print('Run from the repository root: python3 examples/minimal/prepare.py', file=sys.stderr)
        return 2
    required = [BASE / 'case.json', BASE / 'task.md', BASE / 'agent-prompt.txt', BASE / 'materials' / 'filter-a.txt', BASE / 'materials' / 'filter-b.txt']
    if any(not path.is_file() for path in required):
        print('Required example inputs are missing; restore the complete examples/minimal directory before preparing.', file=sys.stderr)
        return 2
    run = BASE / 'run'
    try:
        run.mkdir()  # Refuse to overwrite an existing run, including a symlink.
    except FileExistsError:
        print('examples/minimal/run already exists. Preserve it; resume that run or archive it under a new name before preparing again.', file=sys.stderr)
        return 2
    inputs = run / 'input'
    inputs.mkdir()
    for name in ('case.json', 'task.md'):
        shutil.copyfile(BASE / name, inputs / name)
    shutil.copytree(BASE / 'materials', inputs / 'materials')
    # Only actual inputs are copied. No result, state or gold is manufactured.
    print('Prepared examples/minimal/run/input. No research Agent has run and no result was generated.\n')
    print('Give the following prompt to an actual Agent with local file/Python tools:\n')
    print((BASE / 'agent-prompt.txt').read_text(encoding='utf-8'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
