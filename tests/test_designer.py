import json
import os.path as osp

import pytest
from PIL import Image

pytest.importorskip("flask")

from pibooth.config.parser import PiConfigParser
from pibooth.plugins import create_plugin_manager
from pibooth.plugins.picture_plugin import PicturePlugin
from pibooth.web.designer import render_design
from pibooth.web.server import create_app


@pytest.fixture
def web_cfg(tmp_path, monkeypatch):
    # Don't touch the real ~/.config/autostart entry during tests
    monkeypatch.setattr(PiConfigParser, "handle_autostart", lambda self: None)
    cfg = PiConfigParser(str(tmp_path / "pibooth.cfg"), None)
    cfg.set("GENERAL", "directory", str(tmp_path / "pictures"))
    return cfg


@pytest.fixture
def client(web_cfg):
    pm = create_plugin_manager()
    pm.register(PicturePlugin(pm), name="pibooth-core:picture")
    app = create_app(web_cfg, pm, None)
    app.config["TESTING"] = True
    return app.test_client()


def make_asset(assets_dir, name, size=(20, 20), color=(255, 0, 0)):
    import os

    os.makedirs(assets_dir, exist_ok=True)
    path = osp.join(assets_dir, name)
    Image.new("RGBA", size, (*color, 255)).save(path, format="PNG")
    return path


# ------------------------------------------------------------------ render


def test_render_design_portrait_size_and_mode(tmp_path):
    spec = {"name": "empty", "orientation": "portrait", "elements": []}
    image = render_design(spec, str(tmp_path), (1800, 2700))
    assert image.size == (1800, 2700)
    assert image.mode == "RGBA"


def test_render_design_landscape_size(tmp_path):
    spec = {"name": "empty", "orientation": "landscape", "elements": []}
    image = render_design(spec, str(tmp_path), (2700, 1800))
    assert image.size == (2700, 1800)


def test_render_text_element_draws_pixels(tmp_path):
    spec = {
        "name": "text-test",
        "orientation": "portrait",
        "elements": [
            {
                "type": "text",
                "text": "Hello",
                "font": "Amatic-Bold",
                "color": "#ff0000",
                "x": 0.5,
                "y": 0.5,
                "size": 0.2,
                "rotation": 0,
                "align": "center",
            }
        ],
    }
    image = render_design(spec, str(tmp_path), (1800, 2700))
    # Search a region around the text's center for non-transparent pixels
    cx, cy = int(0.5 * image.width), int(0.5 * image.height)
    region = image.crop((cx - 150, cy - 100, cx + 150, cy + 100))
    alphas = [region.getpixel((x, y))[3] for x in range(region.width) for y in range(region.height)]
    assert any(alpha > 0 for alpha in alphas)


def test_render_frame_element_draws_border(tmp_path):
    spec = {
        "name": "frame-test",
        "orientation": "portrait",
        "elements": [
            {
                "type": "frame",
                "color": "#00ff00",
                "borderWidth": 0.02,
                "radius": 0.0,
                "x": 0.5,
                "y": 0.5,
                "width": 0.9,
                "height": 0.9,
                "rotation": 0,
            }
        ],
    }
    image = render_design(spec, str(tmp_path), (1800, 2700))
    top_edge = int(0.5 * image.height - 0.5 * 0.9 * image.height)
    # Sample along the top border, away from the rounded corners
    x = image.width // 2
    found = False
    for y in range(top_edge - 5, top_edge + 15):
        pixel = image.getpixel((x, y))
        if pixel[3] > 0:
            found = True
            assert pixel[1] > pixel[0] and pixel[1] > pixel[2]  # greenish
    assert found


