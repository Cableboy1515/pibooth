import json
import types

import pytest

from pibooth.config.parser import PiConfigParser
from pibooth.plugins.printer_plugin import build_job_options
from pibooth.printer import QUALITY_LEVELS, Printer, match_media, parse_pwg_media

# --------------------------------------------------------------- parse_pwg_media


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("na_index-4x6_4x6in", (4.0, 6.0)),
        ("na_letter_8.5x11in", (8.5, 11.0)),
        # mm unit, converted to inches
        ("om_small-photo_100x148mm", (100 / 25.4, 148 / 25.4)),
        # landscape name, portrait-normalized (smaller dimension first)
        ("na_foo_9x6in", (6.0, 9.0)),
        ("om_2x6_2x6in", (2.0, 6.0)),
    ],
)
def test_parse_pwg_media_valid(name, expected):
    result = parse_pwg_media(name)
    assert result is not None
    assert result[0] == pytest.approx(expected[0])
    assert result[1] == pytest.approx(expected[1])


@pytest.mark.parametrize(
    "name",
    [
        "custom_min_2x2in",  # custom range bound, not a selectable size
        "custom_max_8.5x14in",  # custom range bound, not a selectable size
        "not-a-media-name",  # garbage
        "na_index-4x6_4x6",  # missing unit
        "",
    ],
)
def test_parse_pwg_media_none(name):
    assert parse_pwg_media(name) is None


# ------------------------------------------------------------------- match_media


def test_match_media_exact():
    supported = ["na_index-4x6_4x6in", "om_2x6_2x6in"]
    assert match_media((4, 6), supported) == "na_index-4x6_4x6in"


def test_match_media_tolerance_boundary():
    supported = ["na_index-4x6_4x6in"]
    # Within 0.08in tolerance
    assert match_media((4.07, 6.0), supported) == "na_index-4x6_4x6in"
    # Just outside tolerance
    assert match_media((4.09, 6.0), supported) is None


def test_match_media_orientation_normalization():
    supported = ["na_index-4x6_4x6in"]
    # Landscape target should still match the portrait-normalized media
    assert match_media((6, 4), supported) == "na_index-4x6_4x6in"


def test_match_media_no_match():
    supported = ["na_index-4x6_4x6in"]
    assert match_media((5, 7), supported) is None


# --------------------------------------------------------------- build_job_options


@pytest.fixture
def job_cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(PiConfigParser, "handle_autostart", lambda self: None)
    cfg = PiConfigParser(str(tmp_path / "pibooth.cfg"), None)
    cfg.set("GENERAL", "directory", str(tmp_path / "pictures"))
    return cfg


def make_stub_app(media=None, trays=None, capture_nbr=4):
    caps = {
        "media": media if media is not None else ["na_index-4x6_4x6in", "om_2x6_2x6in"],
        "trays": trays if trays is not None else ["main", "photo"],
    }
    printer = types.SimpleNamespace(get_capabilities=lambda: caps)
    return types.SimpleNamespace(printer=printer, capture_nbr=capture_nbr)


def write_template(tmp_path, captures=4, paper="2x6", dpi=300, size=(600, 1800)):
    data = {
        "name": "strip",
        "pages": [
            {
                "captures": captures,
                "orientation": "portrait" if size[0] < size[1] else "landscape",
                "paper": paper,
                "dpi": dpi,
                "size": list(size),
                "shapes": [],
            }
        ],
    }
    path = tmp_path / "strip.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_build_job_options_auto_no_template(job_cfg):
    app = make_stub_app()
    options = build_job_options(job_cfg, app)
    assert options == {"media": "na_index-4x6_4x6in"}


def test_build_job_options_auto_with_template(job_cfg, tmp_path):
    template_path = write_template(tmp_path)
    job_cfg.set("PICTURE", "template", template_path)
    app = make_stub_app(capture_nbr=4)
    options = build_job_options(job_cfg, app)
    assert options == {"media": "om_2x6_2x6in"}


def test_build_job_options_explicit_no_match(job_cfg):
    job_cfg.set("PRINTER", "paper_size", "5x7")
    app = make_stub_app()
    options = build_job_options(job_cfg, app)
    assert "media" not in options
    assert options == {}


