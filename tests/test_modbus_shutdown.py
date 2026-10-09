"""The client has a final shut-down state, apart from the close a round uses (F2.2, F1.1, R1).

async_close lets the connection go so that the next round can build a new one;
async_shutdown is the entry on its way out, after which nothing on the object
may connect again. The distinction matters at a reload: a keepalive write that
was already queued when the entry was unloaded used to build a connection of
its own after the close, and nothing ever closed it, so the new entry's client
met the controller's single slot held by a ghost until Home Assistant was
restarted. Driven through the real CtcModbusClient against the pymodbus
stand-in, which raises the moment a second client is built while one is open.
"""

from __future__ import annotations

import asyncio
import itertools

import pytest

from fake_pymodbus import FakeClient, FakeLibrary

_HOSTS = itertools.count(1)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def quick(monkeypatch, modbus_api):
    monkeypatch.setattr(modbus_api, "CONNECT_DELAY", 0)
    monkeypatch.setattr(modbus_api, "MESSAGE_WAIT", 0)
    monkeypatch.setattr(modbus_api, "CLOSE_SETTLE", 0)


@pytest.fixture()
def host() -> str:
    return f"shutdown-{next(_HOSTS)}.test"


class GatedClient(FakeClient):
    """A library client whose write of a chosen register waits for a gate.

    That is a request in flight for as long as the test wants, which is the
    moment a reload can land in: the client lock is held, and everything else
    queues behind it.
    """

    gates: dict[int, asyncio.Event] = {}

    async def write_registers(self, address, values, *, device_id=1):  # type: ignore[override]
        gate = self.gates.get(address)
        if gate is not None:
            await gate.wait()
        return await super().write_registers(address, values, device_id=device_id)


@pytest.fixture()
def gated(monkeypatch) -> FakeLibrary:
    GatedClient.gates = {}
    return FakeLibrary(client_class=GatedClient).install(monkeypatch)


@pytest.fixture()
def library(monkeypatch) -> FakeLibrary:
    return FakeLibrary().install(monkeypatch)


# --------------------------------------------------------------- the two kinds


def test_a_close_is_not_final_so_the_next_round_builds_a_new_client(quick, library, modbus_api, host):
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        await client.async_read(62000, 1)
        await client.async_close()
        assert library.alive == []
        return await client.async_read(62000, 1)

    assert run(scenario()) == [62000]
    assert len(library.clients) == 2
    assert library.alive == [library.clients[1]]
    assert client.closed is False


def test_a_shutdown_is_final_for_reads_and_writes_alike(quick, library, modbus_api, host):
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        await client.async_read(62000, 1)
        await client.async_shutdown()
        assert library.alive == []
        assert client.closed is True
        with pytest.raises(modbus_api.CtcModbusTransportError) as caught:
            await client.async_read(62000, 1)
        assert "shut down" in str(caught.value)
        with pytest.raises(modbus_api.CtcModbusTransportError):
            await client.async_write(1010, 1)
        # Shutting down twice, or closing after, is harmless.
        await client.async_shutdown()
        await client.async_close()

    run(scenario())
    assert len(library.clients) == 1, "nothing is built after the shutdown"
    assert library.alive == []


def test_a_shutdown_on_a_client_that_never_connected_builds_nothing(quick, library, modbus_api, host):
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        await client.async_shutdown()
        with pytest.raises(modbus_api.CtcModbusTransportError):
            await client.async_probe()

    run(scenario())
    assert library.clients == []


# ------------------------------------------------ the reload race, two writes


def test_a_write_queued_behind_the_shutdown_finds_the_door_shut(quick, gated, modbus_api, host):
    """The second of two keepalive writes, with the unload between them.

    The first write holds the lock; the unload's shutdown queues behind it;
    the second write queues behind the shutdown. Before, the second write
    built a new library client after the shutdown had closed the first.
    """
    client = modbus_api.CtcModbusClient(host)
    GatedClient.gates[1010] = asyncio.Event()

    async def scenario():
        await client.async_read(62000, 1)
        first = asyncio.ensure_future(client.async_write(1010, 215))
        await asyncio.sleep(0.01)  # the first write is in flight, lock held
        shutdown = asyncio.ensure_future(client.async_shutdown())
        await asyncio.sleep(0.01)  # queued behind the first write
        second = asyncio.ensure_future(client.async_write(1005, 1))
        await asyncio.sleep(0.01)  # queued behind the shutdown
        GatedClient.gates[1010].set()
        await first
        await shutdown
        with pytest.raises(modbus_api.CtcModbusTransportError):
            await second

    run(scenario())
    assert len(gated.clients) == 1, "exactly one library client was ever built"
    assert gated.alive == []
    assert gated.controller.written == [(1010, [215])], "the second write never went out"