def test_render_frame_element_rotates(tmp_path):
    # A wide (non-square), unrotated frame has border pixels along its full
    # width at the vertical center but none near the corners (well inside
    # the box, above/below the border, given the box's aspect ratio); at
    # rotation=90 that same physical direction now has border where the
    # unrotated one didn't, proving the rotation actually applies.
    size = (1800, 2700)
    base = {
        "type": "frame",
        "color": "#ff00ff",
        "borderWidth": 0.01,
        "radius": 0.0,
        "x": 0.5,
        "y": 0.5,
        "width": 0.8,
        "height": 0.2,
    }
    unrotated = render_design(
        {"name": "r0", "orientation": "portrait", "elements": [{**base, "rotation": 0}]}, str(tmp_path), size
    )
    rotated = render_design(
        {"name": "r90", "orientation": "portrait", "elements": [{**base, "rotation": 90}]}, str(tmp_path), size
    )
    # Sample a small neighborhood on the unrotated frame's left border —
    # well outside the rotated frame's (now tall, narrow) footprint.
    x, y = int(0.5 * size[0] - 0.5 * 0.8 * size[0]), size[1] // 2

    def alphas(img):
        return [img.getpixel((px, py))[3] for px in range(x - 3, x + 3) for py in range(y - 10, y + 10)]

    assert any(alpha > 0 for alpha in alphas(unrotated))
    assert all(alpha == 0 for alpha in alphas(rotated))


def test_render_unknown_element_type_ignored(tmp_path):
    spec = {"name": "unknown", "orientation": "portrait", "elements": [{"type": "sparkle", "x": 0.5, "y": 0.5}]}
    image = render_design(spec, str(tmp_path), (1800, 2700))
    assert image.size == (1800, 2700)
    # Fully transparent: nothing was drawn
    assert all(image.getpixel((x, y))[3] == 0 for x in (0, image.width - 1) for y in (0, image.height - 1))


def test_render_missing_image_asset_skipped(tmp_path):
    spec = {
        "name": "missing-asset",
        "orientation": "portrait",
        "elements": [{"type": "image", "asset": "does-not-exist.png", "x": 0.5, "y": 0.5, "width": 0.2}],
    }
    # Should not raise despite the missing asset
    image = render_design(spec, str(tmp_path), (1800, 2700))
    assert image.size == (1800, 2700)


def _has_ink(image, x_fraction, y_fraction=0.5):
    """True if any non-transparent pixel exists in a region around the given
    fractional position (used to check whether a text element rendered).
    """
    cx, cy = int(x_fraction * image.width), int(y_fraction * image.height)
    region = image.crop((cx - 150, cy - 100, cx + 150, cy + 100))
    return any(region.getpixel((x, y))[3] > 0 for x in range(region.width) for y in range(region.height))


_LAYERED_SPEC = {
    "name": "layered",
    "orientation": "portrait",
    "elements": [
        {
            "type": "text",
            "text": "BG",
            "font": "Amatic-Bold",
            "color": "#ff0000",
            "x": 0.25,
            "y": 0.5,
            "size": 0.2,
            "rotation": 0,
            "align": "center",
            "layer": "background",
        },
        {
            "type": "text",
            "text": "OV",
            "font": "Amatic-Bold",
            "color": "#00ff00",
            "x": 0.75,
            "y": 0.5,
            "size": 0.2,
            "rotation": 0,
            "align": "center",
            "layer": "overlay",
        },
    ],
}


def test_render_design_layer_filter_background(tmp_path):
    image = render_design(_LAYERED_SPEC, str(tmp_path), (1800, 2700), layer="background")
    assert _has_ink(image, 0.25)
    assert not _has_ink(image, 0.75)


def test_render_design_layer_filter_overlay(tmp_path):
    image = render_design(_LAYERED_SPEC, str(tmp_path), (1800, 2700), layer="overlay")
    assert _has_ink(image, 0.75)
    assert not _has_ink(image, 0.25)


def test_render_design_no_layer_renders_every_element(tmp_path):
    image = render_design(_LAYERED_SPEC, str(tmp_path), (1800, 2700))
    assert _has_ink(image, 0.25)
    assert _has_ink(image, 0.75)


# --------------------------------------------------------------------- API


