"""Picture layout templates.

A :class:`Template` describes, for one or more (captures count, orientation)
combinations, how to lay out the raw captures, the two footer texts and
optional decorative images on the final picture. Templates are stored on
disk as a canonical JSON document (see :func:`template_from_dict` for the
schema) but can also be imported from a `diagrams.net <https://app.diagrams.net>`_
(formerly draw.io) ``.xml`` export produced by the ``pibooth-picture-template``
plugin (see :func:`parse_mxgraph`).
"""

from __future__ import annotations

import base64
import json
import os
import os.path as osp
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont
from PIL.Image import Resampling

from pibooth import fonts
from pibooth.pictures import LANDSCAPE, PORTRAIT
from pibooth.pictures.factory import PilPictureFactory
from pibooth.utils import LOGGER

#: Shape kinds
CAPTURE = "capture"
TEXT = "text"
IMAGE = "image"
FRAME = "frame"

#: Muted slot palette used to render the layout guide layer (web overlay
#: designer guide overlay, and the exported guide.svg/guide.png). Mirrors
#: SLOT_COLORS in pibooth/web/static/canvas_editor.js — keep both in sync.
GUIDE_COLORS: list[tuple[int, int, int]] = [
    (143, 184, 174),  # #8fb8ae
    (201, 166, 107),  # #c9a66b
    (169, 143, 184),  # #a98fb8
    (107, 163, 201),  # #6ba3c9
]


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Convert a ``#rrggbb`` hex string into a RGB tuple."""
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Invalid color '#{value}'")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


