"""REST API for the background photo upload queue (see
:py:mod:`pibooth.upload` and :py:mod:`pibooth.plugins.upload_plugin`).
"""

import json
from typing import Any

from flask import Blueprint, Response, abort, current_app, jsonify, request

from pibooth.upload import create_backend
from pibooth.utils import LOGGER

uploads_api = Blueprint("uploads_api", __name__, url_prefix="/api")

#: Presence of any of these top-level keys identifies a Google OAuth client secret file
_CLIENT_SECRET_KEYS = {"installed", "web"}
#: These keys must all be present to identify an authorized user token file
_TOKEN_KEYS = {"refresh_token", "client_id"}


def _pibooth() -> dict[str, Any]:
    return current_app.config["PIBOOTH"]


def _manager() -> Any:
    """Return the upload manager exposed by the upload plugin, if any."""
    application = _pibooth()["application"]
    return getattr(application, "upload_manager", None)


def _require_manager() -> Any:
    manager = _manager()
    if manager is None:
        abort(503, description="Upload manager not running")
    return manager


@uploads_api.route("/uploads", methods=["GET"])
def get_uploads() -> Response:
    manager = _require_manager()
    cfg = _pibooth()["cfg"]
    return jsonify({"backend": cfg.get("UPLOAD", "upload_backend").strip('"'), "entries": manager.entries()})


@uploads_api.route("/uploads/retry", methods=["POST"])
def retry_uploads() -> Response:
    manager = _require_manager()
    manager.retry_failed()
    return jsonify({"ok": True})


@uploads_api.route("/uploads/test", methods=["POST"])
def test_upload_backend() -> Response:
    # Works even without a running manager: only the configuration is needed
    cfg = _pibooth()["cfg"]
    backend = create_backend(cfg)
    if backend is None:
        abort(400, description="Upload service is not configured (see the Upload settings section)")
    try:
        message = backend.test()
    except Exception as ex:
        abort(400, description=str(ex))
    return jsonify({"ok": True, "message": message})


@uploads_api.route("/uploads/google/credentials", methods=["POST"])
def upload_google_credentials() -> Response:
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        abort(400, description="No file provided")

    try:
        payload = json.load(uploaded.stream)
    except ValueError:
        abort(400, description="Not a valid JSON file")
    if not isinstance(payload, dict):
        abort(400, description="Not a valid JSON file")

    cfg = _pibooth()["cfg"]
    if _CLIENT_SECRET_KEYS & payload.keys():
        target = cfg.join_path("google_client_secret.json")
        kind = "client secret"
    elif payload.keys() >= _TOKEN_KEYS:
        target = cfg.join_path("google_token.json")
        kind = "token"
    else:
        abort(400, description="Unrecognized JSON file: expected a Google OAuth client secret or an authorized token")

    with open(target, "w", encoding="utf-8") as fp:
        json.dump(payload, fp)
    LOGGER.info("Google Photos %s file saved in '%s'", kind, target)  # never log the file content
    return jsonify({"ok": True, "kind": kind})
