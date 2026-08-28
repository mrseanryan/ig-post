import json
import os
import sys
import base64
import traceback
from pathlib import Path
from typing import Optional, Tuple

# Instagram caption constraints
IG_CAPTION_LIMIT = 2200
IG_MAX_HASHTAGS = 30

# OpenAI defaults (used only when generation is enabled)
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_HASHTAG_COUNT = 10


def first_line(text: str) -> str:
    return (text or "").splitlines()[0] if text else ""


def _load_openai_client():
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
    mime = "image/jpeg"
    b = path.read_bytes()
    b64 = base64.b64encode(b).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```") and t.endswith("```"):
        lines = t.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return t


def _extract_json(text: str) -> str:
    t = text.strip()
    if t.startswith("{") and t.endswith("}"):
        return t
    t2 = _strip_code_fences(t)
    if t2.startswith("{") and t2.endswith("}"):
        return t2
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start : end + 1]
    return t


def _compose_full_caption(title: str, body: str, hashtags: list[str]) -> tuple[str, list[str]]:
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

    base_parts = []
    if title:
        base_parts.append(title)
    if body:
        base_parts.append("")
        base_parts.append(body)
    base_no_tags = "\n".join(base_parts)

    if len(base_no_tags) > IG_CAPTION_LIMIT:
        allowable = IG_CAPTION_LIMIT - (len(title) + (1 if title else 0) + 1)
        allowable = max(0, allowable)
        truncated_body = (body or "")[:allowable]
        base_no_tags = "\n".join([p for p in [title, "", truncated_body] if p != "" or truncated_body])

    if not clean_tags:
        return base_no_tags[:IG_CAPTION_LIMIT], []

    tag_prefix = ("\n" if base_no_tags else "") + "\n"
    result = base_no_tags
    used = []
    for t in clean_tags:
        candidate = (result + tag_prefix + " ".join([f"#{x}" for x in used + [t]])).strip("\n")
        if len(candidate) <= IG_CAPTION_LIMIT:
            used.append(t)
            result = base_no_tags
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
