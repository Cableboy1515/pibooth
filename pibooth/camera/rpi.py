from typing import TYPE_CHECKING, Any

import pygame
from PIL import Image

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None  # picamera2 is optional
from pibooth.camera.base import BaseCamera
from pibooth.language import get_translated_text
from pibooth.pictures import sizing
from pibooth.utils import LOGGER, PoolingTimer

if TYPE_CHECKING:
    from pibooth.view.window import PiWindow

PREVIEW_MAX_SIZE = (1280, 1280)


def get_rpi_camera_proxy(port: int | None = None) -> Any:
    """Return camera proxy if a Raspberry Pi compatible camera is found
    else return None.

    :param port: look on given camera number
    """
    if not Picamera2:
        return None  # picamera2 is not installed
    try:
        if not Picamera2.global_camera_info():
            return None
        return Picamera2(camera_num=port if port is not None else 0)
    except (OSError, RuntimeError, IndexError):
        pass
    return None


class RpiCamera(BaseCamera):
    """Raspberry Pi camera management (libcamera based, using picamera2).

    The preview is rendered in software: frames are pulled from the camera
    and blitted to the pibooth window (the legacy picamera GPU overlay does
    not exist anymore with the libcamera stack).
    """

    IMAGE_EFFECTS = ["none"]

    def __init__(self, camera_proxy: Any) -> None:
        super().__init__(camera_proxy)
        self._preview_config: Any = None
        self._capture_config: Any = None

    def _specific_initialization(self) -> None:
        """Camera initialization."""
        assert self.resolution is not None
        # ISO of the legacy stack maps to the analogue gain (ISO 100 <=> gain 1.0)
        preview_size = sizing.new_size_keep_aspect_ratio(self.resolution, PREVIEW_MAX_SIZE, "inner")
        self._preview_config = self._cam.create_preview_configuration(
            main={"size": preview_size, "format": "RGB888"},
            controls={"AnalogueGain": self.preview_iso / 100},
        )
        self._capture_config = self._cam.create_still_configuration(
            main={"size": tuple(self.resolution), "format": "RGB888"},
            controls={"AnalogueGain": self.capture_iso / 100},
        )
        LOGGER.debug("Preview resolution is %s", preview_size)

    def _show_overlay(self, text: str | None, alpha: int) -> None:
        """Add an image as an overlay."""
        if self._window:  # No window means no preview displayed
            rect = self.get_rect()
            self._overlay = self.build_overlay((rect.width, rect.height), str(text), alpha)

    def _rotate_image(self, image: Image.Image, rotation: int) -> Image.Image:
        """Rotate a PIL image, same direction than the legacy picamera stack."""
        if rotation == 90:
            return image.transpose(Image.Transpose.ROTATE_270)
        elif rotation == 180:
            return image.transpose(Image.Transpose.ROTATE_180)
        elif rotation == 270:
            return image.transpose(Image.Transpose.ROTATE_90)
        return image

    def _get_preview_image(self) -> Image.Image:
        """Capture a new preview image."""
        rect = self.get_rect()
        assert self.resolution is not None

        image = self._cam.capture_image("main").convert("RGB")
        image = self._rotate_image(image, self.preview_rotation)

        # Crop to keep aspect ratio of the resolution
        image = image.crop(sizing.new_size_by_croping_ratio(image.size, self.resolution))
        # Resize to fit the available space in the window
        size = sizing.new_size_keep_aspect_ratio(image.size, (rect.width, rect.height), "outer")
        image = image.resize(size, Image.Resampling.BILINEAR)

        if self.preview_flip:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

        if self._overlay is not None:
            if self._overlay.size != image.size:
                # Previous operations may create a size with one pixel gap
                self._overlay = self._overlay.resize(image.size)
            image = image.convert("RGBA")
            image.alpha_composite(self._overlay)
            image = image.convert("RGB")
        return image

    def _post_process_capture(self, capture_data: Image.Image) -> Image.Image:
        """Rework capture data.

        :param capture_data: PIL image
        """
        assert self.resolution is not None
        image = self._rotate_image(capture_data.convert("RGB"), self.capture_rotation)

        # Crop to keep aspect ratio of the resolution
        image = image.crop(sizing.new_size_by_croping_ratio(image.size, self.resolution))
        # Resize to fit the resolution
        size = sizing.new_size_keep_aspect_ratio(image.size, self.resolution, "outer")
        image = image.resize(size, Image.Resampling.LANCZOS)

        if self.capture_flip:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        return image

    def preview(self, window: "PiWindow", flip: bool = True) -> None:
        """Setup the preview."""
        if self._cam.started:
            # Already running
            return

        self._window = window
        self.preview_flip = flip
        self._cam.configure(self._preview_config)
        self._cam.start()
        self._window.show_image(self._get_preview_image())

    def preview_countdown(self, timeout: float, alpha: int = 80) -> None:
        """Show a countdown of `timeout` seconds on the preview.
        Returns when the countdown is finished.
        """
        timeout = int(timeout)
        if timeout < 1:
            raise ValueError("Start time shall be greater than 0")
        if not self._cam.started or self._window is None:
            raise OSError("Preview shall be started first")

        timer = PoolingTimer(timeout)
        while not timer.is_timeout():
            remaining = int(timer.remaining() + 1)
            if self._overlay is None or remaining != timeout:
                # Rebuild overlay only if remaining number has changed
                self._show_overlay(str(remaining), alpha)
                timeout = remaining

            updated_rect = self._window.show_image(self._get_preview_image())
            pygame.event.pump()
            if updated_rect:
                pygame.display.update(updated_rect)

        self._show_overlay(get_translated_text("smile"), alpha)
        self._window.show_image(self._get_preview_image())

    def preview_wait(self, timeout: float, alpha: int = 80) -> None:
        """Wait the given time."""
        timeout = int(timeout)
        if timeout < 1:
            raise ValueError("Start time shall be greater than 0")
        if self._window is None:
            raise OSError("Preview shall be started first")

        timer = PoolingTimer(timeout)
        while not timer.is_timeout():
            updated_rect = self._window.show_image(self._get_preview_image())
            pygame.event.pump()
            if updated_rect:
                pygame.display.update(updated_rect)

        self._show_overlay(get_translated_text("smile"), alpha)
        self._window.show_image(self._get_preview_image())

    def stop_preview(self) -> None:
        """Stop the preview."""
        self._hide_overlay()
        if self._cam.started:
            self._cam.stop()
        self._window = None

    def capture(self, effect: str | None = None) -> None:
        """Capture a new picture in a file."""
        effect = str(effect).lower()
        if effect not in self.IMAGE_EFFECTS:
            raise ValueError(f"Invalid capture effect '{effect}' (choose among {self.IMAGE_EFFECTS})")

        if self._cam.started:
            # Preview is running: temporarily switch to the high resolution mode
            image = self._cam.switch_mode_and_capture_image(self._capture_config, "main")
        else:
            self._cam.configure(self._capture_config)
            self._cam.start()
            image = self._cam.capture_image("main")
            self._cam.stop()

        self._captures.append(image)

        self._hide_overlay()  # If stop_preview() has not been called

    def quit(self) -> None:
        """Close the camera driver, it's definitive."""
        self._cam.close()
