"""What an earlier version switched off is switched on again by this one (R21).

Home Assistant reads ``entity_registry_enabled_default`` only when an entity is
first registered, so an installation from before 0.14.0 kept every entity that
version created switched off, and only the two houses had theirs switched on,
by hand. Each platform now switches on, before adding them, the entities whose
registry entry the integration switched off, and leaves alone what somebody
switched off. Here the rule runs against a registry stand-in, in both worlds,
and the call sites are held in the source: every platform, every batch the
sensor platform adds, both coordinators with the entry, and the identity read
at every start. The run under a real core is test_homeassistant_switch_on.py.
"""

from __future__ import annotations

import enum
import logging
import pathlib
from types import SimpleNamespace

import pytest

import ha_stub
from conftest import load

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"
PLATFORMS = ("sensor", "binary_sensor", "number", "select", "button", "event")


class Disabler(enum.StrEnum):
    """Home Assistant's RegistryEntryDisabler, as far as this needs it."""

    INTEGRATION = "integration"
    USER = "user"
    CONFIG_ENTRY = "config_entry"


class FakeRegistry:
    def __init__(self, items) -> None:
        self.items = {item.entity_id: item for item in items}
        self.updates: list[tuple[str, object]] = []

    def async_update_entity(self, entity_id, *, disabled_by):
        self.updates.append((entity_id, disabled_by))
        self.items[entity_id].disabled_by = disabled_by


def _item(entity_id: str, unique_id: str, disabled_by=None, entry_id: str = "entry"):
    return SimpleNamespace(
        entity_id=entity_id,
        unique_id=unique_id,
        domain=entity_id.split(".")[0],
        disabled_by=disabled_by,
        config_entry_id=entry_id,
    )


def _entity(unique_id: str, enabled_default: bool = True):
    return SimpleNamespace(unique_id=unique_id, entity_registry_enabled_default=enabled_default)


@pytest.fixture()
def switch(monkeypatch):
    """The entity module with the registry it asks for replaced by a stand-in."""
    ha_stub.install()
    import homeassistant.helpers.entity_registry as er

    module = load("entity")
    holder = SimpleNamespace(registry=FakeRegistry([]))
    monkeypatch.setattr(er, "async_get", lambda hass: holder.registry)
    monkeypatch.setattr(
        er,
        "async_entries_for_config_entry",
        lambda registry, entry_id: [
            item for item in registry.items.values() if item.config_entry_id == entry_id
        ],
    )
    if not hasattr(er, "RegistryEntryDisabler"):
        monkeypatch.setattr(er, "RegistryEntryDisabler", Disabler, raising=False)
    disabler = er.RegistryEntryDisabler

    def run(items, platform, entities):
        holder.registry = FakeRegistry(items)
        count = module.async_switch_on_new_defaults(
            None, SimpleNamespace(entry_id="entry"), platform, entities
        )
        return count, holder.registry

    run.disabler = disabler
    return run


# ------------------------------------------------------------------ the rule


def test_what_the_integration_switched_off_is_switched_on_and_the_users_choice_stands(
    switch, caplog
):
    d = switch.disabler
    items = [
        _item("sensor.ctc_old_default", "ctc_ecozenith_pump_hp1_fan", d.INTEGRATION),
        _item("sensor.ctc_by_hand", "ctc_ecozenith_pump_hp1_rps", d.USER),
        _item("sensor.ctc_entry_off", "ctc_ecozenith_pump_hp1_in", d.CONFIG_ENTRY),
        _item("sensor.ctc_on", "ctc_ecozenith_pump_hp1_out"),
    ]
    entities = [
        _entity("ctc_ecozenith_pump_hp1_fan"),
        _entity("ctc_ecozenith_pump_hp1_rps"),
        _entity("ctc_ecozenith_pump_hp1_in"),
        _entity("ctc_ecozenith_pump_hp1_out"),
    ]
    with caplog.at_level(logging.WARNING):
        count, registry = switch(items, "sensor", entities)
    assert count == 1
    assert registry.updates == [("sensor.ctc_old_default", None)]
    assert registry.items["sensor.ctc_by_hand"].disabled_by == d.USER, "användarens val står"
    assert registry.items["sensor.ctc_entry_off"].disabled_by == d.CONFIG_ENTRY
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "en rad, inte en per entitet"
    assert "Switched on 1 sensor entities" in warnings[0].getMessage()
    assert "30 seconds" in warnings[0].getMessage(), "omladdningen som följer sägs i förväg"