def test_a_write_queued_before_the_shutdown_finds_the_door_shut_too(quick, gated, modbus_api, host):
    """The same race with the shutdown arriving after the second write queued.

    The lock is first come first served, so the second write gets it before
    the shutdown does; the flag is what stops it, not the order of the queue.
    """
    client = modbus_api.CtcModbusClient(host)
    GatedClient.gates[1010] = asyncio.Event()

    async def scenario():
        await client.async_read(62000, 1)
        first = asyncio.ensure_future(client.async_write(1010, 215))
        await asyncio.sleep(0.01)
        second = asyncio.ensure_future(client.async_write(1005, 1))
        await asyncio.sleep(0.01)
        shutdown = asyncio.ensure_future(client.async_shutdown())
        await asyncio.sleep(0.01)
        GatedClient.gates[1010].set()
        await first
        with pytest.raises(modbus_api.CtcModbusTransportError):
            await second
        await shutdown

    run(scenario())
    assert len(gated.clients) == 1
    assert gated.alive == []
    assert gated.controller.written == [(1010, [215])]


def test_after_a_shutdown_a_new_client_for_the_same_pump_is_the_only_one(quick, gated, modbus_api, host):
    """What the reload needs: the old entry's object is dead, the new one's lives alone."""
    old = modbus_api.CtcModbusClient(host)
    GatedClient.gates[1010] = asyncio.Event()

    async def scenario():
        await old.async_read(62000, 1)
        first = asyncio.ensure_future(old.async_write(1010, 215))
        await asyncio.sleep(0.01)
        shutdown = asyncio.ensure_future(old.async_shutdown())
        await asyncio.sleep(0.01)
        straggler = asyncio.ensure_future(old.async_write(1005, 1))
        GatedClient.gates[1010].set()
        await first
        await shutdown
        # The new entry's client knocks; the stand-in would raise here had the
        # straggler built a client of its own.
        new = modbus_api.CtcModbusClient(host)
        assert await new.async_read(62000, 1) == [62000]
        with pytest.raises(modbus_api.CtcModbusTransportError):
            await straggler
        await new.async_shutdown()

    run(scenario())
    assert len(gated.clients) == 2
    assert gated.alive == []


# ------------------------------------------------ a cancellation in flight


class CancelSwallowingClient(FakeClient):
    """pymodbus 3.13 and later: a cancellation inside a transaction comes out as ModbusIOException.

    The request for a gated register waits, and when the task is cancelled
    the library reports "Request cancelled outside library." with the
    CancelledError chained as the cause, exactly as transaction.py does.
    """

    gates: dict[int, asyncio.Event] = {}

    async def read_holding_registers(self, address, *, count=1, device_id=1):  # type: ignore[override]
        gate = self.gates.get(address)
        if gate is not None:
            try:
                await gate.wait()
            except asyncio.CancelledError as exc:
                from fake_pymodbus import ModbusIOException

                raise ModbusIOException("Request cancelled outside library.") from exc
        return await super().read_holding_registers(address, count=count, device_id=device_id)


@pytest.fixture()
def swallowing(monkeypatch) -> FakeLibrary:
    CancelSwallowingClient.gates = {}
    return FakeLibrary(client_class=CancelSwallowingClient).install(monkeypatch)


def test_a_cancel_inside_a_transaction_ends_the_round_as_a_cancel(quick, swallowing, modbus_api, host):
    poll = __import__("conftest").load("poll")
    blocks = [(62000, 98), (62107, 41), (62167, 7), (62191, 25)]
    CancelSwallowingClient.gates[62107] = asyncio.Event()
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        task = asyncio.ensure_future(poll.read_round(client, blocks))
        await asyncio.sleep(0.01)  # the second block is in flight and waiting
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return task

    task = run(scenario())
    assert task.cancelled(), "the task ended cancelled, not with a result"
    # The gated request never got as far as the controller, and nothing after it was tried.
    assert [a for _, a, _, _ in swallowing.requests] == [62000]
    assert swallowing.alive == [], "the client was let go with the request still in flight"


def test_a_cancel_inside_a_write_is_a_cancel_too(quick, swallowing, modbus_api, host):
    class SwallowingWriter(CancelSwallowingClient):
        async def write_registers(self, address, values, *, device_id=1):  # type: ignore[override]
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as exc:
                from fake_pymodbus import ModbusIOException

                raise ModbusIOException("Request cancelled outside library.") from exc

    swallowing.client_class = SwallowingWriter
    client = modbus_api.CtcModbusClient(host)

    async def scenario():
        task = asyncio.ensure_future(client.async_write(1010, 1))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert swallowing.alive == []


def test_the_cause_alone_is_enough_when_the_task_is_not_cancelling(quick, library, modbus_api, host):
    """Belt and braces: the chained CancelledError is honoured even without the task flag."""
    from fake_pymodbus import ModbusIOException

    def cancelled_outside(client, kind, address):
        try:
            raise asyncio.CancelledError()
        except asyncio.CancelledError as exc:
            raise ModbusIOException("Request cancelled outside library.") from exc

    library.controller.on_request = cancelled_outside
    client = modbus_api.CtcModbusClient(host)
    with pytest.raises(asyncio.CancelledError):
        run(client.async_read(62000, 1))
    assert library.alive == []
