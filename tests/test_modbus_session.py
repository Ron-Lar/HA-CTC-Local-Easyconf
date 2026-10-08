"""The integration owns the Modbus session: one client at a time, closed before replaced (R1).

The controller has a single client slot and pymodbus reconnects by itself as a
default, so an abandoned client would keep that slot busy. These tests drive the
real CtcModbusClient against a stand-in library that raises the moment a second
client is built while the first is still open.
"""

from __future__ import annotations

import asyncio
import itertools
import logging

import pytest

from fake_pymodbus import FakeController, FakeLibrary, OldFakeClient

_HOSTS = itertools.count(1)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def quick(monkeypatch, modbus_api):
    """No waiting for the controller: the pauses are proven elsewhere."""
    monkeypatch.setattr(modbus_api, "CONNECT_DELAY", 0)
    monkeypatch.setattr(modbus_api, "MESSAGE_WAIT", 0)
    monkeypatch.setattr(modbus_api, "CLOSE_SETTLE", 0)


@pytest.fixture()
def host() -> str:
    """A host of its own per test, since the settle book is kept per host."""
    return f"pump-{next(_HOSTS)}.test"


@pytest.fixture()
def library(monkeypatch) -> FakeLibrary:
    return FakeLibrary().install(monkeypatch)


# ------------------------------------------------------------ how it is built


def test_the_library_client_is_told_not_to_retry_or_reconnect(quick, library, modbus_api, host):
    client = modbus_api.CtcModbusClient(host)
    run(client.async_read(62000, 2))
    built = library.clients[0]
    assert built.options["retries"] == 0
    assert built.options["reconnect_delay"] == 0
    assert built.options["timeout"] == modbus_api.REQUEST_TIMEOUT
    assert built.port == 502


def test_one_client_serves_every_read(quick, library, modbus_api, host):
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        for _ in range(5):
            await client.async_read(62000, 3)
        await client.async_write(1000, 1)

    run(scenario())
    assert len(library.clients) == 1
    assert len(library.clients[0].requests) == 6
    assert library.clients[0].connect_calls == 1


def test_the_library_version_is_said_once(quick, library, modbus_api, host, monkeypatch, caplog):
    monkeypatch.setattr(modbus_api, "_SAID_VERSION", False)
    first = modbus_api.CtcModbusClient(host)
    second = modbus_api.CtcModbusClient(host + "-b")

    async def scenario():
        await first.async_read(62000, 1)
        await first.async_close()
        await second.async_read(62000, 1)

    with caplog.at_level(logging.INFO, logger="ctc_ecozenith.modbus_api"):
        run(scenario())
    said = [r for r in caplog.records if "pymodbus" in r.getMessage()]
    assert len(said) == 1
    assert "3.13.1" in said[0].getMessage()


# --------------------------------------------------- never two at the same time


def test_a_line_the_library_gave_up_is_closed_before_another_is_built(quick, library, modbus_api, host):
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        await client.async_read(62000, 1)
        # The library saw the line go between two reads.
        library.clients[0].connected = False
        return await client.async_read(62000, 1)

    assert run(scenario()) == [62000]
    first, second = library.clients
    assert first.closed and first.closed_at <= second.connected_at
    assert library.alive == [second]


def test_the_controller_gets_its_settle_time_when_a_client_is_replaced(
    quick, library, modbus_api, host, monkeypatch
):
    monkeypatch.setattr(modbus_api, "CLOSE_SETTLE", 0.05)
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        await client.async_read(62000, 1)
        library.clients[0].connected = False
        await client.async_read(62000, 1)

    run(scenario())
    first, second = library.clients
    assert second.connected_at - first.closed_at >= 0.05 * 0.8


