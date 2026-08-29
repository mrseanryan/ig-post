import getpass
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired,
    ClientThrottledError,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
)

from PIL import Image
from util_image import prepare_image_for_upload


CONFIG_DIR = Path.home() / ".config" / "ig-post"
SESSION_FILE = CONFIG_DIR / "session.json"
STATE_FILE = CONFIG_DIR / "posted.json"
CONFIG_FILE = CONFIG_DIR / "config.json"


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


def get_credentials() -> tuple[str, str]:
    """
    Get Instagram credentials from env or prompt.
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
    """Persistent login with session load/save."""
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




def format_mtime(path: Path) -> str:
    from datetime import datetime as _dt

    timestamp = path.stat().st_mtime
    return _dt.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def post_images(
    client: Client,
    pending: list[tuple[Path, str, Optional[dict]]],
    default_caption: str,
    state: dict,
    state_file: Path,
    min_delay: float,
    verbose: bool = False,
    copyright_text: Optional[str] = None,
) -> bool:
    """
    Post images sequentially.

    Applies EXIF-based orientation, uploads a temporary JPEG, and removes it.
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

        caption = (
            (generated or {}).get("full_caption")
            if isinstance(generated, dict)
            else None
        ) or default_caption

        if verbose:
            # Print full absolute path and generated details if available
            try:
                abs_path = str(image.resolve())
            except Exception:
                abs_path = str(image)
            print(f"    Path:     {abs_path}")
            if isinstance(generated, dict) and generated:
                title = generated.get("title", "")
                body = generated.get("caption", "")
                hashtags = generated.get("hashtags", []) or []
                print(f"    Title:    {title}")
                print(f"    Caption:  {body}")
                if hashtags:
                    print("    Hashtags: " + " ".join(f"#{t}" for t in hashtags))
            else:
                print(f"    Caption:  {caption}")

        # Prepare oriented temp image
        temp_path = None
        try:
            temp_path = prepare_image_for_upload(image, copyright_text=copyright_text)
        except Exception as exc:
            print(f"Warning: could not prepare image (orientation): {image} ({exc})")
            # Fall back to original path
            temp_path = image

        try:
            media = client.photo_upload(
                str(temp_path),
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
            # Cleanup temp if we created one
            if temp_path is not None and temp_path is not image and Path(temp_path).exists():
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass
            return False

        except LoginRequired as exc:
            print(
                f"\nInstagram session is no longer valid: {exc}"
            )
            print("Stopping. The image was NOT marked as posted.")
            if temp_path is not None and temp_path is not image and Path(temp_path).exists():
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass
            return False

        except Exception as exc:
            print(
                f"\nUpload failed: {exc}",
                file=sys.stderr,
            )
            print("Stopping. The image was NOT marked as posted.")
            if temp_path is not None and temp_path is not image and Path(temp_path).exists():
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass
            return False

        finally:
            # Clean up oriented temp file after attempt; if temp equals original, skip
            if temp_path is not None and temp_path is not image and Path(temp_path).exists():
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        # Only record the image after successful upload.
        entry = {
            "path": str(image),
            "filename": image.name,
            "instagram_id": str(media.pk),
            "posted_at": datetime.now().isoformat(
                timespec="seconds"
            ),
        }

        if isinstance(generated, dict):
            entry["generated_text"] = generated

        state[file_hash] = entry

        save_json(state_file, state)

        posted_count += 1

        print(
            f"  OK — Instagram ID: {media.pk}"
        )

        if index < len(pending):
            delay = min_delay + random.uniform(0, min_delay * 0.25)
            print(
                f"  Waiting {delay:.0f} seconds before the next image..."
            )
            time.sleep(delay)

    print()
    print(f"Done. Posted {posted_count} image(s).")

    return True
