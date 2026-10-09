"""The integration under a real Home Assistant core: set-up, reload and the write path.

The ordinary suite never imports Home Assistant, so three things it cannot see
live here: a set-up that fails must close its Modbus socket, since the controller
has a single client slot and an open socket keeps the entry in setup_retry for
good; a reload must not build a second client before the first is closed; and
the write path, on by default since 0.14.0, has never been driven from the
service call down to the register. The menu counter is the fourth: the tries it
counts must outlive a reload, or every reload walks the panel again.

Runs against the Home Assistant core that pytest-homeassistant-custom-component
provides and skips wherever that is not installed, so the ordinary suite and CI
are untouched. It runs in a virtual environment of its own, since that plugin
pins Home Assistant and its dependencies:

    pip install pytest-homeassistant-custom-component
    python -m pytest -o asyncio_mode=auto tests/test_homeassistant.py

Both clients are stand-ins: nothing here opens a socket, and the plugin blocks
sockets anyway. The Modbus stand-in offers the integration's own client surface
(async_read, async_read_one, async_probe, async_write, async_close,
async_shutdown, connected) and writes down what was asked of it, in order, so a
test can read back that one client was open at a time.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

# The ordinary suite loads the modules one by one into a stub package and never
# needs the repository root on the path; Home Assistant's loader does, because it
# finds the integration by importing custom_components.
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    async_fire_time_changed,
)

import custom_components.ctc_ecozenith as integration  # noqa: E402
from custom_components.ctc_ecozenith import modbus_api  # noqa: E402
from custom_components.ctc_ecozenith.catalogue import MenuReading  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_FAST_INTERVAL,
    CONF_IDENTITY,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_WEB_PORT,
    CONTROL_KEEPALIVE_SECONDS,
    DOMAIN,
)
from custom_components.ctc_ecozenith.identity import Identity  # noqa: E402
from custom_components.ctc_ecozenith.modbus_api import (  # noqa: E402
    CtcModbusError,
    CtcModbusTransportError,
)
from custom_components.ctc_ecozenith.web_api import CtcWebError  # noqa: E402

#: A documentation address (RFC 5737), never a house's.
HOST = "192.0.2.55"
MODEL = "EcoZenith i255"

#: Everything the display can say about itself, made up and complete, so set-up
#: has no gap to fill and never asks the stand-in panel for the identity.
IDENTITY = {
    "serial": "7331 2412 0001",
    "mac": "02:00:00:00:00:01",
    "display_firmware": "2024-03-01",
    "bootloader": "1.0",
    "heatpump_model": "EcoAir 720M",
    "heatpump_firmware": "2.1",
}

#: What the stand-in controller answers, by register. Anything not listed reads
#: 0, which is CTC's own answer for hardware that is not fitted. The room
#: setpoint in the stored block is what the control number mirrors while no
#: override is in force.
READINGS: dict[int, int] = {
    62000: 72,  # outdoor 7.2 °C
    62006: 352,  # radiator water 35.2 °C
    61509: 205,  # stored room setpoint 20.5 °C, mirrored by control register 1010
    61572: 900,  # stored max rps 90.0
}

#: Everything the Modbus stand-ins did, in order, across every instance.
EVENTS: list[tuple[str, int]] = []


class FakeModbus:
    """Stands in for CtcModbusClient: the whole client surface, nothing on the wire.

    Opens on the first transaction and closes on async_close, as the real client
    does, and every instance writes its doings into EVENTS, so a test can check
    that no two clients were ever open at once.
    """

    instances: list["FakeModbus"] = []
    #: Whether the controller is there at all. A subclass says no.
    answers = True

    def __init__(self, host: str, port: int = 502, slave: int = 1) -> None:
        self.host = host
        self.port = port
        self.slave = slave
        self.registers = dict(READINGS)
        self.reads: list[tuple[int, int]] = []
        self.writes: list[tuple[int, int]] = []
        self.closes = 0
        self.connected = False
        self.shut_down = False
        self.number = len(FakeModbus.instances) + 1
        FakeModbus.instances.append(self)
        EVENTS.append(("created", self.number))

    def _transaction(self) -> None:
        if self.shut_down:
            # As the real client: a shut-down client never connects again.
            raise CtcModbusTransportError(f"client #{self.number} has been shut down")
        if not self.answers:
            raise CtcModbusError(f"could not connect to {self.host}:{self.port}")
        if not self.connected:
            self.connected = True
            EVENTS.append(("opened", self.number))

    async def async_read(self, address: int, count: int = 1) -> list[int]:
        self._transaction()
        self.reads.append((address, count))
        return [self.registers.get(address + offset, 0) for offset in range(count)]

    async def async_read_one(self, address: int, count: int = 1) -> list[int] | None:
        try:
            return await self.async_read(address, count)
        except CtcModbusError:
            return None

    async def async_probe(self) -> bool:
        return bool(await self.async_read(62000, 1))

    async def async_write(self, address: int, value: int) -> None:
        self._transaction()
        self.writes.append((address, value))
        EVENTS.append(("write", self.number))

    async def async_close(self) -> None:
        self.closes += 1
        self.connected = False
        EVENTS.append(("closed", self.number))

    async def async_shutdown(self) -> None:
        """The entry's final close: counted as a close, and the client is done."""
        self.shut_down = True
        await self.async_close()


