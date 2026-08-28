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
from typing import Optional, Tuple
import traceback

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

# Instagram caption constraints
IG_CAPTION_LIMIT = 2200
IG_MAX_HASHTAGS = 30

# OpenAI defaults (used only when --generate-text is enabled)
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_HASHTAG_COUNT = 10


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
    pending: list[tuple[Path, str, Optional[dict]]],
    default_caption: str,
    state: dict,
    min_delay: float,
) -> bool:
    """
    Post images sequentially.

    A random delay of at least min_delay is inserted between uploads.
    The image is recorded only after Instagram accepts the upload.
    """
    posted_count = 0

    for index, (image, file_hash, generated) in enumerate(pending, start=1):
        print(
            f"[{index}/{len(pending)}] "
            f"Posting {image.name} "
            f"({format_mtime(image)})..."
        )

        # Prefer a per-image generated caption if provided
        caption = (
            (generated or {}).get("full_caption")
            if isinstance(generated, dict)
            else None
        ) or default_caption

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
        entry = {
            "path": str(image),
            "filename": image.name,
            "instagram_id": str(media.pk),
            "posted_at": datetime.now().isoformat(
                timespec="seconds"
            ),
        }

        # Persist generated text if available
        if isinstance(generated, dict):
            entry["generated_text"] = generated

        state[file_hash] = entry

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


# -----------------------------
# OpenAI integration (optional)
# -----------------------------

def first_line(text: str) -> str:
    """Return the first line of a potentially multi-line string."""
    return (text or "").splitlines()[0] if text else ""


