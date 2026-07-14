import os.path as osp
import pickle

import pytest

from pibooth.counters import Counters


def test_pickle_migration(tmpdir):
    legacy = tmpdir.join("data.pickle")
    with open(str(legacy), "wb") as fp:
        pickle.dump({"nbr_printed": 42}, fp, pickle.HIGHEST_PROTOCOL)

    counters = Counters(str(tmpdir.join("data.json")), nbr_printed=0)
    assert counters.nbr_printed == 42
    assert osp.isfile(str(tmpdir.join("data.json")))
    assert not osp.isfile(str(legacy))
    assert osp.isfile(str(legacy) + ".bak")

    # Migration happens only once, the JSON file is now the reference
    counters.nbr_printed = 7
    reloaded = Counters(str(tmpdir.join("data.json")), nbr_printed=0)
    assert reloaded.nbr_printed == 7


def test_iter(counters):
    for name in counters:
        assert name


def test_getitem(counters):
    assert counters["nbr_printed"] == 0


def test_names(counters):
    assert len(counters.names()) == 1
    assert "nbr_printed" in counters.names()


def test_set(counters):
    assert counters.nbr_printed == 0
    counters.nbr_printed += 2
    assert counters.nbr_printed == 2


def test_reset(counters):
    counters.nbr_printed = 5
    assert counters.nbr_printed == 5
    counters.reset()
    assert counters.nbr_printed == 0


def test_invalid_counter(counters):
    with pytest.raises(AttributeError):
        counters.invalid  # noqa: B018 -- attribute access itself raises


def test_save(counters):
    counters.nbr_printed = 5
    counters.data["nbr_printed"] = 0
    assert counters.nbr_printed == 0
    counters.load()
    assert counters.nbr_printed == 5
    counters.reset()
    counters.load()
    assert counters.nbr_printed == 0
