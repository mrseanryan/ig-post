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
    CONFIG_FILE,
    load_json,
    save_json,
    login,
    post_images,
)
from util_image import prepare_image_to_path
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


def find_images(directory: Path, prefix: Optional[str]) -> list[Path]:
    """
    Recursively find JPG files beginning with the requested prefix.

    Matching is case-insensitive for both the prefix and .jpg extension.

    Results are sorted by modification time, oldest first.
    """
    if not prefix:
        images = [
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() == ".jpg"
        ]
    else:
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


def _normalize_config_path(p: str) -> Path:
    """Normalize a config-provided path across platforms.

    On Windows, also support Git Bash/MSYS style paths like '/d/some/path/file.json'
    by converting them to 'D:/some/path/file.json'.
    """
    s = os.path.expanduser(str(p))
    if os.name == "nt" and len(s) >= 4 and s[0] == "/" and s[2] == "/" and s[1].isalpha():
        drive = s[1].upper()
        rest = s[3:]
        s = f"{drive}:/{rest}"
    return Path(s).resolve()




def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Post previously-unposted JPG images "
            "from a directory to Instagram."
        )
    )

    parser.add_argument(
        "directory",
        type=str,
        help="Directory to recursively search",
    )

    parser.add_argument(
        "--prefix",
        default=None,
        help=(
            "Filename prefix to match. If omitted, all .jpg files are considered."
        ),
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
        "--auto-post",
        action="store_true",
        help=(
            "Actually upload to Instagram (WARNING: may trigger Instagram security checks). "
            "Default behavior is non-posting preview."
        ),
    )

    parser.add_argument(
        "--generate-text",
        action="store_true",
        help=(
            "Use OpenAI to generate a title/caption/hashtags per image. "
            "Requires OPENAI_API_KEY."
        ),
    )

    parser.add_argument(
        "--copyright",
        metavar="TEXT",
        help=(
            "Overlay copyright text at bottom-right of each image. "
            "If omitted, falls back to ~/.config/ig-post/config.json 'copyright_text' if present."
        ),
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Print per-image details: full file path and generated text/caption"
        ),
    )

    parser.add_argument(
        "--mark-as-posted",
        action="store_true",
        help=(
            "Do not upload, but record selected images as posted in the state file (assumes you post manually)."
        ),
    )

    args = parser.parse_args()

    # Accept both native Windows paths and Git Bash/MSYS style paths like '/c/...'
    directory = _normalize_config_path(args.directory)

    if not directory.is_dir():
        print(
            f"Error: not a directory: {directory}",
            file=sys.stderr,
        )
        return 1

    # Empty/omitted prefix means match all JPGs

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
        if args.prefix:
            print(f"No JPG files starting with '{args.prefix}' found.")
        else:
            print("No JPG files found.")
        return 0

    base_caption = caption_from_directory(directory)

    # Load optional runtime config
    config = load_json(CONFIG_FILE, {})

    # Resolve state file location (overridable via config)
    state_file: Path
    cfg_state_path = config.get("posted_state_path") if isinstance(config, dict) else None
    if cfg_state_path:
        state_file = _normalize_config_path(str(cfg_state_path))
        if not state_file.exists():
            print(
                f"Error: configured posted_state_path does not exist: {state_file}",
                file=sys.stderr,
            )
            print("Create the file or remove/adjust 'posted_state_path' in config.")
            return 1
    else:
        state_file = STATE_FILE

    state = load_json(state_file, {})

    copyright_text = (
        args.copyright
        if args.copyright is not None
        else config.get("copyright_text")
    )

    print()
    print(f"Directory:     {directory}")
    prefix_display = args.prefix if args.prefix else "(none — all .jpg)"
    print(f"Prefix:        {prefix_display}")
    print(f"Caption:       {base_caption}")
    print(f"Run limit:     {args.max_images} image(s)")
    print(f"Minimum delay: {args.min_delay:.0f} seconds")
    try:
        print(f"State file:    {str(state_file)}")
    except Exception:
        pass
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

    print()

    # Always prepare images locally into ./temp
    out_dir = Path("./temp").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Preparing images to: {out_dir}")

    for image, file_hash, generated in pending_with_text:
        rel = image.relative_to(directory)
        dst = out_dir / (image.stem + ".prepared.jpg")
        try:
            saved = prepare_image_to_path(
                image,
                dst,
                copyright_text=copyright_text,
            )
            print(f"  PREPARED  {rel} -> {saved}")
            if args.verbose:
                print(f"    Path:     {str(image.resolve())}")
                print(f"    Saved:    {str(saved)}")
                if generated:
                    print(f"    Title:    {generated.get('title','')}")
                    print(f"    Caption:  {generated.get('caption','')}")
                    hashtags = generated.get("hashtags", [])
                    if hashtags:
                        hashtag_line = " ".join(f"#{t}" for t in hashtags[:IG_MAX_HASHTAGS])
                        print(f"    Hashtags: {hashtag_line}")
        except Exception as exc:
            print(
                f"Error: failed to prepare {rel}: {exc}",
                file=sys.stderr,
            )
            return 1

    # Conflict check: cannot both auto-post and mark-as-posted
    if args.auto_post and args.mark_as_posted:
        print(
            "Error: --auto-post and --mark-as-posted are mutually exclusive.",
            file=sys.stderr,
        )
        return 1

    # Mark as posted without uploading
    if args.mark_as_posted:
        print("\nMarking images as posted in state file (no upload).")
        for image, file_hash, generated in pending_with_text:
            entry = {
                "path": str(image),
                "filename": image.name,
                "instagram_id": "manual",
                "posted_at": datetime.now().isoformat(timespec="seconds"),
            }
            if isinstance(generated, dict) and generated:
                entry["generated_text"] = generated
            state[file_hash] = entry
        save_json(state_file, state)
        print(f"Saved state: {state_file}")
        print("Done. No uploads were performed.")
        return 0

    # Default: preview only unless --auto-post
    if not args.auto_post:
        print("\nPreview mode — no uploads performed.")
        print("Use --auto-post to actually upload (WARNING: may trigger Instagram security checks).")
        # Show summary of what would be posted
        for image, file_hash, generated in pending_with_text:
            rel = image.relative_to(directory)
            print(f"  WOULD POST  {rel} ({format_mtime(image)})")
            if args.verbose:
                print(f"    Path:     {str(image.resolve())}")
            if generated:
                print(f"    Title:    {generated.get('title','')}")
                if args.verbose:
                    print(f"    Caption:  {generated.get('caption','')}")
                else:
                    print(f"    Caption:  {first_line(generated.get('caption',''))}")
                hashtags = generated.get("hashtags", [])
                if hashtags:
                    hashtag_line = " ".join(f"#{t}" for t in hashtags[:IG_MAX_HASHTAGS])
                    print(f"    Hashtags: {hashtag_line}")
        return 0

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
        state_file=state_file,
        min_delay=args.min_delay,
        verbose=args.verbose,
        copyright_text=copyright_text,
    )

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
