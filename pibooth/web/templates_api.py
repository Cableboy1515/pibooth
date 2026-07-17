"""REST API for picture layout templates (see :py:mod:`pibooth.pictures.template`)."""

import json
import os
import os.path as osp
import string
import tempfile
from datetime import datetime
from io import BytesIO
from typing import Any

from flask import Blueprint, Response, abort, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from pibooth.pictures import AUTO, LANDSCAPE, PORTRAIT
from pibooth.pictures.template import (
    Template,
    TemplatePage,
    load_template,
    parse_mxgraph,
    render_layout_guide,
    render_layout_guide_svg,
    template_from_dict,
)
from pibooth.utils import LOGGER

templates_api = Blueprint("templates_api", __name__, url_prefix="/api")

#: Characters allowed in a sanitized template name (same rules as events/designs)
_ALLOWED_NAME_CHARS = string.ascii_letters + string.digits + " -_"

#: (width, height) of the default 4x6 inch page at 300dpi, in portrait
_DEFAULT_PAGE_SIZE = (4 * 300, 6 * 300)


def _pibooth() -> dict[str, Any]:
    return current_app.config["PIBOOTH"]


def _sanitize_name(name: str) -> str:
    """Return a filesystem-safe template name (letters, digits, space, dash,
    underscore, stripped and truncated to 60 characters).
    """
    cleaned = "".join(char for char in name if char in _ALLOWED_NAME_CHARS).strip()
    cleaned = cleaned[:60].strip()
    if not cleaned:
        raise ValueError("Template name is empty once sanitized")
    return cleaned


def _templates_dir(cfg: Any) -> str:
    return str(cfg.join_path("templates"))


def _template_paths(cfg: Any, name: str) -> tuple[str, str]:
    """Return (sanitized name, json path) for a template name."""
    name = _sanitize_name(name)
    return name, osp.join(_templates_dir(cfg), f"{name}.json")


def _active_name(cfg: Any) -> str:
    path = cfg.getpath("PICTURE", "template")
    return osp.splitext(osp.basename(path))[0] if path else ""


def load_template_by_name(cfg: Any, name: str) -> Template:
    """Load a saved template by its (sanitized) name.

    :raises ValueError: if the name is invalid once sanitized, no such
                         template exists, or the template file cannot be
                         parsed
    """
    name, json_path = _template_paths(cfg, name)
    if not osp.isfile(json_path):
        raise ValueError(f"Template '{name}' not found")
    return load_template(json_path)


def get_final_picture_size(cfg: Any, variant: int) -> tuple[int, int, str]:
    """Return the ``(width, height, source)`` of the final picture that would
    be produced for the given ``[PICTURE][captures]`` variant.

    If a template is assigned to ``[PICTURE][template]`` and has a page for
    the resolved captures count, that page's size is used
    (``source == "template"``). Otherwise a 4x6 inch page at 300dpi is used,
    honoring ``[PICTURE][orientation]`` (``source == "default"``).

    :param cfg: application configuration
    :param variant: index in the ``[PICTURE][captures]`` tuple
    """
    choices = cfg.gettuple("PICTURE", "captures", int)
    variant = max(0, min(variant, len(choices) - 1))
    captures_count = choices[variant]

    cfg_orientation = cfg.get("PICTURE", "orientation")

    template_path = cfg.getpath("PICTURE", "template")
    if template_path:
        template: Template | None
        try:
            template = load_template(template_path)
        except (ValueError, OSError) as ex:
            LOGGER.warning("Cannot load picture template '%s' for geometry: %s", template_path, ex)
            template = None

        if template is not None:
            pages = [page for page in template.pages if page.captures == captures_count]
            if pages:
                available = {page.orientation for page in pages}
                if cfg_orientation == AUTO:
                    page_orientation = PORTRAIT if PORTRAIT in available else next(iter(available))
                else:
                    page_orientation = cfg_orientation

                try:
                    page = template.get_page(captures_count, page_orientation)
                except ValueError:
                    page = None
                if page is not None:
                    return page.size[0], page.size[1], "template"

    resolved = PORTRAIT if cfg_orientation == AUTO else cfg_orientation
    width, height = _DEFAULT_PAGE_SIZE
    if resolved == LANDSCAPE:
        width, height = height, width
    return width, height, "default"


@templates_api.route("/templates", methods=["GET"])
def list_templates() -> Response:
    cfg = _pibooth()["cfg"]
    templates_dir = _templates_dir(cfg)
    templates = []
    if osp.isdir(templates_dir):
        for filename in os.listdir(templates_dir):
            if not filename.endswith(".json"):
                continue
            json_path = osp.join(templates_dir, filename)
            try:
                with open(json_path, encoding="utf-8") as fp:
                    data = json.load(fp)
                template = template_from_dict(data)
            except (OSError, ValueError) as ex:
                LOGGER.warning("Ignoring invalid template file '%s': %s", json_path, ex)
                continue
            templates.append(
                {
                    "name": template.name,
                    "modified": datetime.fromtimestamp(osp.getmtime(json_path)).isoformat(),
                    "pages": [
                        {
                            "captures": page.captures,
                            "orientation": page.orientation,
                            "size": list(page.size),
                            "paper": page.paper,
                        }
                        for page in template.pages
                    ],
                }
            )
    return jsonify({"active": _active_name(cfg), "templates": sorted(templates, key=lambda item: item["name"])})


@templates_api.route("/templates/<name>", methods=["GET"])
def get_template(name: str) -> Response:
    cfg = _pibooth()["cfg"]
    try:
        name, json_path = _template_paths(cfg, name)
    except ValueError as ex:
        abort(400, description=str(ex))
    if not osp.isfile(json_path):
        abort(404, description=f"Template '{name}' not found")
    with open(json_path, encoding="utf-8") as fp:
        data = json.load(fp)
    return jsonify(data)


