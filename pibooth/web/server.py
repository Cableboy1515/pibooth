"""Flask application serving the pibooth web configuration interface."""

import contextlib
import glob
import io
import os
import os.path as osp
import socket
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pygame
from flask import Flask, Response, abort, jsonify, request, send_from_directory
from PIL import Image, ImageDraw
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server
from werkzeug.utils import secure_filename

import pibooth
from pibooth import fonts
from pibooth.config.parser import DEFAULT, PiConfigParser
from pibooth.pictures import get_picture_factory
from pibooth.printer import QUALITY_LEVELS, parse_pwg_media
from pibooth.utils import LOGGER
from pibooth.web import CONFIG_CHANGED
from pibooth.web.designer_api import designer_api
from pibooth.web.events_api import events_api
from pibooth.web.templates_api import templates_api
from pibooth.web.uploads_api import uploads_api

if TYPE_CHECKING:
    from pibooth.booth import PiApplication

ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

SECTION_LABELS = {
    "GENERAL": "General",
    "WEB": "Web interface",
    "WINDOW": "Screen",
    "PICTURE": "Picture",
    "CAMERA": "Camera",
    "PRINTER": "Printer",
    "UPLOAD": "Photo upload",
    "CONTROLS": "Buttons & GPIO",
}

#: Options holding the path to an image (rendered with an image picker)
IMAGE_OPTIONS = {("PICTURE", "overlays")}

#: Options holding either a RGB color or the path to an image
COLOR_OR_IMAGE_OPTIONS = {("WINDOW", "background"), ("PICTURE", "backgrounds")}

#: Options holding a font name (rendered with the available fonts list)
FONT_OPTIONS = {("WINDOW", "font"), ("PICTURE", "text_fonts")}

#: Placeholder colors used for the sample captures of the preview
SAMPLE_COLORS = [(154, 188, 220), (222, 184, 135), (176, 200, 164), (216, 172, 188)]


def _option_kind(section: str, name: str, default: Any, choices: Any) -> str:
    """Return the widget kind used by the web frontend for an option."""
    key = (section, name)
    if key == ("PICTURE", "captures"):
        return "captures"
    if key == ("PRINTER", "printer_name"):
        return "printer"
    if key == ("PRINTER", "paper_size"):
        return "paper"
    if key == ("PRINTER", "tray"):
        return "tray"
    if key in IMAGE_OPTIONS:
        return "image"
    if key in COLOR_OR_IMAGE_OPTIONS:
        return "color_or_image"
    if key in FONT_OPTIONS:
        return "font"
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, tuple) and len(default) == 3 and all(isinstance(v, int) for v in default):
        return "color"
    if isinstance(choices, (list, tuple)) and choices and all(isinstance(c, str) for c in choices):
        return "choice"
    if isinstance(default, int):
        return "int"
    if isinstance(default, float):
        return "float"
    return "text"


def _option_label(name: str, menu_name: str | None) -> str:
    """Return a friendly label for an option."""
    if menu_name:
        return menu_name
    return name.replace("_", " ").capitalize()


def _split_help(description: str) -> tuple[str, str | None]:
    """Split the option description into help text and the plugin requiring it."""
    plugin = None
    lines = []
    for line in description.splitlines():
        if line.startswith("# Required by "):
            plugin = line[len("# Required by ") :].strip("'\" ").removesuffix(" plugin").strip("'\"")
        else:
            lines.append(line.lstrip("# "))
    return " ".join(lines), plugin


def _resolve_cups_printer_name(conn: Any, name: str) -> str | None:
    """Return the actual CUPS printer name for ``name``, mirroring the
    resolution logic of :py:class:`pibooth.printer.Printer`: ``"default"``
    (or empty) resolves to the CUPS default printer, or the first configured
    printer if there is no default; any other value must match an existing
    CUPS printer name.
    """
    if not name or name.lower() == "default":
        resolved = conn.getDefault()
        if not resolved and conn.getPrinters():
            resolved = next(iter(conn.getPrinters()))
        return resolved
    if name in conn.getPrinters():
        return name
    return None


