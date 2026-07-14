"""Script to display/update counters."""

import json
import os.path as osp
import sys

from pibooth.config import PiConfigParser
from pibooth.counters import Counters
from pibooth.plugins import create_plugin_manager
from pibooth.utils import configure_logging, get_config_dir


def main():
    """Application entry point."""
    configure_logging()
    plugin_manager = create_plugin_manager()
    config = PiConfigParser(osp.join(get_config_dir(), "pibooth.cfg"), plugin_manager)

    counters = Counters(
        config.join_path("counters.json"),
        taken=0,
        printed=0,
        forgotten=0,
        remaining_duplicates=config.getint("PRINTER", "max_duplicates"),
    )

    if "--json" in sys.argv:
        print(json.dumps(counters.data))
    elif "--update" in sys.argv:
        try:
            print("\nUpdating counters (current value in square bracket):\n")
            for name in counters:
                value = input(f" -> {name.capitalize():.<18} [{counters[name]:>4}] : ")
                if value.strip():
                    setattr(counters, name, int(value))
        except KeyboardInterrupt:
            pass
        print()
    else:
        print("\nListing current counters:\n")
        for name in counters:
            print(f" -> {name.capitalize():.<25} : {counters[name]:>4}")
        print()


if __name__ == "__main__":
    main()
