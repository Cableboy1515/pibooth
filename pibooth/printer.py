"""Pibooth printer handling."""

try:
    import cups
    from cups_notify import Subscriber, event
except ImportError:
    cups = None  # CUPS is optional

import os.path as osp
import re
import tempfile
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pibooth.counters import Counters

import pygame
from PIL import Image

from pibooth.pictures import get_picture_factory
from pibooth.utils import LOGGER

PRINTER_TASKS_UPDATED = pygame.USEREVENT + 2

PAPER_FORMATS = {
    "2x6": (2, 6),  # 2x6 pouces - 5x15 cm - 51x152 mm
    "3,5x5": (3.5, 5),  # 3,5x5 pouces - 9x13 cm - 89x127 mm
    "4x6": (4, 6),  # 4x6 pouces - 10x15 cm - 101x152 mm
    "5x7": (5, 7),  # 5x7 pouces - 13x18 cm - 127x178 mm
    "6x8": (6, 8),  # 6x8 pouces - 15x20 cm - 152x203 mm
    "6x9": (6, 9),  # 6x9 pouces - 15x23 cm - 152x229 mm
}

#: IPP print-quality enum values (RFC 8011 §5.4.13 / PWG 5100.13)
QUALITY_LEVELS = {"draft": 3, "normal": 4, "high": 5}

#: A PWG 5101.1 self-describing media name looks like ``class_sizename_WxHunit``,
#: e.g. ``na_index-4x6_4x6in`` or ``om_small-photo_100x148mm``. The trailing
#: ``WxHunit`` chunk is what's parsed here; the ``class`` and ``sizename``
#: parts are informative only. ``custom_min_...`` / ``custom_max_...`` ranges
#: have no fixed size and are intentionally left unparsed (return None).
_PWG_SIZE_RE = re.compile(r"_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(in|mm)$")


def parse_pwg_media(name: str) -> tuple[float, float] | None:
    """Parse a PWG self-describing media name into a portrait-normalized
    ``(width_in, height_in)`` size in inches.

    :param name: PWG media name, e.g. ``"na_index-4x6_4x6in"``

    :return: ``(width_in, height_in)`` with the smaller dimension first,
             or ``None`` if the name cannot be parsed (custom ranges, ...)
    """
    if name.startswith("custom_"):
        # e.g. 'custom_max_8.5x14in': a range bound, not a selectable size
        return None
    match = _PWG_SIZE_RE.search(name)
    if not match:
        return None
    width, height, unit = float(match.group(1)), float(match.group(2)), match.group(3)
    if unit == "mm":
        width /= 25.4
        height /= 25.4
    return (width, height) if width <= height else (height, width)


def match_media(target_inches: tuple[float, float], supported: list[str]) -> str | None:
    """Return the first PWG media name in ``supported`` whose parsed size
    matches ``target_inches`` within 0.08 inch (~2 mm) per dimension.

    :param target_inches: ``(width, height)`` in inches, any orientation
    :param supported: list of PWG media names (as returned by CUPS)

    :return: the matching PWG media name, or ``None`` if nothing matches
    """
    width, height = target_inches
    target = (width, height) if width <= height else (height, width)
    for name in supported:
        parsed = parse_pwg_media(name)
        if parsed is None:
            continue
        if abs(parsed[0] - target[0]) <= 0.08 and abs(parsed[1] - target[1]) <= 0.08:
            return name
    return None


