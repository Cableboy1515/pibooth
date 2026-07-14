from typing import TYPE_CHECKING, Any

import pygame
from PIL import Image

import pibooth
from pibooth.utils import LOGGER, PoolingTimer, get_crash_message

if TYPE_CHECKING:
    from pibooth.booth import PiApplication
    from pibooth.config.parser import PiConfigParser
    from pibooth.view.window import PiWindow


class ViewPlugin:
    """Plugin to manage the pibooth window dans transitions."""

    name = "pibooth-core:view"

    def __init__(self, plugin_manager: Any) -> None:
        self._pm = plugin_manager
        self.count = 0
        self.forgotten = False
        # Seconds to display the failed message
        self.failed_view_timer = PoolingTimer(2)
        # Seconds between each animated frame
        self.animated_frame_timer = PoolingTimer(0)
        # Seconds before going back to the start
        self.choose_timer = PoolingTimer(30)
        # Seconds to display the selected layout
        self.layout_timer = PoolingTimer(4)
        # Seconds to display the selected layout
        self.print_view_timer = PoolingTimer(0)
        # Seconds to display the selected layout
        self.finish_timer = PoolingTimer(1)

    @pibooth.hookimpl
    def state_failsafe_enter(self, win: "PiWindow") -> None:
        win.show_oops()
        self.failed_view_timer.start()
        LOGGER.error(get_crash_message())

    @pibooth.hookimpl
    def state_failsafe_validate(self) -> str | None:
        if self.failed_view_timer.is_timeout():
            return "wait"
        return None

    @pibooth.hookimpl
    def state_wait_enter(self, cfg: "PiConfigParser", app: "PiApplication", win: "PiWindow") -> None:
        self.forgotten = False
        previous_picture: Image.Image | None
        if app.previous_animated:
            previous_picture = next(app.previous_animated)
            # Reset timeout in case of settings changed
            self.animated_frame_timer.timeout = cfg.getfloat("WINDOW", "animate_delay")
            self.animated_frame_timer.start()
        else:
            previous_picture = app.previous_picture

        win.show_intro(previous_picture, app.printer.is_ready() and app.count.remaining_duplicates > 0)
        if app.printer.is_installed():
            win.set_print_number(len(app.printer.get_all_tasks()), not app.printer.is_ready())

    @pibooth.hookimpl
    def state_wait_do(self, app: "PiApplication", win: "PiWindow", events: list[pygame.event.Event]) -> None:
        previous_picture: Image.Image | None
        if app.previous_animated and self.animated_frame_timer.is_timeout():
            previous_picture = next(app.previous_animated)
            win.show_intro(previous_picture, app.printer.is_ready() and app.count.remaining_duplicates > 0)
            self.animated_frame_timer.start()
        else:
            previous_picture = app.previous_picture

        event = app.find_print_status_event(events)
        if event and app.printer.is_installed():
            tasks = app.printer.get_all_tasks()
            win.set_print_number(len(tasks), not app.printer.is_ready())

        if app.find_print_event(events) or (win.get_image() and not previous_picture):
            win.show_intro(previous_picture, app.printer.is_ready() and app.count.remaining_duplicates > 0)

    @pibooth.hookimpl
    def state_wait_validate(
        self, cfg: "PiConfigParser", app: "PiApplication", events: list[pygame.event.Event]
    ) -> str | None:
        if app.find_capture_event(events):
            if len(app.capture_choices) > 1:
                return "choose"
            if cfg.getfloat("WINDOW", "chosen_delay") > 0:
                return "chosen"
            return "preview"
        return None

    @pibooth.hookimpl
    def state_wait_exit(self, win: "PiWindow") -> None:
        self.count = 0
        win.show_image(None)  # Clear currently displayed image

    @pibooth.hookimpl
    def state_choose_enter(self, app: "PiApplication", win: "PiWindow") -> None:
        LOGGER.info("Show picture choice (nothing selected)")
        win.set_print_number(0, False)  # Hide printer status
        win.show_choice(app.capture_choices)
        self.choose_timer.start()

    @pibooth.hookimpl
    def state_choose_validate(self, cfg: "PiConfigParser", app: "PiApplication") -> str | None:
        if app.capture_nbr:
            if cfg.getfloat("WINDOW", "chosen_delay") > 0:
                return "chosen"
            else:
                return "preview"
        elif self.choose_timer.is_timeout():
            return "wait"
        return None

    @pibooth.hookimpl
    def state_chosen_enter(self, cfg: "PiConfigParser", app: "PiApplication", win: "PiWindow") -> None:
        LOGGER.info("Show picture choice (%s captures selected)", app.capture_nbr)
        win.show_choice(app.capture_choices, selected=app.capture_nbr)

        # Reset timeout in case of settings changed
        self.layout_timer.timeout = cfg.getfloat("WINDOW", "chosen_delay")
        self.layout_timer.start()

    @pibooth.hookimpl
    def state_chosen_validate(self) -> str | None:
        if self.layout_timer.is_timeout():
            return "preview"
        return None

    @pibooth.hookimpl
    def state_preview_enter(self, app: "PiApplication", win: "PiWindow") -> None:
        assert app.capture_nbr is not None
        self.count += 1
        win.set_capture_number(self.count, app.capture_nbr)

    @pibooth.hookimpl
    def state_preview_validate(self) -> str | None:
        return "capture"

    @pibooth.hookimpl
    def state_capture_do(self, app: "PiApplication", win: "PiWindow") -> None:
        assert app.capture_nbr is not None
        win.set_capture_number(self.count, app.capture_nbr)

    @pibooth.hookimpl
    def state_capture_validate(self, app: "PiApplication") -> str | None:
        assert app.capture_nbr is not None
        if self.count >= app.capture_nbr:
            return "processing"
        return "preview"

    @pibooth.hookimpl
    def state_processing_enter(self, win: "PiWindow") -> None:
        win.show_work_in_progress()

    @pibooth.hookimpl
    def state_processing_validate(self, cfg: "PiConfigParser", app: "PiApplication") -> str | None:
        if (
            app.printer.is_ready()
            and cfg.getfloat("PRINTER", "printer_delay") > 0
            and app.count.remaining_duplicates > 0
        ):
            return "print"
        return "finish"  # Can not print

    @pibooth.hookimpl
    def state_print_enter(self, cfg: "PiConfigParser", app: "PiApplication", win: "PiWindow") -> None:
        LOGGER.info("Display the final picture")
        win.show_print(app.previous_picture)
        win.set_print_number(len(app.printer.get_all_tasks()), not app.printer.is_ready())

        # Reset timeout in case of settings changed
        self.print_view_timer.timeout = cfg.getfloat("PRINTER", "printer_delay")
        self.print_view_timer.start()

    @pibooth.hookimpl
    def state_print_validate(
        self, app: "PiApplication", win: "PiWindow", events: list[pygame.event.Event]
    ) -> str | None:
        printed = app.find_print_event(events)
        self.forgotten = bool(app.find_capture_event(events))
        if self.print_view_timer.is_timeout() or printed or self.forgotten:
            if printed:
                win.set_print_number(len(app.printer.get_all_tasks()), not app.printer.is_ready())
            return "finish"
        return None

    @pibooth.hookimpl
    def state_finish_enter(self, cfg: "PiConfigParser", app: "PiApplication", win: "PiWindow") -> None:
        if cfg.getfloat("WINDOW", "finish_picture_delay") > 0 and not self.forgotten:
            win.show_finished(app.previous_picture)
            timeout = cfg.getfloat("WINDOW", "finish_picture_delay")
        else:
            win.show_finished()
            timeout = 1

        # Reset timeout in case of settings changed
        self.finish_timer.timeout = timeout
        self.finish_timer.start()

    @pibooth.hookimpl
    def state_finish_validate(self) -> str | None:
        if self.finish_timer.is_timeout():
            return "wait"
        return None
