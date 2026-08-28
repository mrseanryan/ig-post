# ig-post

Small Python 3.12 CLI for posting selected JPGs from a directory to a personal Instagram account using `instagrapi`.

> **Important:** `instagrapi` uses Instagram's unofficial/private API. This project is intended for low-volume, personal automation. It is deliberately conservative about posting frequency and stops rather than aggressively retrying when Instagram throttles or challenges the client.

## What it does

Given a directory, `ig-post`:

1. Finds `.jpg` files whose names start with a configurable prefix.
2. Sorts them by file modification time, oldest first.
3. Uses SHA-256 hashes to remember images already posted.
4. Posts at most **3 images per run** by default.
5. Waits at least **2 minutes between images** by default, with a small random addition.
6. Generates the caption from the directory name, replacing `_` with spaces.
7. Records each successful upload immediately, so an interrupted run resumes safely.
8. Persists the Instagram session in `~/.config/ig-post/session.json`.

Example:

```text
Summer_holiday_2026/
├── cover01.jpg
├── cover02.jpg
├── cover03.jpg
├── cover04.jpg
└── other.jpg
```

```bash
ig-post ~/photos/Summer_holiday_2026
```

The caption will be:

```text
Summer holiday 2026
```

Only `cover*.jpg` files are considered, and only the oldest three unposted images are selected.

## Requirements

- Python 3.12
- An Instagram account
- `instagrapi`
- Optional: OpenAI account and `OPENAI_API_KEY` if you use `--generate-text`
 - Optional: Pillow (installed via package deps) for EXIF-aware rotation handling

The current `instagrapi` release supports Python 3.10+ and its current documentation recommends the persistent `load_settings()` -> `login()` -> `dump_settings()` session pattern used by this project.

## Installation

Create a virtual environment:

Ubuntu:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Windows:

```bash
python -m venv .venv
.venv/Scripts/activate
```

Install the project:

```bash
python -m pip install -e .
```

Or, if you don't want an editable install:

```bash
python -m pip install .
```

## Credentials

The preferred approach is to use environment variables:

```bash
export IG_USERNAME="your_instagram_username"
export IG_PASSWORD="your_instagram_password"
```

The password is not stored by this program.

If the variables aren't present, the program prompts for them interactively.

On first successful login, `instagrapi` settings are saved to:

```text
~/.config/ig-post/session.json
```

This file contains authentication/session information and should be kept private.

## Usage

Basic:

```bash
ig-post /path/to/directory
```

Use another filename prefix:

```bash
ig-post /path/to/directory --prefix holiday
```

For example, `--prefix holiday` matches:

```text
holiday01.jpg
holiday02.jpg
holiday-old.jpg
```

Matching is case-insensitive.

### Dry run

Always useful before the first real run:

```bash
ig-post /path/to/directory --dry-run
```

This shows the files that would be selected without logging into Instagram or uploading anything.

### Generate Title/Caption/Hashtags (optional)

You can ask the tool to generate per-image Instagram text using OpenAI. This is off by default; the tool works normally without it.

Requirements:

- Set `OPENAI_API_KEY` in your environment

Usage:

```bash
export OPENAI_API_KEY=sk-...
ig-post /path/to/directory --generate-text
```

What it does per selected image:

- Generates a short Title (first line)
- Generates a 1–2 sentence Caption
- Generates about 10 relevant hashtags (trimmed to Instagram’s 30-tag limit if you override)

The final Instagram caption field is composed as:

```
Title

Caption text

#tag1 #tag2 #tag3 ...
```

Notes:

- Model: defaults to `gpt-4o-mini` for image understanding and text. You can override with `OPENAI_MODEL`.
- The tool trims content to stay under Instagram’s ~2,200-character caption limit.
- Generated text is persisted in `~/.config/ig-post/posted.json` next to each posted image’s entry.
- On any generation error, the tool falls back to the normal directory-based caption and continues.

### Maximum images

The default maximum is 3:

```bash
ig-post /path/to/directory --max-images 3
```

You can deliberately lower it:

```bash
ig-post /path/to/directory --max-images 1
```

The program will never post more than the specified number in a single invocation.

### Delay

The default minimum delay is 120 seconds:

```bash
ig-post /path/to/directory --min-delay 120
```

The actual delay is randomly selected from:

```text
min_delay ... min_delay + 25%
```

So the default is approximately 120–150 seconds.

The delay is only inserted between posts, not after the final post.

## State

Posted-image state is stored in:

```text
~/.config/ig-post/posted.json
```

The key is a SHA-256 hash of the image contents, not its filename.

This means:

- Renaming an already-posted image does not cause it to be reposted.
- Replacing an image with different contents allows the replacement to be posted.
- Each successful upload is saved immediately.

You can inspect the state:

```bash
cat ~/.config/ig-post/posted.json
```

If you intentionally want to reset the posting history, remove that file:

```bash
rm ~/.config/ig-post/posted.json
```

Be careful: doing so makes the program consider all matching images unposted.

## Ordering

Images are sorted using their filesystem modification time (`mtime`), oldest first.

For example:

```text
cover2.jpg   modified Aug 20
cover1.jpg   modified Aug 22
cover3.jpg   modified Aug 24
```

will be posted in this order:

```text
cover2.jpg
cover1.jpg
cover3.jpg
```

## Caption generation

The caption is the directory's basename with underscores converted to spaces.

```text
My_trip_to_Ireland_2026
```

becomes:

```text
My trip to Ireland 2026
```

Hyphens are intentionally left unchanged.

When `--generate-text` is enabled, the per-image generated text replaces this base caption for that image only. The base caption is still used as a hint for generation and as a fallback.

## Conservative API behavior

This tool is deliberately designed for low-volume use.

Defaults:

- Maximum 3 images per invocation
- Minimum 2 minutes between images
- No automatic retry loop after Instagram throttling
- Successful uploads are recorded immediately

If Instagram returns a throttling, feedback, or challenge response, the program stops instead of repeatedly retrying.

This does **not** guarantee that Instagram will never rate-limit or challenge the account.

For best results, use a stable device/session and avoid repeatedly deleting and recreating the saved session.

## Project layout

```text
ig-post/
├── ig-post.py
├── pyproject.toml
├── README.md
└── .gitignore
```

The runtime files are intentionally stored outside the project:

```text
~/.config/ig-post/
├── session.json
└── posted.json
```

## Development

Run directly:

```bash
python ig-post.py /path/to/directory --dry-run
```

Or after installation:

```bash
ig-post /path/to/directory --dry-run
```

Check the CLI help:

```bash
ig-post --help
```

## Security

Do not commit:

```text
session.json
posted.json
.env
```

The included `.gitignore` protects against accidentally adding the local runtime state if the project is placed under Git.

If you use environment variables for credentials, remember that shell history and process/environment handling can have their own security implications.

## License

 This small wrapper is provided without warranty. `instagrapi` is a separate project with its own license and terms.

## Image Orientation (EXIF)

Some photos carry an EXIF orientation flag instead of being physically rotated. The tool now auto-applies EXIF orientation before upload, writing a temporary JPEG to ensure correct on-platform rotation.