def test_designs_create_list_get_roundtrip(client, web_cfg):
    assets_dir = web_cfg.join_path("assets")
    make_asset(assets_dir, "logo.png")

    spec = {
        "name": "gold-frame",
        "orientation": "portrait",
        "elements": [
            {
                "type": "text",
                "text": "Smith Wedding",
                "font": "Amatic-Bold",
                "color": "#d4af37",
                "x": 0.5,
                "y": 0.92,
                "size": 0.06,
                "rotation": 0,
                "align": "center",
            },
            {"type": "image", "asset": "logo.png", "x": 0.85, "y": 0.08, "width": 0.2, "rotation": 0, "opacity": 1.0},
            {
                "type": "frame",
                "color": "#d4af37",
                "borderWidth": 0.01,
                "radius": 0.03,
                "x": 0.5,
                "y": 0.5,
                "width": 0.9,
                "height": 0.9,
                "rotation": 0,
            },
        ],
    }

    response = client.post("/api/designs", json={"spec": spec, "assign": False})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["name"] == "gold-frame"

    designs_dir = osp.join(assets_dir, "designs")
    assert osp.isfile(osp.join(designs_dir, "gold-frame.json"))
    assert osp.isfile(osp.join(designs_dir, "gold-frame.png"))

    # The rendered PNG follows the current final-picture geometry (no template
    # assigned here, so this is the 4x6@300dpi default) rather than a constant.
    geometry = client.get("/api/geometry").get_json()
    with Image.open(osp.join(designs_dir, "gold-frame.png")) as png:
        assert png.size == (geometry["width"], geometry["height"])

    listing = client.get("/api/designs").get_json()["designs"]
    names = [item["name"] for item in listing]
    assert "gold-frame" in names
    entry = next(item for item in listing if item["name"] == "gold-frame")
    assert entry["orientation"] == "portrait"
    assert entry["png"] is True
    assert entry["png_path"] == osp.join(designs_dir, "gold-frame.png")
    # No background-layer element in this design
    assert entry["has_background"] is False
    assert entry["background_png_path"] is None

    fetched = client.get("/api/designs/gold-frame").get_json()
    assert fetched["orientation"] == "portrait"
    assert len(fetched["elements"]) == 3


def test_designs_list_reports_background_fields(client, web_cfg):
    assets_dir = web_cfg.join_path("assets")
    make_asset(assets_dir, "photo.png")
    spec = {
        "name": "with-bg-listing",
        "orientation": "portrait",
        "elements": [
            {
                "type": "image",
                "asset": "photo.png",
                "x": 0.5,
                "y": 0.5,
                "width": 0.5,
                "rotation": 0,
                "opacity": 1.0,
                "layer": "background",
            }
        ],
    }
    client.post("/api/designs", json={"spec": spec, "assign": False})

    designs_dir = osp.join(assets_dir, "designs")
    listing = client.get("/api/designs").get_json()["designs"]
    entry = next(item for item in listing if item["name"] == "with-bg-listing")
    assert entry["has_background"] is True
    assert entry["background_png_path"] == osp.join(designs_dir, "with-bg-listing.background.png")
    assert osp.isfile(entry["background_png_path"])


def test_designs_assign_sets_overlay_config(client, web_cfg):
    spec = {"name": "simple", "orientation": "portrait", "elements": []}
    response = client.post("/api/designs", json={"spec": spec, "assign": True})
    assert response.status_code == 200
    png_path = response.get_json()["png_path"]

    assert web_cfg.get("PICTURE", "overlays") == f'"{png_path}"'
    with open(web_cfg.filename) as fp:
        assert png_path in fp.read()


def test_designs_assign_sets_background_config(client, web_cfg):
    assets_dir = web_cfg.join_path("assets")
    make_asset(assets_dir, "photo.png")
    spec = {
        "name": "with-bg",
        "orientation": "portrait",
        "elements": [
            {
                "type": "image",
                "asset": "photo.png",
                "x": 0.5,
                "y": 0.5,
                "width": 0.5,
                "rotation": 0,
                "opacity": 1.0,
                "layer": "background",
            }
        ],
    }
    response = client.post("/api/designs", json={"spec": spec, "assign": True})
    assert response.status_code == 200
    payload = response.get_json()
    png_path = payload["png_path"]
    bg_png_path = payload["background_png_path"]
    assert bg_png_path is not None

    designs_dir = osp.join(assets_dir, "designs")
    assert osp.isfile(osp.join(designs_dir, "with-bg.png"))
    assert osp.isfile(osp.join(designs_dir, "with-bg.background.png"))

    assert web_cfg.get("PICTURE", "overlays") == f'"{png_path}"'
    assert web_cfg.get("PICTURE", "backgrounds") == f'"{bg_png_path}"'


