"""Карточка к посту (заголовок в цветах рубрики) + водяной знак канала — по образцу uz-business-news."""

from __future__ import annotations

import io
import math
import re

from PIL import Image, ImageDraw, ImageFont

from .config import ROOT

FONT_REGULAR = ROOT / "assets" / "fonts" / "DejaVuSans.ttf"
FONT_BOLD = ROOT / "assets" / "fonts" / "DejaVuSans-Bold.ttf"
CARD_SIZE = (1280, 720)

KIND_COLORS = {  # (верх градиента, низ градиента, акцент)
    "morning": ((10, 70, 60), (4, 28, 26), (46, 204, 150)),
    "evening": ((28, 40, 92), (10, 14, 38), (255, 176, 59)),
}
# эмодзи в шрифте DejaVu нет — убираем их с картинки
EMOJI = re.compile("[\U0001F000-\U0001FFFF☀-➿️‍⬀-⯿⃣]")


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit(draw, text, max_width, max_height, sizes, bold=True):
    for size in sizes:
        font = _font(bold, size)
        lines = _wrap(draw, text, font, max_width)
        line_height = int(size * 1.25)
        if len(lines) * line_height <= max_height:
            return font, lines, line_height
    lines = lines[: max(1, max_height // line_height)]
    lines[-1] = lines[-1].rstrip(" ,.;:") + "…"
    return font, lines, line_height


def make_card(kind: str, label: str, headline: str, subline: str, date_text: str) -> Image.Image:
    width, height = CARD_SIZE
    top, bottom, accent = KIND_COLORS.get(kind, KIND_COLORS["morning"])
    card = Image.new("RGB", CARD_SIZE)
    draw = ImageDraw.Draw(card)
    for y in range(height):
        t = y / (height - 1)
        draw.line([(0, y), (width, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    pad = 72
    draw.rectangle([0, 0, 14, height], fill=accent)
    draw.text((pad, pad - 8), label, font=_font(True, 30), fill=accent)

    headline = EMOJI.sub("", headline).strip()
    subline = EMOJI.sub("", subline).strip()
    max_width = width - 2 * pad
    font, lines, lh = _fit(draw, headline, max_width, 330, (64, 58, 52, 46, 42, 38))
    y = pad + 80
    for line in lines:
        draw.text((pad, y), line, font=font, fill=(255, 255, 255))
        y += lh
    y += 24
    sub_font, sub_lines, sub_lh = _fit(draw, subline, max_width, height - y - pad - 50, (34, 30, 26), bold=False)
    for line in sub_lines:
        draw.text((pad, y), line, font=sub_font, fill=(200, 210, 225))
        y += sub_lh
    draw.text((pad, height - pad - 28), date_text, font=_font(False, 28), fill=(200, 210, 225))
    return card


def apply_watermark(image: Image.Image, text: str, opacity: float = 0.85, tiled_opacity: float = 0.10) -> Image.Image:
    if not text:
        return image
    base = image.convert("RGBA")
    width, height = base.size
    scale = max(width, height) / 1280

    # бледный диагональный повтор по всему полю
    tile_font = _font(True, max(18, int(34 * scale)))
    layer = Image.new("RGBA", (int(math.hypot(width, height)),) * 2, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    step_x = int(draw.textlength(text, font=tile_font) + 220 * scale)
    step_y = int(170 * scale)
    alpha = int(255 * tiled_opacity)
    for row, y in enumerate(range(0, layer.height, step_y)):
        offset = (step_x // 2) * (row % 2)
        for x in range(-step_x, layer.width, step_x):
            draw.text((x + offset, y), text, font=tile_font, fill=(255, 255, 255, alpha),
                      stroke_width=1, stroke_fill=(0, 0, 0, alpha))
    layer = layer.rotate(30, resample=Image.Resampling.BICUBIC)
    left, top = (layer.width - width) // 2, (layer.height - height) // 2
    base.alpha_composite(layer.crop((left, top, left + width, top + height)))

    # плашка @канал в правом нижнем углу
    font = _font(True, max(16, int(30 * scale)))
    pad = int(16 * scale)
    measure = ImageDraw.Draw(base)
    text_w = int(measure.textlength(text, font=font))
    bbox = font.getbbox("Ag@")
    text_h = bbox[3] - bbox[1]
    plate_w, plate_h = pad * 2 + text_w, pad * 2 + text_h
    margin = int(24 * scale)
    x0, y0 = width - plate_w - margin, height - plate_h - margin
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([x0, y0, x0 + plate_w, y0 + plate_h], radius=int(14 * scale),
                         fill=(0, 0, 0, int(150 * opacity)))
    od.text((x0 + pad, y0 + pad - bbox[1]), text, font=font, fill=(255, 255, 255, int(255 * opacity)))
    base.alpha_composite(overlay)
    return base.convert("RGB")


def to_jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=88, optimize=True, progressive=True)
    return buffer.getvalue()