def _format_media_label(name: str) -> str:
    """Return a friendly label for a PWG media name, e.g. ``4x6"``, falling
    back to the raw name when its size cannot be parsed.
    """
    parsed = parse_pwg_media(name)
    if parsed is None:
        return name

    def _fmt(value: float) -> str:
        return f"{value:.2f}".rstrip("0").rstrip(".")

    return f'{_fmt(parsed[0])}x{_fmt(parsed[1])}"'


def get_local_ip() -> str:
    """Return the LAN IP address of this machine (best effort)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is actually sent, the OS only resolves the route
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def build_sample_captures(cfg: PiConfigParser, count: int) -> list[Image.Image]:
    """Return sample captures for the preview: the latest real captures found
    in the save directory if any, generated placeholder pictures otherwise.
    """
    for savedir in cfg.gettuple("GENERAL", "directory", "path"):
        rawroot = osp.join(savedir, "raw")
        if not osp.isdir(rawroot):
            continue
        for session in sorted(os.listdir(rawroot), reverse=True):
            files = sorted(glob.glob(osp.join(rawroot, session, "*.jpg")))
            if len(files) >= count:
                try:
                    return [Image.open(name) for name in files[:count]]
                except OSError:
                    continue

    # No real captures available, generate placeholders
    resolution = cfg.gettyped("CAMERA", "resolution")
    if not (isinstance(resolution, (tuple, list)) and len(resolution) == 2):
        resolution = (1934, 2464)
    ratio = resolution[1] / resolution[0]
    size = (600, int(600 * ratio)) if ratio else (600, 450)

    captures = []
    for i in range(count):
        image = Image.new("RGB", size, SAMPLE_COLORS[i % len(SAMPLE_COLORS)])
        draw = ImageDraw.Draw(image)
        text = str(i + 1)
        font = fonts.get_pil_font(text, fonts.get_filename("Amatic-Bold"), size[0] * 0.4, size[1] * 0.4)
        bbox = draw.textbbox((0, 0), text, font=font)
        position = ((size[0] - bbox[2] - bbox[0]) // 2, (size[1] - bbox[3] - bbox[1]) // 2)
        draw.text(position, text, fill=(255, 255, 255), font=font)
        captures.append(image)
    return captures


def build_preview_picture(
    cfg: PiConfigParser, plugin_manager: Any, application: "PiApplication | None", variant: int
) -> Image.Image:
    """Build the final picture as it would be rendered for the given
    captures-number variant, using sample captures.
    """
    choices = cfg.gettuple("PICTURE", "captures", int)
    variant = max(0, min(variant, len(choices) - 1))

    captures = build_sample_captures(cfg, choices[variant])

    # The core picture plugin formats footer texts with these variables
    picture_plugin = plugin_manager.get_plugin("pibooth-core:picture")
    if picture_plugin is not None:
        picture_plugin.texts_vars["date"] = datetime.now()
        if application is not None:
            picture_plugin.texts_vars["count"] = application.count

    default_factory = get_picture_factory(captures, cfg.get("PICTURE", "orientation"), force_pil=True, dpi=200)
    factory = plugin_manager.hook.pibooth_setup_picture_factory(cfg=cfg, opt_index=variant, factory=default_factory)
    return factory.build()


def create_app(cfg: PiConfigParser, plugin_manager: Any, application: "PiApplication | None" = None) -> Flask:
    """Create the Flask application exposing the configuration REST API
    and the static single-page frontend.
    """
    app = Flask(__name__, static_folder="static", static_url_path="")
    lock = threading.Lock()
    assets_dir = cfg.join_path("assets")

    def notify_config_changed() -> None:
        # pygame may not be initialized (e.g. unit tests)
        with contextlib.suppress(pygame.error):
            pygame.event.post(pygame.event.Event(CONFIG_CHANGED))

    app.config["PIBOOTH"] = {
        "cfg": cfg,
        "plugin_manager": plugin_manager,
        "application": application,
        "lock": lock,
        "notify": notify_config_changed,
    }

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException) -> Response:
        # The frontend expects the abort() description as JSON, not HTML
        response = jsonify({"description": error.description})
        response.status_code = error.code or 500
        return response

    @app.route("/")
    def index() -> Response:
        assert app.static_folder is not None
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/api/config", methods=["GET"])
    def get_config() -> Response:
        sections = []
        for section, options in DEFAULT.items():
            serialized = []
            for name, (default, description, menu_name, choices) in options.items():
                help_text, plugin = _split_help(description)
                serialized.append(
                    {
                        "name": name,
                        "label": _option_label(name, menu_name),
                        "help": help_text,
                        "plugin": plugin,
                        "kind": _option_kind(section, name, default, choices),
                        "choices": [str(c) for c in choices] if isinstance(choices, (list, tuple)) else None,
                        # Free-text options edited with quotes by the pygame menu
                        "quoted": isinstance(choices, str),
                        "value": cfg.get(section, name).strip('"'),
                        "default": str(default),
                    }
                )
            sections.append(
                {
                    "name": section,
                    "label": SECTION_LABELS.get(section, section.replace("_", " ").capitalize()),
                    "options": serialized,
                }
            )
        return jsonify({"version": pibooth.__version__, "sections": sections})

    @app.route("/api/config", methods=["PUT"])
    def put_config() -> Response:
        payload = request.get_json(silent=True) or {}
        values = payload.get("values", {})
        if not isinstance(values, dict) or not values:
            abort(400, description="No values provided")

        with lock:
            for section, options in values.items():
                if section not in DEFAULT or not isinstance(options, dict):
                    abort(400, description=f"Unknown section '{section}'")
                for name, value in options.items():
                    if name not in DEFAULT[section]:
                        abort(400, description=f"Unknown option '{name}' in section '{section}'")
                    cfg.set(section, name, str(value))
            cfg.save()

        LOGGER.info("Configuration updated from the web interface")
        notify_config_changed()
        return jsonify({"ok": True})

    @app.route("/api/status", methods=["GET"])
    def get_status() -> Response:
        camera_name = None
        printer_name = None
        printer_connected = False
        if application is not None:
            camera_name = type(application.camera).__name__
            printer_connected = application.printer.is_installed()
            printer_name = application.printer.name
        return jsonify(
            {
                "version": pibooth.__version__,
                "camera": {"detected": camera_name, "configured": cfg.get("CAMERA", "type")},
                "printer": {"connected": printer_connected, "name": printer_name},
            }
        )

    @app.route("/api/printers", methods=["GET"])
    def get_printers() -> Response:
        try:
            import cups
        except ImportError:
            return jsonify({"available": False, "printers": [], "default": None})
        try:
            conn = cups.Connection()
            return jsonify({"available": True, "printers": sorted(conn.getPrinters()), "default": conn.getDefault()})
        except RuntimeError as ex:
            LOGGER.warning("Cannot connect to CUPS server: %s", ex)
            return jsonify({"available": False, "printers": [], "default": None})

    @app.route("/api/printers/<name>/capabilities", methods=["GET"])
    def get_printer_capabilities(name: str) -> Response:
        empty: dict[str, Any] = {
            "available": False,
            "model": None,
            "state_message": None,
            "media": [],
            "media_default": None,
            "quality": [],
            "quality_default": None,
            "trays": [],
            "tray_default": None,
        }
        try:
            import cups
        except ImportError:
            return jsonify(empty)
        try:
            conn = cups.Connection()
            resolved = _resolve_cups_printer_name(conn, name)
            if not resolved:
                return jsonify(empty)
            attrs = conn.getPrinterAttributes(
                resolved,
                requested_attributes=[
                    "media-supported",
                    "media-default",
                    "print-quality-supported",
                    "print-quality-default",
                    "media-source-supported",
                    "media-source-default",
                    "printer-make-and-model",
                    "printer-state-message",
                ],
            )
        except (RuntimeError, cups.IPPError) as ex:
            LOGGER.warning("Cannot get capabilities of printer '%s': %s", name, ex)
            return jsonify(empty)

        media_supported = attrs.get("media-supported") or []
        quality_supported = attrs.get("print-quality-supported") or []
        quality_reverse = {value: level for level, value in QUALITY_LEVELS.items()}

        return jsonify(
            {
                "available": True,
                "model": attrs.get("printer-make-and-model"),
                "state_message": attrs.get("printer-state-message"),
                "media": [
                    {
                        "name": media_name,
                        "inches": list(parsed) if (parsed := parse_pwg_media(media_name)) else None,
                        "label": _format_media_label(media_name),
                    }
                    for media_name in media_supported
                ],
                "media_default": attrs.get("media-default"),
                "quality": [
                    {"value": value, "label": quality_reverse[value]}
                    for value in quality_supported
                    if value in quality_reverse
                ],
                "quality_default": attrs.get("print-quality-default"),
                "trays": attrs.get("media-source-supported") or [],
                "tray_default": attrs.get("media-source-default"),
            }
        )

    @app.route("/api/fonts", methods=["GET"])
    def get_fonts() -> Response:
        try:
            return jsonify({"fonts": fonts.get_available_fonts()})
        except pygame.error:
            return jsonify({"fonts": []})

    @app.route("/api/assets", methods=["GET"])
    def list_assets() -> Response:
        names = []
        if osp.isdir(assets_dir):
            names = sorted(
                name for name in os.listdir(assets_dir) if osp.splitext(name)[1].lower() in ALLOWED_IMAGE_EXT
            )
        return jsonify({"assets": [{"name": name, "path": osp.join(assets_dir, name)} for name in names]})

    @app.route("/api/assets", methods=["POST"])
    def upload_asset() -> Response:
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            abort(400, description="No file provided")
        filename = secure_filename(uploaded.filename)
        if osp.splitext(filename)[1].lower() not in ALLOWED_IMAGE_EXT:
            abort(400, description=f"Unsupported file type, expected one of {sorted(ALLOWED_IMAGE_EXT)}")
        os.makedirs(assets_dir, exist_ok=True)
        path = osp.join(assets_dir, filename)
        uploaded.save(path)
        LOGGER.info("New asset uploaded in '%s'", path)
        return jsonify({"name": filename, "path": path})

    @app.route("/api/assets/<name>", methods=["GET"])
    def get_asset(name: str) -> Response:
        return send_from_directory(assets_dir, secure_filename(name))

    @app.route("/api/assets/<name>", methods=["DELETE"])
    def delete_asset(name: str) -> Response:
        path = osp.join(assets_dir, secure_filename(name))
        if not osp.isfile(path):
            abort(404)
        os.remove(path)
        return jsonify({"ok": True})

    @app.route("/api/preview", methods=["GET"])
    def get_preview() -> Response:
        try:
            variant = int(request.args.get("variant", 0))
        except ValueError:
            variant = 0
        no_overlay = request.args.get("overlay") == "0"
        try:
            with lock:
                previous_overlay = cfg.get("PICTURE", "overlays")
                try:
                    if no_overlay:
                        # In-memory only: give the designer a plain backdrop
                        # without persisting the change to disk.
                        cfg.set("PICTURE", "overlays", "")
                    picture = build_preview_picture(cfg, plugin_manager, application, variant)
                finally:
                    if no_overlay:
                        cfg.set("PICTURE", "overlays", previous_overlay)
        except Exception as ex:  # Rendering shall never crash the booth
            LOGGER.warning("Cannot build preview picture: %s", ex)
            abort(500, description=f"Cannot build preview picture: {ex}")
        picture.thumbnail((1200, 1200))
        buffer = io.BytesIO()
        picture.save(buffer, format="PNG")
        buffer.seek(0)
        return Response(buffer.read(), mimetype="image/png")

    app.register_blueprint(events_api)
    app.register_blueprint(designer_api)
    app.register_blueprint(templates_api)
    app.register_blueprint(uploads_api)

    return app


class WebServer:
    """Threaded HTTP server hosting the web configuration interface.

    The server runs in a daemon thread so it never prevents the main
    pygame loop from exiting.
    """

    def __init__(
        self,
        cfg: PiConfigParser,
        plugin_manager: Any,
        application: "PiApplication | None" = None,
        host: str = "0.0.0.0",
        port: int = 8080,
    ) -> None:
        self.port = port
        self.app = create_app(cfg, plugin_manager, application)
        self._server = make_server(host, port, self.app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, name="pibooth-web", daemon=True)

    @property
    def url(self) -> str:
        """Return the URL at which the interface is reachable on the LAN."""
        return f"http://{get_local_ip()}:{self.port}"

    def start(self) -> None:
        """Start serving in a background thread."""
        self._thread.start()
        LOGGER.info("Web configuration interface started on %s", self.url)

    def stop(self) -> None:
        """Shutdown the server and wait for the thread to finish."""
        self._server.shutdown()
        self._thread.join(timeout=5)