def test_an_entity_this_version_does_not_add_stays_switched_off(switch):
    """An orphan switched on would only stand in the registry unavailable."""
    d = switch.disabler
    items = [_item("sensor.ctc_gone", "ctc_ecozenith_pump_p30_gone", d.INTEGRATION)]
    count, registry = switch(items, "sensor", [_entity("ctc_ecozenith_pump_hp1_fan")])
    assert count == 0
    assert registry.updates == []


def test_an_entity_whose_default_is_still_off_is_left_off(switch):
    d = switch.disabler
    items = [_item("sensor.ctc_quiet", "ctc_ecozenith_pump_quiet", d.INTEGRATION)]
    count, registry = switch(items, "sensor", [_entity("ctc_ecozenith_pump_quiet", enabled_default=False)])
    assert count == 0
    assert registry.updates == []


def test_each_platform_switches_on_its_own_and_nothing_else(switch):
    d = switch.disabler
    items = [
        _item("number.ctc_room", "ctc_ecozenith_pump_ctl_room_setpoint_1", d.INTEGRATION),
        _item("select.ctc_dhw", "ctc_ecozenith_pump_ctl_dhw_mode", d.INTEGRATION),
    ]
    entities = [_entity("ctc_ecozenith_pump_ctl_room_setpoint_1"), _entity("ctc_ecozenith_pump_ctl_dhw_mode")]
    count, registry = switch(items, "number", entities)
    assert count == 1
    assert registry.updates == [("number.ctc_room", None)]
    assert registry.items["select.ctc_dhw"].disabled_by == d.INTEGRATION


def test_another_entrys_entities_are_not_looked_at(switch):
    d = switch.disabler
    items = [_item("sensor.other_pump", "ctc_ecozenith_pump_hp1_fan", d.INTEGRATION, entry_id="other")]
    count, registry = switch(items, "sensor", [_entity("ctc_ecozenith_pump_hp1_fan")])
    assert count == 0
    assert registry.updates == []


def test_nothing_to_switch_on_says_nothing(switch, caplog):
    items = [_item("sensor.ctc_on", "ctc_ecozenith_pump_hp1_out")]
    with caplog.at_level(logging.DEBUG):
        count, registry = switch(items, "sensor", [_entity("ctc_ecozenith_pump_hp1_out")])
    assert count == 0
    assert registry.updates == []
    assert not [r for r in caplog.records if r.name.endswith(".entity")]


# ------------------------------------------------------------------ in code


def _setup_of(platform: str) -> str:
    source = (COMPONENT / f"{platform}.py").read_text(encoding="utf-8")
    return source.split("async def async_setup_entry")[1].split("\nclass ")[0]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_platform_switches_on_before_it_adds(platform):
    setup = _setup_of(platform)
    call = f'async_switch_on_new_defaults(hass, entry, "{platform}", '
    assert call in setup, f"{platform} slår inte på det som stängdes av"
    # Before the entities are added, so they come up in this very set-up.
    assert setup.index(call) < setup.index("async_add_entities(")


def test_every_batch_the_sensor_platform_adds_goes_through_the_switch():
    """At set-up, the rows that turn up later and the identity that fills in later."""
    setup = _setup_of("sensor")
    helper = setup.split("def add(")[1].split("\n\n")[0]
    assert "async_switch_on_new_defaults(" in helper
    assert helper.index("async_switch_on_new_defaults(") < helper.index("async_add_entities(new)")
    assert setup.count("async_add_entities(") == 1, "alla tillägg går via add()"


def test_both_coordinators_are_given_the_entry():
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    setup = source.split("async def async_setup_entry")[1].split("\nasync def ")[0]
    for name in ("CtcModbusCoordinator(", "CtcWebCoordinator("):
        call = setup.split(name)[1].split("\n    )")[0]
        assert "config_entry=entry" in call, name


def test_a_coordinator_built_without_an_entry_leaves_the_base_class_its_default():
    coordinator = load("coordinator") if ha_stub.install() else None
    if coordinator is None:
        pytest.skip(ha_stub.SKIP_REASON)
    assert coordinator._entry_kwargs(None) == {}
    entry = object()
    assert coordinator._entry_kwargs(entry) == {"config_entry": entry}
