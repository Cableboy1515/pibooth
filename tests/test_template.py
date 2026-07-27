import copy
import json
import os
import os.path as osp

import pytest
from PIL import Image

from pibooth.pictures import LANDSCAPE, PORTRAIT
from pibooth.pictures.template import (
    FrameStyle,
    Template,
    TemplatePictureFactory,
    load_frame_styles,
    load_template,
    parse_mxgraph,
    render_layout_guide,
    render_layout_guide_svg,
    resolve_draw_order,
    resolve_shape_rect,
    save_frame_styles,
    template_from_dict,
)

HERE = osp.dirname(osp.abspath(__file__))
DATA_DIR = osp.join(HERE, "data")


def make_capture(size=(100, 150), color=(255, 0, 0)):
    return Image.new("RGB", size, color)


def sample_template_dict():
    """A minimal, valid canonical template: 1 or 2 captures, portrait, with texts."""
    return {
        "name": "sample",
        "pages": [
            {
                "captures": 1,
                "orientation": "portrait",
                "paper": "custom",
                "dpi": 300,
                "size": [400, 600],
                "shapes": [
                    {"type": "capture", "index": 1, "x": 0.05, "y": 0.05, "width": 0.9, "height": 0.7, "rotation": 0},
                    {"type": "text", "index": 1, "x": 0.1, "y": 0.85, "width": 0.8, "height": 0.1, "rotation": 0},
                ],
            },
            {
                "captures": 2,
                "orientation": "portrait",
                "paper": "custom",
                "dpi": 300,
                "size": [400, 600],
                "shapes": [
                    {"type": "capture", "index": 1, "x": 0.05, "y": 0.05, "width": 0.9, "height": 0.4, "rotation": 0},
                    {"type": "capture", "index": 2, "x": 0.05, "y": 0.5, "width": 0.9, "height": 0.4, "rotation": 0},
                    {"type": "text", "index": 1, "x": 0.1, "y": 0.92, "width": 0.8, "height": 0.06, "rotation": 0},
                ],
            },
        ],
    }


# --------------------------------------------------------------- canonical


def test_template_from_dict_round_trip():
    data = sample_template_dict()
    template = template_from_dict(data)
    assert template.name == "sample"
    assert len(template.pages) == 2

    page = template.get_page(2, PORTRAIT)
    assert page.size == (400, 600)
    assert page.orientation == PORTRAIT
    assert len([s for s in page.shapes if s.kind == "capture"]) == 2

    with pytest.raises(ValueError):
        template.get_page(3, PORTRAIT)

    # Round-trip through to_dict() / template_from_dict()
    reloaded = template_from_dict(template.to_dict())
    assert reloaded.name == template.name
    assert len(reloaded.pages) == len(template.pages)


def test_template_from_dict_frame_shape_round_trip():
    data = sample_template_dict()
    data["pages"][0]["shapes"].append(
        {
            "type": "frame",
            "x": 0.1,
            "y": -0.05,
            "width": 1.15,
            "height": 1.15,
            "rotation": 5,
            "anchor": 1,
            "styleId": "gold-thin",
            "lockAspect": False,
        }
    )
    template = template_from_dict(data)
    page = template.get_page(1, PORTRAIT)
    frame = next(s for s in page.shapes if s.kind == "frame")
    assert frame.anchor == 1
    assert frame.style_id == "gold-thin"
    assert frame.lock_aspect is False

    reloaded = template_from_dict(template.to_dict())
    reloaded_frame = next(s for s in reloaded.get_page(1, PORTRAIT).shapes if s.kind == "frame")
    assert reloaded_frame.anchor == 1
    assert reloaded_frame.style_id == "gold-thin"
    assert reloaded_frame.lock_aspect is False


def test_template_from_dict_shape_without_anchor_defaults_absolute():
    # Pre-existing templates (saved before the anchor field existed) must
    # still parse, with every shape treated as absolute (anchor == 0).
    data = sample_template_dict()
    assert "anchor" not in data["pages"][0]["shapes"][0]
    template = template_from_dict(data)
    shape = template.get_page(1, PORTRAIT).shapes[0]
    assert shape.anchor == 0


def test_template_from_dict_duplicate_page_rejected():
    data = sample_template_dict()
    # Add a second page with the same (captures, orientation) as the first
    data["pages"].append(copy.deepcopy(data["pages"][0]))
    with pytest.raises(ValueError):
        template_from_dict(data)


def test_template_from_dict_bad_fraction_rejected():
    data = sample_template_dict()
    data["pages"][0]["shapes"][0]["x"] = "half"
    with pytest.raises(ValueError):
        template_from_dict(data)


def test_template_from_dict_missing_pages_rejected():
    with pytest.raises(ValueError):
        template_from_dict({"name": "empty", "pages": []})


def test_template_from_dict_missing_name_rejected():
    data = sample_template_dict()
    del data["name"]
    with pytest.raises(ValueError):
        template_from_dict(data)


# --------------------------------------------------------- resolve_shape_rect


