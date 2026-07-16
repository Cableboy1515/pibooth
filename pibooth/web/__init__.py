"""Pibooth web configuration interface.

This package provides a small web application to edit the pibooth
configuration from any device of the local network (phone, laptop, ...).

The Flask application itself lives in :py:mod:`pibooth.web.server` and is
imported lazily so that ``flask`` remains an optional runtime dependency
for the rest of the application.
"""

import pygame

#: Event posted in the pygame event queue when the configuration
#: has been changed from the web interface (see pibooth.booth main loop).
CONFIG_CHANGED = pygame.USEREVENT + 3