def _load_openai_client():
    """
    Lazy-import and initialize the OpenAI client.

    Returns (client, model).
    Exits with an error message if the API key is missing or the SDK is not installed.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print(
            "Error: --generate-text requires OPENAI_API_KEY to be set.",
            file=sys.stderr,
        )
        return None, None

    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:
        print(
            f"Error: OpenAI Python package is required for --generate-text: {exc}",
            file=sys.stderr,
        )
        return None, None

    client = OpenAI()
    model = os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    return client, model


def _image_to_data_url(path: Path) -> str:
    """Encode an image file as a base64 data URL for chat image input."""
    import base64

    mime = "image/jpeg"
    b = path.read_bytes()
    b64 = base64.b64encode(b).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```") and t.endswith("```"):
        # Remove first and last fence lines
        lines = t.splitlines()
        # Drop first and last fence
        if len(lines) >= 2:
            # If the first line is like ```json keep stripping it
            return "\n".join(lines[1:-1]).strip()
    return t


def _extract_json(text: str) -> str:
    """
    Try to extract a JSON object from model output.
    Attempts:
    1) direct text
    2) strip ```json fences
    3) substring from first '{' to last '}'
    """
    # 1) direct
    t = text.strip()
    if t.startswith("{") and t.endswith("}"):
        return t

    # 2) strip code fences
    t2 = _strip_code_fences(t)
    if t2.startswith("{") and t2.endswith("}"):
        return t2

    # 3) best-effort brace slice
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start : end + 1]

    return t


def _compose_full_caption(title: str, body: str, hashtags: list[str]) -> tuple[str, list[str]]:
    """
    Compose the final Instagram caption under platform limits.

    Strategy:
    - Trim hashtags to IG_MAX_HASHTAGS
    - Build caption as: Title + blank + Body + blank + hashtags
    - If over 2200 chars, first truncate Body, then reduce hashtags
    """
    clean_tags = [t.strip().lower().replace(" ", "") for t in hashtags if t and t.strip()]
    clean_tags = [t for t in clean_tags if t]
    clean_tags = clean_tags[:IG_MAX_HASHTAGS]

    tag_line = " ".join(f"#{t}" for t in clean_tags)

    parts = []
    if title:
        parts.append(title)
    if body:
        parts.append("")
        parts.append(body)
    if tag_line:
        parts.append("")
        parts.append(tag_line)

    full = "\n".join(parts)

    if len(full) <= IG_CAPTION_LIMIT:
        return full, clean_tags

    # Try truncating body first
    # Rebuild without hashtags for length calc
    base_parts = []
    if title:
        base_parts.append(title)
    if body:
        base_parts.append("")
        base_parts.append(body)
    base_no_tags = "\n".join(base_parts)

    # Available space for hashtags (including separating blank line and tags)
    if len(base_no_tags) > IG_CAPTION_LIMIT:
        # Need to truncate body
        # Keep title and a blank line if possible
        allowable = IG_CAPTION_LIMIT - (len(title) + (1 if title else 0) + 1)  # rough allowance for newlines
        allowable = max(0, allowable)
        truncated_body = (body or "")[: max(0, allowable)]
        base_no_tags = "\n".join([p for p in [title, "", truncated_body] if p != "" or truncated_body])

    # Now try adding as many tags as fit
    if not clean_tags:
        return base_no_tags[:IG_CAPTION_LIMIT], []

    tag_prefix = ("\n" if base_no_tags else "") + "\n"  # blank line then tags
    result = base_no_tags
    used = []
    for t in clean_tags:
        candidate = (result + tag_prefix + " ".join([f"#{x}" for x in used + [t]])).strip("\n")
        if len(candidate) <= IG_CAPTION_LIMIT:
            used.append(t)
            result = base_no_tags  # keep base; only measuring
            continue
        else:
            break

    tag_line2 = " ".join(f"#{t}" for t in used)
    if tag_line2:
        final = base_no_tags + "\n\n" + tag_line2 if base_no_tags else tag_line2
    else:
        final = base_no_tags[:IG_CAPTION_LIMIT]
    return final, used


def _generate_for_image(client, model: str, image: Path, base_caption_hint: str) -> Optional[dict]:
    """
    Call OpenAI to generate title/caption/hashtags for a single image.
    Returns a dict with keys: title, caption, hashtags, model, full_caption
    On failure, returns None.
    """
    try:
        data_url = _image_to_data_url(image)
    except Exception as exc:
        print(f"Warning: could not read image for generation: {image} ({exc})")
        return None

    system_prompt = (
        "You write succinct, tasteful Instagram text for an amateur "
        "photographer's portfolio. Keep it authentic, avoid hype, and do not use emojis."
    )
    user_prompt = (
        "Analyze the photo and propose: \n"
        "- title: 3–8 words, Title Case, <= 60 chars.\n"
        "- caption: 1–2 short sentences suitable for Instagram. No emojis.\n"
        f"- hashtags: {DEFAULT_HASHTAG_COUNT} relevant, specific, lowercase tags (no #, no spaces).\n"
        "Focus on photography aspects (light, mood, subject, location if evident).\n"
        f"Base caption hint (directory name): '{base_caption_hint}'.\n"
        "Return strictly a compact JSON object with keys: title, caption, hashtags."
    )

    try:
        # OpenAI Python SDK v1.x style
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            temperature=0.7,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        tb = traceback.format_exc()
        print(
            "Warning: OpenAI request failed for "
            f"{image.name}: {exc}\n"
            f"{os.linesep.join(tb.splitlines()[:6])}"
        )
        return None

    try:
        content = (resp.choices[0].message.content or "").strip()
        json_text = _extract_json(content)
        payload = json.loads(json_text)
        title = str(payload.get("title", "")).strip()
        body = str(payload.get("caption", "")).strip()
        tags = payload.get("hashtags", []) or []
        if not isinstance(tags, list):
            tags = []
        tags = [str(t).strip().lower().replace("#", "") for t in tags]
    except Exception as exc:
        tb = traceback.format_exc()
        snippet = content[:200].replace("\n", "\\n") if 'content' in locals() else ""
        print(
            "Warning: could not parse OpenAI response for "
            f"{image.name}: {exc}\n"
            f"Top of stack:\n{os.linesep.join(tb.splitlines()[:6])}\n"
            f"Response snippet: {snippet}"
        )
        return None

    full_caption, used_tags = _compose_full_caption(title, body, tags)
    return {
        "title": title,
        "caption": body,
        "hashtags": used_tags[:IG_MAX_HASHTAGS],
        "model": model,
        "full_caption": full_caption,
    }


def generate_text_for_pending(
    pending: list[tuple[Path, str]],
    directory: Path,
    base_caption_hint: str,
) -> Tuple[bool, list[tuple[Path, str, Optional[dict]]]]:
    """
    Generate text for each image in the pending list.
    Returns (ok, pending_with_text). If not ok, prints an error and pending_with_text is empty.
    """
    client, model = _load_openai_client()
    if not client:
        return False, []

    result: list[tuple[Path, str, Optional[dict]]] = []
    for image, file_hash in pending:
        print(f"Generating text via OpenAI for: {image.relative_to(directory)}")
        generated = _generate_for_image(client, model, image, base_caption_hint)
        if not generated:
            print("  Falling back to base caption for this image.")
        result.append((image, file_hash, generated))

    return True, result


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