def test_build_job_options_quality_high(job_cfg):
    job_cfg.set("PRINTER", "paper_size", "default")
    job_cfg.set("PRINTER", "quality", "high")
    app = make_stub_app()
    options = build_job_options(job_cfg, app)
    assert options == {"print-quality": str(QUALITY_LEVELS["high"])}


def test_build_job_options_tray_in_caps(job_cfg):
    job_cfg.set("PRINTER", "paper_size", "default")
    job_cfg.set("PRINTER", "tray", "main")
    app = make_stub_app()
    options = build_job_options(job_cfg, app)
    assert options == {"media-source": "main"}


def test_build_job_options_tray_not_in_caps(job_cfg):
    job_cfg.set("PRINTER", "paper_size", "default")
    job_cfg.set("PRINTER", "tray", "nonexistent")
    app = make_stub_app()
    options = build_job_options(job_cfg, app)
    assert "media-source" not in options
    assert options == {}


# --------------------------------------------------------------- Printer.print_file


class _FakeConn:
    def __init__(self):
        self.calls = []

    def printFile(self, name, filename, title, options):
        self.calls.append((name, filename, title, options))


def test_print_file_merges_options(tmp_path):
    printer = Printer()  # cups not installed in the venv: early return, name is None
    picture = tmp_path / "picture.jpg"
    picture.write_bytes(b"fake")

    fake_conn = _FakeConn()
    printer.name = "TestPrinter"
    printer._conn = fake_conn
    printer.options = {"foo": "bar"}

    printer.print_file(str(picture), options={"media": "na_index-4x6_4x6in"})

    assert len(fake_conn.calls) == 1
    name, filename, title, options = fake_conn.calls[0]
    assert name == "TestPrinter"
    assert filename == str(picture)
    assert options == {"foo": "bar", "media": "na_index-4x6_4x6in"}
    # self.options must be left untouched
    assert printer.options == {"foo": "bar"}


def test_print_file_without_options_keeps_self_options(tmp_path):
    printer = Printer()
    picture = tmp_path / "picture.jpg"
    picture.write_bytes(b"fake")

    fake_conn = _FakeConn()
    printer.name = "TestPrinter"
    printer._conn = fake_conn
    printer.options = {"foo": "bar"}

    printer.print_file(str(picture))

    assert fake_conn.calls[0][3] == {"foo": "bar"}


# --------------------------------------------------------------- Printer.get_capabilities


class _FakeIPPError(Exception):
    pass


class _FakeCapsConn:
    def __init__(self, attrs):
        self._attrs = attrs

    def getPrinterAttributes(self, name, requested_attributes=None):
        return self._attrs


def _fake_cups_module(attrs):
    module = types.ModuleType("cups")
    module.IPPError = _FakeIPPError
    module.Connection = lambda: _FakeCapsConn(attrs)
    return module


def test_get_capabilities_parses_attributes(monkeypatch):
    attrs = {
        "media-supported": ["na_index-4x6_4x6in", "om_2x6_2x6in"],
        "media-default": "na_index-4x6_4x6in",
        "print-quality-supported": [3, 4, 5],
        "print-quality-default": 4,
        "media-source-supported": ["main", "photo"],
        "media-source-default": "main",
        "printer-make-and-model": "Canon SELPHY CP1300",
        "printer-state-message": "",
    }
    printer = Printer()  # constructed with cups absent: avoids touching cups_notify
    fake_cups = _fake_cups_module(attrs)
    monkeypatch.setattr("pibooth.printer.cups", fake_cups)
    printer.name = "TestPrinter"
    printer._conn = fake_cups.Connection()

    caps = printer.get_capabilities()
    assert caps["media"] == ["na_index-4x6_4x6in", "om_2x6_2x6in"]
    assert caps["quality"] == [3, 4, 5]
    assert caps["model"] == "Canon SELPHY CP1300"

    # Cached: a second call returns the same dict without needing _conn again
    printer._conn = None
    assert printer.get_capabilities() == caps


def test_get_capabilities_empty_without_cups():
    printer = Printer()  # cups not installed: name/_conn are None
    assert printer.get_capabilities() == {}
