"""REST API for the browser-based print overlay designer (see
:py:mod:`pibooth.web.designer` for the PIL rendering logic).

A design is stored as a re-editable JSON spec plus its rendered PNG in
``<configdir>/assets/designs/<name>.json`` and ``<name>.png``.
"""

import json
import os
import os.path as osp
import string
from datetime import datetime
from typing import Any

from flask import Blueprint, Response, abort, current_app, jsonify, request, send_file

from pibooth import fonts
from pibooth.utils import LOGGER
from pibooth.web.designer import render_design
from pibooth.web.templates_api import get_final_picture_size

designer_api = Blueprint("designer_api", __name__, url_prefix="/api")

#: Characters allowed in a sanitized design name (same rules as event names)
_ALLOWED_NAME_CHARS = string.ascii_letters + string.digits + " -_"

#: Required and numeric fields for each known element type. Unknown element
#: types are accepted (ignored gracefully at render time) but not validated.
_ELEMENT_SCHEMA: dict[str, dict[str, list[str]]] = {
    "text": {
        "required": ["text", "font", "color", "x", "y", "size"],
        "numeric": ["x", "y", "size", "rotation"],
    },
    "image": {
        "required": ["asset", "x", "y", "width"],
        "numeric": ["x", "y", "width", "rotation", "opacity"],
    },
    "frame": {
        "required": ["color", "width", "radius", "inset"],
        "numeric": ["width", "radius", "inset"],
    },
}


def _pibooth() -> dict[str, Any]:
    return current_app.config["PIBOOTH"]


def _sanitize_name(name: str) -> str:
    """Return a filesystem-safe design name (letters, digits, space, dash,
    underscore, stripped and truncated to 60 characters).
    """
    cleaned = "".join(char for char in name if char in _ALLOWED_NAME_CHARS).strip()
    cleaned = cleaned[:60].strip()
    if not cleaned:
        raise ValueError("Design name is empty once sanitized")
    return cleaned


def _designs_dir(cfg: Any) -> str:
    return osp.join(cfg.join_path("assets"), "designs")


def _canvas_size(cfg: Any, orientation: str) -> tuple[int, int]:
    """Return the canvas size to render a design of the given orientation,
    following the current final-picture geometry (see
    :py:func:`pibooth.web.templates_api.get_final_picture_size`).

    The geometry is always resolved for its own (possibly template-driven)
    orientation; when that differs from the design's own orientation, the
    dimensions are swapped so the design still renders in its own orientation.
    """
    width, height, _source = get_final_picture_size(cfg, variant=0)
    geometry_orientation = "portrait" if width < height else "landscape"
    if geometry_orientation != orientation:
        width, height = height, width
    return width, height


def _design_paths(cfg: Any, name: str) -> tuple[str, str, str]:
    """Return (sanitized name, json path, png path) for a design name."""
    name = _sanitize_name(name)
    designs_dir = _designs_dir(cfg)
    return name, osp.join(designs_dir, f"{name}.json"), osp.join(designs_dir, f"{name}.png")