def test_resolve_shape_rect_absolute_unchanged():
    data = sample_template_dict()
    template = template_from_dict(data)
    page = template.get_page(1, PORTRAIT)
    shape = page.shapes[0]  # capture, x=0.05 y=0.05 w=0.9 h=0.7, no anchor
    assert shape.anchor == 0
    x, y, w, h, rotation = resolve_shape_rect(shape, page)
    assert (x, y, w, h) == shape.rect_px(page.size)
    assert rotation == shape.rotation


def test_resolve_shape_rect_anchored_tracks_slot():
    data = sample_template_dict()
    data["pages"][0]["shapes"].append(
        {"type": "frame", "x": 0, "y": 0, "width": 1.0, "height": 1.0, "rotation": 0, "anchor": 1}
    )
    template = template_from_dict(data)
    page = template.get_page(1, PORTRAIT)
    anchor_shape = next(s for s in page.shapes if s.kind == "capture")
    frame = next(s for s in page.shapes if s.kind == "frame")

    # width=1.0, height=1.0, x=y=0 (centered, same size) -> resolves to
    # exactly the anchor's own rect.
    resolved = resolve_shape_rect(frame, page)
    assert resolved[:4] == anchor_shape.rect_px(page.size)

    # Move/resize the anchor: the frame's resolved rect must follow it.
    anchor_shape.x, anchor_shape.y, anchor_shape.width, anchor_shape.height = 0.1, 0.2, 0.5, 0.3
    resolved_after = resolve_shape_rect(frame, page)
    assert resolved_after[:4] == anchor_shape.rect_px(page.size)


def test_resolve_shape_rect_anchor_offset_and_scale():
    data = sample_template_dict()
    # Anchor is slot 1: x=0.05 y=0.05 w=0.9 h=0.7 (page 400x600 -> px: 20,30,360,420)
    data["pages"][0]["shapes"].append(
        {"type": "frame", "x": 0, "y": 0, "width": 1.2, "height": 1.2, "rotation": 10, "anchor": 1}
    )
    template = template_from_dict(data)
    page = template.get_page(1, PORTRAIT)
    frame = next(s for s in page.shapes if s.kind == "frame")

    x, y, w, h, rotation = resolve_shape_rect(frame, page)
    assert w == round(360 * 1.2)
    assert h == round(420 * 1.2)
    # Centered on the anchor's own center regardless of the scale change.
    anchor_cx, anchor_cy = 20 + 360 / 2, 30 + 420 / 2
    assert abs((x + w / 2) - anchor_cx) <= 1
    assert abs((y + h / 2) - anchor_cy) <= 1
    assert rotation == 10  # anchor has rotation 0, frame adds 10


