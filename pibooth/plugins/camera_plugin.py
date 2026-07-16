import time
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

import pygame

import pibooth
from pibooth import camera
from pibooth.utils import LOGGER

if TYPE_CHECKING:
    from pibooth.booth import PiApplication
    from pibooth.config.parser import PiConfigParser
    from pibooth.view.window import PiWindow


class CameraPlugin:
    """Plugin to manage the camera captures."""

    name = "pibooth-core:camera"

    def __init__(self, plugin_manager: Any) -> None:
        self._pm = plugin_manager
        self.count = 0

    @pibooth.hookimpl(hookwrapper=True)
    def pibooth_setup_camera(self, cfg: "PiConfigParser") -> Generator[None, Any, None]:
        outcome = yield  # all corresponding hookimpls are invoked here
        cam = outcome.get_result()

        if not cam:
            LOGGER.debug("Fallback to pibooth default camera management system")
            cam = camera.find_camera(cfg.get("CAMERA", "type"))

        cam.initialize(
            cfg.gettuple("CAMERA", "iso", (int, str), 2),
            cfg.gettyped("CAMERA", "resolution"),
            cfg.gettuple("CAMERA", "rotation", int, 2),
            cfg.getboolean("CAMERA", "flip"),
            cfg.getboolean("CAMERA", "delete_internal_memory"),
        )
        outcome.force_result(cam)

    @pibooth.hookimpl
    def pibooth_cleanup(self, app: "PiApplication") -> None:
        app.camera.quit()

    @pibooth.hookimpl
    def state_failsafe_enter(self, app: "PiApplication") -> None:
        """Reset variables set in this plugin."""
        app.capture_date = None
        app.capture_nbr = None
        app.camera.drop_captures()  # Flush previous captures

    @pibooth.hookimpl
    def state_wait_enter(self, app: "PiApplication") -> None:
        app.capture_date = None
        if len(app.capture_choices) > 1:
            app.capture_nbr = None
        else:
            app.capture_nbr = app.capture_choices[0]

    @pibooth.hookimpl
    def state_choose_do(self, app: "PiApplication", events: list[pygame.event.Event]) -> None:
        event = app.find_choice_event(events)
        if event:
            if event.key == pygame.K_LEFT:
                app.capture_nbr = app.capture_choices[0]
            elif event.key == pygame.K_RIGHT:
                app.capture_nbr = app.capture_choices[1]

    @pibooth.hookimpl
    def state_preview_enter(self, cfg: "PiConfigParser", app: "PiApplication", win: "PiWindow") -> None:
        LOGGER.info("Show preview before next capture")
        if not app.capture_date:
            app.capture_date = time.strftime("%Y-%m-%d-%H-%M-%S")
        app.camera.preview(win)

    @pibooth.hookimpl
    def state_preview_do(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        pygame.event.pump()  # Before blocking actions
        if cfg.getboolean("WINDOW", "preview_countdown"):
            app.camera.preview_countdown(cfg.getint("WINDOW", "preview_delay"))
        else:
            app.camera.preview_wait(cfg.getint("WINDOW", "preview_delay"))

    @pibooth.hookimpl
    def state_preview_exit(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        if cfg.getboolean("WINDOW", "preview_stop_on_capture"):
            app.camera.stop_preview()

    @pibooth.hookimpl
    def state_capture_do(self, cfg: "PiConfigParser", app: "PiApplication", win: "PiWindow") -> None:
        assert app.capture_nbr is not None
        effects = cfg.gettyped("PICTURE", "captures_effects")
        if not isinstance(effects, (list, tuple)):
            # Same effect for all captures
            effect = effects
        elif len(effects) >= app.capture_nbr:
            # Take the effect corresponding to the current capture
            effect = effects[self.count]
        else:
            # Not possible
            raise ValueError(f"Not enough effects defined for {app.capture_nbr} captures {effects}")

        LOGGER.info("Take a capture")
        if cfg.getboolean("WINDOW", "flash"):
            with win.flash(2):  # Manage the window here, have no choice
                app.camera.capture(effect)
        else:
            app.camera.capture(effect)

        self.count += 1

    @pibooth.hookimpl
    def state_capture_exit(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        if not cfg.getboolean("WINDOW", "preview_stop_on_capture"):
            app.camera.stop_preview()

    @pibooth.hookimpl
    def state_processing_enter(self, app: "PiApplication") -> None:
        self.count = 0