class DeadModbus(FakeModbus):
    """A controller that is away: every transaction fails to connect."""

    answers = False


class FakePanel:
    """Stands in for CtcWebClient: a display that is never reached.

    Set-up itself never needs the display when no pages are chosen and the
    identity is complete, and the menu reading is stubbed one level up, so every
    method here refuses: a test that lands in one has strayed on to the network.
    """

    def __init__(self, session, host: str, port: int = 80, language: int = 1) -> None:
        self.host = host
        self.port = port
        self.panel = asyncio.Lock()

    def __getattr__(self, name: str):
        if name.startswith("async_"):

            async def refuse(*args, **kwargs):
                raise CtcWebError(f"{name}: no display in this test")

            return refuse
        raise AttributeError(name)


@pytest.fixture(scope="module", autouse=True)
def _needs_auto_asyncio_mode(request):
    """Say why rather than fail obscurely when the plugin's async fixtures cannot run."""
    config = request.config
    mode = config.getoption("asyncio_mode", None) or config.getini("asyncio_mode")
    if mode != "auto":
        pytest.skip("run with -o asyncio_mode=auto: the Home Assistant fixtures are async")


@pytest.fixture(autouse=True)
def stubs(hass, enable_custom_integrations):
    """Everything the integration touches outside the controller, stubbed.

    Also starts every run from a clean slate: the counters the integration
    keeps across reloads are module globals, and a test about one of them must
    not inherit another test's tally.
    """
    # Declared dependencies that need the browser frontend build to set up.
    hass.config.components.update({"frontend", "http", "lovelace", "network"})
    FakeModbus.instances.clear()
    EVENTS.clear()
    integration._MENU_TRIES.clear()
    integration._MENU_LAST.clear()
    integration._WALKED.clear()
    integration._FAILURES.clear()
    closed_at = getattr(modbus_api, "_CLOSED_AT", None)
    if isinstance(closed_at, dict):
        closed_at.clear()

    # A reading that found nothing, as the real walk answers when the display
    # would not give the menu up.
    discover = AsyncMock(return_value=MenuReading())
    with (
        patch(f"custom_components.{DOMAIN}.CtcModbusClient", FakeModbus),
        patch(f"custom_components.{DOMAIN}.config_flow.CtcModbusClient", FakeModbus),
        patch(f"custom_components.{DOMAIN}.CtcWebClient", FakePanel),
        patch(f"custom_components.{DOMAIN}.dashboard.async_register", AsyncMock()),
        patch(f"custom_components.{DOMAIN}.dashboard.async_announce_change"),
        patch(f"custom_components.{DOMAIN}.dashboard.async_unregister"),
        patch(f"custom_components.{DOMAIN}.async_setup_stats", AsyncMock()),
        patch(f"custom_components.{DOMAIN}.async_stop_stats", AsyncMock()),
        patch(
            f"custom_components.{DOMAIN}.async_read_identity",
            AsyncMock(return_value=Identity()),
        ),
        patch(
            f"custom_components.{DOMAIN}.async_read_identity_via_panel",
            AsyncMock(return_value=Identity()),
        ),
        patch(f"custom_components.{DOMAIN}.async_latest_release", AsyncMock(return_value=None)),
        patch(f"custom_components.{DOMAIN}.async_discover_pages", discover),
    ):
        yield SimpleNamespace(discover=discover)


