from typing import TYPE_CHECKING, Any, ClassVar

import pygame
from PIL import Image, ImageDraw

from pibooth import fonts
from pibooth.pictures import sizing

if TYPE_CHECKING:
    from pibooth.view.window import PiWindow


class BaseCamera:
    IMAGE_EFFECTS: ClassVar[list[str]]

    def __init__(self, camera_proxy: Any) -> None:
        self._cam = camera_proxy
        self._border = 50
        self._window: PiWindow | None = None
        self._overlay: Any = None
        self._captures: list[Any] = []

        self.resolution: tuple[int, int] | None = None
        self.delete_internal_memory = False
        self.preview_rotation, self.capture_rotation = (0, 0)
        self.preview_iso, self.capture_iso = (100, 100)
        self.preview_flip, self.capture_flip = (False, False)

    def initialize(
        self,
        iso: int | tuple[int, int],
        resolution: tuple[int, int],
        rotation: int | tuple[int, int] = 0,
        flip: bool = False,
        delete_internal_memory: bool = False,
    ) -> None:
        """Initialize the camera."""
        if not isinstance(rotation, (tuple, list)):
            rotation = (rotation, rotation)
        self.preview_rotation, self.capture_rotation = rotation
        for name in ("preview", "capture"):
            value = getattr(self, f"{name}_rotation")
            if value not in (0, 90, 180, 270):
                raise ValueError(f"Invalid {name} camera rotation value '{value}' (should be 0, 90, 180 or 270)")
        self.resolution = resolution
        self.capture_flip = flip
        if not isinstance(iso, (tuple, list)):
            iso = (iso, iso)
        self.preview_iso, self.capture_iso = iso
        self.delete_internal_memory = delete_internal_memory
        self._specific_initialization()

    def _specific_initialization(self) -> None:
        """Specific camera initialization."""
        pass

    def _show_overlay(self, text: str | None, alpha: int) -> None:
        """Add an image as an overlay."""
        self._overlay = text

    def _hide_overlay(self) -> None:
        """Remove any existing overlay."""
        if self._overlay is not None:
            self._overlay = None

    def _post_process_capture(self, capture_data: Any) -> Image.Image:
        """Rework and return a PIL Image object from capture data."""
        raise NotImplementedError

    def get_rect(self, max_size: tuple[int, int] | None = None) -> pygame.Rect:
        """Return a Rect object (as defined in pygame) for resizing preview and images
        in order to fit to the defined window.
        """
        if self._window is None or self.resolution is None:
            raise RuntimeError("Camera preview is not started or camera is not initialized")
        rect = self._window.get_rect(absolute=True)
        size = (rect.width - 2 * self._border, rect.height - 2 * self._border)
        if max_size:
            size = (min(size[0], max_size[0]), min(size[1], max_size[1]))
        res = sizing.new_size_keep_aspect_ratio(self.resolution, size)
        return pygame.Rect(rect.centerx - res[0] // 2, rect.centery - res[1] // 2, res[0], res[1])

    def build_overlay(self, size: tuple[int, int], text: str, alpha: int) -> Image.Image:
        """Return a PIL image with the given text that can be used
        as an overlay for the camera.
        """
        image = Image.new("RGBA", size)
        draw = ImageDraw.Draw(image)

        font = fonts.get_pil_font(text, fonts.CURRENT, 0.9 * size[0], 0.9 * size[1])
        bbox = draw.textbbox((0, 0), text, font=font)
        txt_width = bbox[2] - bbox[0]
        txt_height = bbox[3] - bbox[1]

        position = ((size[0] - txt_width) // 2, (size[1] - txt_height) // 2 - size[1] // 10)
        draw.text(position, text, (255, 255, 255, alpha), font=font)
        return image

    def preview(self, window: "PiWindow", flip: bool = True) -> None:
        """Setup the preview."""
        raise NotImplementedError

    def preview_countdown(self, timeout: float, alpha: int = 60) -> None:
        """Show a countdown of `timeout` seconds on the preview.
        Returns when the countdown is finished.
        """
        raise NotImplementedError

    def preview_wait(self, timeout: float, alpha: int = 60) -> None:
        """Wait the given time and let doing the job.
        Returns when the timeout is reached.
        """
        raise NotImplementedError

    def stop_preview(self) -> None:
        """Stop the preview."""
        raise NotImplementedError

    def capture(self, effect: str | None = None) -> None:
        """Capture a new picture."""
        raise NotImplementedError

    def get_captures(self) -> list[Image.Image]:
        """Return all buffered captures as PIL images (buffer dropped after call)."""
        images = []
        for data in self._captures:
            images.append(self._post_process_capture(data))
        self.drop_captures()
        return images

    def drop_captures(self) -> None:
        """Delete all buffered captures."""
        self._captures.clear()

    def quit(self) -> None:
        """Close the camera driver, it's definitive."""
        raise NotImplementedError
