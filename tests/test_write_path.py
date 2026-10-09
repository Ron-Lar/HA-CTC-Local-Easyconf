"""The write path lets nothing but CTC's volatile control registers through.

The 61500 block lives in EEPROM with a limited number of write cycles, and the
README promises it is never written. These tests make that promise something
the suite proves, against a stand-in for pymodbus that is already connected, so
no heat pump is involved and no address is ever sent anywhere.
"""

from __future__ import annotations

import asyncio

import pytest

import ha_stub
from conftest import load


def run(coro):
    return asyncio.run(coro)


class FakeResult:
    def __init__(self, error: bool = False) -> None:
        self._error = error

    def isError(self) -> bool:  # noqa: N802 - pymodbus' spelling
        return self._error


class FakePymodbus:
    """Stands in for pymodbus' AsyncModbusTcpClient, already connected.

    Records every write it is asked for, which is what the tests look at: a
    refused address must never turn up here.
    """

    connected = True

    def __init__(self) -> None:
        self.writes: list[tuple[int, list[int]]] = []
        self.fail = False

    async def write_registers(self, address, values, slave=None, device_id=None):
        self.writes.append((address, list(values)))
        return FakeResult(error=self.fail)

    async def read_holding_registers(self, address, count=1, slave=None, device_id=None):
        raise AssertionError("the write path should not read")

    def close(self) -> None:
        pass


def _client(modbus_api):
    """A real CtcModbusClient over the fake, so the real guard is what runs."""
    client = modbus_api.CtcModbusClient("192.0.2.55")
    fake = FakePymodbus()
    client._client = fake
    return client, fake


# ---------------------------------------------------------------- the set


def test_every_writable_address_is_in_the_thousand_block(const):
    """The set is exactly the control tables, and all of them sit below 1100."""
    assert const.CONTROL_ADDRESSES, "nothing would be writable at all"
    for address in const.CONTROL_ADDRESSES:
        assert 1000 <= address < 1100, address
    assert const.CONTROL_ADDRESSES == {
        register.address for register in const.CONTROL_NUMBERS + const.CONTROL_SELECTS
    }
    # The virtual inputs register has no entity, no service and no proof against
    # a unit, so nothing may write it until it comes with all three.
    assert const.CONTROL_VDI_REGISTER not in const.CONTROL_ADDRESSES
    # And none of what is read: the stored settings and the readings.
    for description in const.MODBUS_SENSORS + const.MODBUS_SETTINGS:
        assert description.address not in const.CONTROL_ADDRESSES, description.key


# -------------------------------------------------------------- the guard


@pytest.mark.parametrize("address", [61500, 61503, 62000])
def test_a_stored_setting_or_a_reading_never_reaches_the_wire(modbus_api, address):
    client, fake = _client(modbus_api)
    with pytest.raises(modbus_api.CtcModbusError):
        run(client.async_write(address, 1))
    assert fake.writes == []


def test_the_guard_answers_before_the_lock_is_taken(modbus_api):
    """A refused write neither waits for the connection nor touches it.

    The lock is held by somebody else for the whole test; a check that came
    after it would hang until the timeout instead of refusing at once.
    """
    client, fake = _client(modbus_api)

    async def scenario():
        async with client._lock:
            with pytest.raises(modbus_api.CtcModbusError):
                await asyncio.wait_for(client.async_write(61500, 1), timeout=0.5)

    run(scenario())
    assert fake.writes == []


def test_a_control_register_is_written_with_function_code_16_one_at_a_time(modbus_api):
    client, fake = _client(modbus_api)
    run(client.async_write(1002, 450))
    assert fake.writes == [(1002, [450])]


def test_a_negative_value_goes_out_as_two_s_complement(modbus_api):
    client, fake = _client(modbus_api)
    run(client.async_write(1010, -5))
    assert fake.writes == [(1010, [65531])]


def test_an_error_reply_is_an_error_to_the_caller(modbus_api):
    client, fake = _client(modbus_api)
    fake.fail = True
    with pytest.raises(modbus_api.CtcModbusError):
        run(client.async_write(1002, 450))


# ------------------------------------------------ the manager, called directly


@pytest.fixture()
def manager_and_fake(modbus_api):
    """A CtcControlManager over a real client over the fake connection.

    This is the path a future service, SmartGrid feature or EMS would take, so
    the guard has to hold when the manager is called directly as well.
    """
    ha_stub.install()
    coordinator = load("coordinator")
    client, fake = _client(modbus_api)
    manager = coordinator.CtcControlManager(hass=object(), client=client)
    # The keepalive timer is not under test here; ha_stub records rather than
    # starts it, and a leftover entry from another test must not confuse this one.
    ha_stub.tracked.clear()
    return manager, fake


def test_setting_a_control_writes_the_raw_value(manager_and_fake):
    manager, fake = manager_and_fake
    run(manager.async_set(1002, 450))
    assert fake.writes == [(1002, [450])]
    assert manager.get(1002) == 450
    assert manager.active == {1002: 450}


def test_releasing_a_control_writes_nothing(manager_and_fake):
    manager, fake = manager_and_fake
    run(manager.async_set(1007, 2))
    run(manager.async_set(1007, None))
    assert fake.writes == [(1007, [2])]
    assert manager.get(1007) is None
    assert manager.active == {}


def test_the_manager_cannot_be_talked_into_the_stored_settings(manager_and_fake):
    manager, fake = manager_and_fake
    with pytest.raises(Exception):
        run(manager.async_set(61503, 2))
    assert fake.writes == []
    # And it does not claim to be in control of what it never wrote.
    assert manager.get(61503) is None
    assert manager.active == {}


def test_release_all_writes_nothing_either(manager_and_fake):
    manager, fake = manager_and_fake
    run(manager.async_set(1002, 450))
    run(manager.async_set(1007, 2))
    manager.async_release_all()
    assert fake.writes == [(1002, [450]), (1007, [2])]
    assert manager.active == {}
