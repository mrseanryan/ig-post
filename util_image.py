import os
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps


def _load_scaled_font(img_width: int, img_height: int, text: str):
    """Load a scalable TrueType font sized to target width.

    Targets approximately 30% of image width for the rendered text.
    Tries Pillow-bundled DejaVuSans or common system fonts.
    Returns (font, stroke_width, is_ttf). Falls back to default font if TTF unavailable.
    """
    from PIL import ImageFont, Image, ImageDraw
    import PIL

    # Establish size bounds relative to height; we will target width in search
    min_size = max(12, int(img_height * 0.02))
    max_size = max(min_size + 4, int(img_height * 0.10))
    target_w = max(36, int(img_width * 0.30))

    # Find bundled/system fonts
    pil_dir = Path(PIL.__file__).resolve().parent
    candidates = [
        pil_dir / "DejaVuSans.ttf",
        pil_dir / "fonts" / "DejaVuSans.ttf",
    ]
    if os.name == "nt":
        candidates += [
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/segoeui.ttf"),
        ]
    else:
        candidates += [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/Library/Fonts/Arial.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        ]

    font_path = None
    for p in candidates:
        if p.exists():
            font_path = str(p)
            break

    # If no TTF, return default font
    if not font_path:
        try:
            from PIL import ImageFont as _IF
            return _IF.load_default(), 2, False
        except Exception:
            return None, 2, False

    # Helper to measure height/width for a size
    def measure(size: int) -> tuple[int, int]:
        try:
            f = ImageFont.truetype(font_path, size=size)
        except Exception:
            return (0, 0)
        im = Image.new("L", (10, 10))
        dr = ImageDraw.Draw(im)
        try:
            bbox = dr.textbbox((0, 0), text, font=f)
            return (bbox[2] - bbox[0], bbox[3] - bbox[1])
        except Exception:
            try:
                w = int(dr.textlength(text, font=f))
                h = int(size * 0.6)
                return (w, h)
            except Exception:
                return (0, 0)

    from PIL import ImageFont, ImageDraw

    # Binary search for size maximizing font size while keeping width <= target_w
    lo, hi = min_size, max_size
    best_size = min_size
    while lo <= hi:
        mid = (lo + hi) // 2
        w, h = measure(mid)
        if h == 0:
            hi = mid - 1
            continue
        if w <= target_w:
            best_size = mid
            lo = mid + 1
        else:
            hi = mid - 1

    try:
        font = ImageFont.truetype(font_path, size=best_size)
    except Exception:
        try:
            return ImageFont.load_default(), 2, False
        except Exception:
            return None, 2, False

    stroke = max(2, int(best_size * 0.08))
    return font, stroke, True


