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
        "elements": [{"type": "frame", "color": "#00ff00", "width": 0.02, "radius": 0.0, "inset": 0.05}],
    }
    image = render_design(spec, str(tmp_path), (1800, 2700))
    min_dim = min(image.size)
    inset = int(0.05 * min_dim)
    # Sample along the top border, away from the rounded corners
    x = image.width // 2
    found = False
    for y in range(inset - 5, inset + 15):
        pixel = image.getpixel((x, y))
        if pixel[3] > 0:
            found = True
            assert pixel[1] > pixel[0] and pixel[1] > pixel[2]  # greenish
    assert found


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
            {"type": "frame", "color": "#d4af37", "width": 0.01, "radius": 0.03, "inset": 0.02},
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

    fetched = client.get("/api/designs/gold-frame").get_json()
    assert fetched["orientation"] == "portrait"
    assert len(fetched["elements"]) == 3


def test_designs_assign_sets_overlay_config(client, web_cfg):
    spec = {"name": "simple", "orientation": "portrait", "elements": []}
    response = client.post("/api/designs", json={"spec": spec, "assign": True})
    assert response.status_code == 200
    png_path = response.get_json()["png_path"]

    assert web_cfg.get("PICTURE", "overlays") == f'"{png_path}"'
    with open(web_cfg.filename) as fp:
        assert png_path in fp.read()


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


def test_designs_delete_missing_returns_404(client):
    assert client.delete("/api/designs/does-not-exist").status_code == 404


def test_designs_get_missing_returns_404(client):
    assert client.get("/api/designs/does-not-exist").status_code == 404


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
