# ig-post

Small Python 3.12 CLI for preparing and optionally posting selected JPGs from a directory to a personal Instagram account using `instagrapi`, with optional AI-generated captions and tags.

> WARNING: Uploading via private API can trigger Instagram security checks. By default this tool does not upload; use `--auto-post` only if you accept that risk.

> **Important:** `instagrapi` uses Instagram's unofficial/private API. This project is intended for low-volume, personal automation. It is deliberately conservative about posting frequency and stops rather than aggressively retrying when Instagram throttles or challenges the client.

## What it does

Given a directory, `ig-post`:

1. Finds `.jpg` files whose names start with a configurable prefix.
2. Sorts them by file modification time, oldest first.
3. Uses SHA-256 hashes to remember images already posted.
4. Optionally uses AI (OpenAI) to generate suitable titles, captions and hashtags.
5. Always prepares oriented images (EXIF-aware) with optional copyright overlay into `./temp` for review.
6. Does not upload by default; uploading is opt-in via `--auto-post`.
7. Can mark images as posted without uploading via `--mark-as-posted` for manual posting workflows.
8. Posts at most **3 images per run** by default when uploading.
9. Waits at least **2 minutes between images** by default when uploading, with a small random addition.
10. Generates the base caption from the directory name, replacing `_` with spaces.
11. Records each successful upload immediately, so an interrupted run resumes safely.
12. Persists the Instagram session in `~/.config/ig-post/session.json`.

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

### Example Output

```
Already posted: 13
Pending:        11
Selected:       3
Generating text via OpenAI for: Transfagarasan_via_Poenari_Citadel_and_Balea_Lake\9-Transfagarasan_North\cover.SA709810.ok.JPG
Generating text via OpenAI for: Transfagarasan_via_Poenari_Citadel_and_Balea_Lake\4-Poenari_Citadel\cover.SA709649.ok.JPG

Preparing images to: C:\tools\ig-post\ig-post\temp
  PREPARED  Transfagarasan_via_Poenari_Citadel_and_Balea_Lake\9-Transfagarasan_North\cover.SA709810.ok.JPG -> C:\tools\ig-post\ig-post\temp\cover.SA709810.ok.prepared.jpg
    Path:     c:\galleries\2026-07_-_Transylvania_Romania\Transfagarasan_via_Poenari_Citadel_and_Balea_Lake\9-Transfagarasan_North\cover.SA709810.ok.JPG
    Saved:    C:\tools\ig-post\ig-post\temp\cover.SA709810.ok.prepared.jpg
    Title:    Road Through Transylvanian Mountains
    Caption:  A serene landscape unfolding beneath a clear sky. The mountains stand tall, inviting exploration.
    Hashtags: #transylvania #romania #landscapephotography #mountainview #roadtrip #naturelovers #scenicroute #outdoorphotography #travelphotography #adventure
  PREPARED  Transfagarasan_via_Poenari_Citadel_and_Balea_Lake\3-Lowland_Rivers\cover.SA709632.edit.editted.jpg -> C:\tools\ig-post\ig-post\temp\cover.SA709632.edit.editted.prepared.jpg
    Path:     c:\galleries\2026-07_-_Transylvania_Romania\Transfagarasan_via_Poenari_Citadel_and_Balea_Lake\3-Lowland_Rivers\cover.SA709632.edit.editted.jpg
    Saved:    C:\tools\ig-post\ig-post\temp\cover.SA709632.edit.editted.prepared.jpg
    Title:    Serenity in Transylvania's Landscape
    Caption:  A tranquil river winds through the lush greenery of Transylvania. The clear skies and vibrant colors evoke a sense of peace.
    Hashtags: #transylvania #landscapephotography #naturephotography #river #greenery #scenicview #outdoorphotography #romania #peaceful #explore

Marking images as posted in state file (no upload).
Saved state: c:\galleries\ig-posted.json
Done. No uploads were performed.
```

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

### Default: Preview (No Posting)

By default, the tool does not upload anything. It previews what would be posted, prepares oriented images with optional overlay, and writes them to `./temp`.

Use `--verbose` to show full paths and, when `--generate-text` is enabled, the generated title/caption/hashtags per selected image:

```bash
ig-post /path/to/directory --generate-text --verbose
```

### Prepared Images

The tool always prepares images locally (EXIF orientation + optional copyright overlay) and writes them to `./temp` as `<original>.prepared.jpg`. Paths are printed; add `--verbose` to also print the original path and generated text details when enabled.

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

### Copyright Overlay (optional)

Add a small copyright mark at the bottom-right of each image. The overlay is applied after EXIF orientation and saved into `./temp` as a prepared JPEG. If you choose to upload, those prepared images are used.

Usage (CLI override):

```bash
ig-post /path/to/directory --copyright "© 2026 Your Name"
```

Or set it once in a config file:

```jsonc
// ~/.config/ig-post/config.json
{
  "copyright_text": "© 2026 Your Name"
}
```

Notes:

  - The overlay uses a dark semi-transparent rectangle and white text with a black border for legibility.
  - Text width targets about 30% of the image width after EXIF orientation; padding and margins scale accordingly.
  - You can safely iterate since prepared images are always written to `./temp` and posting is opt-in.
- The default font is used to avoid external font dependencies.
- If the overlay fails for any reason, the image is uploaded without the overlay.

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

## Posting

Uploading is opt-in and may trigger Instagram security checks. Proceed cautiously.

```bash
ig-post /path/to/directory --auto-post
```

### Mark As Posted (manual posting)

If you prefer to post manually but still want the tool to remember the images as posted, use:

```bash
ig-post /path/to/directory --mark-as-posted
```

This adds the selected images to the state file without uploading anything. Combine with `--generate-text` and `--verbose` to see suggested text while you post manually.

## State

Posted-image state is stored by default in:

```text
~/.config/ig-post/posted.json
```

The key is a SHA-256 hash of the image contents, not its filename.

This means:

- Renaming an already-posted image does not cause it to be reposted.
- Replacing an image with different contents allows the replacement to be posted.
- Each successful upload is saved immediately.

You can override the state file path via config:

```jsonc
// ~/.config/ig-post/config.json
{
  "posted_state_path": "/absolute/or/relative/path/to/posted.json"
}
```

Relative paths are resolved from the current working directory. On Windows, Git Bash/MSYS-style drive paths like `/d/dir/file.json` are also accepted and normalized to `D:/dir/file.json`. If `posted_state_path` is set but the file does not exist, the tool exits with an error (it will not create a new file at a custom path). Create the file first or remove the override. You can inspect the state file:

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
python ig-post.py /path/to/directory
```

Or after installation:

```bash
ig-post /path/to/directory
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

Some photos carry an EXIF orientation flag instead of being physically rotated. The tool auto-applies EXIF orientation and writes prepared JPEGs into `./temp`. If the copyright overlay is enabled, it is applied after orientation. If you later choose to upload with `--auto-post`, the prepared images are used.