def _validate_element(element: Any, index: int) -> None:
    """Raise ValueError with a helpful description if the element is invalid."""
    if not isinstance(element, dict):
        raise ValueError(f"Element #{index} must be an object")

    schema = _ELEMENT_SCHEMA.get(str(element.get("type", "")))
    if schema is None:
        return  # Unknown type: ignored gracefully at render time

    missing = [key for key in schema["required"] if key not in element]
    if missing:
        raise ValueError(f"Element #{index} ('{element['type']}') missing field(s): {', '.join(missing)}")

    for key in schema["numeric"]:
        if key not in element:
            continue
        value = element[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Element #{index} ('{element['type']}') field '{key}' must be numeric")


def _validate_spec(spec: Any) -> str:
    """Validate a design spec, return the sanitized design name.

    :raises ValueError: with a helpful description if the spec is invalid
    """
    if not isinstance(spec, dict):
        raise ValueError("Missing 'spec' object")

    name = _sanitize_name(str(spec.get("name", "")))

    orientation = spec.get("orientation")
    if orientation not in ("portrait", "landscape"):
        raise ValueError("'orientation' must be 'portrait' or 'landscape'")

    elements = spec.get("elements")
    if not isinstance(elements, list):
        raise ValueError("'elements' must be a list")

    for index, element in enumerate(elements):
        _validate_element(element, index)

    return name


@designer_api.route("/designs", methods=["GET"])
def list_designs() -> Response:
    cfg = _pibooth()["cfg"]
    designs_dir = _designs_dir(cfg)
    designs = []
    if osp.isdir(designs_dir):
        for filename in os.listdir(designs_dir):
            if not filename.endswith(".json"):
                continue
            name = filename[: -len(".json")]
            json_path = osp.join(designs_dir, filename)
            try:
                with open(json_path, encoding="utf-8") as fp:
                    spec = json.load(fp)
            except (OSError, ValueError):
                continue
            designs.append(
                {
                    "name": name,
                    "orientation": spec.get("orientation", "portrait"),
                    "modified": datetime.fromtimestamp(osp.getmtime(json_path)).isoformat(),
                    "png": osp.isfile(osp.join(designs_dir, f"{name}.png")),
                }
            )
    return jsonify({"designs": sorted(designs, key=lambda item: item["name"])})


@designer_api.route("/designs/<name>", methods=["GET"])
def get_design(name: str) -> Response:
    cfg = _pibooth()["cfg"]
    try:
        name, json_path, _ = _design_paths(cfg, name)
    except ValueError as ex:
        abort(400, description=str(ex))
    if not osp.isfile(json_path):
        abort(404, description=f"Design '{name}' not found")
    with open(json_path, encoding="utf-8") as fp:
        spec = json.load(fp)
    return jsonify(spec)


@designer_api.route("/designs", methods=["POST"])
def create_design() -> Response:
    payload = request.get_json(silent=True) or {}
    spec = payload.get("spec")
    assign = bool(payload.get("assign", False))

    try:
        name = _validate_spec(spec)
    except ValueError as ex:
        abort(400, description=str(ex))
    assert isinstance(spec, dict)  # Guaranteed by _validate_spec, guides mypy

    pibooth = _pibooth()
    cfg = pibooth["cfg"]
    assets_dir = cfg.join_path("assets")
    designs_dir = _designs_dir(cfg)

    with pibooth["lock"]:
        try:
            size = _canvas_size(cfg, spec["orientation"])
            image = render_design(spec, assets_dir, size)
        except (OSError, ValueError) as ex:
            abort(400, description=f"Cannot render design: {ex}")

        os.makedirs(designs_dir, exist_ok=True)
        png_path = osp.join(designs_dir, f"{name}.png")
        json_path = osp.join(designs_dir, f"{name}.json")
        image.save(png_path)
        stored_spec = {**spec, "name": name}
        with open(json_path, "w", encoding="utf-8") as fp:
            json.dump(stored_spec, fp, indent=2)

        if assign:
            cfg.set("PICTURE", "overlays", f'"{png_path}"')
            cfg.save()

    LOGGER.info("Design '%s' saved from the web interface", name)
    if assign:
        pibooth["notify"]()
    return jsonify({"name": name, "png_path": png_path})


@designer_api.route("/designs/<name>", methods=["DELETE"])
def delete_design(name: str) -> Response:
    pibooth = _pibooth()
    cfg = pibooth["cfg"]
    try:
        name, json_path, png_path = _design_paths(cfg, name)
    except ValueError as ex:
        abort(400, description=str(ex))
    if not osp.isfile(json_path):
        abort(404, description=f"Design '{name}' not found")

    with pibooth["lock"]:
        current = cfg.get("PICTURE", "overlays").strip('"')
        was_assigned = bool(current) and osp.isfile(png_path) and osp.abspath(current) == osp.abspath(png_path)

        if osp.isfile(json_path):
            os.remove(json_path)
        if osp.isfile(png_path):
            os.remove(png_path)

        if was_assigned:
            cfg.set("PICTURE", "overlays", "")
            cfg.save()

    LOGGER.info("Design '%s' deleted from the web interface", name)
    if was_assigned:
        pibooth["notify"]()
    return jsonify({"ok": True})


@designer_api.route("/fonts/<name>/file", methods=["GET"])
def get_font_file(name: str) -> Response:
    try:
        path = fonts.get_filename(name)
    except ValueError as ex:
        abort(404, description=str(ex))
    response = send_file(path)
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response
