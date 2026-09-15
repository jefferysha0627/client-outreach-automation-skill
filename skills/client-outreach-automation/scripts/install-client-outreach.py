#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
ASSET_DIR = SKILL_DIR / "assets" / "client-outreach"


def copytree(src: Path, dst: Path, force: bool) -> None:
    if dst.exists():
        if not force:
            raise SystemExit(f"Refusing to overwrite existing {dst}. Re-run with --force if intended.")
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the client-outreach tool into a project.")
    parser.add_argument("--target", default=".", help="Project directory to receive client-outreach/")
    parser.add_argument("--force", action="store_true", help="Overwrite existing client-outreach/")
    args = parser.parse_args()

    target = Path(args.target).resolve()
    if not ASSET_DIR.exists():
        raise SystemExit(f"Missing bundled asset directory: {ASSET_DIR}")
    target.mkdir(parents=True, exist_ok=True)

    destination = target / "client-outreach"
    copytree(ASSET_DIR, destination, args.force)

    config_example = destination / "config.example.json"
    config = destination / "config.json"
    if config_example.exists() and not config.exists():
        shutil.copy2(config_example, config)

    for relative in ("processed/.gitkeep", "logs/.gitkeep"):
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    outputs = target / "outputs"
    outputs.mkdir(exist_ok=True)

    print(f"Installed client-outreach tool at: {destination}")
    print("Next steps:")
    print("  1. Edit client-outreach/config.json sender fields and limits.")
    print("  2. Edit client-outreach/templates/email-template*.txt.")
    print("  3. Run: python3 client-outreach/setup-gmail-oauth.py")
    print("  4. Run: python3 client-outreach/run-outreach.py --check-setup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