def test_designs_assign_without_background_leaves_config_untouched(client, web_cfg):
    spec = {
        "name": "overlay-only",
        "orientation": "portrait",
        "elements": [
            {
                "type": "frame",
                "color": "#000000",
                "borderWidth": 0.01,
                "radius": 0.0,
                "x": 0.5,
                "y": 0.5,
                "width": 0.9,
                "height": 0.9,
            }
        ],
    }
    response = client.post("/api/designs", json={"spec": spec, "assign": True})
    assert response.status_code == 200
    assert response.get_json()["background_png_path"] is None
    assert web_cfg.get("PICTURE", "backgrounds") == "(255, 255, 255)"
    designs_dir = osp.join(web_cfg.join_path("assets"), "designs")
    assert not osp.isfile(osp.join(designs_dir, "overlay-only.background.png"))


def test_designs_reassign_without_background_resets_config(client, web_cfg):
    assets_dir = web_cfg.join_path("assets")
    make_asset(assets_dir, "photo.png")
    spec_with_bg = {
        "name": "toggle-bg",
        "orientation": "portrait",
        "elements": [
            {
                "type": "image",
                "asset": "photo.png",
                "x": 0.5,
                "y": 0.5,
                "width": 0.5,
                "rotation": 0,
                "opacity": 1.0,
                "layer": "background",
            }
        ],
    }
    response = client.post("/api/designs", json={"spec": spec_with_bg, "assign": True})
    bg_png_path = response.get_json()["background_png_path"]
    assert web_cfg.get("PICTURE", "backgrounds") == f'"{bg_png_path}"'

    spec_without_bg = {"name": "toggle-bg", "orientation": "portrait", "elements": []}
    response2 = client.post("/api/designs", json={"spec": spec_without_bg, "assign": True})
    assert response2.status_code == 200
    assert response2.get_json()["background_png_path"] is None
    assert web_cfg.get("PICTURE", "backgrounds") == "(255, 255, 255)"

    designs_dir = osp.join(assets_dir, "designs")
    assert not osp.isfile(osp.join(designs_dir, "toggle-bg.background.png"))


def test_designs_invalid_layer_value(client):
    spec = {
        "name": "bad-layer",
        "orientation": "portrait",
        "elements": [
            {
                "type": "frame",
                "color": "#000000",
                "borderWidth": 0.01,
                "radius": 0.0,
                "x": 0.5,
                "y": 0.5,
                "width": 0.9,
                "height": 0.9,
                "layer": "sideways",
            }
        ],
    }
    response = client.post("/api/designs", json={"spec": spec})
    assert response.status_code == 400


def test_designs_invalid_spec_missing_name(client):
    spec = {"orientation": "portrait", "elements": []}
    assert client.post("/api/designs", json={"spec": spec}).status_code == 400


def test_designs_invalid_spec_bad_orientation(client):
    spec = {"name": "bad", "orientation": "square", "elements": []}
    assert client.post("/api/designs", json={"spec": spec}).status_code == 400


def test_designs_invalid_spec_elements_not_list(client):
    spec = {"name": "bad", "orientation": "portrait", "elements": "nope"}
    assert client.post("/api/designs", json={"spec": spec}).status_code == 400


def test_designs_invalid_element_missing_field(client):
    spec = {"name": "bad", "orientation": "portrait", "elements": [{"type": "text", "text": "hi"}]}
    response = client.post("/api/designs", json={"spec": spec})
    assert response.status_code == 400
    assert "field" in response.get_json()["description"].lower()