@templates_api.route("/templates", methods=["POST"])
def create_template() -> Response:
    payload = request.get_json(silent=True) or {}
    raw_template = payload.get("template")
    assign = bool(payload.get("assign", False))

    try:
        template = template_from_dict(raw_template)
        name = _sanitize_name(template.name)
    except ValueError as ex:
        abort(400, description=str(ex))

    pibooth = _pibooth()
    cfg = pibooth["cfg"]
    templates_dir = _templates_dir(cfg)

    with pibooth["lock"]:
        os.makedirs(templates_dir, exist_ok=True)
        json_path = osp.join(templates_dir, f"{name}.json")
        canonical = template.to_dict()
        canonical["name"] = name
        with open(json_path, "w", encoding="utf-8") as fp:
            json.dump(canonical, fp, indent=2)

        if assign:
            # A single plain path (not a "list of quoted paths" option), and
            # read back with cfg.getpath() which does not strip quotes.
            cfg.set("PICTURE", "template", json_path)
            cfg.save()

    LOGGER.info("Template '%s' saved from the web interface", name)
    if assign:
        pibooth["notify"]()
    return jsonify({"name": name, "path": json_path})


@templates_api.route("/templates/import", methods=["POST"])
def import_template() -> Response:
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        abort(400, description="No file provided")

    stem = osp.splitext(secure_filename(uploaded.filename))[0]
    try:
        name = _sanitize_name(stem or "template")
    except ValueError as ex:
        abort(400, description=str(ex))

    pibooth = _pibooth()
    cfg = pibooth["cfg"]
    assets_dir = cfg.join_path("assets")
    templates_dir = _templates_dir(cfg)

    with pibooth["lock"]:
        os.makedirs(templates_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(suffix=".xml")
        os.close(fd)
        try:
            uploaded.save(tmp_path)
            try:
                data = parse_mxgraph(tmp_path, assets_dir)
            except ValueError as ex:
                abort(400, description=str(ex))
        finally:
            os.remove(tmp_path)

        data["name"] = name
        try:
            template = template_from_dict(data)
        except ValueError as ex:
            abort(400, description=str(ex))

        canonical = template.to_dict()
        json_path = osp.join(templates_dir, f"{name}.json")
        with open(json_path, "w", encoding="utf-8") as fp:
            json.dump(canonical, fp, indent=2)

    LOGGER.info("Template '%s' imported from the web interface", name)
    return jsonify(canonical)


@templates_api.route("/templates/<name>", methods=["DELETE"])
def delete_template(name: str) -> Response:
    pibooth = _pibooth()
    cfg = pibooth["cfg"]
    try:
        name, json_path = _template_paths(cfg, name)
    except ValueError as ex:
        abort(400, description=str(ex))
    if not osp.isfile(json_path):
        abort(404, description=f"Template '{name}' not found")

    with pibooth["lock"]:
        current = cfg.getpath("PICTURE", "template")
        was_assigned = bool(current) and osp.abspath(current) == osp.abspath(json_path)

        os.remove(json_path)

        if was_assigned:
            cfg.set("PICTURE", "template", "")
            cfg.save()

    LOGGER.info("Template '%s' deleted from the web interface", name)
    if was_assigned:
        pibooth["notify"]()
    return jsonify({"ok": True})


@templates_api.route("/geometry", methods=["GET"])
def get_geometry() -> Response:
    cfg = _pibooth()["cfg"]
    try:
        variant = int(request.args.get("variant", 0))
    except ValueError:
        variant = 0
    width, height, source = get_final_picture_size(cfg, variant)
    orientation = PORTRAIT if width < height else LANDSCAPE
    return jsonify({"width": width, "height": height, "orientation": orientation, "source": source})


def _guide_page(name: str) -> tuple[str, TemplatePage]:
    """Resolve (sanitized name, page) for a guide export request, aborting
    with the appropriate status code (404 unknown template, 400 bad request
    params or unknown page) on failure.
    """
    cfg = _pibooth()["cfg"]
    try:
        name, json_path = _template_paths(cfg, name)
    except ValueError as ex:
        abort(400, description=str(ex))
    if not osp.isfile(json_path):
        abort(404, description=f"Template '{name}' not found")

    try:
        template = load_template(json_path)
    except ValueError as ex:
        abort(400, description=str(ex))

    try:
        captures = int(request.args.get("captures", ""))
    except ValueError:
        abort(400, description="'captures' query parameter must be an integer")

    orientation = request.args.get("orientation", "")
    if orientation not in (PORTRAIT, LANDSCAPE):
        abort(400, description="'orientation' query parameter must be 'portrait' or 'landscape'")

    try:
        page = template.get_page(captures, orientation)
    except ValueError:
        available = ", ".join(f"{p.captures} captures/{p.orientation}" for p in template.pages)
        abort(
            400,
            description=(
                f"No page for {captures} captures in '{orientation}' orientation. Available pages: {available}"
            ),
        )
    return name, page


@templates_api.route("/templates/<name>/guide.svg", methods=["GET"])
def get_template_guide_svg(name: str) -> Response:
    name, page = _guide_page(name)
    svg = render_layout_guide_svg(page)
    filename = f"{name}-{page.captures}-{page.orientation}-guide.svg"
    response = Response(svg, mimetype="image/svg+xml")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@templates_api.route("/templates/<name>/guide.png", methods=["GET"])
def get_template_guide_png(name: str) -> Response:
    name, page = _guide_page(name)
    image = render_layout_guide(page)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    filename = f"{name}-{page.captures}-{page.orientation}-guide.png"
    return send_file(buffer, mimetype="image/png", as_attachment=True, download_name=filename)
