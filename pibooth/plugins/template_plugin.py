import os.path as osp
from typing import TYPE_CHECKING, Any

import pibooth
from pibooth.pictures import AUTO
from pibooth.pictures.template import Template, TemplatePictureFactory, load_template
from pibooth.utils import LOGGER

if TYPE_CHECKING:
    from pibooth.config.parser import PiConfigParser
    from pibooth.pictures.factory import PictureFactory


class TemplatePlugin:
    """Core plugin setting up :py:class:`TemplatePictureFactory` when a
    picture layout template is configured (see ``[PICTURE][template]``).
    """

    name = "pibooth-core:template"

    def __init__(self, plugin_manager: Any) -> None:
        self._pm = plugin_manager
        self._cache_path: str | None = None
        self._cache_mtime: float | None = None
        self._cache_template: Template | None = None

    def _get_template(self, path: str) -> Template | None:
        """Return the parsed template, cached and reloaded when the file
        on disk changes so that web-UI edits are picked up without restart.
        """
        try:
            mtime = osp.getmtime(path)
        except OSError as ex:
            LOGGER.error("Cannot access picture template '%s': %s", path, ex)
            return None

        if self._cache_template is None or self._cache_path != path or self._cache_mtime != mtime:
            try:
                self._cache_template = load_template(path)
            except ValueError as ex:
                LOGGER.error("Cannot load picture template '%s': %s", path, ex)
                self._cache_template = None
            self._cache_path = path
            self._cache_mtime = mtime

        return self._cache_template

    @pibooth.hookimpl
    def pibooth_setup_picture_factory(
        self, cfg: "PiConfigParser", opt_index: Any, factory: "PictureFactory"
    ) -> "PictureFactory | None":
        """Setup :py:class:`TemplatePictureFactory` if a template path is given."""
        path = cfg.getpath("PICTURE", "template")
        if not path:
            return None

        template = self._get_template(path)
        if template is None:
            return None  # Fall back to the default grid factory

        orientation = cfg.get("PICTURE", "orientation")
        if orientation == AUTO:
            orientation = template.get_best_orientation(factory._images)

        try:
            return TemplatePictureFactory(template, orientation, *factory._images, assets_dir=cfg.join_path("assets"))
        except ValueError as ex:
            LOGGER.error("Cannot use picture template '%s': %s", path, ex)
            return None
