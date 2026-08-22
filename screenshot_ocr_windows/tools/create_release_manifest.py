"""Create a signed-artifact manifest for internal distribution.

The manifest is intentionally transport-neutral: publish the artifact and its
JSON file to the approved intranet server, file share, or MDM catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an internal release manifest")
    parser.add_argument("artifact", type=Path, help="EXE/APK artifact to publish")
    parser.add_argument("--app-id", default="com.lqpricecheck.desktop")
    parser.add_argument("--version", help="Release version; overrides --version-file")
    parser.add_argument("--version-file", type=Path, default=Path("VERSION"))
    parser.add_argument("--channel", default="stable", choices=("stable", "pilot"))
    parser.add_argument("--notes", default="", help="Short release note")
    parser.add_argument("--output", type=Path, default=Path("release-manifest.json"))
    args = parser.parse_args()

    artifact = args.artifact.resolve()
    if not artifact.is_file():
        parser.error(f"artifact does not exist: {artifact}")
    version = args.version or args.version_file.read_text(encoding="utf-8").strip()
    if not version:
        parser.error(f"version file is empty: {args.version_file}")

    manifest = {
        "appId": args.app_id,
        "channel": args.channel,
        "version": version,
        "publishedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "artifact": {
            "fileName": artifact.name,
            "sizeBytes": artifact.stat().st_size,
            "sha256": sha256(artifact),
        },
        "notes": args.notes,
    }
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {args.output} for {artifact.name}")


if __name__ == "__main__":
    main()
