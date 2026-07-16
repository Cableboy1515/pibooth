from typing import TYPE_CHECKING, Any

import pibooth
from pibooth.utils import LOGGER

if TYPE_CHECKING:
    from pibooth.booth import PiApplication
    from pibooth.config.parser import PiConfigParser


class WebPlugin:
    """Plugin to manage the web configuration interface."""

    name = "pibooth-core:web"

    def __init__(self, plugin_manager: Any) -> None:
        self._pm = plugin_manager
        self._server: Any = None

    @pibooth.hookimpl
    def pibooth_startup(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        if not cfg.getboolean("WEB", "web_enabled"):
            return
        try:
            from pibooth.web.server import WebServer
        except ImportError:
            LOGGER.warning(
                "Web settings are enabled in the configuration but 'flask' is not installed, run 'pip install flask'"
            )
            return
        try:
            self._server = WebServer(cfg, self._pm, app, port=cfg.getint("WEB", "web_port"))
            self._server.start()
        except OSError as ex:
            LOGGER.warning("Cannot start the web configuration interface: %s", ex)
            self._server = None

    @pibooth.hookimpl
    def pibooth_cleanup(self, app: "PiApplication") -> None:
        if self._server is not None:
            self._server.stop()
            self._server = None