def test_designs_invalid_frame_missing_width_height(client):
    # A frame is now a free box like image/text elements — width/height are
    # required, not derived from an inset.
    spec = {
        "name": "bad-frame",
        "orientation": "portrait",
        "elements": [{"type": "frame", "color": "#000000", "borderWidth": 0.01, "radius": 0.0, "x": 0.5, "y": 0.5}],
    }
    response = client.post("/api/designs", json={"spec": spec})
    assert response.status_code == 400
    assert "field" in response.get_json()["description"].lower()


def test_designs_invalid_element_non_numeric_field(client):
    spec = {
        "name": "bad",
        "orientation": "portrait",
        "elements": [
            {
                "type": "text",
                "text": "hi",
                "font": "Amatic-Bold",
                "color": "#000000",
                "x": "half",
                "y": 0.5,
                "size": 0.1,
            }
        ],
    }
    response = client.post("/api/designs", json={"spec": spec})
    assert response.status_code == 400


def test_designs_with_template_renders_at_template_page_size(client, web_cfg):
    template = {
        "name": "wedding-strip",
        "pages": [
            {
                "captures": 2,
                "orientation": "portrait",
                "paper": "custom",
                "dpi": 300,
                "size": [600, 1800],
                "shapes": [],
            }
        ],
    }
    response = client.post("/api/templates", json={"template": template})
    assert response.status_code == 200

    spec = {"name": "for-strip", "orientation": "portrait", "elements": []}
    response = client.post("/api/designs", json={"spec": spec, "template": "wedding-strip"})
    assert response.status_code == 200

    designs_dir = osp.join(web_cfg.join_path("assets"), "designs")
    with Image.open(osp.join(designs_dir, "for-strip.png")) as png:
        assert png.size == (600, 1800)

    with open(osp.join(designs_dir, "for-strip.json"), encoding="utf-8") as fp:
        stored = json.load(fp)
    assert stored["template"] == "wedding-strip"

    # The template must not have been assigned to the booth as a side effect
    assert web_cfg.get("PICTURE", "template") == ""


def test_designs_with_template_swaps_dims_for_mismatched_orientation(client, web_cfg):
    # Template only has a landscape page; requesting a portrait design must
    # swap dimensions so the render still comes out in the design's own
    # orientation (mirrors _canvas_size()'s existing swap behaviour).
    template = {
        "name": "landscape-only",
        "pages": [
            {
                "captures": 1,
                "orientation": "landscape",
                "paper": "custom",
                "dpi": 300,
                "size": [1800, 1200],
                "shapes": [],
            }
        ],
    }
    response = client.post("/api/templates", json={"template": template})
    assert response.status_code == 200

    spec = {"name": "portrait-design", "orientation": "portrait", "elements": []}
    response = client.post("/api/designs", json={"spec": spec, "template": "landscape-only"})
    assert response.status_code == 200

    designs_dir = osp.join(web_cfg.join_path("assets"), "designs")
    with Image.open(osp.join(designs_dir, "portrait-design.png")) as png:
        assert png.size == (1200, 1800)


def test_designs_with_unknown_template_returns_400(client):
    spec = {"name": "orphan", "orientation": "portrait", "elements": []}
    response = client.post("/api/designs", json={"spec": spec, "template": "does-not-exist"})
    assert response.status_code == 400


def test_designs_delete_removes_files_and_resets_overlay(client, web_cfg):
    spec = {"name": "to-delete", "orientation": "portrait", "elements": []}
    response = client.post("/api/designs", json={"spec": spec, "assign": True})
    png_path = response.get_json()["png_path"]
    assert web_cfg.get("PICTURE", "overlays") == f'"{png_path}"'

    delete_response = client.delete("/api/designs/to-delete")
    assert delete_response.status_code == 200

    designs_dir = osp.join(web_cfg.join_path("assets"), "designs")
    assert not osp.isfile(osp.join(designs_dir, "to-delete.json"))
    assert not osp.isfile(osp.join(designs_dir, "to-delete.png"))
    assert web_cfg.get("PICTURE", "overlays") == ""


