from typing import TYPE_CHECKING, Any

import pygame

import pibooth
from pibooth.pictures import AUTO, PORTRAIT
from pibooth.pictures.template import load_template
from pibooth.printer import PAPER_FORMATS, QUALITY_LEVELS, match_media
from pibooth.utils import LOGGER

if TYPE_CHECKING:
    from pibooth.booth import PiApplication
    from pibooth.config.parser import PiConfigParser

#: Fallback picture size (inches) when no template is active
_DEFAULT_PAPER_INCHES = (4, 6)


def _target_inches_from_template(cfg: "PiConfigParser", app: "PiApplication") -> tuple[float, float]:
    """Return the ``(width, height)`` inches of the page that would be used
    for the active picture layout template, falling back to 4x6 when no
    template is configured or its page geometry cannot be resolved.
    """
    template_path = cfg.getpath("PICTURE", "template")
    if not template_path:
        return _DEFAULT_PAPER_INCHES

    captures = app.capture_nbr or 1
    try:
        template = load_template(template_path)
        orientation = cfg.get("PICTURE", "orientation")
        if orientation == AUTO:
            pages = [page for page in template.pages if page.captures == captures]
            available = {page.orientation for page in pages}
            orientation = PORTRAIT if PORTRAIT in available else next(iter(available), PORTRAIT)
        page = template.get_page(captures, orientation)
    except (ValueError, OSError) as ex:
        LOGGER.warning("Cannot resolve picture template '%s' for print paper size: %s", template_path, ex)
        return _DEFAULT_PAPER_INCHES

    if page.paper in PAPER_FORMATS:
        return PAPER_FORMATS[page.paper]
    return page.size[0] / page.dpi, page.size[1] / page.dpi


def build_job_options(cfg: "PiConfigParser", app: "PiApplication") -> dict[str, str]:
    """Return the CUPS job options (``media``, ``print-quality``,
    ``media-source``) derived from the ``[PRINTER]`` configuration and the
    active picture layout.

    :param cfg: application configuration
    :param app: running application (used to read the active printer
                capabilities and the current captures count)
    """
    options: dict[str, str] = {}

    paper_size = cfg.get("PRINTER", "paper_size")
    if paper_size != "default":
        if paper_size == "auto":
            target = _target_inches_from_template(cfg, app)
        elif paper_size in PAPER_FORMATS:
            target = PAPER_FORMATS[paper_size]
        else:
            LOGGER.warning("Unknown printer paper size '%s', ignored", paper_size)
            target = None

        if target is not None:
            caps = app.printer.get_capabilities()
            media = match_media(target, caps.get("media", []))
            if media:
                options["media"] = media
            else:
                LOGGER.warning("No printer media matches the requested paper size %s", target)

    quality = cfg.get("PRINTER", "quality")
    if quality != "default":
        level = QUALITY_LEVELS.get(quality)
        if level is None:
            LOGGER.warning("Unknown printer quality '%s', ignored", quality)
        else:
            options["print-quality"] = str(level)

    tray = cfg.get("PRINTER", "tray")
    if tray and tray != "default":
        caps = app.printer.get_capabilities()
        trays = caps.get("trays") or []
        if not trays or tray in trays:
            options["media-source"] = tray
        else:
            LOGGER.warning("Printer tray '%s' not supported by the connected printer, ignored", tray)

    return options


class PrinterPlugin:
    """Plugin to manage the printer."""

    name = "pibooth-core:printer"

    def __init__(self, plugin_manager: Any) -> None:
        self._pm = plugin_manager

    def print_picture(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        LOGGER.info("Send final picture to printer")
        assert app.previous_picture_file is not None
        app.printer.print_file(
            app.previous_picture_file,
            cfg.getint("PRINTER", "pictures_per_page"),
            options=build_job_options(cfg, app),
        )
        app.count.printed += 1
        app.count.remaining_duplicates -= 1

    @pibooth.hookimpl
    def pibooth_cleanup(self, app: "PiApplication") -> None:
        app.printer.quit()

    @pibooth.hookimpl
    def state_failsafe_enter(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        """Reset variables set in this plugin."""
        app.count.remaining_duplicates = cfg.getint("PRINTER", "max_duplicates")

    @pibooth.hookimpl
    def state_wait_do(self, cfg: "PiConfigParser", app: "PiApplication", events: list[pygame.event.Event]) -> None:
        if app.find_print_event(events) and app.previous_picture_file and app.printer.is_installed():
            if app.count.remaining_duplicates <= 0:
                LOGGER.warning(
                    "Too many duplicates sent to the printer (%s max)", cfg.getint("PRINTER", "max_duplicates")
                )
                return

            elif not app.printer.is_ready():
                LOGGER.warning(
                    "Maximum number of printed pages reached (%s/%s max)",
                    app.count.printed,
                    cfg.getint("PRINTER", "max_pages"),
                )
                return

            self.print_picture(cfg, app)

    @pibooth.hookimpl
    def state_processing_enter(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        app.count.remaining_duplicates = cfg.getint("PRINTER", "max_duplicates")

    @pibooth.hookimpl
    def state_processing_do(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        if app.previous_picture_file and app.printer.is_ready():
            number = cfg.gettyped("PRINTER", "auto_print")
            if number == "max":
                number = cfg.getint("PRINTER", "max_duplicates")
            for _i in range(number):
                if app.count.remaining_duplicates > 0:
                    self.print_picture(cfg, app)

    @pibooth.hookimpl
    def state_print_do(self, cfg: "PiConfigParser", app: "PiApplication", events: list[pygame.event.Event]) -> None:
        if app.find_print_event(events) and app.previous_picture_file:
            self.print_picture(cfg, app)
