"""Promote a recorded run into a replayable demo cassette.

Usage (on the machine that ran it, from the repo root):
  python scripts/promote_run.py <run_id> [<run_id> ...]          # -> demo/cassettes/<slug>/ (commit these)
  python scripts/promote_run.py --data <run_id>                  # -> $DATA_DIR/cassettes/<slug>/ (server only)
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cassettes import SHIPPED_DIR, Cassette, slugify  # noqa: E402
from app.config import settings  # noqa: E402


def promote(run_id: str, target_root: Path) -> Path:
    src = settings.data_dir / "runs" / run_id / "cassette"
    if not (src / "cassette.json").is_file():
        raise SystemExit(f"run {run_id} has no recording at {src} (only finished live runs are recorded)")
    cassette = Cassette.load(src)
    dest = target_root / slugify(cassette.question)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def main() -> None:
    args = sys.argv[1:]
    target = SHIPPED_DIR
    if args and args[0] == "--data":
        target, args = settings.data_dir / "cassettes", args[1:]
    if not args:
        raise SystemExit(__doc__)
    for run_id in args:
        print(f"{run_id} -> {promote(run_id, target)}")


if __name__ == "__main__":
    main()
