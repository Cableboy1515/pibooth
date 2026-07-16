"""REST API for the event profiles (see :py:mod:`pibooth.config.events`)."""

from typing import Any

from flask import Blueprint, Response, abort, current_app, jsonify, request

from pibooth.config.events import EventManager
from pibooth.utils import LOGGER

events_api = Blueprint("events_api", __name__, url_prefix="/api")


def _pibooth() -> dict[str, Any]:
    return current_app.config["PIBOOTH"]


@events_api.route("/events", methods=["GET"])
def list_events() -> Response:
    manager = EventManager(_pibooth()["cfg"])
    return jsonify({"active": manager.active(), "events": manager.list_events()})


@events_api.route("/events", methods=["POST"])
def create_event() -> Response:
    payload = request.get_json(silent=True) or {}
    name = payload.get("name", "")

    pibooth = _pibooth()
    manager = EventManager(pibooth["cfg"])
    with pibooth["lock"]:
        try:
            event = manager.save_event(name)
        except ValueError as ex:
            abort(400, description=str(ex))
        except FileExistsError as ex:
            abort(409, description=str(ex))

    LOGGER.info("Event saved from the web interface")
    return jsonify(event)


@events_api.route("/events/<name>", methods=["PUT"])
def update_event(name: str) -> Response:
    pibooth = _pibooth()
    manager = EventManager(pibooth["cfg"])
    with pibooth["lock"]:
        try:
            event = manager.save_event(name, overwrite=True)
        except ValueError as ex:
            abort(400, description=str(ex))

    LOGGER.info("Event '%s' updated from the web interface", name)
    return jsonify(event)


@events_api.route("/events/<name>/apply", methods=["POST"])
def apply_event(name: str) -> Response:
    pibooth = _pibooth()
    manager = EventManager(pibooth["cfg"])
    with pibooth["lock"]:
        try:
            manager.apply_event(name)
        except ValueError as ex:
            abort(400, description=str(ex))
        except FileNotFoundError as ex:
            abort(404, description=str(ex))

    LOGGER.info("Event '%s' applied from the web interface", name)
    pibooth["notify"]()
    return jsonify({"ok": True})


@events_api.route("/events/<name>", methods=["DELETE"])
def delete_event(name: str) -> Response:
    pibooth = _pibooth()
    manager = EventManager(pibooth["cfg"])
    with pibooth["lock"]:
        try:
            was_active = manager.active() == manager.sanitize(name)
            manager.delete_event(name)
        except ValueError as ex:
            abort(400, description=str(ex))
        except FileNotFoundError as ex:
            abort(404, description=str(ex))

    LOGGER.info("Event '%s' deleted from the web interface", name)
    if was_active:
        pibooth["notify"]()
    return jsonify({"ok": True})
