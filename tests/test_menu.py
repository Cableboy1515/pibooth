"""Smoke tests for the settings menu (pygame-menu integration)."""

from pibooth.config.menu import PiConfigMenu
from pibooth.counters import Counters
from pibooth.plugins import create_plugin_manager
from pibooth.view.window import PiWindow


class ApplicationMock:
    def __init__(self, counters):
        self.count = counters


def test_menu_construct_and_process(tmpdir, cfg, init):
    plugin_manager = create_plugin_manager()
    window = PiWindow("Test menu")
    app = ApplicationMock(
        Counters(str(tmpdir.join("counters.json")), taken=0, printed=0, forgotten=0, remaining_duplicates=3)
    )

    menu = PiConfigMenu(plugin_manager, cfg, app, window)
    assert not menu.is_shown()

    menu.show()
    assert menu.is_shown()

    # Draw one frame and navigate with generated events to exercise
    # the pygame-menu API surface used by pibooth
    menu.process([])
    menu.process([menu.create_next_event()])
    menu.process([menu.create_click_event()])
    menu.process([menu.create_back_event()])
    assert menu.is_shown()
