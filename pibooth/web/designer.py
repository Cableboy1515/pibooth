"""Rendering of print overlay designs created with the web overlay designer.

A design is a JSON spec (see :py:func:`render_design`) describing a stack of
elements (text, image, frame) positioned as fractions of the canvas size. It
is rendered server-side with PIL to get an authoritative, print-resolution
RGBA PNG independent of the client's canvas approximation.
"""

import os.path as osp
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from PIL.Image import Resampling
from werkzeug.utils import secure_filename

from pibooth import fonts
from pibooth.utils import LOGGER

#: Canvas size in pixels (300 dpi 4x6 inch paper) for each orientation
CANVAS_SIZES = {
    "portrait": (1800, 2700),
    "landscape": (2700, 1800),
}

DEFAULT_ORIENTATION = "portrait"


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Convert a ``#rrggbb`` hex string into a RGB tuple."""
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Invalid color '#{value}'")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _render_text_element(canvas: Image.Image, element: dict[str, Any], assets_dir: str) -> None:
    """Alpha-composite a text element onto the canvas."""
    text = str(element.get("text", ""))
    if not text:
        return
    width, height = canvas.size
    font_name = str(element.get("font", "Amatic-Bold"))
    try:
        font_path = fonts.get_filename(font_name)
    except ValueError as ex:
        LOGGER.warning("Cannot render text element: %s", ex)
        return
    size = int(float(element.get("size", 0.06)) * height)
    if size <= 0:
        return
    font = ImageFont.truetype(font_path, size)
    color = _hex_to_rgb(str(element.get("color", "#000000")))
    align = str(element.get("align", "center"))
    if align not in ("left", "center", "right"):
        align = "center"

    # Draw on a dedicated transparent layer sized to the text so it can be
    # rotated with expand=True before being composited on the real canvas.
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = measure.multiline_textbbox((0, 0), text, font=font, align=align)
    pad = max(1, size // 4)
    layer_size = (int(bbox[2] - bbox[0]) + 2 * pad, int(bbox[3] - bbox[1]) + 2 * pad)
    layer = Image.new("RGBA", layer_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.multiline_text((pad - bbox[0], pad - bbox[1]), text, fill=(*color, 255), font=font, align=align)

    rotation = float(element.get("rotation", 0))
    if rotation:
        layer = layer.rotate(rotation, expand=True, resample=Resampling.BICUBIC)

    x = float(element.get("x", 0.5)) * width
    y = float(element.get("y", 0.5)) * height
    origin = (int(x - layer.width / 2), int(y - layer.height / 2))
    canvas.alpha_composite(layer, origin)


def _render_image_element(canvas: Image.Image, element: dict[str, Any], assets_dir: str) -> None:
    """Alpha-composite an image element onto the canvas."""
    asset = str(element.get("asset", ""))
    if not asset:
        return
    filename = secure_filename(asset)
    path = osp.join(assets_dir, filename)
    if not osp.isfile(path):
        LOGGER.warning("Design references missing asset '%s'", asset)
        return

    width, height = canvas.size
    try:
        image = Image.open(path).convert("RGBA")
    except OSError as ex:
        LOGGER.warning("Cannot open design asset '%s': %s", path, ex)
        return

    target_width = max(1, int(float(element.get("width", 0.2)) * width))
    ratio = image.height / image.width if image.width else 1
    target_height = max(1, int(target_width * ratio))
    image = image.resize((target_width, target_height), Resampling.LANCZOS)

    opacity = max(0.0, min(1.0, float(element.get("opacity", 1.0))))
    if opacity < 1.0:
        alpha = image.getchannel("A").point(lambda a: int(a * opacity))
        image.putalpha(alpha)

    rotation = float(element.get("rotation", 0))
    if rotation:
        image = image.rotate(rotation, expand=True, resample=Resampling.BICUBIC)

    x = float(element.get("x", 0.5)) * width
    y = float(element.get("y", 0.5)) * height
    origin = (int(x - image.width / 2), int(y - image.height / 2))
    canvas.alpha_composite(image, origin)


def _render_frame_element(canvas: Image.Image, element: dict[str, Any], assets_dir: str) -> None:
    """Draw a rounded-rectangle frame outline onto the canvas."""
    width, height = canvas.size
    min_dim = min(width, height)
    inset = float(element.get("inset", 0.02)) * min_dim
    outline_width = max(1, int(float(element.get("width", 0.01)) * min_dim))
    radius = max(0, int(float(element.get("radius", 0.03)) * min_dim))
    color = _hex_to_rgb(str(element.get("color", "#000000")))

    draw = ImageDraw.Draw(canvas)
    box = (inset, inset, width - inset, height - inset)
    draw.rounded_rectangle(box, radius=radius, outline=(*color, 255), width=outline_width)


_RENDERERS = {
    "text": _render_text_element,
    "image": _render_image_element,
    "frame": _render_frame_element,
}


def render_design(spec: dict[str, Any], assets_dir: str) -> Image.Image:
    """Render a design spec into a transparent RGBA PNG at print resolution.

    :param spec: design spec, see module documentation for the JSON schema
    :param assets_dir: directory in which referenced image assets are looked up

    :return: RGBA image, sized according to ``spec["orientation"]``
    """
    orientation = spec.get("orientation", DEFAULT_ORIENTATION)
    size = CANVAS_SIZES.get(orientation, CANVAS_SIZES[DEFAULT_ORIENTATION])
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))

    for element in spec.get("elements", []):
        element_type = element.get("type")
        renderer = _RENDERERS.get(element_type)
        if renderer is None:
            LOGGER.warning("Ignoring unknown design element type '%s'", element_type)
            continue
        renderer(canvas, element, assets_dir)

    return canvas
