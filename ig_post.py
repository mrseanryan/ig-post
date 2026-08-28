#!/usr/bin/env python3

import argparse
import getpass
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired,
    ClientThrottledError,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
)

CONFIG_DIR = Path.home() / ".config" / "ig-post"
SESSION_FILE = CONFIG_DIR / "session.json"
STATE_FILE = CONFIG_DIR / "posted.json"

DEFAULT_MAX_IMAGES = 3
DEFAULT_MIN_DELAY = 120.0


def load_json(path: Path, default):
    if not path.exists():
        return default

    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not read {path}: {exc}")
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic replacement prevents corruption if interrupted.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


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


def get_credentials() -> tuple[str, str]:
    """
    Get Instagram credentials.

    Prefer environment variables:
        IG_USERNAME
        IG_PASSWORD

    Otherwise prompt interactively.
    """
    username = os.environ.get("IG_USERNAME")
    password = os.environ.get("IG_PASSWORD")

    if not username:
        username = input("Instagram username: ").strip()

    if not password:
        password = getpass.getpass("Instagram password: ")

    if not username or not password:
        raise RuntimeError(
            "Instagram username and password are required."
        )

    return username, password


def login() -> Client:
    """
    Log into Instagram using instagrapi's persistent-session pattern.

    Existing settings are loaded before login().
    Settings are saved again after a successful login.
    """
    username, password = get_credentials()

    client = Client()

    if SESSION_FILE.exists():
        print(f"Loading saved session: {SESSION_FILE}")

        try:
            client.load_settings(SESSION_FILE)
        except Exception as exc:
            print(f"Warning: could not load saved session: {exc}")
            print("Starting with a fresh client.")

    print("Logging in...")

    try:
        client.login(username, password)

    except Exception as exc:
        if isinstance(exc, ChallengeRequired):
            print(
                "\nInstagram requires an account challenge.\n"
                "Open Instagram in the official app and complete "
                "the requested verification."
            )

        elif isinstance(exc, ClientThrottledError):
            print(
                "\nInstagram throttled this client (HTTP 429).\n"
                "Do not repeatedly retry. Wait and try again later."
            )

        elif isinstance(exc, PleaseWaitFewMinutes):
            print(
                "\nInstagram asked you to wait before continuing.\n"
                "Do not repeatedly retry."
            )

        raise

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    client.dump_settings(SESSION_FILE)

    print(f"Session saved to: {SESSION_FILE}")

    return client


def post_images(
    client: Client,
    pending: list[tuple[Path, str]],
    caption: str,
    state: dict,
    min_delay: float,
) -> bool:
    """
    Post images sequentially.

    A random delay of at least min_delay is inserted between uploads.
    The image is recorded only after Instagram accepts the upload.
    """
    posted_count = 0

    for index, (image, file_hash) in enumerate(pending, start=1):
        print(
            f"[{index}/{len(pending)}] "
            f"Posting {image.name} "
            f"({format_mtime(image)})..."
        )

        try:
            media = client.photo_upload(
                str(image),
                caption,
            )

        except (
            ClientThrottledError,
            PleaseWaitFewMinutes,
            FeedbackRequired,
        ) as exc:
            print(
                f"\nInstagram is limiting this account/client: {exc}"
            )
            print("Stopping without retrying.")
            print(f"{posted_count} image(s) were posted this run.")
            return False

        except LoginRequired as exc:
            print(
                f"\nInstagram session is no longer valid: {exc}"
            )
            print("Stopping. The image was NOT marked as posted.")
            return False

        except Exception as exc:
            print(
                f"\nUpload failed: {exc}",
                file=sys.stderr,
            )
            print("Stopping. The image was NOT marked as posted.")
            return False

        # Only record the image after successful upload.
        state[file_hash] = {
            "path": str(image),
            "filename": image.name,
            "instagram_id": str(media.pk),
            "posted_at": datetime.now().isoformat(
                timespec="seconds"
            ),
        }

        save_json(STATE_FILE, state)

        posted_count += 1

        print(
            f"  OK — Instagram ID: {media.pk}"
        )

        # Don't wait after the final upload.
        if index < len(pending):
            # Minimum delay plus a random 0–25% addition.
            delay = min_delay + random.uniform(
                0,
                min_delay * 0.25,
            )

            print(
                f"  Waiting {delay:.0f} seconds "
                f"before the next image..."
            )

            time.sleep(delay)

    print()
    print(f"Done. Posted {posted_count} image(s).")

    return True


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

    caption = caption_from_directory(directory)

    state = load_json(
        STATE_FILE,
        {},
    )

    print()
    print(f"Directory:     {directory}")
    print(f"Prefix:        {args.prefix}")
    print(f"Caption:       {caption}")
    print(f"Run limit:     {args.max_images} image(s)")
    print(f"Minimum delay: {args.min_delay:.0f} seconds")
    print()

    print(
        f"Found {len(images)} matching image(s) recursively"
    )
    print()

    pending = []

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

    if args.dry_run:
        print()
        print(
            "DRY RUN — nothing will be uploaded."
        )
        print()

        for image, _ in pending:
            print(
                f"  WOULD POST  "
                f"{image.relative_to(directory)} "
                f"({format_mtime(image)})"
            )

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
        pending=pending,
        caption=caption,
        state=state,
        min_delay=args.min_delay,
    )

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