def test_a_failed_connect_leaves_nothing_open(quick, library, modbus_api, host):
    library.controller.reachable = False
    client = modbus_api.CtcModbusClient(host)
    with pytest.raises(modbus_api.CtcModbusTransportError):
        run(client.async_read(62000, 1))
    assert library.alive == []
    # A second attempt builds a second client; the stand-in would have raised
    # had the first still been open.
    library.controller.reachable = OSError("no route to host")
    with pytest.raises(modbus_api.CtcModbusTransportError):
        run(client.async_read(62000, 1))
    assert len(library.clients) == 2 and library.alive == []
    library.controller.reachable = True
    assert run(client.async_read(62000, 1)) == [62000]
    assert library.alive == [library.clients[2]]


def test_a_connect_that_is_cancelled_leaves_nothing_open(quick, library, modbus_api, host):
    library.controller.reachable = "hang"
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        task = asyncio.ensure_future(client.async_read(62000, 1))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert len(library.clients) == 1
    assert library.alive == []


def test_close_hands_the_slot_back_and_notes_the_time(quick, library, modbus_api, host, monkeypatch):
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        await client.async_read(62000, 1)
        await client.async_close()

    run(scenario())
    assert library.alive == []
    monkeypatch.setattr(modbus_api, "CLOSE_SETTLE", 10.0)
    import time

    assert modbus_api.settle_wait(host, 502, time.monotonic()) > 9.0


def test_two_callers_share_the_one_connection(quick, library, modbus_api, host):
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        return await asyncio.gather(
            client.async_read(62000, 1),
            client.async_read(62100, 1),
            client.async_write(1000, 7),
        )

    assert run(scenario()) == [[62000], [62100], None]
    assert len(library.clients) == 1


# ------------------------------------------------ silence against a lost line


def test_silence_keeps_the_connection(quick, library, modbus_api, host):
    library.controller.silent.add(61500)
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        await client.async_read(62000, 1)
        with pytest.raises(modbus_api.CtcModbusSilence) as caught:
            await client.async_read(61500, 22)
        assert caught.value.address == 61500
        assert caught.value.line_up is True
        return await client.async_read(62000, 1)

    assert run(scenario()) == [62000]
    assert len(library.clients) == 1, "a silent register must not cost the connection"
    assert library.clients[0].connected


def test_silence_that_takes_the_line_is_still_silence(quick, library, modbus_api, host):
    # pymodbus before 3.9 closes the connection after any silent request.
    library.controller.silent.add(61500)
    library.controller.closes_after_silence = True
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        await client.async_read(62000, 1)
        with pytest.raises(modbus_api.CtcModbusSilence) as caught:
            await client.async_read(61500, 22)
        assert caught.value.line_up is False
        assert library.alive == [], "a client the library closed is let go at once"
        return await client.async_read(62000, 1)

    assert run(scenario()) == [62000]
    assert len(library.clients) == 2


def test_a_lost_line_drops_the_client_at_once(quick, library, modbus_api, host):
    library.controller.gone_before.add(62107)
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        await client.async_read(62000, 1)
        with pytest.raises(modbus_api.CtcModbusTransportError) as caught:
            await client.async_read(62107, 41)
        assert caught.value.address == 62107
        assert "62107" in str(caught.value)
        assert library.alive == []
        library.controller.gone_before.clear()
        return await client.async_read(62107, 2)

    assert run(scenario()) == [62107, 62108]
    assert len(library.clients) == 2


def test_a_reset_under_a_request_is_silence_without_a_line(quick, library, modbus_api, host):
    library.controller.reset_at.add(62107)
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        await client.async_read(62000, 1)
        with pytest.raises(modbus_api.CtcModbusSilence) as caught:
            await client.async_read(62107, 41)
        return caught.value

    err = run(scenario())
    assert err.line_up is False
    assert library.alive == []


def test_an_exception_code_is_neither(quick, library, modbus_api, host):
    library.controller.rejected.add(61500)
    client = modbus_api.CtcModbusClient(host)
    with pytest.raises(modbus_api.CtcModbusError) as caught:
        run(client.async_read(61500, 1))
    assert not isinstance(caught.value, modbus_api.CtcModbusSilence)
    assert not isinstance(caught.value, modbus_api.CtcModbusTransportError)
    assert caught.value.address == 61500
    assert library.clients[0].connected, "the controller answered, so the line is fine"