@dataclass
class TemplateShape:
    """One positioned element of a :class:`TemplatePage`.

    :attr kind: ``"capture"``, ``"text"``, ``"image"`` or ``"frame"``
    :attr index: capture index (1..4), text index (1..2), 0 for images/frames
    :attr asset: basename of the image file in ``<configdir>/assets/`` (image shapes only)
    :attr x: left position, as a fraction of the page width — or, when
              ``anchor`` is set, the horizontal offset of this shape's center
              from the anchor capture slot's own center, as a fraction of
              the anchor's width (0 = centered on the slot)
    :attr y: top position, as a fraction of the page height — or, when
              ``anchor`` is set, the same offset semantics as ``x`` along
              the vertical axis
    :attr width: width, as a fraction of the page width — or, when
                  ``anchor`` is set, a scale multiplier of the anchor slot's
                  own width (1.0 = same width as the slot)
    :attr height: height, as a fraction of the page height — or, when
                   ``anchor`` is set, a scale multiplier of the anchor
                   slot's own height
    :attr rotation: rotation in degrees, clockwise, as authored by a user —
                     or, when ``anchor`` is set, an additional rotation on
                     top of the anchor slot's own rotation
    :attr anchor: capture slot index this shape is anchored to, or ``0`` for
                   absolute positioning (the default, and the only meaning
                   for templates saved before this field existed)
    :attr style_id: id of the :class:`FrameStyle` this shape uses (frame
                     shapes only); empty/unknown ids are skipped at render time
    :attr lock_aspect: for an image-kind frame style, whether ``height`` is
                        derived from the image's own aspect ratio (``True``,
                        the default) or independently authored (``False``);
                        unused otherwise
    """

    kind: str
    index: int
    asset: str
    x: float
    y: float
    width: float
    height: float
    rotation: float = 0.0
    anchor: int = 0
    style_id: str = ""
    lock_aspect: bool = True

    def rect_px(self, page_size: tuple[int, int]) -> tuple[int, int, int, int]:
        """Return the ``(x, y, width, height)`` pixel rectangle of this shape
        for the given page size, ignoring any ``anchor`` (see
        :func:`resolve_shape_rect` for the anchor-aware resolution).
        """
        page_width, page_height = page_size
        return (
            int(round(self.x * page_width)),
            int(round(self.y * page_height)),
            int(round(self.width * page_width)),
            int(round(self.height * page_height)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-serializable representation."""
        data: dict[str, Any] = {
            "type": self.kind,
            "index": self.index,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
        }
        if self.anchor:
            data["anchor"] = self.anchor
        if self.kind == IMAGE:
            data["asset"] = self.asset
        if self.kind == FRAME:
            data["styleId"] = self.style_id
            data["lockAspect"] = self.lock_aspect
        return data


@dataclass
class TemplatePage:
    """One page of a :class:`Template`, for a given captures count.

    :attr captures: number of distinct capture placeholders on this page
    :attr size: page size in pixels ``(width, height)``, authoritative
    :attr dpi: informative resolution metadata
    :attr paper: informative paper format name (:data:`pibooth.printer.PAPER_FORMATS`
                 key) or ``"custom"``
    :attr shapes: shapes to draw, in z-order (first drawn first)
    """

    captures: int
    size: tuple[int, int]
    dpi: int
    paper: str
    shapes: list[TemplateShape] = field(default_factory=list)

    @property
    def orientation(self) -> str:
        """Return :data:`~pibooth.pictures.PORTRAIT` or
        :data:`~pibooth.pictures.LANDSCAPE` depending on the page size.
        """
        return PORTRAIT if self.size[0] < self.size[1] else LANDSCAPE

    @property
    def captures_orientation(self) -> str:
        """Return the majority orientation of the capture shapes on this
        page (portrait if at least half of them are taller than wide).
        """
        captures = [shape for shape in self.shapes if shape.kind == CAPTURE]
        if not captures:
            return PORTRAIT
        portrait_count = 0
        for shape in captures:
            _, _, width, height = shape.rect_px(self.size)
            if width < height:
                portrait_count += 1
        if portrait_count / len(captures) >= 0.5:
            return PORTRAIT
        return LANDSCAPE

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-serializable representation."""
        return {
            "captures": self.captures,
            "orientation": self.orientation,
            "paper": self.paper,
            "dpi": self.dpi,
            "size": list(self.size),
            "shapes": [shape.to_dict() for shape in self.shapes],
        }


def resolve_shape_rect(shape: TemplateShape, page: TemplatePage) -> tuple[int, int, int, int, float]:
    """Return ``(x, y, width, height, rotation)`` in PIXELS for ``shape`` on
    ``page``, resolving its ``anchor`` if set.

    An anchored shape's ``x``/``y``/``width``/``height``/``rotation`` are
    reinterpreted relative to the capture-kind shape whose ``index`` matches
    ``shape.anchor``: width/height become scale multipliers of the anchor's
    own width/height, x/y become center-offset fractions of the anchor's
    size, and rotation becomes an additional rotation on top of the
    anchor's own rotation (see :class:`TemplateShape` for the exact
    semantics). If the anchor target doesn't exist (e.g. the capture count
    changed), the shape falls back to being treated as absolute rather than
    disappearing.
    """
    if not shape.anchor:
        x, y, width, height = shape.rect_px(page.size)
        return x, y, width, height, shape.rotation

    anchor_shape = next((s for s in page.shapes if s.kind == CAPTURE and s.index == shape.anchor), None)
    if anchor_shape is None:
        x, y, width, height = shape.rect_px(page.size)
        return x, y, width, height, shape.rotation

    acx = anchor_shape.x + anchor_shape.width / 2
    acy = anchor_shape.y + anchor_shape.height / 2
    resolved_width = anchor_shape.width * shape.width
    resolved_height = anchor_shape.height * shape.height
    resolved_cx = acx + shape.x * anchor_shape.width
    resolved_cy = acy + shape.y * anchor_shape.height
    resolved_x = resolved_cx - resolved_width / 2
    resolved_y = resolved_cy - resolved_height / 2
    rotation = anchor_shape.rotation + shape.rotation

    page_width, page_height = page.size
    return (
        int(round(resolved_x * page_width)),
        int(round(resolved_y * page_height)),
        int(round(resolved_width * page_width)),
        int(round(resolved_height * page_height)),
        rotation,
    )


def _draw_order_key(index: int, page: TemplatePage) -> tuple[int, ...]:
    """Sort key placing ``page.shapes[index]`` next to the capture slot it is
    anchored to (see :func:`resolve_draw_order`).

    The key is the chain of list positions from the anchor root down to the
    shape, with the shape's own position repeated at the end. Lexicographic
    ordering then keeps a slot adjacent to everything anchored to it, while
    the repeated element gives a child something to compare against its
    parent's own position — so a child listed before its parent still draws
    behind it, instead of a plain prefix always sorting the parent first.
    Nested anchors stay nested. A dangling anchor or an anchor cycle degrades
    to the shape's own position, i.e. today's plain list order.
    """
    chain: list[int] = [index]
    seen = {index}
    current = page.shapes[index]
    while current.anchor:
        parent = next(
            (i for i, s in enumerate(page.shapes) if s.kind == CAPTURE and s.index == current.anchor),
            None,
        )
        if parent is None or parent in seen:
            break  # dangling anchor, or a cycle — stop rather than loop forever
        chain.insert(0, parent)
        seen.add(parent)
        current = page.shapes[parent]
    return tuple(chain) + (index,)


def resolve_draw_order(page: TemplatePage) -> list[TemplateShape]:
    """Return ``page.shapes`` in the order they must be drawn.

    A shape anchored to a capture slot belongs to that slot's layer: it has to
    be obscured by whatever covers the slot. Drawing the raw list order would
    let a frame anchored to slot 1 but added last paint over slots 2 and 3,
    which is not what "anchored to slot 1" means to the person who drew it.

    Anchored shapes are therefore grouped with their anchor, keeping their
    existing position relative to it — a frame added after its slot still
    draws on top of it, and a mat deliberately placed before its slot still
    draws behind it. Only their position relative to *other* slots changes.
    The stored list order is untouched; this is purely a render-time view of
    it, so moving a slot's layer carries everything anchored to it along.
    """
    return [shape for _, shape in sorted(enumerate(page.shapes), key=lambda pair: _draw_order_key(pair[0], page))]


class Template:
    """A named collection of :class:`TemplatePage`, at most one per
    (captures count, orientation) pair.
    """

    def __init__(self, name: str, pages: list[TemplatePage]) -> None:
        self.name = name
        self.pages = pages

    def get_page(self, captures: int, orientation: str) -> TemplatePage:
        """Return the page matching the given captures count and orientation.

        :raises ValueError: if no page matches
        """
        for page in self.pages:
            if page.captures == captures and page.orientation == orientation:
                return page
        raise ValueError(f"No template page for {captures} captures in '{orientation}' orientation")

    def get_best_orientation(self, captures: Sequence[Image.Image]) -> str:
        """Return the best orientation (:data:`~pibooth.pictures.PORTRAIT` or
        :data:`~pibooth.pictures.LANDSCAPE`) for the given captures, depending
        on their own orientation and the available template pages.

        The size of the first capture is used to determine the orientation of
        the whole capture sequence.

        :param captures: list of captures to concatenate
        """
        nbr = len(captures)
        is_portrait = captures[0].size[0] < captures[0].size[1]
        captures_orientation = PORTRAIT if is_portrait else LANDSCAPE

        matching = [page for page in self.pages if page.captures == nbr]
        for page in matching:
            if page.captures_orientation == captures_orientation:
                return page.orientation
        if matching:
            return matching[0].orientation
        return PORTRAIT

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-serializable representation."""
        return {"name": self.name, "pages": [page.to_dict() for page in self.pages]}


@dataclass
class FrameStyle:
    """A named, reusable frame appearance, referenced by frame-kind
    :class:`TemplateShape` instances via ``style_id`` — so editing a style
    updates every shape using it, in any template. Stored as a flat list at
    ``<configdir>/assets/frame_styles.json`` (see :func:`load_frame_styles`).

    :attr id: stable identifier referenced by ``TemplateShape.style_id``
    :attr name: display name shown in the editor
    :attr kind: ``"vector"`` (a drawn outline) or ``"image"`` (a transparent
                PNG asset)
    :attr color: outline color, ``vector`` only
    :attr border_width: outline thickness, ``vector`` only — as a fraction
                         of the *shape's own resolved box* (not the page),
                         so the same style looks proportionally identical
                         wherever/however large it's used
    :attr radius: corner radius, same units as ``border_width``, ``vector`` only
    :attr asset: basename of the image file in ``<configdir>/assets/``, ``image`` only
    :attr opacity: image opacity, 0..1, ``image`` only
    """

    id: str
    name: str
    kind: str
    color: str = "#000000"
    border_width: float = 0.01
    radius: float = 0.03
    asset: str = ""
    opacity: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-serializable representation."""
        data: dict[str, Any] = {"id": self.id, "name": self.name, "kind": self.kind}
        if self.kind == "vector":
            data["color"] = self.color
            data["borderWidth"] = self.border_width
            data["radius"] = self.radius
        elif self.kind == "image":
            data["asset"] = self.asset
            data["opacity"] = self.opacity
        return data


def _require_number(value: Any, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{description} must be numeric")
    return float(value)


def _shape_from_dict(raw: Any, page_index: int, shape_index: int) -> TemplateShape:
    """Parse and validate one canonical shape dict."""
    if not isinstance(raw, dict):
        raise ValueError(f"Page #{page_index} shape #{shape_index} must be an object")

    kind = raw.get("type")
    if kind not in (CAPTURE, TEXT, IMAGE, FRAME):
        raise ValueError(f"Page #{page_index} shape #{shape_index}: invalid 'type' {kind!r}")

    label = f"Page #{page_index} shape #{shape_index} ('{kind}')"
    x = _require_number(raw.get("x"), f"{label} 'x'")
    y = _require_number(raw.get("y"), f"{label} 'y'")
    width = _require_number(raw.get("width"), f"{label} 'width'")
    height = _require_number(raw.get("height"), f"{label} 'height'")
    rotation = _require_number(raw.get("rotation", 0), f"{label} 'rotation'") if "rotation" in raw else 0.0

    try:
        index = int(raw.get("index", 0))
    except (TypeError, ValueError) as ex:
        raise ValueError(f"{label} 'index' must be an integer") from ex

    try:
        anchor = int(raw.get("anchor", 0))
    except (TypeError, ValueError) as ex:
        raise ValueError(f"{label} 'anchor' must be an integer") from ex

    asset = str(raw.get("asset", "")) if kind == IMAGE else ""
    style_id = str(raw.get("styleId", "")) if kind == FRAME else ""
    lock_aspect = bool(raw.get("lockAspect", True)) if kind == FRAME else True

    return TemplateShape(
        kind=kind,
        index=index,
        asset=asset,
        x=x,
        y=y,
        width=width,
        height=height,
        rotation=rotation,
        anchor=anchor,
        style_id=style_id,
        lock_aspect=lock_aspect,
    )


def template_from_dict(data: Any) -> Template:
    """Build and validate a :class:`Template` from its canonical dict
    representation (see module documentation for the JSON schema).

    :raises ValueError: with a human readable message if the data is invalid
    """
    if not isinstance(data, dict):
        raise ValueError("Template must be a JSON object")

    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Template 'name' is required")

    raw_pages = data.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise ValueError("Template must contain a non-empty 'pages' list")

    pages: list[TemplatePage] = []
    seen: set[tuple[int, str]] = set()
    for page_index, raw_page in enumerate(raw_pages):
        if not isinstance(raw_page, dict):
            raise ValueError(f"Page #{page_index} must be an object")

        try:
            captures = int(raw_page["captures"])
        except (KeyError, TypeError, ValueError) as ex:
            raise ValueError(f"Page #{page_index}: invalid or missing 'captures': {ex}") from ex

        size_raw = raw_page.get("size")
        if not (isinstance(size_raw, (list, tuple)) and len(size_raw) == 2):
            raise ValueError(f"Page #{page_index}: 'size' must be a [width, height] pair")
        try:
            size = (int(size_raw[0]), int(size_raw[1]))
        except (TypeError, ValueError) as ex:
            raise ValueError(f"Page #{page_index}: invalid 'size': {ex}") from ex
        if size[0] <= 0 or size[1] <= 0:
            raise ValueError(f"Page #{page_index}: 'size' must be positive")

        dpi = int(raw_page.get("dpi", 300))
        paper = str(raw_page.get("paper", "custom"))

        shapes_raw = raw_page.get("shapes", [])
        if not isinstance(shapes_raw, list):
            raise ValueError(f"Page #{page_index}: 'shapes' must be a list")

        shapes = [_shape_from_dict(raw_shape, page_index, i) for i, raw_shape in enumerate(shapes_raw)]

        page = TemplatePage(captures=captures, size=size, dpi=dpi, paper=paper, shapes=shapes)

        key = (page.captures, page.orientation)
        if key in seen:
            raise ValueError(f"Several pages defined for {page.captures} captures in '{page.orientation}' orientation")
        seen.add(key)
        pages.append(page)

    return Template(name=name, pages=pages)


def load_template(path: str) -> Template:
    """Load a :class:`Template` from a file.

    The format is dispatched on the file extension: ``.json`` is parsed as
    the canonical format, ``.xml`` is imported from a diagrams.net export
    (see :func:`parse_mxgraph`).

    :param path: path to the template file

    :raises ValueError: if the file cannot be parsed or is invalid
    """
    ext = osp.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, encoding="utf-8") as fp:
            try:
                data = json.load(fp)
            except ValueError as ex:
                raise ValueError(f"Cannot parse template '{path}': {ex}") from ex
        return template_from_dict(data)

    if ext == ".xml":
        # Assumes the pibooth <configdir>/templates/<name>.{json,xml} and
        # <configdir>/assets/ convention to store any embedded image.
        assets_dir = osp.join(osp.dirname(osp.dirname(osp.abspath(path))), "assets")
        return template_from_dict(parse_mxgraph(path, assets_dir))

    raise ValueError(f"Unsupported template file extension '{ext}'")


# --- frame styles (shared, reusable frame-kind shape appearances) ----------


def frame_style_from_dict(raw: Any, index: int) -> FrameStyle:
    """Parse and validate one canonical frame-style dict."""
    if not isinstance(raw, dict):
        raise ValueError(f"Frame style #{index} must be an object")

    style_id = str(raw.get("id", "")).strip()
    if not style_id:
        raise ValueError(f"Frame style #{index}: 'id' is required")

    kind = raw.get("kind")
    if kind not in ("vector", "image"):
        raise ValueError(f"Frame style '{style_id}': invalid 'kind' {kind!r}")

    name = str(raw.get("name", "")).strip() or style_id

    return FrameStyle(
        id=style_id,
        name=name,
        kind=kind,
        color=str(raw.get("color", "#000000")),
        border_width=_require_number(raw.get("borderWidth", 0.01), f"Frame style '{style_id}' 'borderWidth'"),
        radius=_require_number(raw.get("radius", 0.03), f"Frame style '{style_id}' 'radius'"),
        asset=str(raw.get("asset", "")),
        opacity=_require_number(raw.get("opacity", 1.0), f"Frame style '{style_id}' 'opacity'"),
    )


def frame_styles_from_list(data: Any) -> list[FrameStyle]:
    """Build and validate the list of :class:`FrameStyle` from its canonical
    list-of-dicts representation.

    :raises ValueError: with a human readable message if the data is invalid
    """
    if not isinstance(data, list):
        raise ValueError("Frame styles must be a JSON array")
    styles = [frame_style_from_dict(raw, index) for index, raw in enumerate(data)]
    seen_ids = {style.id for style in styles}
    if len(seen_ids) != len(styles):
        raise ValueError("Frame style ids must be unique")
    return styles


def _frame_styles_path(assets_dir: str) -> str:
    return osp.join(assets_dir, "frame_styles.json")


def load_frame_styles(assets_dir: str) -> list[FrameStyle]:
    """Return the frame styles saved at ``<assets_dir>/frame_styles.json``,
    or an empty list if the file doesn't exist or can't be parsed.
    """
    path = _frame_styles_path(assets_dir)
    if not osp.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
        return frame_styles_from_list(data)
    except (OSError, ValueError) as ex:
        LOGGER.warning("Cannot load frame styles '%s': %s", path, ex)
        return []


def save_frame_styles(assets_dir: str, styles: list[FrameStyle]) -> None:
    """Save ``styles`` to ``<assets_dir>/frame_styles.json``, creating
    ``assets_dir`` if needed.
    """
    os.makedirs(assets_dir, exist_ok=True)
    path = _frame_styles_path(assets_dir)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump([style.to_dict() for style in styles], fp, indent=2)


# --- diagrams.net (mxGraph) XML import --------------------------------------


def px(cin: Any, dpi: int = 600) -> int:
    """Convert a dimension in centiinch (mxGraph page size unit) into pixels.

    :param cin: dimension in centiinch
    :param dpi: dot-per-inch
    """
    return int(float(cin) * dpi / 100)


def _inflate(data: str) -> str:
    """Decompress a diagrams.net compressed diagram payload.

    In ~2016 Flowchart Maker started compressing diagrams "using standard
    deflate" (base64 + raw zlib deflate), see
    https://about.draw.io/extracting-the-xml-from-mxfiles
    """
    raw = base64.b64decode(data)
    return unquote(zlib.decompress(raw, -15).decode("utf8"))


def _parse_style(style_attr: str | None) -> dict[str, str]:
    """Parse a ``key1=value1;key2=value2;...`` mxCell style attribute."""
    styledict: dict[str, str] = {"name": ""}
    if style_attr:
        parts = [part for part in style_attr.split(";") if part.strip()]
        if parts and "=" not in parts[0]:
            styledict["name"] = parts.pop(0)
        for key_value in parts:
            key, _, value = key_value.partition("=")
            styledict[key] = value
    return styledict


def _parse_cell_text(cell: ElementTree.Element) -> str:
    """Return the text value of a mxCell, unwrapping any inline HTML markup."""
    raw = cell.get("value")
    try:
        parsed = ElementTree.fromstring(str(raw))
        return parsed.text or ""
    except ElementTree.ParseError:
        return raw or ""


def _classify(cell: ElementTree.Element) -> str:
    """Return ``"capture"``, ``"text"``, ``"image"`` or ``"unknown"`` for a mxCell."""
    style = cell.get("style") or ""
    if cell.get("vertex") == "1" and style.startswith("shape=image"):
        return IMAGE
    if cell.get("vertex") == "1" and style.startswith("text;"):
        return TEXT
    if cell.get("vertex") == "1":
        return CAPTURE
    return "unknown"


def parse_mxgraph(path: str, assets_dir: str) -> dict[str, Any]:
    """Import a diagrams.net (Flowchart Maker) XML export into the canonical
    template dict format (see :func:`template_from_dict`).

    Each ``<diagram>`` of the mxfile becomes one page. Capture placeholders
    are vertices whose value is ``1``-``4``, text placeholders are vertices
    styled ``text;...`` whose value is ``1``, ``2``, ``footer_text1`` or
    ``footer_text2``, and any other vertex styled ``shape=image;...`` with an
    embedded base64 image is extracted as an image shape (the image data is
    saved into ``assets_dir``).

    :param path: path to the ``.xml`` file to import
    :param assets_dir: directory in which embedded images are extracted

    :raises ValueError: if the file cannot be parsed or is invalid
    """
    name = osp.splitext(osp.basename(path))[0]
    try:
        doc = ElementTree.parse(path).getroot()
    except ElementTree.ParseError as ex:
        raise ValueError(f"Cannot parse XML template '{path}': {ex}") from ex

    os.makedirs(assets_dir, exist_ok=True)
    image_counter = 0
    pages: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()

    for diagram in doc.iter("diagram"):
        if not list(diagram) and diagram.text and diagram.text.strip():  # Compressed
            template_el = ElementTree.fromstring(_inflate(diagram.text))
        else:
            found = diagram.find("mxGraphModel")
            if found is None:
                continue
            template_el = found

        root = template_el.find("root")
        dpi = 600
        if root is not None and len(root):
            dpi = int(root[0].get("dpi", 600))

        size = (px(template_el.get("pageWidth", "400"), dpi), px(template_el.get("pageHeight", "600"), dpi))

        raw_shapes: list[dict[str, Any]] = []
        distinct_captures: set[str] = set()

        for cell in template_el.iter("mxCell"):
            kind = _classify(cell)
            if kind == "unknown":
                continue

            text = _parse_cell_text(cell)
            style = _parse_style(cell.get("style"))
            rotation = float(style.get("rotation", 0))

            geometry = cell.find("mxGeometry")
            if geometry is None:
                x = y = width = height = 0
            else:
                x = px(geometry.get("x", 0), dpi)
                y = px(geometry.get("y", 0), dpi)
                width = px(geometry.get("width", 0), dpi)
                height = px(geometry.get("height", 0), dpi)

            asset = ""
            if kind == CAPTURE:
                if text not in ("1", "2", "3", "4"):
                    LOGGER.warning("Template capture holder with text '%s' ignored", text)
                    continue
                distinct_captures.add(text)
                index = int(text)
            elif kind == TEXT:
                if text not in ("1", "2", "footer_text1", "footer_text2"):
                    LOGGER.warning("Template text holder with text '%s' ignored", text)
                    continue
                text = text[-1]  # Keep only the index value (matches the bounds-check log below)
                index = int(text)
            else:  # IMAGE
                image_data = style.get("image", "")
                if not image_data or "," not in image_data:
                    LOGGER.warning("Template image shape without embedded data ignored")
                    continue
                image_counter += 1
                asset = f"{name}-image{image_counter}.png"
                with open(osp.join(assets_dir, asset), "wb") as fp:
                    fp.write(base64.b64decode(image_data.split(",", 1)[1]))
                index = 0

            # If shape is on the left/right (resp. above/below) of the page, wrap it back in
            if x + width <= 0 or x >= size[0]:
                LOGGER.warning("Template shape '%s' X-position out of bounds, try to auto-adjust", text)
                x = x % size[0] if size[0] else 0
            if y + height <= 0 or y >= size[1]:
                LOGGER.warning("Template shape '%s' Y-position out of bounds, try to auto-adjust", text)
                y = y % size[1] if size[1] else 0

            raw_shapes.append(
                {
                    "type": kind,
                    "index": index,
                    "asset": asset,
                    "x": x / size[0] if size[0] else 0.0,
                    "y": y / size[1] if size[1] else 0.0,
                    "width": width / size[0] if size[0] else 0.0,
                    "height": height / size[1] if size[1] else 0.0,
                    "rotation": rotation,
                }
            )

        captures_count = len(distinct_captures)
        orientation = PORTRAIT if size[0] < size[1] else LANDSCAPE
        key = (captures_count, orientation)
        if key in seen:
            raise ValueError(
                f"Several template pages found for {captures_count} captures in '{orientation}' orientation"
            )
        seen.add(key)

        pages.append(
            {
                "captures": captures_count,
                "orientation": orientation,
                "paper": "custom",
                "dpi": dpi,
                "size": list(size),
                "shapes": raw_shapes,
            }
        )

    if not pages:
        raise ValueError(f"No template page found in '{path}'")

    return {"name": name, "pages": pages}


# --- rendering ---------------------------------------------------------------


class TemplatePictureFactory(PilPictureFactory):
    """Picture factory that lays out captures, texts and decorative images
    according to a :class:`Template` page (selected by captures count and
    orientation), instead of the default grid layout.
    """

    def __init__(self, template: Template, orientation: str, *images: Image.Image, assets_dir: str = "") -> None:
        self.template = template
        self.orientation = orientation
        self.assets_dir = assets_dir
        self._page = template.get_page(len(images), orientation)
        super().__init__(self._page.size[0], self._page.size[1], *images)

    def _iter_images_rects(self) -> Any:
        """Not applicable: layout is driven by :attr:`_page` instead."""
        raise NotImplementedError("Not applicable for template")

    def _iter_texts_rects(self, interline: int | None = None) -> Any:
        """Not applicable: layout is driven by :attr:`_page` instead."""
        raise NotImplementedError("Not applicable for template")

    def _paste_shape(
        self, image: Image.Image, dest_image: Image.Image, pos_x: int, pos_y: int, angle: float | None = None
    ) -> None:
        """Paste ``image`` onto ``dest_image`` at ``(pos_x, pos_y)``, optionally rotated.

        ``angle`` follows the template's clockwise-degrees convention (as
        authored by a user), while :py:meth:`PIL.Image.Image.rotate` is
        counter-clockwise for positive angles, hence the sign flip.
        """
        width, height = image.size
        if angle:
            image = image.rotate(-angle, expand=True)
        mask = image if angle is not None else None
        dest_image.paste(image, (pos_x + (width - image.width) // 2, pos_y + (height - image.height) // 2), mask)

    def _build_matrix(self, image: Image.Image) -> Image.Image:
        """Draw every shape of the template page, in z-order."""
        frame_styles = load_frame_styles(self.assets_dir) if any(s.kind == FRAME for s in self._page.shapes) else []

        for shape in resolve_draw_order(self._page):
            rect_x, rect_y, rect_w, rect_h, rotation = resolve_shape_rect(shape, self._page)
            if rect_w <= 0 or rect_h <= 0:
                continue

            if shape.kind == CAPTURE:
                index = shape.index - 1
                if index < 0 or index >= len(self._images):
                    continue  # No image available for this index
                src_image, width, height = self._image_resize_keep_ratio(
                    self._images[index], rect_w, rect_h, self._crop
                )
                layer = Image.new("RGBA", (rect_w, rect_h), (255, 0, 0, 0))
                self._paste_shape(src_image, layer, (rect_w - width) // 2, (rect_h - height) // 2)
                self._paste_shape(layer, image, rect_x, rect_y, rotation)

            elif shape.kind == TEXT:
                index = shape.index - 1
                if index < 0 or index >= len(self._texts):
                    continue  # No text available for this index
                text, font_name, color, align = self._texts[index]
                if not text:
                    continue
                layer = Image.new("RGBA", (rect_w, rect_h), (255, 0, 0, 0))
                draw = ImageDraw.Draw(layer)
                font = fonts.get_pil_font(text, font_name, rect_w, rect_h)
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = int(bbox[2] - bbox[0])
                text_height = int(bbox[3] - bbox[1])
                offset_x, offset_y = int(bbox[0]), int(bbox[1])

                x = 0
                if align == self.CENTER:
                    x = (rect_w - text_width) // 2
                elif align == self.RIGHT:
                    x = rect_w - text_width

                draw.text((x - offset_x // 2, (rect_h - text_height) // 2 - offset_y // 2), text, color, font=font)
                self._paste_shape(layer, image, rect_x, rect_y, rotation)

            elif shape.kind == IMAGE:
                if not shape.asset:
                    continue
                asset_path = osp.join(self.assets_dir, shape.asset)
                if not osp.isfile(asset_path):
                    LOGGER.warning("Template image asset '%s' not found, skipped", shape.asset)
                    continue
                try:
                    src_image = Image.open(asset_path).convert("RGBA")
                except OSError as ex:
                    LOGGER.warning("Cannot open template image asset '%s': %s", asset_path, ex)
                    continue
                src_image = src_image.resize((rect_w, rect_h), Resampling.LANCZOS)
                layer = Image.new("RGBA", (rect_w, rect_h), (255, 0, 0, 0))
                self._paste_shape(src_image, layer, 0, 0)
                self._paste_shape(layer, image, rect_x, rect_y, rotation)

            elif shape.kind == FRAME:
                if not shape.style_id:
                    continue
                style = next((s for s in frame_styles if s.id == shape.style_id), None)
                if style is None:
                    continue

                if style.kind == "vector":
                    min_dim = min(rect_w, rect_h)
                    border_width_px = max(1, int(style.border_width * min_dim))
                    inset = min(border_width_px / 2, (rect_w - 1) / 2, (rect_h - 1) / 2)
                    radius_px = max(0, min(int(style.radius * min_dim), int(rect_w / 2 - inset), int(rect_h / 2 - inset)))
                    layer = Image.new("RGBA", (rect_w, rect_h), (0, 0, 0, 0))
                    draw = ImageDraw.Draw(layer)
                    color = _hex_to_rgb(style.color)
                    draw.rounded_rectangle(
                        (inset, inset, rect_w - inset, rect_h - inset),
                        radius=radius_px,
                        outline=(*color, 255),
                        width=border_width_px,
                    )
                    self._paste_shape(layer, image, rect_x, rect_y, rotation)

                elif style.kind == "image":
                    if not style.asset:
                        continue
                    asset_path = osp.join(self.assets_dir, style.asset)
                    if not osp.isfile(asset_path):
                        LOGGER.warning("Frame style '%s' image asset '%s' not found, skipped", style.id, style.asset)
                        continue
                    try:
                        src_image = Image.open(asset_path).convert("RGBA")
                    except OSError as ex:
                        LOGGER.warning("Cannot open frame style '%s' image asset '%s': %s", style.id, style.asset, ex)
                        continue

                    target_w = max(1, rect_w)
                    if shape.lock_aspect:
                        ratio = src_image.height / src_image.width if src_image.width else 1
                        target_h = max(1, int(target_w * ratio))
                    else:
                        target_h = max(1, rect_h)
                    src_image = src_image.resize((target_w, target_h), Resampling.LANCZOS)

                    if style.opacity < 1.0:
                        alpha = src_image.getchannel("A").point(lambda a: int(a * style.opacity))
                        src_image.putalpha(alpha)

                    paste_y = rect_y + (rect_h - target_h) // 2
                    layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
                    self._paste_shape(src_image, layer, 0, 0)
                    self._paste_shape(layer, image, rect_x, paste_y, rotation)

        return image

    def _build_texts(self, image: Image.Image) -> None:
        """No-op: texts are drawn by :meth:`_build_matrix` to preserve the
        z-order defined by the template.
        """

    def _build_outlines(self, image: Image.Image) -> None:
        """Draw outlines for every shape, useful to investigate position issues."""
        font = ImageFont.load_default()
        for shape in resolve_draw_order(self._page):
            rect_x, rect_y, rect_w, rect_h, rotation = resolve_shape_rect(shape, self._page)
            if rect_w <= 0 or rect_h <= 0:
                continue
            layer = Image.new("RGBA", (rect_w, rect_h), (255, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            draw.rectangle(((0, 0), (rect_w - 1, rect_h - 1)), outline="red")
            label = shape.asset if shape.kind == IMAGE else str(shape.index)
            draw.text((10, 10), label, "red", font)
            self._paste_shape(layer, image, rect_x, rect_y, rotation)


# --- layout guide export (web overlay/layout designer) -----------------------


def _composite_guide_layer(
    canvas: Image.Image, layer: Image.Image, rect_x: int, rect_y: int, rect_w: int, rect_h: int, rotation: float
) -> None:
    """Rotate ``layer`` (sized to the shape's un-rotated rect) about its
    center and alpha-composite it onto ``canvas``.

    ``rotation`` follows the template's clockwise-degrees convention, while
    :py:meth:`PIL.Image.Image.rotate` is counter-clockwise for positive
    angles, hence the sign flip (same convention as
    :meth:`TemplatePictureFactory._paste_shape`).
    """
    if rotation:
        layer = layer.rotate(-rotation, expand=True)
    width, height = layer.size
    pos_x = rect_x + (rect_w - width) // 2
    pos_y = rect_y + (rect_h - height) // 2
    canvas.alpha_composite(layer, (pos_x, pos_y))


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    rect_w: int,
    rect_h: int,
    max_height_fraction: float,
    color: tuple[int, int, int, int],
) -> None:
    """Draw ``text`` centered in a ``rect_w`` x ``rect_h`` area, sized to fit
    within ``max_height_fraction`` of the rect height (and the rect width).
    """
    font = fonts.get_pil_font(text, fonts.CURRENT, rect_w * 0.9, rect_h * max_height_fraction)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((rect_w - text_w) / 2 - bbox[0], (rect_h - text_h) / 2 - bbox[1]), text, fill=color, font=font)


def render_layout_guide(page: TemplatePage) -> Image.Image:
    """Render a transparent RGBA overlay of ``page``'s shapes, meant to be
    used as a guide layer when designing artwork for the print layout in an
    external image editor.

    Same visual language as the web canvas guide layer: capture slots are
    filled translucent rounded rects with a big slot number, text slots are
    outlined boxes labeled "Text N", image slots are thin gray outlines
    labeled with the asset basename.
    """
    canvas = Image.new("RGBA", page.size, (0, 0, 0, 0))
    min_dim = min(page.size)

    for shape in resolve_draw_order(page):
        if shape.kind not in (CAPTURE, TEXT, IMAGE):
            continue  # frame shapes are decorative, not position guides
        rect_x, rect_y, rect_w, rect_h, rotation = resolve_shape_rect(shape, page)
        if rect_w <= 0 or rect_h <= 0:
            continue

        layer = Image.new("RGBA", (rect_w, rect_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        radius = max(1, min(int(min_dim * 0.02), rect_w // 2, rect_h // 2))

        if shape.kind == CAPTURE:
            color = GUIDE_COLORS[(shape.index - 1) % len(GUIDE_COLORS)]
            draw.rounded_rectangle((0, 0, rect_w - 1, rect_h - 1), radius=radius, fill=(*color, 115))
            _draw_centered_text(draw, str(shape.index), rect_w, rect_h, 0.5, (255, 255, 255, 255))
        elif shape.kind == TEXT:
            draw.rectangle((0, 0, rect_w - 1, rect_h - 1), outline=(102, 102, 102, 160), width=2)
            _draw_centered_text(draw, f"Text {shape.index}", rect_w, rect_h, 0.25, (102, 102, 102, 220))
        elif shape.kind == IMAGE:
            draw.rectangle((0, 0, rect_w - 1, rect_h - 1), outline=(150, 150, 150, 255), width=2)
            _draw_centered_text(draw, shape.asset or "image", rect_w, rect_h, 0.15, (120, 120, 120, 255))

        _composite_guide_layer(canvas, layer, rect_x, rect_y, rect_w, rect_h, rotation)

    return canvas


def render_layout_guide_svg(page: TemplatePage) -> str:
    """Render a standalone SVG document of ``page``'s shapes as a single
    deletable guide layer/group, meant for import at true print size into an
    external image editor (see :func:`render_layout_guide` for the PIL/PNG
    equivalent and its visual language).
    """
    width_px, height_px = page.size
    width_in = width_px / page.dpi
    height_in = height_px / page.dpi
    min_dim = min(width_px, height_px)
    radius = min_dim * 0.02

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_in}in" height="{height_in}in" '
        f'viewBox="0 0 {width_px} {height_px}">',
        '<g id="pibooth-layout-guides">',
    ]

    for shape in resolve_draw_order(page):
        if shape.kind not in (CAPTURE, TEXT, IMAGE):
            continue  # frame shapes are decorative, not position guides
        rect_x, rect_y, rect_w, rect_h, rotation = resolve_shape_rect(shape, page)
        if rect_w <= 0 or rect_h <= 0:
            continue
        cx = rect_x + rect_w / 2
        cy = rect_y + rect_h / 2
        # SVG's rotate(angle, cx, cy) is clockwise-positive in its y-down
        # coordinate system, the same sense as our canonical `rotation`
        # field, so (unlike the PIL rendering path, which must negate for
        # PIL.Image.rotate's counter-clockwise convention) no sign flip.
        transform = f' transform="rotate({rotation} {cx} {cy})"' if rotation else ""

        if shape.kind == CAPTURE:
            color = GUIDE_COLORS[(shape.index - 1) % len(GUIDE_COLORS)]
            fill = "#{:02x}{:02x}{:02x}".format(*color)
            parts.append(
                f'<rect x="{rect_x}" y="{rect_y}" width="{rect_w}" height="{rect_h}" rx="{radius}" '
                f'fill="{fill}" fill-opacity="0.45"{transform}/>'
            )
            parts.append(
                f'<text x="{cx}" y="{cy}" font-family="sans-serif" font-weight="bold" fill="white" '
                f'font-size="{rect_h * 0.5}" text-anchor="middle" dominant-baseline="central"{transform}>'
                f"{escape(str(shape.index))}</text>"
            )
        elif shape.kind == TEXT:
            parts.append(
                f'<rect x="{rect_x}" y="{rect_y}" width="{rect_w}" height="{rect_h}" fill="none" '
                f'stroke="#666666" stroke-width="2" stroke-dasharray="6,4"{transform}/>'
            )
            parts.append(
                f'<text x="{cx}" y="{cy}" font-family="sans-serif" fill="#666666" '
                f'font-size="{rect_h * 0.25}" text-anchor="middle" dominant-baseline="central"{transform}>'
                f"{escape(f'Text {shape.index}')}</text>"
            )
        elif shape.kind == IMAGE:
            label = osp.basename(shape.asset) if shape.asset else "image"
            parts.append(
                f'<rect x="{rect_x}" y="{rect_y}" width="{rect_w}" height="{rect_h}" fill="none" '
                f'stroke="#969696" stroke-width="2"{transform}/>'
            )
            parts.append(
                f'<text x="{cx}" y="{cy}" font-family="sans-serif" fill="#969696" '
                f'font-size="{rect_h * 0.15}" text-anchor="middle" dominant-baseline="central"{transform}>'
                f"{escape(label)}</text>"
            )

    parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts)