def add_copyright_overlay(im: Image.Image, text: str) -> Image.Image:
    """
    Draw a semi-transparent dark rectangle with white text and black border at the bottom-right.
    """
    if not text:
        return im

    from PIL import ImageDraw, ImageFont

    def _sanitize_overlay_text(t: str) -> str:
        if not t:
            return t
        invis = [
            "\ufeff",  # BOM
            "\u200b", "\u200c", "\u200d", "\u200e", "\u200f",
            "\u2060",
        ]
        for ch in invis:
            t = t.replace(ch, "")
        t = t.replace("\u00a0", " ")
        return t.strip()

    text = _sanitize_overlay_text(text)

    # Work in RGBA for transparency blending
    base = im.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font, stroke_width, is_ttf = _load_scaled_font(im.width, im.height, text)

    # Dynamic spacing based on font size
    try:
        ascent, descent = (font.getmetrics() if hasattr(font, 'getmetrics') else (getattr(font, 'size', 24), 0))
        approx_size = ascent + descent if ascent else getattr(font, 'size', 24)
    except Exception:
        approx_size = getattr(font, 'size', 24)

    margin = max(12, int(approx_size * 0.9))
    pad_x, pad_y = max(6, int(approx_size * 0.5)), max(4, int(approx_size * 0.45))

    img_w, img_h = base.size

    if is_ttf and hasattr(draw, "textbbox"):
        try:
            bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        except TypeError:
            bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        rect_w = text_w + 2 * pad_x
        rect_h = text_h + 2 * pad_y

        x0 = max(0, img_w - margin - rect_w)
        y0 = max(0, img_h - margin - rect_h)
        x1 = min(img_w, x0 + rect_w)
        y1 = min(img_h, y0 + rect_h)

        draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, 140))

        tx = x0 + pad_x
        ty = y0 + pad_y
        try:
            draw.text(
                (tx, ty),
                text,
                fill=(255, 255, 255, 255),
                font=font,
                stroke_width=stroke_width,
                stroke_fill=(0, 0, 0, 255),
            )
        except TypeError:
            r = max(1, int(approx_size * 0.08))
            offsets = []
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if dx == 0 and dy == 0:
                        continue
                    if dx*dx + dy*dy <= r*r:
                        offsets.append((dx, dy))
            for dx, dy in offsets:
                draw.text((tx + dx, ty + dy), text, fill=(0, 0, 0, 255), font=font)
            draw.text((tx, ty), text, fill=(255, 255, 255, 255), font=font)
    else:
        # Raster fallback: render then scale to ~30% of image width
        from PIL import ImageDraw, ImageFont
        try:
            tf = ImageFont.load_default()
        except Exception:
            tf = None
        if tf is None:
            return im.convert("RGB")

        tmp_img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        tmp_dr = ImageDraw.Draw(tmp_img)
        bbox = tmp_dr.textbbox((0, 0), text, font=tf)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        layer = Image.new("RGBA", (tw + 8, th + 8), (0, 0, 0, 0))
        ldr = ImageDraw.Draw(layer)
        ox, oy = 4, 4
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                ldr.text((ox + dx, oy + dy), text, fill=(0, 0, 0, 255), font=tf)
        ldr.text((ox, oy), text, fill=(255, 255, 255, 255), font=tf)

        target_w = max(48, int(img_w * 0.30))
        scale = target_w / max(1, (tw + 8))
        new_w = max(1, int((tw + 8) * scale))
        new_h = max(1, int((th + 8) * scale))
        layer_big = layer.resize((new_w, new_h), Image.LANCZOS)

        rect_w = new_w + 2 * pad_x
        rect_h = new_h + 2 * pad_y
        x0 = max(0, img_w - margin - rect_w)
        y0 = max(0, img_h - margin - rect_h)
        x1 = min(img_w, x0 + rect_w)
        y1 = min(img_h, y0 + rect_h)

        draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, 140))
        tx = x0 + pad_x
        ty = y0 + pad_y
        overlay.alpha_composite(layer_big, dest=(tx, ty))

    composed = Image.alpha_composite(base, overlay)
    return composed.convert("RGB")


def prepare_image_for_upload(src: Path, copyright_text: Optional[str] = None) -> Path:
    """
    Apply EXIF orientation and optional overlay, write to a temporary JPEG, and return its path.
    Caller is responsible for removing the temp file.
    """
    with Image.open(src) as im:
        im2 = ImageOps.exif_transpose(im)
        if copyright_text:
            try:
                im2 = add_copyright_overlay(im2, copyright_text)
            except Exception as exc:
                print(f"Warning: failed to draw copyright overlay: {exc}")
        if im2.mode not in ("RGB", "L"):
            im2 = im2.convert("RGB")

        tmp = tempfile.NamedTemporaryFile(prefix="igp_", suffix=".jpg", delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()
        im2.save(tmp_path, format="JPEG", quality=95, optimize=True)
        return tmp_path


def prepare_image_to_path(src: Path, dst: Path, copyright_text: Optional[str] = None) -> Path:
    """
    Apply EXIF orientation and optional overlay, write to destination, and return path.
    """
    with Image.open(src) as im:
        im2 = ImageOps.exif_transpose(im)
        if copyright_text:
            try:
                im2 = add_copyright_overlay(im2, copyright_text)
            except Exception as exc:
                print(f"Warning: failed to draw copyright overlay: {exc}")
        if im2.mode not in ("RGB", "L"):
            im2 = im2.convert("RGB")
        dst.parent.mkdir(parents=True, exist_ok=True)
        im2.save(dst, format="JPEG", quality=95, optimize=True)
        return dst
