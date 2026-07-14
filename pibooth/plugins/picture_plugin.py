import itertools
import os
import os.path as osp
from collections.abc import Generator
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pygame
from PIL import Image

import pibooth
from pibooth.pictures import get_picture_factory
from pibooth.pictures.pool import PicturesFactoryPool
from pibooth.utils import LOGGER, PoolingTimer

if TYPE_CHECKING:
    from pibooth.booth import PiApplication
    from pibooth.config.parser import PiConfigParser


class PicturePlugin:
    """Plugin to build the final picture."""

    name = "pibooth-core:picture"

    def __init__(self, plugin_manager: Any) -> None:
        self._pm = plugin_manager
        self.factory_pool = PicturesFactoryPool()
        self.picture_destroy_timer = PoolingTimer(0)
        self.second_previous_picture: Image.Image | None = None
        self.texts_vars: dict[str, Any] = {}

    def _reset_vars(self, app: "PiApplication") -> None:
        """Destroy final picture (can not be used anymore)."""
        self.factory_pool.clear()
        app.previous_picture = None
        app.previous_animated = None
        app.previous_picture_file = None

    @pibooth.hookimpl(hookwrapper=True)
    def pibooth_setup_picture_factory(
        self, cfg: "PiConfigParser", opt_index: Any, factory: Any
    ) -> Generator[None, Any, None]:

        outcome = yield  # all corresponding hookimpls are invoked here
        factory = outcome.get_result() or factory

        factory.set_margin(cfg.getint("PICTURE", "margin_thick"))

        backgrounds = cfg.gettuple("PICTURE", "backgrounds", ("color", "path"), 2)
        factory.set_background(backgrounds[opt_index])

        overlays = cfg.gettuple("PICTURE", "overlays", "path", 2)
        if overlays[opt_index]:
            factory.set_overlay(overlays[opt_index])

        texts = [
            cfg.get("PICTURE", "footer_text1").strip('"').format(**self.texts_vars),
            cfg.get("PICTURE", "footer_text2").strip('"').format(**self.texts_vars),
        ]
        colors = cfg.gettuple("PICTURE", "text_colors", "color", len(texts))
        text_fonts = cfg.gettuple("PICTURE", "text_fonts", str, len(texts))
        alignments = cfg.gettuple("PICTURE", "text_alignments", str, len(texts))
        if any(elem != "" for elem in texts):
            for params in zip(texts, text_fonts, colors, alignments):
                factory.add_text(*params)

        if cfg.getboolean("PICTURE", "captures_cropping"):
            factory.set_cropping()

        if cfg.getboolean("GENERAL", "debug"):
            factory.set_outlines()

        outcome.force_result(factory)

    @pibooth.hookimpl
    def pibooth_cleanup(self) -> None:
        self.factory_pool.quit()

    @pibooth.hookimpl
    def state_failsafe_enter(self, app: "PiApplication") -> None:
        self._reset_vars(app)

    @pibooth.hookimpl
    def state_wait_enter(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        animated = self.factory_pool.get()
        if cfg.getfloat("WINDOW", "wait_picture_delay") == 0:
            # Do it here to avoid a transient display of the picture
            self._reset_vars(app)
        elif animated:
            app.previous_animated = itertools.cycle(animated)

        # Reset timeout in case of settings changed
        self.picture_destroy_timer.timeout = max(0, cfg.getfloat("WINDOW", "wait_picture_delay"))
        self.picture_destroy_timer.start()

    @pibooth.hookimpl
    def state_wait_do(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        if (
            cfg.getfloat("WINDOW", "wait_picture_delay") > 0
            and self.picture_destroy_timer.is_timeout()
            and app.previous_picture_file
        ):
            self._reset_vars(app)

    @pibooth.hookimpl
    def state_processing_enter(self, app: "PiApplication") -> None:
        self.second_previous_picture = app.previous_picture
        self._reset_vars(app)

    @pibooth.hookimpl
    def state_processing_do(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        assert app.capture_nbr is not None and app.capture_date is not None
        idx = app.capture_choices.index(app.capture_nbr)
        self.texts_vars["date"] = datetime.strptime(app.capture_date, "%Y-%m-%d-%H-%M-%S")
        self.texts_vars["count"] = app.count

        LOGGER.info("Saving raw captures")
        captures = app.camera.get_captures()

        for savedir in cfg.gettuple("GENERAL", "directory", "path"):
            rawdir = osp.join(savedir, "raw", app.capture_date)
            os.makedirs(rawdir)

            for capture in captures:
                count = captures.index(capture)
                capture.save(osp.join(rawdir, f"pibooth{count:03}.jpg"))

        LOGGER.info("Creating the final picture")
        default_factory = get_picture_factory(captures, cfg.get("PICTURE", "orientation"))
        factory = self._pm.hook.pibooth_setup_picture_factory(cfg=cfg, opt_index=idx, factory=default_factory)
        app.previous_picture = factory.build()

        for savedir in cfg.gettuple("GENERAL", "directory", "path"):
            app.previous_picture_file = osp.join(savedir, app.picture_filename)
            factory.save(app.previous_picture_file)

        if cfg.getboolean("WINDOW", "animate") and app.capture_nbr > 1:
            LOGGER.info("Asyncronously generate pictures for animation")
            for capture in captures:
                default_factory = get_picture_factory(
                    (capture,), cfg.get("PICTURE", "orientation"), force_pil=True, dpi=200
                )
                factory = self._pm.hook.pibooth_setup_picture_factory(cfg=cfg, opt_index=idx, factory=default_factory)
                factory.set_margin(factory._margin // 3)  # 1/3 since DPI is divided by 3
                self.factory_pool.add(factory)

    @pibooth.hookimpl
    def state_processing_exit(self, app: "PiApplication") -> None:
        app.count.taken += 1  # Do it here because 'print' state can be skipped

    @pibooth.hookimpl
    def state_print_do(self, cfg: "PiConfigParser", app: "PiApplication", events: list[pygame.event.Event]) -> None:
        if app.find_capture_event(events):
            LOGGER.info("Moving the picture in the forget folder")
            for savedir in cfg.gettuple("GENERAL", "directory", "path"):
                forgetdir = osp.join(savedir, "forget")
                os.makedirs(forgetdir, exist_ok=True)
                os.rename(osp.join(savedir, app.picture_filename), osp.join(forgetdir, app.picture_filename))

            self._reset_vars(app)
            app.count.forgotten += 1
            app.previous_picture = self.second_previous_picture

            # Deactivate the print function for the backuped picture
            # as we don't known how many times it has already been printed
            app.count.remaining_duplicates = 0
