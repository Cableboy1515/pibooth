"""Event profiles: reusable snapshots of the look-and-feel configuration.

An event profile is a folder ``<configdir>/events/<name>/`` holding an
``event.cfg`` INI snapshot of :data:`EVENT_OPTIONS` plus copies of the image
files those options reference. Applying a profile writes its values back
into the live configuration (one-way apply, there is no layering).
"""

import configparser
import os
import os.path as osp
import shutil
import string
from datetime import datetime
from typing import Any

from pibooth.config.parser import DEFAULT, PiConfigParser
from pibooth.utils import LOGGER

#: (section, option) pairs snapshotted into an event profile
EVENT_OPTIONS: list[tuple[str, str]] = [
    ("PICTURE", "orientation"),
    ("PICTURE", "captures"),
    ("PICTURE", "captures_effects"),
    ("PICTURE", "captures_cropping"),
    ("PICTURE", "margin_thick"),
    ("PICTURE", "footer_text1"),
    ("PICTURE", "footer_text2"),
    ("PICTURE", "text_colors"),
    ("PICTURE", "text_fonts"),
    ("PICTURE", "text_alignments"),
    ("PICTURE", "overlays"),
    ("PICTURE", "backgrounds"),
    ("PICTURE", "template"),
    ("WINDOW", "background"),
    ("WINDOW", "font"),
    ("WINDOW", "text_color"),
]

#: Options among EVENT_OPTIONS whose value may reference existing image file(s)
PATH_OPTIONS = {
    ("PICTURE", "overlays"),
    ("PICTURE", "backgrounds"),
    ("PICTURE", "template"),
    ("WINDOW", "background"),
}

#: File extensions listed as "images" of an event profile
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

#: Characters allowed in a sanitized event name
_ALLOWED_NAME_CHARS = string.ascii_letters + string.digits + " -_"

#: Name of the snapshot file stored in each event folder
EVENT_FILENAME = "event.cfg"


def _render_value(values: tuple[Any, ...]) -> str:
    """Return the INI-compatible string representation of a gettuple() result."""
    if not values:
        return ""
    if len(values) == 1:
        value = values[0]
        return value if isinstance(value, str) else str(value)
    parts = [repr(value) if isinstance(value, str) else str(value) for value in values]
    return "(" + ", ".join(parts) + ")"


