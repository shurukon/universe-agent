"""scripts/cleanup_test_temps.py — sweep orphaned Universe test temp dirs.

The test suite creates gax-test-*/gax-live-* dirs under the system temp.
Each fixture has a teardown, but a KILLED pytest run (Ctrl-C, crash,
timeout) skips it — those orphans accumulate (audit 2026-09: 3,803 dirs,
~47.5GB, disk at 100%). Run this after any interrupted suite:

    python scripts/cleanup_test_temps.py [--dry-run]

Only dirs matching the Universe prefixes are touched; anything else in
temp is left alone. Dirs modified in the last 30 minutes are skipped —
they may belong to a suite still running.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
import time
from pathlib import Path

PREFIXES = ("gax-test-", "gax-live-")
MIN_AGE_S = 30 * 60  # skip anything possibly in active use


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be removed without removing")
    args = ap.parse_args()
    root = Path(tempfile.gettempdir())
    if not root.exists():
        print(f"temp dir {root} does not exist")
        return 0
    now = time.time()
    removed = kept_fresh = 0
    freed_bytes = 0
    for d in root.iterdir():
        if not d.is_dir():
            continue
        # Windows mkdtemp may append a trailing dot; normalize for matching
        if not d.name.rstrip(".").startswith(PREFIXES):
            continue
        try:
            age = now - d.stat().st_mtime
        except OSError:
            continue
        if age < MIN_AGE_S:
            kept_fresh += 1
            continue
        try:
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        except OSError:
            size = 0
        if args.dry_run:
            print(f"[dry-run] would remove {d} ({size / 1e6:.1f} MB)")
        else:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception as e:
                print(f"failed to remove {d}: {e}")
                continue
        removed += 1
        freed_bytes += size
    print(f"removed {removed} dir(s), freed {freed_bytes / 1e9:.2f} GB"
          f"{'; skipped ' + str(kept_fresh) + ' recent dir(s) still in use' if kept_fresh else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
