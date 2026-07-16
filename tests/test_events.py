import os.path as osp
from urllib.parse import quote

import pytest
from PIL import Image

pytest.importorskip("flask")

from pibooth.config.events import EventManager
from pibooth.config.parser import PiConfigParser
from pibooth.plugins import create_plugin_manager
from pibooth.plugins.picture_plugin import PicturePlugin
from pibooth.web.server import create_app


@pytest.fixture
def web_cfg(tmp_path, monkeypatch):
    # Don't touch the real ~/.config/autostart entry during tests
    monkeypatch.setattr(PiConfigParser, "handle_autostart", lambda self: None)
    cfg = PiConfigParser(str(tmp_path / "pibooth.cfg"), None)
    cfg.set("GENERAL", "directory", str(tmp_path / "pictures"))
    return cfg


@pytest.fixture
def manager(web_cfg):
    return EventManager(web_cfg)


@pytest.fixture
def client(web_cfg):
    pm = create_plugin_manager()
    pm.register(PicturePlugin(pm), name="pibooth-core:picture")
    app = create_app(web_cfg, pm, None)
    app.config["TESTING"] = True
    return app.test_client()


def make_png(path):
    Image.new("RGB", (10, 10), (255, 0, 0)).save(str(path), format="PNG")


# --------------------------------------------------------------- EventManager


def test_sanitize_strips_and_rejects(manager):
    assert manager.sanitize("  Smith Wedding !! ") == "Smith Wedding"
    assert manager.sanitize("a" * 100) == "a" * 60
    with pytest.raises(ValueError):
        manager.sanitize("###")
    with pytest.raises(ValueError):
        manager.sanitize("   ")


def test_save_event_snapshots_values_and_copies_image(manager, web_cfg, tmp_path):
    image_path = tmp_path / "bg.png"
    make_png(image_path)
    web_cfg.set("WINDOW", "background", f'"{image_path}"')
    web_cfg.set("PICTURE", "footer_text1", '"My Event"')

    event = manager.save_event("Smith Wedding")
    assert event["name"] == "Smith Wedding"
    assert event["images"] == ["bg.png"]

    event_dir = osp.join(web_cfg.join_path("events"), "Smith Wedding")
    copied = osp.join(event_dir, "bg.png")
    assert osp.isfile(copied)

    with open(osp.join(event_dir, "event.cfg"), encoding="utf-8") as fp:
        content = fp.read()
    assert copied in content
    assert 'footer_text1 = "My Event"' in content


def test_save_event_existing_without_overwrite_raises(manager, web_cfg):
    manager.save_event("Party")
    with pytest.raises(FileExistsError):
        manager.save_event("Party")


def test_save_event_overwrite_updates_snapshot(manager, web_cfg):
    manager.save_event("Party")
    web_cfg.set("PICTURE", "footer_text1", '"Updated"')

    event = manager.save_event("Party", overwrite=True)
    assert event["name"] == "Party"

    event_dir = osp.join(web_cfg.join_path("events"), "Party")
    with open(osp.join(event_dir, "event.cfg"), encoding="utf-8") as fp:
        assert 'footer_text1 = "Updated"' in fp.read()


def test_apply_event_round_trip(manager, web_cfg):
    web_cfg.set("PICTURE", "footer_text1", '"Original"')
    manager.save_event("Reunion")

    web_cfg.set("PICTURE", "footer_text1", '"Changed"')
    manager.apply_event("Reunion")

    assert web_cfg.get("PICTURE", "footer_text1") == '"Original"'
    assert web_cfg.get("GENERAL", "event") == "Reunion"
    assert osp.isfile(web_cfg.filename)


def test_apply_event_missing_raises(manager):
    with pytest.raises(FileNotFoundError):
        manager.apply_event("Nope")


def test_delete_active_event_resets_config(manager, web_cfg, tmp_path):
    image_path = tmp_path / "overlay.png"
    make_png(image_path)
    web_cfg.set("PICTURE", "overlays", f'"{image_path}"')

    manager.save_event("Gala")
    manager.apply_event("Gala")

    assert web_cfg.get("GENERAL", "event") == "Gala"
    assert "Gala" in web_cfg.get("PICTURE", "overlays")

    manager.delete_event("Gala")

    assert web_cfg.get("GENERAL", "event") == ""
    assert web_cfg.get("PICTURE", "overlays") == ""
    assert manager.list_events() == []


def test_delete_inactive_event_does_not_touch_config(manager, web_cfg):
    manager.save_event("Unused")
    web_cfg.set("PICTURE", "footer_text1", '"Untouched"')

    manager.delete_event("Unused")

    assert web_cfg.get("PICTURE", "footer_text1") == '"Untouched"'
    assert manager.list_events() == []


def test_delete_missing_event_raises(manager):
    with pytest.raises(FileNotFoundError):
        manager.delete_event("Nope")


# ---------------------------------------------------------------------- API


def test_events_api_full_flow(client, web_cfg):
    web_cfg.set("PICTURE", "footer_text1", '"My Event"')

    response = client.post("/api/events", json={"name": "Smith Wedding"})
    assert response.status_code == 200
    assert response.get_json()["name"] == "Smith Wedding"

    payload = client.get("/api/events").get_json()
    assert payload["active"] == ""
    assert "Smith Wedding" in [event["name"] for event in payload["events"]]

    name = quote("Smith Wedding")
    response = client.post(f"/api/events/{name}/apply")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert web_cfg.get("GENERAL", "event") == "Smith Wedding"
    assert client.get("/api/events").get_json()["active"] == "Smith Wedding"

    web_cfg.set("PICTURE", "footer_text1", '"Updated"')
    response = client.put(f"/api/events/{name}")
    assert response.status_code == 200

    response = client.delete(f"/api/events/{name}")
    assert response.status_code == 200
    assert web_cfg.get("GENERAL", "event") == ""
    assert client.get("/api/events").get_json()["events"] == []


def test_events_api_duplicate_conflict(client):
    client.post("/api/events", json={"name": "Party"})
    response = client.post("/api/events", json={"name": "Party"})
    assert response.status_code == 409


def test_events_api_missing(client):
    assert client.post("/api/events/Nope/apply").status_code == 404
    assert client.delete("/api/events/Nope").status_code == 404


def test_events_api_bad_name(client):
    assert client.post("/api/events", json={"name": "###"}).status_code == 400
    assert client.post("/api/events", json={}).status_code == 400
