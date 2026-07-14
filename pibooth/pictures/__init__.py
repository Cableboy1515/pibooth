import os.path as osp
from collections.abc import Sequence

import pygame
from PIL import Image, ImageOps
from PIL.Image import Resampling

from pibooth import fonts, language
from pibooth.pictures import factory, sizing
from pibooth.pictures.factory import PictureFactory

AUTO = "auto"
PORTRAIT = "portrait"
LANDSCAPE = "landscape"


def get_filename(name: str) -> str:
    """Return absolute path to a picture located in the current package.

    :param name: name of an image located in language folders

    :return: absolute image path
    """
    return osp.join(osp.dirname(osp.abspath(__file__)), "assets", name)


def colorize_pil_image(
    pil_image: Image.Image, color: tuple[int, ...], bg_color: tuple[int, ...] | None = None
) -> Image.Image:
    """Convert a picto in white to the corresponding color.

    :param pil_image: PIL image to be colorized
    :param color: RGB color to convert the picto
    :param bg_color: RGB color to use for the picto's background
    """
    if not bg_color:
        bg_color = (abs(color[0] - 255), abs(color[1] - 255), abs(color[2] - 255))
    _, _, _, alpha = pil_image.split()
    gray_pil_image = pil_image.convert("L")
    new_pil_image = ImageOps.colorize(gray_pil_image, black=bg_color, white=color)
    new_pil_image.putalpha(alpha)
    return new_pil_image


def get_pygame_main_color(surface: pygame.Surface) -> tuple[int, ...]:
    """Return the main color of the given pygame surface."""
    monopixel_surface = pygame.transform.scale(surface, (1, 1))
    return tuple(monopixel_surface.get_at((0, 0)))


def get_pygame_image(
    name: str,
    size: tuple[float, float] | None = None,
    antialiasing: bool = True,
    hflip: bool = False,
    vflip: bool = False,
    crop: bool = False,
    angle: int = 0,
    color: tuple[int, ...] | None = (255, 255, 255),
    bg_color: tuple[int, ...] | None = None,
) -> pygame.Surface:
    """Return a Pygame image. If a size is given, the image is
    resized keeping the original image's aspect ratio.

    :param name: name of an image located in language folders
    :param size: resize image to this size
    :param antialiasing: use antialiasing algorithm when resize
    :param hflip: apply an horizontal flip
    :param vflip: apply a vertical flip
    :param crop: crop image to fit aspect ration of the size
    :param angle: angle of rotation of the image
    :param color: recolorize the image with this RGB color
    :param bg_color: recolorize the image background with this RGB color

    :return: pygame.Surface with image
    """
    size_int = None if size is None else (int(size[0]), int(size[1]))
    path = get_filename(name)
    if not size_int and not color:
        image = pygame.image.load(path).convert()
    else:
        if osp.isfile(path):
            pil_image: Image.Image = Image.open(path)
        elif size_int:
            pil_image = Image.new("RGBA", size_int, (0, 0, 0, 0))
        else:
            raise ValueError(f"Image '{path}' not found and no size given to create an empty one")

        if color:
            pil_image = colorize_pil_image(pil_image, color, bg_color)

        if size_int:
            if crop:
                pil_image = pil_image.crop(sizing.new_size_by_croping_ratio(pil_image.size, size_int))
            pil_image = pil_image.resize(
                sizing.new_size_keep_aspect_ratio(pil_image.size, size_int),
                Resampling.LANCZOS if antialiasing else Resampling.NEAREST,
            )

        image = pygame.image.frombuffer(pil_image.tobytes(), pil_image.size, pil_image.mode)  # type: ignore[arg-type]

    if hflip or vflip:
        image = pygame.transform.flip(image, hflip, vflip)
    if angle != 0:
        image = pygame.transform.rotate(image, angle)
    return image


def get_pygame_layout_image(
    text_color: tuple[int, ...], bg_color: tuple[int, ...], layout_number: int, size: tuple[float, float]
) -> pygame.Surface:
    """Generate the layout image with the corresponding text.

    :param text_color: RGB color for texts
    :param layout_number: number of captures on the layout
    :param size: maximum size of the layout surface

    :return: surface
    """
    layout_image = get_pygame_image(f"layout{layout_number}.png", size, color=text_color, bg_color=bg_color)
    text = language.get_translated_text(str(layout_number))
    if text:
        rect = layout_image.get_rect()
        rect = pygame.Rect(
            rect.x + rect.width * 0.3 / 2, rect.y + rect.height * 0.76, rect.width * 0.7, rect.height * 0.20
        )
        text_font = fonts.get_pygame_font(text, fonts.CURRENT, rect.width, rect.height)
        surface = text_font.render(text, True, bg_color)
        layout_image.blit(surface, surface.get_rect(center=rect.center))
    return layout_image


def get_best_orientation(captures: Sequence[Image.Image]) -> str:
    """Return the most adapted orientation (PORTRAIT or LANDSCAPE),
    depending on the resolution of the given captures.

    It use the size of the first capture to determine the orientation.

    :param captures: list of captures to concatenate

    :return: orientation PORTRAIT or LANDSCAPE
    """
    is_portrait = captures[0].size[0] < captures[0].size[1]
    if len(captures) == 1 or len(captures) == 4:
        if is_portrait:
            orientation = PORTRAIT
        else:
            orientation = LANDSCAPE
    elif len(captures) == 2 or len(captures) == 3:
        if is_portrait:
            orientation = LANDSCAPE
        else:
            orientation = PORTRAIT
    else:
        raise ValueError(f"List of max 4 pictures expected, got {len(captures)}")
    return orientation


def get_picture_factory(
    captures: Sequence[Image.Image],
    orientation: str = AUTO,
    paper_format: tuple[int, int] = (4, 6),
    force_pil: bool = False,
    dpi: int = 600,
) -> PictureFactory:
    """Return the picture factory use to concatenate the captures.

    :param captures: list of captures to concatenate
    :param orientation: paper orientation
    :param paper_format: paper size in inches
    :param force_pil: force use PIL implementation
    :param dpi: dot-per-inche resolution
    """
    assert orientation in (AUTO, PORTRAIT, LANDSCAPE), f"Unknown orientation '{orientation}'"
    if orientation == AUTO:
        orientation = get_best_orientation(captures)

    # Ensure paper format is given in portrait (don't manage orientation with it)
    if paper_format[0] > paper_format[1]:
        paper_format = (paper_format[1], paper_format[0])

    size = (paper_format[0] * dpi, paper_format[1] * dpi)
    if orientation == LANDSCAPE:
        size = (size[1], size[0])

    if not factory.cv2 or force_pil:
        return factory.PilPictureFactory(size[0], size[1], *captures)

    return factory.OpenCvPictureFactory(size[0], size[1], *captures)
