import copy
import json
import os
import os.path as osp

import pytest
from PIL import Image

from pibooth.pictures import LANDSCAPE, PORTRAIT
from pibooth.pictures.template import (
    Template,
    TemplatePictureFactory,
    load_template,
    parse_mxgraph,
    render_layout_guide,
    render_layout_guide_svg,
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