async def _set_up(hass, **options) -> MockConfigEntry:
    """One i255 with control on, no display pages and a menu this version has read.

    The menu version is stamped with the running version, so the catch-up task
    has nothing owed and ends at once; a test about the menu passes an older one.
    """
    version = str((await async_get_integration(hass, DOMAIN)).version)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{HOST}",
        title=f"{MODEL} ({HOST})",
        data={
            CONF_HOST: HOST,
            CONF_MODBUS_PORT: 502,
            CONF_WEB_PORT: 80,
            CONF_SLAVE: 1,
            "model": MODEL,
        },
        options={CONF_MENU_VERSION: version, CONF_IDENTITY: IDENTITY, **options},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _let_the_background_run(hass) -> None:
    """Give the catch-up task its turn.

    It is a background task, which async_block_till_done does not wait for, and
    waiting for it to finish would wait out the pause it sleeps through.
    """
    await hass.async_block_till_done()
    for _ in range(20):
        await asyncio.sleep(0)


async def _advance(hass, seconds: float) -> None:
    """Fire every timer due within ``seconds``, the catch-up task's sleep included."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await _let_the_background_run(hass)


def _entity_id(hass, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{DOMAIN}_{HOST}_{key}")
    assert entity_id, f"no {platform} entity for {key}"
    return entity_id


def _open_at_once(events: list[tuple[str, int]]) -> int:
    """The most clients that were open at the same moment."""
    open_now: set[int] = set()
    most = 0
    for what, number in events:
        if what == "opened":
            open_now.add(number)
        elif what == "closed":
            open_now.discard(number)
        most = max(most, len(open_now))
    return most


# ------------------------------------------------------------------- set-up


async def test_a_controller_that_is_away_at_set_up_is_retried_with_its_socket_closed(
    hass, monkeypatch
):
    """The trap from the first weeks in service.

    A set-up attempt that failed without closing its socket kept the
    controller's only client slot, so every retry failed too and the entry sat
    in setup_retry until somebody restarted Home Assistant. The failed attempt
    has to close its one client, exactly once, and the next attempt has to work
    with a fresh one.
    """
    with patch(f"custom_components.{DOMAIN}.CtcModbusClient", DeadModbus):
        entry = await _set_up(hass)
        assert entry.state is ConfigEntryState.SETUP_RETRY
        (client,) = FakeModbus.instances
        # The poll round lets the line go when nothing answered, and the set-up
        # path closes once more on its way out: both are the same client, and
        # a close on a client already dropped is a no-op. What matters is that
        # it was closed, and that no second client is built before it was.
        assert client.closes >= 1
        assert not client.connected

        # The controller comes back, and Home Assistant's own retry finds it.
        # The retry is a background task of Home Assistant's own, so it has to
        # be waited for by name.
        monkeypatch.setattr(DeadModbus, "answers", True)
        await _advance(hass, 61)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    first, second = FakeModbus.instances
    assert first.closes >= 1
    assert second.connected
    assert _open_at_once(EVENTS) == 1
    outdoor = hass.states.get(_entity_id(hass, "sensor", "outdoor_temp"))
    assert outdoor.state == "7.2"


async def test_writing_the_options_reloads_the_entry_with_one_client_at_a_time(hass):
    """A reload is an unload and a set-up in the same breath.

    The controller keeps believing in its old session for a while after a
    reset, so the old client must be closed before a new one is even built;
    two knocking at once is how a pump was lost for forty minutes.
    """
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    (first,) = FakeModbus.instances
    assert first.connected

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_FAST_INTERVAL: 45}
    )
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    first, second = FakeModbus.instances
    assert first.closes == 1
    assert not first.connected
    assert second.connected
    assert EVENTS.index(("closed", 1)) < EVENTS.index(("created", 2))
    assert _open_at_once(EVENTS) == 1
    # And the reload is what took the new option on.
    assert entry.runtime_data.modbus.update_interval == timedelta(seconds=45)


# --------------------------------------------------------------- the write path


async def test_a_setpoint_is_written_kept_alive_and_released(hass):
    """From the service call to the register, and back again.

    The number entity scales its value into the raw register value and hands
    it to the control manager, whose one way out is the client's async_write:
    function code 16, one register, as CTC specifies. The 1000 block is
    forgotten by the controller about five minutes after the last write, so
    the manager writes it again every minute, and "Släpp all styrning" simply
    stops writing: nothing is sent, the controller lets go by itself.
    """
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    (client,) = FakeModbus.instances
    number = _entity_id(hass, "number", "ctl_room_setpoint_1")

    # Idle, the number mirrors the unit's own stored setpoint.
    state = hass.states.get(number)
    assert state.state == "20.5"
    assert state.attributes["styrning aktiv"] == "nej"
    assert client.writes == []

    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 21.5}, blocking=True
    )
    assert client.writes == [(1010, 215)]
    state = hass.states.get(number)
    assert state.state == "21.5"
    assert state.attributes["styrning aktiv"] == "ja"

    # A minute on, the same value goes out again without anyone asking.
    await _advance(hass, CONTROL_KEEPALIVE_SECONDS + 1)
    assert client.writes == [(1010, 215), (1010, 215)]

    await hass.services.async_call(
        "button", "press", {"entity_id": _entity_id(hass, "button", "release_control")},
        blocking=True,
    )
    state = hass.states.get(number)
    assert state.state == "20.5"
    assert state.attributes["styrning aktiv"] == "nej"

    # Released means silent: the next minute brings no write at all.
    await _advance(hass, 2 * CONTROL_KEEPALIVE_SECONDS + 2)
    assert client.writes == [(1010, 215), (1010, 215)]
    # Nothing else was ever written, and nothing was written outside the 1000 block.
    assert all(1000 <= address < 1100 for address, _ in client.writes)


# ------------------------------------------------------------------ the menu


async def test_the_menu_tries_are_counted_across_reloads(hass, stubs):
    """Three tries in total, not three per reload.

    A new version reads the display's menu again. The display may be busy, so
    the reading is tried a few times with a pause between, and both the count
    and the pause live outside the entry: writing the options reloads it, and
    a counter that started over at every reload would walk the panel through
    its menus endlessly, while a pause that lived in a sleeping task would be
    skipped by the very reload that ends it.
    """
    entry = await _set_up(hass, **{CONF_MENU_VERSION: "0.9.0"})
    await _let_the_background_run(hass)
    assert integration._MENU_TRIES[entry.entry_id] == 1
    assert stubs.discover.await_count == 1

    # A reload keeps the count, and keeps the pause: the panel is not walked again at once.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await _let_the_background_run(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert integration._MENU_TRIES[entry.entry_id] == 1
    assert stubs.discover.await_count == 1

    pause = integration.MENU_READ_RETRY.total_seconds() + 60
    await _advance(hass, pause)
    assert integration._MENU_TRIES[entry.entry_id] == 2
    assert stubs.discover.await_count == 2

    await _advance(hass, pause)
    assert integration._MENU_TRIES[entry.entry_id] == 3
    assert stubs.discover.await_count == 3

    # The tries are spent. Neither a pause nor a reload buys another.
    await _advance(hass, pause)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await _let_the_background_run(hass)
    assert integration._MENU_TRIES[entry.entry_id] == 3
    assert stubs.discover.await_count == 3
