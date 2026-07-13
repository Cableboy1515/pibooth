# -*- coding: utf-8 -*-

import json
import os
import os.path as osp
import pickle

from pibooth.utils import LOGGER


class Counters:

    def __init__(self, filename='', **kwargs):
        self.data = kwargs.copy()
        self.default = kwargs
        self.filename = osp.abspath(osp.expanduser(filename))
        if osp.isfile(self.filename):
            self.load()
        else:
            self._migrate_pickle()

    def __str__(self):
        return ", ".join(f"{key}:{value}" for key, value in self.data.items())

    def __iter__(self):
        """Iterate over counters names.
        """
        return iter(self.data)

    def __getitem__(self, name):
        """Get value from counter name.
        """
        return self.__getattr__(name)

    def __getattr__(self, name):
        """Called only when an attribute does not exist.
        """
        if name not in self.data:
            raise AttributeError(f"No counter with name '{name}'")
        return self.data[name]

    def __setattr__(self, name, value):
        """Called each time an attribute is set.
        """
        if name != 'data' and name in self.data:
            self.data[name] = value
            self.save()
        else:
            super().__setattr__(name, value)

    def names(self):
        """Return the list of counters.
        """
        return list(self.data)

    def _migrate_pickle(self):
        """Migrate counters from the legacy pickle format (pre-JSON versions).

        The old pickle file is loaded once, saved as JSON and renamed with a
        '.bak' suffix so it is never loaded again.
        """
        legacy = osp.splitext(self.filename)[0] + '.pickle'
        if not osp.isfile(legacy):
            return
        try:
            with open(legacy, 'rb') as fp:
                self.data.update(pickle.load(fp))
            self.save()
            os.replace(legacy, legacy + '.bak')
            LOGGER.info("Migrated counters from '%s' to '%s'", legacy, self.filename)
        except Exception as ex:
            LOGGER.warning("Could not migrate legacy counters file '%s': %s", legacy, ex)

    def load(self):
        """Load the saved counters.
        """
        with open(self.filename, encoding='utf-8') as fp:
            self.data.update(json.load(fp))

    def reset(self):
        """Reset all counters.
        """
        self.data = self.default.copy()
        self.save()

    def save(self):
        """Save the current counters in a file.
        """
        with open(self.filename, 'w', encoding='utf-8') as fp:
            json.dump(self.data, fp, indent=2)
