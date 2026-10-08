"""pymodbus's real surface, as the adapter uses it (R59).

Skipped where pymodbus is not installed. In CI it runs against the floor the
manifest allows, the version Home Assistant ships and whatever PyPI has today,
so a renamed argument or a changed exception shows up here and not as a
setup_retry in somebody's house. Nothing here connects anywhere: the client is
built against 127.0.0.1 and left unconnected, and the one test that does open a
socket talks to a scripted controller inside the test process over loopback.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import pathlib
import struct

import pytest

pymodbus = pytest.importorskip("pymodbus")

MANIFEST = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith" / "manifest.json"


def run(coro):
    return asyncio.run(coro)


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".")[:3] if part.isdigit())


# ---------------------------------------------------------------- the bounds


def test_the_manifest_caps_the_major_version():
    requirements = json.loads(MANIFEST.read_text(encoding="utf-8"))["requirements"]
    assert requirements == ["pymodbus>=3.6.9,<4"]


def test_the_installed_version_is_one_the_manifest_allows():
    # Fails on purpose in the "latest" leg the day a 4.x appears on PyPI: that
    # is the early warning, and the manifest keeps users on 3.x meanwhile.
    installed = version_tuple(pymodbus.__version__)
    assert (3, 6, 9) <= installed < (4,), pymodbus.__version__


# -------------------------------------------------------------- the surface


def test_the_client_is_built_the_way_the_adapter_builds_it(modbus_api):
    async def scenario():
        from pymodbus.client import AsyncModbusTcpClient

        client = AsyncModbusTcpClient("127.0.0.1", port=502, **modbus_api.client_options())
        assert client.connected is False
        assert inspect.iscoroutinefunction(client.connect)

        read = inspect.signature(client.read_holding_registers).parameters
        assert "count" in read
        assert "slave" in read or "device_id" in read
        write = inspect.signature(client.write_registers).parameters
        assert "slave" in write or "device_id" in write

        adapter = modbus_api.CtcModbusClient("127.0.0.1", 502, slave=1)
        unit = adapter._slave_kwargs(client, "read_holding_registers")
        assert list(unit.values()) == [1]
        assert set(unit) <= {"slave", "device_id"}
        assert adapter._slave_kwargs(client, "write_registers") == unit

        # Closing a client that never connected is allowed and leaves it closed.
        result = client.close()
        if asyncio.iscoroutine(result):
            await result
        assert client.connected is False

    run(scenario())


def test_the_library_exceptions_sort_into_the_two_kinds(modbus_api):
    from pymodbus.exceptions import ConnectionException, ModbusException, ModbusIOException

    assert modbus_api.is_silence(ModbusIOException("No response received after 0 retries"))
    assert not modbus_api.is_silence(ConnectionException("Not connected"))
    assert not modbus_api.is_silence(ModbusException("anything else"))


def test_a_request_on_a_client_that_never_connected_is_a_lost_line(modbus_api):
    async def scenario():
        from pymodbus.client import AsyncModbusTcpClient

        client = AsyncModbusTcpClient("127.0.0.1", port=502, **modbus_api.client_options())
        adapter = modbus_api.CtcModbusClient("127.0.0.1")
        unit = adapter._slave_kwargs(client, "read_holding_registers")
        with pytest.raises(Exception) as caught:
            await client.read_holding_registers(62000, count=1, **unit)
        assert not modbus_api.is_silence(caught.value)
        client.close()

    run(scenario())


# ------------------------------------------------- the whole chain, loopback

SILENT = 61500


class LoopbackController:
    """A Modbus TCP server on 127.0.0.1 that answers like a CTC: data for most
    registers, silence for one the model lacks, and an echo for a write."""

    def __init__(self) -> None:
        self.writers: list[asyncio.StreamWriter] = []
        self.written: list[tuple[int, list[int]]] = []
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        try:
            while True:
                head = await reader.readexactly(6)
                tid, _protocol, length = struct.unpack(">HHH", head)
                body = await reader.readexactly(length)
                unit, function, address = struct.unpack(">BBH", body[:4])
                if address == SILENT:
                    continue
                if function == 3:
                    (count,) = struct.unpack(">H", body[4:6])
                    data = b"".join(struct.pack(">H", (address + i) & 0xFFFF) for i in range(count))
                    pdu = struct.pack(">BBB", unit, function, len(data)) + data
                else:
                    count, size = struct.unpack(">HB", body[4:7])
                    values = list(struct.unpack(f">{count}H", body[7 : 7 + size]))
                    self.written.append((address, values))
                    pdu = struct.pack(">BBHH", unit, function, address, count)
                writer.write(struct.pack(">HHH", tid, 0, len(pdu)) + pdu)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    async def vanish(self) -> None:
        """Stop listening and drop every connection, like a controller losing power."""
        assert self.server is not None
        self.server.close()
        for writer in self.writers:
            writer.close()
        await asyncio.sleep(0.05)


def test_the_whole_chain_against_a_loopback_controller(modbus_api, monkeypatch):
    monkeypatch.setattr(modbus_api, "REQUEST_TIMEOUT", 1)
    monkeypatch.setattr(modbus_api, "CONNECT_DELAY", 0)
    monkeypatch.setattr(modbus_api, "CLOSE_SETTLE", 0)
    monkeypatch.setattr(modbus_api, "MESSAGE_WAIT", 0)

    async def scenario():
        controller = LoopbackController()
        port = await controller.start()
        client = modbus_api.CtcModbusClient("127.0.0.1", port)

        assert await client.async_read(62000, 2) == [62000, 62001]
        with pytest.raises(modbus_api.CtcModbusSilence) as silence:
            await client.async_read(SILENT, 1)
        assert silence.value.address == SILENT
        # Whatever this pymodbus did with the line after the silence, the next
        # register still answers, on the same connection or a fresh one.
        assert await client.async_read(62000, 1) == [62000]
        await client.async_write(1000, 5)
        assert controller.written == [(1000, [5])]

        await controller.vanish()
        # The library notices the line go on its own; wait for that rather than
        # race it, since a request sent before it does merely times out.
        for _ in range(50):
            if not getattr(client._client, "connected", False):
                break
            await asyncio.sleep(0.02)
        with pytest.raises(modbus_api.CtcModbusTransportError) as lost:
            await client.async_read(62000, 1)
        assert "could not connect" in str(lost.value)
        await client.async_close()
        assert client._client is None

    run(scenario())
