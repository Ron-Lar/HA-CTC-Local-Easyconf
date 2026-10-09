"""The harvest store is written out before a write of the options that reloads the entry.

The last harvest goes to its store with a delay of a few seconds. At the
first start after an update the menu is read again, which ends in a write of
the options and a reload, and the first harvest, queued on the panel lock
while the menu was read, walks just before that write. The entry the reload
brings reads the store at set-up; a harvest ten seconds short of being there
would have cost the panel a whole walk more, the one the new entry makes at
once when it finds nothing. So the memory can be told to write what is
waiting down now, and the catch-up task tells it, under the lock, before the
write. The run under a real core is in test_homeassistant_first_start.py;
these hold the rule itself and the order in the source.
"""

from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timezone

import pytest

from conftest import load

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def harvest():
    return load("harvest")


class DeferringStore:
    """A Store whose delayed write happens when the test says, as Home Assistant's does."""

    def __init__(self) -> None:
        self.waiting = None
        self.saved: list[dict] = []

    def async_delay_save(self, data_func, delay) -> None:
        self.waiting = (data_func, delay)

    async def async_save(self, data) -> None:
        # Given the data outright, the store drops the delayed write.
        self.waiting = None
        self.saved.append(data)

    def write_now(self) -> None:
        data_func, _ = self.waiting
        self.waiting = None
        self.saved.append(data_func())


def _remember(memory) -> bool:
    return memory.remember({"p22_avgiven_varme": 11.1}, {"p22_avgiven_varme": NOW}, NOW)


def test_a_flush_writes_the_waiting_harvest_down_once(harvest):
    store = DeferringStore()
    memory = harvest.HarvestMemory(store)
    assert _remember(memory)
    assert store.saved == [], "skörden väntar ut sin fördröjning"

    assert run(memory.async_flush())
    assert len(store.saved) == 1
    assert store.saved[0]["values"] == {"p22_avgiven_varme": 11.1}
    assert store.waiting is None, "den fördröjda skrivningen är borta"
    assert not run(memory.async_flush()), "inget väntar längre"
    assert len(store.saved) == 1


def test_nothing_is_flushed_once_the_store_has_written_by_itself(harvest):
    store = DeferringStore()
    memory = harvest.HarvestMemory(store)
    assert _remember(memory)
    store.write_now()
    assert not run(memory.async_flush()), "storen hann själv"
    assert len(store.saved) == 1


def test_nothing_is_flushed_where_nothing_was_remembered(harvest):
    memory = harvest.HarvestMemory(DeferringStore())
    assert not run(memory.async_flush())
    # A refresh that ran no harvest is nothing to write either.
    assert not memory.remember({}, {}, None)
    assert not run(memory.async_flush())


def test_a_flush_that_fails_is_swallowed_and_not_retried(harvest):
    class Broken(DeferringStore):
        async def async_save(self, data) -> None:
            raise OSError("disk")

    memory = harvest.HarvestMemory(Broken())
    assert _remember(memory)
    assert not run(memory.async_flush())
    assert not run(memory.async_flush())


def test_the_catch_up_writes_the_harvest_out_under_the_lock_before_the_write_that_reloads():
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    catch_up = source.split("async def _async_catch_up")[1].split("\ndef ")[0]
    writing = catch_up.split("if changed:")[1]
    reloading = writing.split("return")[0]
    assert "async with client.panel:" in reloading
    assert reloading.index("async with client.panel:") < reloading.index("async_flush()")
    assert reloading.index("async_flush()") < reloading.index("async_update_entry(")
    # And the memory is on the runtime, from the set-up that made it.
    setup = source.split("async def async_setup_entry")[1].split("\nasync def ")[0]
    assert "runtime.harvest_memory = memory" in setup
