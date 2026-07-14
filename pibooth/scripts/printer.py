"""Script to display the configuration of the printer."""

import json
import os.path as osp
import sys
from typing import Any

import cups

from pibooth.config import PiConfigParser
from pibooth.plugins import create_plugin_manager
from pibooth.utils import LOGGER, configure_logging, get_config_dir


def main() -> None:
    """Application entry point."""
    configure_logging()
    plugin_manager = create_plugin_manager()
    config = PiConfigParser(osp.join(get_config_dir(), "pibooth.cfg"), plugin_manager)

    conn = cups.Connection()
    config_name = config.get("PRINTER", "printer_name")
    name: str | None = config_name

    if not config_name or config_name.lower() == "default":
        name = conn.getDefault()
        if not name and conn.getPrinters():
            name = list(conn.getPrinters().keys())[0]  # Take first one
    elif config_name not in conn.getPrinters():
        name = None

    if not name:
        if not config_name or config_name.lower() == "default":
            LOGGER.warning("No printer configured in CUPS (see http://localhost:631)")
            return
        else:
            LOGGER.warning("No printer named '%s' in CUPS (see http://localhost:631)", config_name)
            return
    else:
        LOGGER.info("Connected to printer '%s'", name)

    f = conn.getPPD(name)
    ppd = cups.PPD(f)
    groups = ppd.optionGroups
    options = []
    for group in groups:
        group_name = f"{group.name} - {group.text}"
        for opt in group.options:
            option: dict[str, Any] = {"group": group_name}
            values = [x["choice"] for x in opt.choices]
            texts = [x["text"] for x in opt.choices]
            option["keyword"] = opt.keyword
            option["value"] = opt.defchoice
            option["description"] = opt.text
            if values != texts:
                option["choices"] = {v: texts[values.index(v)] for v in values}
            else:
                option["choices"] = values
            options.append(option)

    if "--json" in sys.argv:
        print(json.dumps({option["keyword"]: option["value"] for option in options}))
    else:
        for option in options:
            print("{} = {}".format(option["keyword"], option["value"]))
            print("     Description: {}".format(option["description"]))
            if isinstance(option["choices"], dict):
                choices = [f"{value} = {descr}" for value, descr in option["choices"].items()]
                print(f"     Choices:     {choices[0]}")
                for choice in choices[1:]:
                    print(f"                  {choice}")
            else:
                print("     Choices:     {}".format(", ".join(option["choices"])))

            print()


if __name__ == "__main__":
    main()