class EventManager:
    """Manage event profiles (snapshot, list, apply, delete) for a given
    :py:class:`~pibooth.config.parser.PiConfigParser` instance.
    """

    def __init__(self, cfg: PiConfigParser) -> None:
        self._cfg = cfg
        self._events_dir = cfg.join_path("events")

    def sanitize(self, name: str) -> str:
        """Return a filesystem-safe event name.

        Only letters, digits, spaces, dashes and underscores are kept, the
        result is stripped and truncated to 60 characters.

        :param name: raw name to sanitize
        """
        cleaned = "".join(char for char in name if char in _ALLOWED_NAME_CHARS).strip()
        cleaned = cleaned[:60].strip()
        if not cleaned:
            raise ValueError("Event name is empty once sanitized")
        return cleaned

    def _event_dir(self, name: str) -> str:
        return osp.join(self._events_dir, self.sanitize(name))

    def active(self) -> str:
        """Return the name of the currently applied event, or an empty string."""
        return self._cfg.get("GENERAL", "event")

    def list_events(self) -> list[dict[str, Any]]:
        """Return the list of available event profiles, sorted by name.

        Each entry is a dict with keys ``name``, ``modified`` (ISO string)
        and ``images`` (list of image filenames found in the event folder).
        """
        if not osp.isdir(self._events_dir):
            return []

        events = []
        for name in os.listdir(self._events_dir):
            event_dir = osp.join(self._events_dir, name)
            cfg_file = osp.join(event_dir, EVENT_FILENAME)
            if not osp.isfile(cfg_file):
                continue
            images = sorted(
                filename
                for filename in os.listdir(event_dir)
                if osp.splitext(filename)[1].lower() in IMAGE_EXTENSIONS and osp.isfile(osp.join(event_dir, filename))
            )
            modified = datetime.fromtimestamp(osp.getmtime(cfg_file)).isoformat()
            events.append({"name": name, "modified": modified, "images": images})

        return sorted(events, key=lambda event: event["name"])

    def _copy_referenced_file(self, path: str, event_dir: str, copied: dict[str, str], used_names: set[str]) -> str:
        """Copy ``path`` into ``event_dir`` (deduping same source and clashing
        basenames) and return the destination absolute path.
        """
        if path in copied:
            return copied[path]

        stem, ext = osp.splitext(osp.basename(path))
        candidate = f"{stem}{ext}"
        index = 1
        while candidate in used_names:
            candidate = f"{stem}_{index}{ext}"
            index += 1
        used_names.add(candidate)

        dest = osp.join(event_dir, candidate)
        shutil.copy2(path, dest)
        copied[path] = dest
        return dest

    def _snapshot_value(
        self, section: str, option: str, event_dir: str, copied: dict[str, str], used_names: set[str]
    ) -> str:
        """Return the value to store in event.cfg for one option, copying
        any referenced existing image file(s) into the event folder.
        """
        raw = self._cfg.get(section, option)
        if (section, option) not in PATH_OPTIONS:
            return raw

        try:
            values = self._cfg.gettuple(section, option, ("color", "path"))
        except (ValueError, SyntaxError) as ex:
            LOGGER.warning("Cannot parse [%s][%s]=%r for event snapshot: %s", section, option, raw, ex)
            return raw

        new_values = []
        for value in values:
            if isinstance(value, str) and value and osp.isfile(value):
                new_values.append(self._copy_referenced_file(value, event_dir, copied, used_names))
            else:
                new_values.append(value)
        return _render_value(tuple(new_values))

    def save_event(self, name: str, overwrite: bool = False) -> dict[str, Any]:
        """Snapshot the current look-and-feel configuration as an event
        profile. Referenced existing image files are copied into the
        event folder.

        :param name: event name (sanitized)
        :param overwrite: if True, replace an existing event with the same name
        """
        name = self.sanitize(name)
        event_dir = self._event_dir(name)
        cfg_file = osp.join(event_dir, EVENT_FILENAME)

        if osp.isfile(cfg_file) and not overwrite:
            raise FileExistsError(f"Event '{name}' already exists")

        if osp.isdir(event_dir):
            shutil.rmtree(event_dir)
        os.makedirs(event_dir)

        parser = configparser.RawConfigParser()
        copied: dict[str, str] = {}
        used_names: set[str] = set()
        for section, option in EVENT_OPTIONS:
            if not parser.has_section(section):
                parser.add_section(section)
            parser.set(section, option, self._snapshot_value(section, option, event_dir, copied, used_names))

        with open(cfg_file, "w", encoding="utf-8") as fp:
            parser.write(fp)

        LOGGER.info("Event '%s' saved in '%s'", name, event_dir)
        return self._describe(name)

    def _describe(self, name: str) -> dict[str, Any]:
        for event in self.list_events():
            if event["name"] == name:
                return event
        raise FileNotFoundError(f"Event '{name}' not found")

    def apply_event(self, name: str) -> None:
        """Apply an event profile: write its values into the live
        configuration and save it.

        :param name: event name to apply
        """
        name = self.sanitize(name)
        cfg_file = osp.join(self._event_dir(name), EVENT_FILENAME)
        if not osp.isfile(cfg_file):
            raise FileNotFoundError(f"Event '{name}' not found")

        parser = configparser.RawConfigParser()
        parser.read(cfg_file, encoding="utf-8")

        for section in parser.sections():
            if section not in DEFAULT:
                continue
            for option, value in parser.items(section):
                if option not in DEFAULT[section]:
                    continue
                self._cfg.set(section, option, value)

        self._cfg.set("GENERAL", "event", name)
        self._cfg.save()
        LOGGER.info("Event '%s' applied", name)

    def delete_event(self, name: str) -> None:
        """Delete an event profile. If it is the currently applied one, the
        affected configuration options are reset to their default value.

        :param name: event name to delete
        """
        name = self.sanitize(name)
        event_dir = self._event_dir(name)
        if not osp.isdir(event_dir):
            raise FileNotFoundError(f"Event '{name}' not found")

        was_active = self.active() == name
        shutil.rmtree(event_dir)

        if was_active:
            self._cfg.set("GENERAL", "event", "")
            for section, option in EVENT_OPTIONS:
                if event_dir in self._cfg.get(section, option):
                    default = DEFAULT[section][option][0]
                    self._cfg.set(section, option, str(default))
            self._cfg.save()

        LOGGER.info("Event '%s' deleted", name)