class Printer:
    def __init__(
        self,
        name: str = "default",
        max_pages: int = -1,
        options: Any = None,
        counters: "Counters | None" = None,
    ) -> None:
        self._conn = cups.Connection() if cups else None
        self._notifier = Subscriber(self._conn) if cups else None
        self.name: str | None = None
        self.max_pages = max_pages
        # May come from config as any parsed type, normalized to a dict below
        self.options: Any = options
        self.count = counters
        self._capabilities: dict[str, Any] | None = None
        if not cups:
            LOGGER.warning("No printer found (pycups or pycups-notify not installed)")
            return  # CUPS is not installed
        assert self._conn is not None

        if not name or name.lower() == "default":
            self.name = self._conn.getDefault()
            if not self.name and self._conn.getPrinters():
                self.name = list(self._conn.getPrinters().keys())[0]  # Take first one
        elif name in self._conn.getPrinters():
            self.name = name

        if not self.name:
            if name.lower() == "default":
                LOGGER.warning("No printer configured in CUPS (see http://localhost:631)")
            else:
                LOGGER.warning("No printer named '%s' in CUPS (see http://localhost:631)", name)
        else:
            LOGGER.info("Connected to printer '%s'", self.name)

        if self.options and not isinstance(self.options, dict):
            LOGGER.warning("Invalid printer options '%s', dict is expected", self.options)
            self.options = {}
        elif not self.options:
            self.options = {}

    def _on_event(self, evt: Any) -> None:
        """
        Call for each new printer event.
        """
        LOGGER.info(evt.title)
        pygame.event.post(pygame.event.Event(PRINTER_TASKS_UPDATED, evt=evt))

    def is_installed(self) -> bool:
        """Return True if the CUPS server is available for printing."""
        return cups is not None and self.name is not None

    def is_ready(self) -> bool:
        """Return False if paper/ink counter is reached or printing is disabled."""
        if not self.is_installed():
            return False
        if self.max_pages < 0 or self.count is None:  # No limit
            return True
        return self.count.printed < self.max_pages

    def get_capabilities(self) -> dict[str, Any]:
        """Return the printer's IPP capabilities: supported/default media,
        print quality levels, input trays, make/model and state message.

        Cached on first successful call (a new :class:`Printer` instance is
        created whenever the configuration changes, so there is no need to
        invalidate the cache).

        :return: dict with keys ``media``, ``media_default``, ``quality``,
                 ``quality_default``, ``trays``, ``tray_default``, ``model``,
                 ``state_message``; empty dict if capabilities are unavailable
        """
        if self._capabilities is not None:
            return self._capabilities
        if not cups or not self.name or not self._conn:
            return {}
        try:
            attrs = self._conn.getPrinterAttributes(
                self.name,
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
        except (cups.IPPError, RuntimeError) as ex:
            LOGGER.warning("Cannot get capabilities of printer '%s': %s", self.name, ex)
            return {}

        capabilities = {
            "media": attrs.get("media-supported", []),
            "media_default": attrs.get("media-default"),
            "quality": attrs.get("print-quality-supported", []),
            "quality_default": attrs.get("print-quality-default"),
            "trays": attrs.get("media-source-supported", []),
            "tray_default": attrs.get("media-source-default"),
            "model": attrs.get("printer-make-and-model"),
            "state_message": attrs.get("printer-state-message"),
        }
        self._capabilities = capabilities
        return capabilities

    def print_file(self, filename: str, copies: int = 1, options: dict[str, str] | None = None) -> None:
        """Send a file to the CUPS server to the default printer.

        :param filename: path of the picture file to print
        :param copies: number of copies of the picture to render on the same page
        :param options: extra CUPS job options merged over :attr:`options`
                         for this job only (``self.options`` is left untouched)
        """
        if not self.name or not self._conn:
            raise OSError("No printer found (check config file or CUPS config)")
        if not osp.isfile(filename):
            raise OSError(f"No such file or directory: {filename}")
        if self._notifier and not self._notifier.is_subscribed(self._on_event):
            self._notifier.subscribe(
                self._on_event,
                [
                    event.CUPS_EVT_JOB_COMPLETED,
                    event.CUPS_EVT_JOB_CREATED,
                    event.CUPS_EVT_JOB_STOPPED,
                    event.CUPS_EVT_PRINTER_STATE_CHANGED,
                    event.CUPS_EVT_PRINTER_STOPPED,
                ],
            )

        job_options = {**self.options, **options} if options else self.options

        if copies > 1:
            with tempfile.NamedTemporaryFile(suffix=osp.basename(filename)) as fp:
                picture = Image.open(filename)
                factory = get_picture_factory((picture,) * copies)
                # Don't call setup factory hook here, as the selected parameters
                # are the one necessary to render several pictures on same page.
                factory.set_margin(2)
                factory.save(fp.name)
                self._conn.printFile(self.name, fp.name, osp.basename(filename), job_options)
        else:
            self._conn.printFile(self.name, filename, osp.basename(filename), job_options)
        LOGGER.debug("File '%s' sent to the printer with options %s", filename, job_options)

    def cancel_all_tasks(self) -> None:
        """Cancel all tasks in the queue."""
        if not self.name or not self._conn:
            raise OSError("No printer found (check config file or CUPS config)")
        self._conn.cancelAllJobs(self.name)

    def get_all_tasks(self) -> dict[Any, Any]:
        """Return a dict (indexed by job ID) of dicts representing all tasks
        in the queue.
        """
        if not self.name or not self._conn:
            return {}  # No printer found
        return self._conn.getJobs(my_jobs=True, requested_attributes=["job-id", "job-name", "job-uri", "job-state"])

    def quit(self) -> None:
        """Do cleanup actions."""
        if self._notifier:
            self._notifier.unsubscribe_all()