def test_designs_delete_removes_background_and_resets_config(client, web_cfg):
    assets_dir = web_cfg.join_path("assets")
    make_asset(assets_dir, "photo.png")
    spec = {
        "name": "bg-to-delete",
        "orientation": "portrait",
        "elements": [
            {
                "type": "image",
                "asset": "photo.png",
                "x": 0.5,
                "y": 0.5,
                "width": 0.5,
                "rotation": 0,
                "opacity": 1.0,
                "layer": "background",
            }
        ],
    }
    response = client.post("/api/designs", json={"spec": spec, "assign": True})
    bg_png_path = response.get_json()["background_png_path"]
    assert web_cfg.get("PICTURE", "backgrounds") == f'"{bg_png_path}"'

    delete_response = client.delete("/api/designs/bg-to-delete")
    assert delete_response.status_code == 200

    designs_dir = osp.join(assets_dir, "designs")
    assert not osp.isfile(osp.join(designs_dir, "bg-to-delete.background.png"))
    assert web_cfg.get("PICTURE", "backgrounds") == "(255, 255, 255)"


def test_designs_delete_missing_returns_404(client):
    assert client.delete("/api/designs/does-not-exist").status_code == 404


def test_designs_get_missing_returns_404(client):
    assert client.get("/api/designs/does-not-exist").status_code == 404


# --------------------------------------------------------------- design image


def test_design_image_serves_overlay_by_default(client, web_cfg):
    spec = {"name": "for-thumb", "orientation": "portrait", "elements": []}
    client.post("/api/designs", json={"spec": spec})

    response = client.get("/api/designs/for-thumb/image")
    assert response.status_code == 200
    assert response.data.startswith(b"\x89PNG")


def test_design_image_serves_background_when_present(client, web_cfg):
    assets_dir = web_cfg.join_path("assets")
    make_asset(assets_dir, "photo.png")
    spec = {
        "name": "for-thumb-bg",
        "orientation": "portrait",
        "elements": [
            {
                "type": "image",
                "asset": "photo.png",
                "x": 0.5,
                "y": 0.5,
                "width": 0.5,
                "rotation": 0,
                "opacity": 1.0,
                "layer": "background",
            }
        ],
    }
    client.post("/api/designs", json={"spec": spec})

    response = client.get("/api/designs/for-thumb-bg/image?layer=background")
    assert response.status_code == 200
    assert response.data.startswith(b"\x89PNG")


def test_design_image_background_404_when_absent(client, web_cfg):
    spec = {"name": "overlay-only-thumb", "orientation": "portrait", "elements": []}
    client.post("/api/designs", json={"spec": spec})

    response = client.get("/api/designs/overlay-only-thumb/image?layer=background")
    assert response.status_code == 404


def test_design_image_unknown_design_returns_404(client):
    response = client.get("/api/designs/does-not-exist/image")
    assert response.status_code == 404


def test_design_image_invalid_layer_returns_400(client, web_cfg):
    spec = {"name": "bad-layer-thumb", "orientation": "portrait", "elements": []}
    client.post("/api/designs", json={"spec": spec})

    response = client.get("/api/designs/bad-layer-thumb/image?layer=sideways")
    assert response.status_code == 400


# -------------------------------------------------------------------- preview


def test_preview_overlay_zero_does_not_change_config(client, web_cfg, tmp_path):
    overlay_path = tmp_path / "overlay.png"
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(str(overlay_path))
    web_cfg.set("PICTURE", "overlays", f'"{overlay_path}"')

    response = client.get("/api/preview?variant=0&overlay=0")
    assert response.status_code == 200
    assert response.data.startswith(b"\x89PNG")

    assert web_cfg.get("PICTURE", "overlays") == f'"{overlay_path}"'


# ----------------------------------------------------------------------- fonts


def test_font_file_served(client):
    response = client.get("/api/fonts/Amatic-Bold/file")
    assert response.status_code == 200


def test_font_file_missing_returns_404(client):
    response = client.get("/api/fonts/definitely-not-a-font-xyz/file")
    assert response.status_code == 404
