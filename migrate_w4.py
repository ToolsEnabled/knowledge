#!/usr/bin/env python3
"""Export a deterministic W4 HEAD conversion to a NEW directory; never write source."""
import argparse
from pathlib import Path
import sys
from knowledge import KnowledgeError, read_snapshot, render_document


def convert(source, destination):
    snapshot = read_snapshot(Path(source), 5)
    destination = Path(destination)
    # Exclusive creation: never overwrite a previous export or any owner file.
    destination.mkdir(mode=0o700)
    for path, content in snapshot['files'].items():
        if path in snapshot['documents']:
            content = render_document(snapshot['documents'][path])
        target = destination/path
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open('xb') as handle:
            handle.write(content.encode('utf-8'))
    return snapshot['revision']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        revision = convert(args.source, args.destination)
    except (KnowledgeError,OSError,ValueError):
        print('Migration failed; source unchanged; retain any partial export for inspection.', file=sys.stderr)
        return 2
    print('Exported CURRENT/HISTORY from committed revision '+revision+'; source unchanged.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
