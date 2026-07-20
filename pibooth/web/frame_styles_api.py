"""REST API for reusable frame styles, referenced by frame-kind shapes in
picture layout templates (see :py:mod:`pibooth.pictures.template`).

Styles are stored as one flat list at ``<configdir>/assets/frame_styles.json``
so editing a style updates every template shape referencing it by id.
"""

from typing import Any

from flask import Blueprint, Response, abort, current_app, jsonify, request

from pibooth.pictures.template import frame_style_from_dict, load_frame_styles, save_frame_styles
from pibooth.utils import LOGGER

frame_styles_api = Blueprint("frame_styles_api", __name__, url_prefix="/api")


def _pibooth() -> dict[str, Any]:
    return current_app.config["PIBOOTH"]


def _assets_dir() -> str:
    return str(_pibooth()["cfg"].join_path("assets"))


@frame_styles_api.route("/frame-styles", methods=["GET"])
def list_frame_styles() -> Response:
    styles = load_frame_styles(_assets_dir())
    return jsonify({"styles": [style.to_dict() for style in styles]})


@frame_styles_api.route("/frame-styles", methods=["POST"])
def upsert_frame_style() -> Response:
    payload = request.get_json(silent=True) or {}
    raw_style = payload.get("style")

    try:
        style = frame_style_from_dict(raw_style, 0)
    except ValueError as ex:
        abort(400, description=str(ex))

    pibooth = _pibooth()
    assets_dir = _assets_dir()

    with pibooth["lock"]:
        styles = load_frame_styles(assets_dir)
        styles = [existing for existing in styles if existing.id != style.id]
        styles.append(style)
        save_frame_styles(assets_dir, styles)

    LOGGER.info("Frame style '%s' saved from the web interface", style.id)
    pibooth["notify"]()
    return jsonify({"style": style.to_dict()})


@frame_styles_api.route("/frame-styles/<style_id>", methods=["DELETE"])
def delete_frame_style(style_id: str) -> Response:
    pibooth = _pibooth()
    assets_dir = _assets_dir()

    with pibooth["lock"]:
        styles = load_frame_styles(assets_dir)
        remaining = [style for style in styles if style.id != style_id]
        if len(remaining) == len(styles):
            abort(404, description=f"Frame style '{style_id}' not found")
        save_frame_styles(assets_dir, remaining)

    LOGGER.info("Frame style '%s' deleted from the web interface", style_id)
    pibooth["notify"]()
    return jsonify({"ok": True})
