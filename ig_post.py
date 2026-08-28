#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from util_instagram import (
    CONFIG_DIR,
    SESSION_FILE,
    STATE_FILE,
    load_json,
    save_json,
    login,
    post_images,
)
from util_gen_text import (
    generate_text_for_pending,
    first_line,
    IG_MAX_HASHTAGS,
)


def sha256_file(path: Path) -> str:
    """Return SHA-256 hash of a file."""
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def caption_from_directory(directory: Path) -> str:
    """
    Convert the input directory name into an Instagram caption.

    Example:
        My_summer_holiday_2026
        ->
        My summer holiday 2026
    """
    return directory.name.replace("_", " ")


def find_images(directory: Path, prefix: str) -> list[Path]:
    """
    Recursively find JPG files beginning with the requested prefix.

    Matching is case-insensitive for both the prefix and .jpg extension.

    Results are sorted by modification time, oldest first.
    """
    prefix_lower = prefix.lower()

    images = [
        path
        for path in directory.rglob("*")
        if (
            path.is_file()
            and path.name.lower().startswith(prefix_lower)
            and path.suffix.lower() == ".jpg"
        )
    ]

    # Oldest first.
    images.sort(key=lambda path: path.stat().st_mtime)

    return images


def format_mtime(path: Path) -> str:
    timestamp = path.stat().st_mtime
    return datetime.fromtimestamp(timestamp).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


DEFAULT_MAX_IMAGES = 3
DEFAULT_MIN_DELAY = 120.0




def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Post previously-unposted JPG images "
            "from a directory to Instagram."
        )
    )

    parser.add_argument(
        "directory",
        type=Path,
        help="Directory to recursively search",
    )

    parser.add_argument(
        "--prefix",
        default="cover",
        help="Filename prefix to match (default: cover)",
    )

    parser.add_argument(
        "--max-images",
        type=int,
        default=DEFAULT_MAX_IMAGES,
        help=(
            "Maximum number of images to post in one run "
            f"(default: {DEFAULT_MAX_IMAGES})"
        ),
    )

    parser.add_argument(
        "--min-delay",
        type=float,
        default=DEFAULT_MIN_DELAY,
        help=(
            "Minimum delay between posts in seconds "
            f"(default: {DEFAULT_MIN_DELAY:.0f})"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be posted without uploading anything",
    )

    parser.add_argument(
        "--generate-text",
        action="store_true",
        help=(
            "Use OpenAI to generate a title/caption/hashtags per image. "
            "Requires OPENAI_API_KEY."
        ),
    )

    args = parser.parse_args()

    directory = args.directory.expanduser().resolve()

    if not directory.is_dir():
        print(
            f"Error: not a directory: {directory}",
            file=sys.stderr,
        )
        return 1

    if not args.prefix:
        print(
            "Error: --prefix cannot be empty.",
            file=sys.stderr,
        )
        return 1

    if args.max_images < 1:
        print(
            "Error: --max-images must be at least 1.",
            file=sys.stderr,
        )
        return 1

    if args.min_delay < 0:
        print(
            "Error: --min-delay cannot be negative.",
            file=sys.stderr,
        )
        return 1

    images = find_images(
        directory,
        args.prefix,
    )

    if not images:
        print(
            f"No JPG files starting with "
            f"'{args.prefix}' found."
        )
        return 0

    base_caption = caption_from_directory(directory)

    state = load_json(
        STATE_FILE,
        {},
    )

    print()
    print(f"Directory:     {directory}")
    print(f"Prefix:        {args.prefix}")
    print(f"Caption:       {base_caption}")
    print(f"Run limit:     {args.max_images} image(s)")
    print(f"Minimum delay: {args.min_delay:.0f} seconds")
    print()

    print(
        f"Found {len(images)} matching image(s) recursively"
    )
    print()

    pending: list[tuple[Path, str]] = []

    for image in images:
        file_hash = sha256_file(image)

        if file_hash in state:
            print(
                f"  SKIP     {image.relative_to(directory)} "
                f"({format_mtime(image)})"
            )
        else:
            print(
                f"  PENDING  {image.relative_to(directory)} "
                f"({format_mtime(image)})"
            )
            pending.append(
                (image, file_hash)
            )

    print()

    already_posted = len(images) - len(pending)

    print(f"Already posted: {already_posted}")
    print(f"Pending:        {len(pending)}")

    if not pending:
        print("\nNothing to post.")
        return 0

    # Hard per-run limit.
    pending = pending[:args.max_images]

    print(f"Selected:       {len(pending)}")

    # If requested, generate per-image text before dry-run or posting
    pending_with_text: list[tuple[Path, str, Optional[dict]]] = []
    if args.generate_text:
        ok, gen_pending = generate_text_for_pending(
            pending,
            directory=directory,
            base_caption_hint=base_caption,
        )
        if not ok:
            return 1
        pending_with_text = gen_pending
    else:
        # Preserve shape: (image, file_hash, None)
        pending_with_text = [
            (image, file_hash, None) for (image, file_hash) in pending
        ]

    if args.dry_run:
        print()
        print("DRY RUN — nothing will be uploaded.")
        print()

        for image, file_hash, generated in pending_with_text:
            rel = image.relative_to(directory)
            print(f"  WOULD POST  {rel} ({format_mtime(image)})")
            if generated:
                print(f"    Title:    {generated.get('title','')}")
                print(f"    Caption:  {first_line(generated.get('caption',''))}")
                hashtags = generated.get("hashtags", [])
                if hashtags:
                    hashtag_line = " ".join(f"#{t}" for t in hashtags[:IG_MAX_HASHTAGS])
                    print(f"    Hashtags: {hashtag_line}")

        return 0

    print()

    try:
        client = login()
    except Exception as exc:
        print(
            f"\nLogin failed: {exc}",
            file=sys.stderr,
        )
        return 1

    print()

    success = post_images(
        client=client,
        pending=pending_with_text,
        default_caption=base_caption,
        state=state,
        min_delay=args.min_delay,
    )

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