def test_resolve_shape_rect_missing_anchor_falls_back_to_absolute():
    data = sample_template_dict()
    data["pages"][0]["shapes"].append(
        {"type": "frame", "x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2, "rotation": 0, "anchor": 99}
    )
    template = template_from_dict(data)
    page = template.get_page(1, PORTRAIT)
    frame = next(s for s in page.shapes if s.kind == "frame")
    assert resolve_shape_rect(frame, page)[:4] == frame.rect_px(page.size)


def test_load_template_json(tmp_path):
    path = tmp_path / "sample.json"
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(sample_template_dict(), fp)
    template = load_template(str(path))
    assert template.name == "sample"
    assert len(template.pages) == 2


def test_get_best_orientation():
    template = template_from_dict(sample_template_dict())
    # Portrait capture (width < height): 1 capture -> only portrait page available
    portrait_capture = make_capture((100, 150))
    assert template.get_best_orientation([portrait_capture]) == PORTRAIT


# ---------------------------------------------------------------- XML import


def test_parse_mxgraph_page_count_and_shapes(tmp_path):
    assets_dir = str(tmp_path / "assets")
    data = parse_mxgraph(osp.join(DATA_DIR, "template_1-2-3-4.xml"), assets_dir)
    assert data["name"] == "template_1-2-3-4"
    assert len(data["pages"]) == 4

    by_captures = {page["captures"]: page for page in data["pages"]}
    for nbr in (1, 2, 3, 4):
        page = by_captures[nbr]
        captures = [s for s in page["shapes"] if s["type"] == "capture"]
        texts = [s for s in page["shapes"] if s["type"] == "text"]
        assert len(captures) == nbr
        assert len(texts) == 2


def test_parse_mxgraph_capture_and_text_positions_within_page(tmp_path):
    assets_dir = str(tmp_path / "assets")
    data = parse_mxgraph(osp.join(DATA_DIR, "template_1-2-3-4.xml"), assets_dir)
    template = template_from_dict(data)

    for page in template.pages:
        for shape in page.shapes:
            x, y, width, height = shape.rect_px(page.size)
            # The shape rectangle must be within the page bounds (collides with it)
            assert x < page.size[0]
            assert y < page.size[1]
            assert x + width > 0
            assert y + height > 0


def test_parse_mxgraph_orientation_split(tmp_path):
    assets_dir = str(tmp_path / "assets")
    data = parse_mxgraph(osp.join(DATA_DIR, "template_1-2-3-4.xml"), assets_dir)
    template = template_from_dict(data)

    orientations = {page.captures: page.captures_orientation for page in template.pages}
    # Matches the reference plugin's own test_parser.py expectations for this file
    assert orientations[1] == PORTRAIT
    assert orientations[2] == LANDSCAPE
    assert orientations[3] == LANDSCAPE
    assert orientations[4] == PORTRAIT


def test_parse_mxgraph_compressed_symmetric_template(tmp_path):
    assets_dir = str(tmp_path / "assets")
    data = parse_mxgraph(osp.join(DATA_DIR, "symetric_template_1-2-3-4.xml"), assets_dir)
    template = template_from_dict(data)
    assert len(template.pages) == 4

    for page in template.pages:
        captures = [s for s in page.shapes if s.kind == "capture"]
        texts = [s for s in page.shapes if s.kind == "text"]
        # Symmetric template: twice as many capture shapes as the captures count, 3 texts
        assert len(captures) == page.captures * 2
        assert len(texts) == 3


def test_load_template_xml_dispatch(tmp_path):
    # load_template() must dispatch .xml files through parse_mxgraph() and
    # extract any embedded asset next to a sibling 'assets' directory.
    configdir = tmp_path / "config"
    templates_dir = configdir / "templates"
    templates_dir.mkdir(parents=True)
    xml_path = templates_dir / "template_1-2-3-4.xml"
    with open(osp.join(DATA_DIR, "template_1-2-3-4.xml"), "rb") as src, open(xml_path, "wb") as dst:
        dst.write(src.read())

    template = load_template(str(xml_path))
    assert isinstance(template, Template)
    assert len(template.pages) == 4
    assert osp.isdir(configdir / "assets")


# ------------------------------------------------------------------ factory


def test_template_picture_factory_build_size_and_capture_pixels(tmp_path):
    template = template_from_dict(sample_template_dict())
    captures = [make_capture((100, 150), (255, 0, 0)), make_capture((100, 150), (0, 0, 255))]

    factory = TemplatePictureFactory(template, PORTRAIT, *captures, assets_dir=str(tmp_path))
    assert (factory.width, factory.height) == (400, 600)

    factory.add_text("Hello", "Amatic-Bold", (0, 0, 0))
    image = factory.build()
    assert image.size == (400, 600)

    # First capture rect center: x=0.05..0.95 (center 0.5*400=200), y=0.05..0.45 (center 0.25*600=150)
    assert image.getpixel((200, 150)) != (255, 255, 255)
    # Second capture rect center: y=0.5..0.9 (center 0.7*600=420)
    assert image.getpixel((200, 420)) != (255, 255, 255)


def test_template_picture_factory_single_capture(tmp_path):
    template = template_from_dict(sample_template_dict())
    captures = [make_capture((100, 150), (10, 200, 10))]

    factory = TemplatePictureFactory(template, PORTRAIT, *captures, assets_dir=str(tmp_path))
    assert (factory.width, factory.height) == (400, 600)
    image = factory.build()
    assert image.size == (400, 600)


def test_template_picture_factory_text_does_not_crash_on_pillow10(tmp_path):
    """Regression test: the reference plugin used PIL APIs removed in Pillow 10."""
    template = template_from_dict(sample_template_dict())
    captures = [make_capture((100, 150))]
    factory = TemplatePictureFactory(template, PORTRAIT, *captures, assets_dir=str(tmp_path))
    factory.add_text("A somewhat long footer text", "Amatic-Bold", (0, 0, 0), align=factory.RIGHT)
    image = factory.build()
    assert image.size == (400, 600)


def test_template_picture_factory_image_shape(tmp_path):
    data = sample_template_dict()
    data["pages"][0]["shapes"].append(
        {"type": "image", "asset": "logo.png", "x": 0.7, "y": 0.02, "width": 0.2, "height": 0.05, "rotation": 0}
    )
    template = template_from_dict(data)

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    Image.new("RGBA", (20, 20), (0, 255, 0, 255)).save(assets_dir / "logo.png")

    captures = [make_capture((100, 150))]
    factory = TemplatePictureFactory(template, PORTRAIT, *captures, assets_dir=str(assets_dir))
    image = factory.build()
    x, y = int(0.8 * 400), int(0.045 * 600)
    pixel = image.getpixel((x, y))
    assert pixel != (255, 255, 255)


def test_template_picture_factory_missing_image_asset_skipped(tmp_path):
    data = sample_template_dict()
    data["pages"][0]["shapes"].append(
        {"type": "image", "asset": "missing.png", "x": 0.7, "y": 0.02, "width": 0.2, "height": 0.05, "rotation": 0}
    )
    template = template_from_dict(data)
    captures = [make_capture((100, 150))]
    factory = TemplatePictureFactory(template, PORTRAIT, *captures, assets_dir=str(tmp_path))
    image = factory.build()  # Must not raise despite the missing asset
    assert image.size == (400, 600)


# ------------------------------------------------------------- draw order


def _two_slot_page(extra_shapes):
    """A 2-capture page (slots 1 and 2 side by side) plus `extra_shapes`,
    returning the built TemplatePage. Shapes are appended in list order, so an
    anchored shape here starts out *after* both slots."""
    data = sample_template_dict()
    data["pages"][1]["shapes"] = [
        {"type": "capture", "index": 1, "x": 0.0, "y": 0.0, "width": 0.5, "height": 1.0, "rotation": 0},
        {"type": "capture", "index": 2, "x": 0.5, "y": 0.0, "width": 0.5, "height": 1.0, "rotation": 0},
    ] + list(extra_shapes)
    return template_from_dict(data).get_page(2, PORTRAIT)


def _order_labels(page):
    return [f"{s.kind}{s.index}" if s.kind == "capture" else s.kind for s in resolve_draw_order(page)]


def test_resolve_draw_order_unanchored_keeps_list_order():
    page = _two_slot_page([{"type": "text", "index": 1, "x": 0.1, "y": 0.9, "width": 0.8, "height": 0.05, "rotation": 0}])
    assert _order_labels(page) == ["capture1", "capture2", "text"]


def test_resolve_draw_order_anchored_shape_follows_its_slot():
    # The frame is listed LAST but anchored to slot 1, so it must be drawn
    # right after slot 1 — and therefore be obscured by slot 2.
    page = _two_slot_page(
        [{"type": "frame", "x": 0, "y": 0, "width": 1.0, "height": 1.0, "rotation": 0, "anchor": 1, "styleId": "s"}]
    )
    assert _order_labels(page) == ["capture1", "frame", "capture2"]


def test_resolve_draw_order_keeps_shape_below_its_slot():
    # Listed BEFORE its anchor slot -> it stays behind it (e.g. a mat), while
    # still moving as a group relative to the other slots.
    data = sample_template_dict()
    data["pages"][1]["shapes"] = [
        {"type": "frame", "x": 0, "y": 0, "width": 1.2, "height": 1.2, "rotation": 0, "anchor": 1, "styleId": "s"},
        {"type": "capture", "index": 1, "x": 0.0, "y": 0.0, "width": 0.5, "height": 1.0, "rotation": 0},
        {"type": "capture", "index": 2, "x": 0.5, "y": 0.0, "width": 0.5, "height": 1.0, "rotation": 0},
    ]
    page = template_from_dict(data).get_page(2, PORTRAIT)
    assert _order_labels(page) == ["frame", "capture1", "capture2"]


def test_resolve_draw_order_moving_a_slot_carries_its_anchored_shapes():
    page = _two_slot_page(
        [{"type": "frame", "x": 0, "y": 0, "width": 1.0, "height": 1.0, "rotation": 0, "anchor": 1, "styleId": "s"}]
    )
    assert _order_labels(page) == ["capture1", "frame", "capture2"]
    # Send slot 1 above slot 2 by swapping their list positions; the frame
    # anchored to it must come along rather than stay behind slot 2.
    page.shapes[0], page.shapes[1] = page.shapes[1], page.shapes[0]
    assert _order_labels(page) == ["capture2", "capture1", "frame"]


def test_resolve_draw_order_multiple_shapes_on_one_slot_keep_relative_order():
    page = _two_slot_page(
        [
            {"type": "frame", "x": 0, "y": 0, "width": 1.0, "height": 1.0, "rotation": 0, "anchor": 1, "styleId": "a"},
            {"type": "image", "asset": "logo.png", "x": 0, "y": 0, "width": 0.2, "height": 0.2, "rotation": 0, "anchor": 1},
        ]
    )
    assert _order_labels(page) == ["capture1", "frame", "image", "capture2"]


def test_resolve_draw_order_dangling_anchor_falls_back_to_list_order():
    # anchor=9 doesn't exist (e.g. the capture count changed) -> plain order.
    page = _two_slot_page(
        [{"type": "frame", "x": 0, "y": 0, "width": 1.0, "height": 1.0, "rotation": 0, "anchor": 9, "styleId": "s"}]
    )
    assert _order_labels(page) == ["capture1", "capture2", "frame"]


def test_resolve_draw_order_anchor_cycle_terminates():
    # Two capture slots anchored to each other: must not hang, and must still
    # return every shape exactly once.
    data = sample_template_dict()
    data["pages"][1]["shapes"] = [
        {"type": "capture", "index": 1, "x": 0.0, "y": 0.0, "width": 0.5, "height": 1.0, "rotation": 0, "anchor": 2},
        {"type": "capture", "index": 2, "x": 0.5, "y": 0.0, "width": 0.5, "height": 1.0, "rotation": 0, "anchor": 1},
    ]
    page = template_from_dict(data).get_page(2, PORTRAIT)
    assert sorted(_order_labels(page)) == ["capture1", "capture2"]


def test_template_picture_factory_anchored_frame_is_obscured_by_later_slot(tmp_path):
    """End-to-end: the printed picture must show slot 2's photo covering a
    frame that is anchored to slot 1 but listed last."""
    data = sample_template_dict()
    # Slot 1 fills the left half; slot 2 the right half but OVERLAPPING the
    # middle, so it covers the right part of slot 1's frame.
    data["pages"][1]["shapes"] = [
        {"type": "capture", "index": 1, "x": 0.0, "y": 0.0, "width": 0.6, "height": 1.0, "rotation": 0},
        {"type": "capture", "index": 2, "x": 0.4, "y": 0.0, "width": 0.6, "height": 1.0, "rotation": 0},
        # Frame on slot 1, listed last -> used to paint over everything.
        {"type": "frame", "x": 0, "y": 0, "width": 1.0, "height": 1.0, "rotation": 0, "anchor": 1, "styleId": "green"},
    ]
    template = template_from_dict(data)

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    save_frame_styles(
        str(assets_dir),
        [FrameStyle(id="green", name="Green", kind="vector", color="#00ff00", border_width=0.08, radius=0.0)],
    )

    captures = [make_capture((100, 150), (255, 0, 0)), make_capture((100, 150), (0, 0, 255))]
    factory = TemplatePictureFactory(template, PORTRAIT, *captures, assets_dir=str(assets_dir))
    image = factory.build()

    def greenish(pixel):
        return pixel[1] > pixel[0] + 40 and pixel[1] > pixel[2] + 40

    y = image.height // 2
    # Slot 1 spans x 0..240 on a 400px-wide page; its frame's right edge sits
    # at ~x=240, inside slot 2's box (x 160..400). Slot 2 is drawn after slot
    # 1's group, so that edge must be covered.
    assert not any(greenish(image.getpixel((x, y))) for x in range(200, 260)), "slot 2 must cover slot 1's frame"
    # The frame's left edge is outside slot 2, so it must still be visible.
    assert any(greenish(image.getpixel((x, y))) for x in range(0, 40)), "frame's left edge should still show"


def test_template_picture_factory_vector_frame_at_anchored_position(tmp_path):
    data = sample_template_dict()
    # Anchor slot 1 is x=0.05 y=0.05 w=0.9 h=0.7 (page 400x600 -> px 20,30,360,420)
    data["pages"][0]["shapes"].append(
        {
            "type": "frame",
            "x": 0,
            "y": 0,
            "width": 1.0,
            "height": 1.0,
            "rotation": 0,
            "anchor": 1,
            "styleId": "gold-thin",
        }
    )
    template = template_from_dict(data)

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    save_frame_styles(
        str(assets_dir), [FrameStyle(id="gold-thin", name="Gold thin", kind="vector", color="#00ff00", border_width=0.05, radius=0.0)]
    )

    captures = [make_capture((100, 150))]
    factory = TemplatePictureFactory(template, PORTRAIT, *captures, assets_dir=str(assets_dir))
    image = factory.build()  # RGB output (no alpha channel), white background

    # Border should appear near the anchor's own top edge (y=30), not at
    # the full-canvas edge and not at the canvas center.
    x = image.width // 2

    def greenish(pixel):
        return pixel[1] > pixel[0] and pixel[1] > pixel[2]

    assert any(greenish(image.getpixel((x, y))) for y in range(25, 45))
    assert not greenish(image.getpixel((x, 0)))  # canvas top edge: no border here


def test_template_picture_factory_image_frame_style(tmp_path):
    data = sample_template_dict()
    # x/y are TOP-LEFT for an absolute (unanchored) shape -> box spans
    # (200,300)-(400,600) on the 400x600 page.
    data["pages"][0]["shapes"].append(
        {"type": "frame", "x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5, "rotation": 0, "styleId": "photo-frame"}
    )
    template = template_from_dict(data)

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    Image.new("RGBA", (40, 20), (0, 0, 255, 255)).save(assets_dir / "frame.png")
    save_frame_styles(str(assets_dir), [FrameStyle(id="photo-frame", name="Photo frame", kind="image", asset="frame.png")])

    captures = [make_capture((100, 150))]
    factory = TemplatePictureFactory(template, PORTRAIT, *captures, assets_dir=str(assets_dir))
    image = factory.build()

    x, y = 300, 450  # well inside the (200,300)-(400,600) box
    pixel = image.getpixel((x, y))
    assert pixel[2] > pixel[0]  # bluish


def test_template_picture_factory_frame_missing_style_skipped(tmp_path):
    data = sample_template_dict()
    data["pages"][0]["shapes"].append(
        {"type": "frame", "x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5, "rotation": 0, "styleId": "does-not-exist"}
    )
    template = template_from_dict(data)
    captures = [make_capture((100, 150))]
    factory = TemplatePictureFactory(template, PORTRAIT, *captures, assets_dir=str(tmp_path))
    image = factory.build()  # Must not raise despite the unknown style
    assert image.size == (400, 600)


def test_template_picture_factory_frame_no_style_id_skipped(tmp_path):
    data = sample_template_dict()
    data["pages"][0]["shapes"].append(
        {"type": "frame", "x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5, "rotation": 0}
    )
    template = template_from_dict(data)
    captures = [make_capture((100, 150))]
    factory = TemplatePictureFactory(template, PORTRAIT, *captures, assets_dir=str(tmp_path))
    image = factory.build()
    assert image.size == (400, 600)


def test_template_picture_factory_image_frame_lock_aspect_false_stretches(tmp_path):
    data = sample_template_dict()
    # A square 40x40 source image stretched into a wide, short box (0.4 x 0.1
    # of a 400x600 page -> 160x60px) must fill it exactly when unlocked,
    # rather than preserving its own 1:1 aspect ratio.
    data["pages"][0]["shapes"].append(
        {
            "type": "frame",
            "x": 0.3,
            "y": 0.3,
            "width": 0.4,
            "height": 0.1,
            "rotation": 0,
            "styleId": "stretchy",
            "lockAspect": False,
        }
    )
    template = template_from_dict(data)

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    Image.new("RGBA", (40, 40), (255, 0, 0, 255)).save(assets_dir / "square.png")
    save_frame_styles(str(assets_dir), [FrameStyle(id="stretchy", name="Stretchy", kind="image", asset="square.png")])

    captures = [make_capture((100, 150))]
    factory = TemplatePictureFactory(template, PORTRAIT, *captures, assets_dir=str(assets_dir))
    image = factory.build()

    # Corners of the resolved 160x60 box (top-left at 0.3*400=120, 0.3*600=180)
    # should be red if stretched to fill; an aspect-preserved 60x60 square
    # centered in that box would leave these corners at the white background.
    assert image.getpixel((125, 185)) != (255, 255, 255)
    assert image.getpixel((275, 185)) != (255, 255, 255)


def test_load_frame_styles_missing_file_returns_empty(tmp_path):
    assert load_frame_styles(str(tmp_path)) == []


def test_save_and_load_frame_styles_round_trip(tmp_path):
    styles = [
        FrameStyle(id="a", name="A", kind="vector", color="#112233", border_width=0.02, radius=0.05),
        FrameStyle(id="b", name="B", kind="image", asset="b.png", opacity=0.5),
    ]
    save_frame_styles(str(tmp_path), styles)
    loaded = load_frame_styles(str(tmp_path))
    assert [s.id for s in loaded] == ["a", "b"]
    assert loaded[0].color == "#112233"
    assert loaded[1].opacity == 0.5


# ------------------------------------------------------------ layout guide


def test_render_layout_guide_size_and_capture_pixels():
    template = template_from_dict(sample_template_dict())
    page = template.get_page(2, PORTRAIT)

    image = render_layout_guide(page)
    assert image.size == page.size
    assert image.mode == "RGBA"

    # First capture rect: x=0.05..0.95, y=0.05..0.45 -> center (200, 150)
    assert image.getpixel((200, 150))[3] > 0
    # Outside every shape (top-left corner, before any capture/text rect)
    assert image.getpixel((1, 1))[3] == 0


def test_render_layout_guide_rotation_does_not_crash():
    data = sample_template_dict()
    data["pages"][0]["shapes"][0]["rotation"] = 25
    template = template_from_dict(data)
    page = template.get_page(1, PORTRAIT)

    image = render_layout_guide(page)
    assert image.size == page.size


def test_render_layout_guide_svg_contents():
    data = sample_template_dict()
    data["pages"][0]["shapes"].append(
        {"type": "image", "asset": "my <logo>.png", "x": 0.6, "y": 0.02, "width": 0.2, "height": 0.05, "rotation": 15}
    )
    template = template_from_dict(data)
    page = template.get_page(1, PORTRAIT)

    svg = render_layout_guide_svg(page)

    # Physical size in inches (page.size is in px at page.dpi) and viewBox
    width_in = page.size[0] / page.dpi
    height_in = page.size[1] / page.dpi
    assert f'width="{width_in}in"' in svg
    assert f'height="{height_in}in"' in svg
    assert f'viewBox="0 0 {page.size[0]} {page.size[1]}"' in svg

    # Single deletable guide layer/group
    assert svg.count('<g id="pibooth-layout-guides">') == 1

    # One <rect> per capture shape, with the translucent fill
    captures = [s for s in page.shapes if s.kind == "capture"]
    assert svg.count("fill-opacity") == len(captures)

    # Labels are escaped, not left as raw (unsafe) markup
    assert "&lt;logo&gt;" in svg
    assert "<logo>" not in svg

    # Rotation renders as a rotate() transform
    assert "rotate(15" in svg


# ------------------------------------------------------------------- plugin


@pytest.fixture
def web_cfg(tmp_path, monkeypatch):
    from pibooth.config.parser import PiConfigParser

    monkeypatch.setattr(PiConfigParser, "handle_autostart", lambda self: None)
    cfg = PiConfigParser(str(tmp_path / "pibooth.cfg"), None)
    cfg.set("GENERAL", "directory", str(tmp_path / "pictures"))
    return cfg


def write_template_json(cfg, data, name="sample"):
    templates_dir = cfg.join_path("templates")
    os.makedirs(templates_dir, exist_ok=True)
    path = osp.join(templates_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp)
    return path


def test_plugin_returns_template_factory_when_configured(web_cfg):
    from pibooth.pictures import get_picture_factory
    from pibooth.plugins import create_plugin_manager
    from pibooth.plugins.picture_plugin import PicturePlugin
    from pibooth.plugins.template_plugin import TemplatePlugin

    path = write_template_json(web_cfg, sample_template_dict())
    web_cfg.set("PICTURE", "template", path)

    pm = create_plugin_manager()
    pm.register(PicturePlugin(pm), name="pibooth-core:picture")
    pm.register(TemplatePlugin(pm), name="pibooth-core:template")

    captures = [make_capture((100, 150)), make_capture((100, 150))]
    default_factory = get_picture_factory(captures, "portrait", force_pil=True)
    factory = pm.hook.pibooth_setup_picture_factory(cfg=web_cfg, opt_index=0, factory=default_factory)

    assert (factory.width, factory.height) == (400, 600)


def test_plugin_passthrough_when_template_unset(web_cfg):
    from pibooth.pictures import get_picture_factory
    from pibooth.plugins import create_plugin_manager
    from pibooth.plugins.picture_plugin import PicturePlugin
    from pibooth.plugins.template_plugin import TemplatePlugin

    pm = create_plugin_manager()
    pm.register(PicturePlugin(pm), name="pibooth-core:picture")
    pm.register(TemplatePlugin(pm), name="pibooth-core:template")

    captures = [make_capture((100, 150))]
    default_factory = get_picture_factory(captures, "portrait", force_pil=True)
    factory = pm.hook.pibooth_setup_picture_factory(cfg=web_cfg, opt_index=0, factory=default_factory)

    assert (factory.width, factory.height) == (default_factory.width, default_factory.height)


def test_plugin_reloads_template_on_mtime_change(web_cfg):
    from pibooth.plugins import create_plugin_manager
    from pibooth.plugins.template_plugin import TemplatePlugin

    data = sample_template_dict()
    path = write_template_json(web_cfg, data)
    web_cfg.set("PICTURE", "template", path)

    pm = create_plugin_manager()
    plugin = TemplatePlugin(pm)

    captures = [make_capture((100, 150)), make_capture((100, 150))]
    from pibooth.pictures import get_picture_factory

    default_factory = get_picture_factory(captures, "portrait", force_pil=True)
    factory = plugin.pibooth_setup_picture_factory(cfg=web_cfg, opt_index=0, factory=default_factory)
    assert (factory.width, factory.height) == (400, 600)

    # Modify the page size and bump mtime: the cache must reload
    data["pages"][1]["size"] = [500, 700]
    time_offset = osp.getmtime(path) + 5
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp)
    os.utime(path, (time_offset, time_offset))

    factory = plugin.pibooth_setup_picture_factory(cfg=web_cfg, opt_index=0, factory=default_factory)
    assert (factory.width, factory.height) == (500, 700)


# ---------------------------------------------------------------------- API

pytest.importorskip("flask")


@pytest.fixture
def client(web_cfg):
    from pibooth.plugins import create_plugin_manager
    from pibooth.plugins.picture_plugin import PicturePlugin
    from pibooth.web.server import create_app

    pm = create_plugin_manager()
    pm.register(PicturePlugin(pm), name="pibooth-core:picture")
    app = create_app(web_cfg, pm, None)
    app.config["TESTING"] = True
    return app.test_client()


def test_api_templates_create_list_get_assign_delete(client, web_cfg):
    data = sample_template_dict()

    response = client.post("/api/templates", json={"template": data, "assign": True})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["name"] == "sample"

    assert web_cfg.get("PICTURE", "template") == payload["path"]

    listing = client.get("/api/templates").get_json()
    assert listing["active"] == "sample"
    names = [item["name"] for item in listing["templates"]]
    assert "sample" in names
    entry = next(item for item in listing["templates"] if item["name"] == "sample")
    assert len(entry["pages"]) == 2

    fetched = client.get("/api/templates/sample").get_json()
    assert fetched["name"] == "sample"
    assert len(fetched["pages"]) == 2

    delete_response = client.delete("/api/templates/sample")
    assert delete_response.status_code == 200
    assert web_cfg.get("PICTURE", "template") == ""
    assert client.get("/api/templates/sample").status_code == 404


def test_api_templates_invalid_rejected(client):
    response = client.post("/api/templates", json={"template": {"name": "", "pages": []}})
    assert response.status_code == 400


def test_api_templates_get_missing_returns_404(client):
    assert client.get("/api/templates/does-not-exist").status_code == 404


def test_api_templates_delete_missing_returns_404(client):
    assert client.delete("/api/templates/does-not-exist").status_code == 404


# ------------------------------------------------------------- frame styles


def test_api_frame_styles_create_list_update_delete(client, web_cfg):
    style = {"id": "gold-thin", "name": "Gold thin", "kind": "vector", "color": "#d4af37", "borderWidth": 0.01, "radius": 0.03}

    response = client.post("/api/frame-styles", json={"style": style})
    assert response.status_code == 200
    assert response.get_json()["style"]["id"] == "gold-thin"

    listing = client.get("/api/frame-styles").get_json()["styles"]
    assert [s["id"] for s in listing] == ["gold-thin"]
    assert listing[0]["name"] == "Gold thin"

    # Upsert: posting the same id again updates in place, not appends
    updated = {**style, "name": "Gold thin (updated)", "color": "#ffffff"}
    client.post("/api/frame-styles", json={"style": updated})
    listing = client.get("/api/frame-styles").get_json()["styles"]
    assert len(listing) == 1
    assert listing[0]["name"] == "Gold thin (updated)"
    assert listing[0]["color"] == "#ffffff"

    delete_response = client.delete("/api/frame-styles/gold-thin")
    assert delete_response.status_code == 200
    assert client.get("/api/frame-styles").get_json()["styles"] == []


def test_api_frame_styles_invalid_rejected(client):
    response = client.post("/api/frame-styles", json={"style": {"id": "bad", "name": "Bad", "kind": "not-a-kind"}})
    assert response.status_code == 400


def test_api_frame_styles_missing_id_rejected(client):
    response = client.post("/api/frame-styles", json={"style": {"name": "No id", "kind": "vector"}})
    assert response.status_code == 400


def test_api_frame_styles_delete_missing_returns_404(client):
    assert client.delete("/api/frame-styles/does-not-exist").status_code == 404


def test_api_templates_import_xml(client, web_cfg):
    with open(osp.join(DATA_DIR, "template_1-2-3-4.xml"), "rb") as fp:
        response = client.post(
            "/api/templates/import",
            data={"file": (fp, "template_1-2-3-4.xml")},
            content_type="multipart/form-data",
        )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["name"] == "template_1-2-3-4"
    assert len(payload["pages"]) == 4

    templates_dir = web_cfg.join_path("templates")
    assert osp.isfile(osp.join(templates_dir, "template_1-2-3-4.json"))


def test_api_templates_import_bad_xml_returns_400(client):
    response = client.post(
        "/api/templates/import",
        data={"file": (__import__("io").BytesIO(b"not xml"), "bad.xml")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_api_geometry_without_template_uses_default(client, web_cfg):
    web_cfg.set("PICTURE", "orientation", "portrait")
    response = client.get("/api/geometry?variant=0")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "default"
    assert payload["orientation"] == "portrait"
    assert (payload["width"], payload["height"]) == (1200, 1800)


def test_api_geometry_with_template(client, web_cfg):
    data = sample_template_dict()
    # Use a distinctive "strip" size for the 2-captures page to make the
    # geometry response unambiguous.
    data["pages"][1]["size"] = [600, 1800]
    response = client.post("/api/templates", json={"template": data, "assign": True})
    assert response.status_code == 200

    # variant selecting the 2-captures option
    choices = web_cfg.gettuple("PICTURE", "captures", int)
    variant = choices.index(2) if 2 in choices else 0
    if 2 not in choices:
        web_cfg.set("PICTURE", "captures", "(2, 1)")
        variant = 0

    response = client.get(f"/api/geometry?variant={variant}")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "template"
    assert (payload["width"], payload["height"]) == (600, 1800)


# ------------------------------------------------------------- guide export


def test_api_template_guide_svg(client):
    data = sample_template_dict()
    response = client.post("/api/templates", json={"template": data})
    assert response.status_code == 200

    response = client.get("/api/templates/sample/guide.svg?captures=2&orientation=portrait")
    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    assert "attachment" in response.headers["Content-Disposition"]
    assert "sample-2-portrait-guide.svg" in response.headers["Content-Disposition"]
    assert b"pibooth-layout-guides" in response.data


def test_api_template_guide_png(client):
    data = sample_template_dict()
    response = client.post("/api/templates", json={"template": data})
    assert response.status_code == 200

    response = client.get("/api/templates/sample/guide.png?captures=1&orientation=portrait")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert "attachment" in response.headers["Content-Disposition"]
    assert "sample-1-portrait-guide.png" in response.headers["Content-Disposition"]
    assert response.data.startswith(b"\x89PNG")


def test_api_template_guide_unknown_template_returns_404(client):
    assert client.get("/api/templates/does-not-exist/guide.svg?captures=1&orientation=portrait").status_code == 404
    assert client.get("/api/templates/does-not-exist/guide.png?captures=1&orientation=portrait").status_code == 404


def test_api_template_guide_unknown_page_returns_400(client):
    data = sample_template_dict()
    response = client.post("/api/templates", json={"template": data})
    assert response.status_code == 200

    response = client.get("/api/templates/sample/guide.svg?captures=4&orientation=portrait")
    assert response.status_code == 400
    assert "Available pages" in response.get_json()["description"]


# -------------------------------------------------------------------- events


def test_save_event_copies_template_json_and_rewrites_value(tmp_path, web_cfg):
    from pibooth.config.events import EventManager

    path = write_template_json(web_cfg, sample_template_dict())
    web_cfg.set("PICTURE", "template", path)

    manager = EventManager(web_cfg)
    manager.save_event("Smith Wedding")

    event_dir = osp.join(web_cfg.join_path("events"), "Smith Wedding")
    copied = osp.join(event_dir, "sample.json")
    assert osp.isfile(copied)

    with open(osp.join(event_dir, "event.cfg"), encoding="utf-8") as fp:
        content = fp.read()
    assert copied in content