def test_the_two_kinds_are_told_apart_by_the_library_exception(library, modbus_api):
    from fake_pymodbus import ConnectionException, ModbusIOException

    assert modbus_api.is_silence(ModbusIOException("No response received after 0 retries"))
    assert modbus_api.is_silence(asyncio.TimeoutError())
    assert not modbus_api.is_silence(ConnectionException("Not connected"))
    assert not modbus_api.is_silence(OSError("reset by peer"))
    assert not modbus_api.is_silence(TypeError("unexpected keyword"))


# -------------------------------------------------------- the requests made


def test_reads_are_chunked_at_the_block_limit(quick, library, modbus_api, host):
    client = modbus_api.CtcModbusClient(host)
    values = run(client.async_read(62000, 150))
    assert values == [62000 + i for i in range(150)]
    assert [(a, n) for _, a, n, _ in library.requests] == [(62000, 100), (62100, 50)]


def test_requests_are_paced(library, modbus_api, host, monkeypatch):
    monkeypatch.setattr(modbus_api, "CONNECT_DELAY", 0)
    monkeypatch.setattr(modbus_api, "CLOSE_SETTLE", 0)
    monkeypatch.setattr(modbus_api, "MESSAGE_WAIT", 0.03)
    import time

    stamps: list[float] = []
    library.controller.on_request = lambda *_: stamps.append(time.monotonic())
    client = modbus_api.CtcModbusClient(host)
    run(client.async_read(62000, 250))
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(gaps) == 2
    assert min(gaps) >= 0.03 * 0.8


def test_writes_use_function_16_one_register_at_a_time(quick, library, modbus_api, host):
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        await client.async_write(1000, 5)
        await client.async_write(1001, -1)
        await client.async_write(1002, 70000)

    run(scenario())
    assert library.controller.written == [(1000, [5]), (1001, [65535]), (1002, [70000 & 0xFFFF])]
    assert all(kind == "write" for kind, *_ in library.requests)


def test_write_failures_are_sorted_like_reads(quick, library, modbus_api, host):
    library.controller.silent.add(1000)
    library.controller.gone_before.add(1001)
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        with pytest.raises(modbus_api.CtcModbusSilence):
            await client.async_write(1000, 1)
        assert library.alive, "silence on a write keeps the connection too"
        with pytest.raises(modbus_api.CtcModbusTransportError) as caught:
            await client.async_write(1001, 1)
        assert caught.value.address == 1001
        assert library.alive == []

    run(scenario())


def test_the_unit_keyword_follows_the_library(quick, modbus_api, host, monkeypatch):
    new = FakeLibrary().install(monkeypatch)
    client = modbus_api.CtcModbusClient(host, slave=3)
    run(client.async_read(62000, 1))
    assert new.requests[0][3] == {"device_id": 3}

    old = FakeLibrary(client_class=OldFakeClient, version="3.6.9").install(monkeypatch)
    client = modbus_api.CtcModbusClient(host + "-old", slave=3)
    run(client.async_read(62000, 1))
    assert old.requests[0][3] == {"slave": 3}


def test_an_unknown_failure_is_treated_as_a_lost_line(quick, library, modbus_api, host):
    # The library's API drifting would show up as a TypeError on every call. It
    # must surface as a failure with the reason in it, not hang or hide.
    def drift(client, kind, address):
        raise TypeError("read_holding_registers() got an unexpected keyword argument")

    library.controller.on_request = drift
    client = modbus_api.CtcModbusClient(host)
    with pytest.raises(modbus_api.CtcModbusTransportError) as caught:
        run(client.async_read(62000, 1))
    assert "unexpected keyword" in str(caught.value)
    assert library.alive == []


def test_the_controller_is_scripted_as_expected():
    # The stand-in itself: a sanity check so a broken fake cannot pass the rest.
    controller = FakeController()
    controller.values[62000] = 215
    assert controller.value(62000) == 215
    assert controller.value(62001) == 62001
