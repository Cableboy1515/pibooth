"""Pibooth base states."""

import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pibooth.utils import LOGGER, BlockConsoleHandler

if TYPE_CHECKING:
    from pibooth.config.parser import PiConfigParser
    from pibooth.view.window import PiWindow


class StateMachine:
    def __init__(
        self, plugins_manager: Any, configuration: "PiConfigParser", application: Any, window: "PiWindow"
    ) -> None:
        self.states: set[str] = set()
        self.failsafe_state: str | None = None
        self.active_state: str | None = None

        # Share the application to manage between states
        self.app = application
        self.win = window
        self.cfg = configuration
        self.pm = plugins_manager

        self._start_time = time.time()

    def add_state(self, name: str) -> None:
        """Add a state to the internal dictionary."""
        self.states.add(name)

    def add_failsafe_state(self, name: str) -> None:
        """Add a state that will be call in case of exception."""
        self.failsafe_state = name
        self.states.add(name)

    def remove_state(self, name: str) -> None:
        """Remove a state to the internal dictionary."""
        self.states.discard(name)
        if name == self.failsafe_state:
            self.failsafe_state = None

    def process(self, events: Sequence[Any]) -> None:
        """Let the current state do it's thing."""
        # Only continue if there is an active state
        if self.active_state is None:
            return

        try:
            # Perform the actions of the active state
            hook = getattr(self.pm.hook, f"state_{self.active_state}_do")
            hook(cfg=self.cfg, app=self.app, win=self.win, events=events)

            # Check conditions to activate the next state
            hook = getattr(self.pm.hook, f"state_{self.active_state}_validate")
            new_state_name = hook(cfg=self.cfg, app=self.app, win=self.win, events=events)
        except Exception as ex:
            if self.failsafe_state and self.active_state != self.failsafe_state:
                LOGGER.error(str(ex))
                LOGGER.debug("Back to failsafe state due to error:", exc_info=True)
                new_state_name = self.failsafe_state
            else:
                raise

        if new_state_name is not None:
            self.set_state(new_state_name)

    def set_state(self, state_name: str) -> None:
        """Change state machine's active state"""
        try:
            # Perform any exit actions of the current state
            if self.active_state is not None:
                hook = getattr(self.pm.hook, f"state_{self.active_state}_exit")
                hook(cfg=self.cfg, app=self.app, win=self.win)
                BlockConsoleHandler.dedent()
                LOGGER.debug("took %0.3f seconds", time.time() - self._start_time)
        except Exception as ex:
            if self.failsafe_state and self.active_state != self.failsafe_state:
                LOGGER.error(str(ex))
                LOGGER.debug("Back to failsafe state due to error:", exc_info=True)
                state_name = self.failsafe_state
            else:
                raise

        if state_name not in self.states:
            raise ValueError(f'"{state_name}" not in registered states...')

        # Switch to the new state and perform its entry actions
        BlockConsoleHandler.indent()
        self._start_time = time.time()
        LOGGER.debug("Activate state '%s'", state_name)
        self.active_state = state_name

        try:
            hook = getattr(self.pm.hook, f"state_{self.active_state}_enter")
            hook(cfg=self.cfg, app=self.app, win=self.win)
        except Exception as ex:
            if self.failsafe_state and self.active_state != self.failsafe_state:
                LOGGER.error(str(ex))
                LOGGER.debug("Back to failsafe state due to error:", exc_info=True)
                self.set_state(self.failsafe_state)
            else:
                raise
