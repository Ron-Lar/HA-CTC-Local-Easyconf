"""A stand-in for pymodbus that keeps the books on every client ever built.

Installed in sys.modules for the duration of a test, so the integration's own
lazy ``from pymodbus.client import AsyncModbusTcpClient`` picks it up. The
controller behind it is scripted per register address, and the library's own
rules can be switched: by default it behaves like pymodbus 3.9 and later, where
a register nobody answers raises ModbusIOException and leaves the line up; set
``closes_after_silence`` for the older behaviour, where the library also drops
the connection.

The one invariant the whole thing exists to prove: building a second client
while the first is still open raises on the spot.
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
from typing import Any, Callable


class ModbusIOException(Exception):
    """What pymodbus raises for a request nobody answered."""


class ConnectionException(Exception):
    """What pymodbus raises for a request on a line that is not up."""


class Result:
    def __init__(self, registers: list[int] | None = None, error: bool = False) -> None:
        self.registers = registers or []
        self._error = error

    def isError(self) -> bool:  # noqa: N802 - pymodbus spelling
        return self._error


class FakeController:
    """The scripted heat pump: what happens per register address."""

    def __init__(self) -> None:
        #: Whether a connect() succeeds. An exception instance is raised instead.
        self.reachable: bool | Exception = True
        #: Registers the controller never answers; the line stays up.
        self.silent: set[int] = set()
        #: Registers under whose request the controller resets the line: the
        #: answer never comes and the library finds the line gone.
        self.reset_at: set[int] = set()
        #: Registers before whose request the line is already found gone.
        self.gone_before: set[int] = set()
        #: Registers answered with an exception code rather than data.
        self.rejected: set[int] = set()
        #: pymodbus before 3.9: a silent request also closes the connection.
        self.closes_after_silence = False
        #: Register values; anything unscripted reads as its own address.
        self.values: dict[int, int] = {}
        #: Writes that reached the controller, as (address, values).
        self.written: list[tuple[int, list[int]]] = []
        #: Called with (client, kind, address) before every request.
        self.on_request: Callable[[Any, str, int], None] | None = None

    def value(self, address: int) -> int:
        return self.values.get(address, address & 0xFFFF)


class FakeClient:
    """One AsyncModbusTcpClient, with pymodbus 3.9+ keyword names."""

    def __init__(self, library: FakeLibrary, host: str, port: int = 502, **options: Any) -> None:
        if library.alive:
            raise AssertionError(
                f"a second client was built for {host} while {library.alive[0]!r} was still open"
            )
        self.library = library
        self.host = host
        self.port = port
        self.options = options
        self.connected = False
        self.closed = False
        self.connect_calls = 0
        self.connected_at: float | None = None
        self.closed_at: float | None = None
        #: Every request made on this client: (kind, address, count or values, unit kwargs).
        self.requests: list[tuple[str, int, Any, dict[str, int]]] = []
        library.clients.append(self)

    def __repr__(self) -> str:
        return f"<FakeClient #{self.library.clients.index(self) + 1} {self.host}>"

    async def connect(self) -> bool:
        self.connect_calls += 1
        self.connected_at = time.monotonic()
        reachable = self.library.controller.reachable
        if isinstance(reachable, Exception):
            raise reachable
        if reachable == "hang":
            await asyncio.Event().wait()
        if not reachable:
            return False
        self.connected = True
        return True

    def close(self) -> None:
        self.closed = True
        self.connected = False
        self.closed_at = time.monotonic()

    def _request(self, kind: str, address: int, payload: Any, unit: dict[str, int]) -> Result:
        controller = self.library.controller
        if controller.on_request is not None:
            controller.on_request(self, kind, address)
        if address in controller.gone_before:
            self.connected = False
        if not self.connected:
            raise ConnectionException(f"Not connected[{self!r}]")
        self.requests.append((kind, address, payload, dict(unit)))
        if address in controller.reset_at:
            self.connected = False
            raise ModbusIOException("No response received after 0 retries")
        if address in controller.silent:
            if controller.closes_after_silence:
                self.connected = False
            raise ModbusIOException("No response received after 0 retries, continue with next request")
        if address in controller.rejected:
            return Result(error=True)
        if kind == "read":
            return Result([controller.value(address + i) for i in range(payload)])
        controller.written.append((address, list(payload)))
        return Result()

    async def read_holding_registers(self, address: int, *, count: int = 1, device_id: int = 1) -> Result:
        return self._request("read", address, count, {"device_id": device_id})

    async def write_registers(self, address: int, values: list[int], *, device_id: int = 1) -> Result:
        return self._request("write", address, values, {"device_id": device_id})


class OldFakeClient(FakeClient):
    """The same client with the keyword pymodbus used before 3.9."""

    async def read_holding_registers(self, address: int, count: int = 1, slave: int = 0) -> Result:  # type: ignore[override]
        return self._request("read", address, count, {"slave": slave})

    async def write_registers(self, address: int, values: list[int], slave: int = 0) -> Result:  # type: ignore[override]
        return self._request("write", address, values, {"slave": slave})


class FakeLibrary:
    """The pymodbus stand-in itself: modules, the client class and the books."""

    def __init__(
        self,
        controller: FakeController | None = None,
        version: str = "3.13.1",
        client_class: type[FakeClient] = FakeClient,
    ) -> None:
        self.controller = controller or FakeController()
        self.version = version
        self.client_class = client_class
        self.clients: list[FakeClient] = []

    @property
    def alive(self) -> list[FakeClient]:
        """Clients built and not yet closed, whether or not they ever connected."""
        return [client for client in self.clients if not client.closed]

    @property
    def requests(self) -> list[tuple[str, int, Any, dict[str, int]]]:
        """Every request on every client, in order."""
        return [request for client in self.clients for request in client.requests]

    def install(self, monkeypatch) -> "FakeLibrary":
        """Put the stand-in where ``import pymodbus`` will find it."""
        library = self

        def build(host: str, port: int = 502, **options: Any) -> FakeClient:
            return library.client_class(library, host, port=port, **options)

        package = types.ModuleType("pymodbus")
        package.__version__ = self.version
        package.__path__ = []
        client_module = types.ModuleType("pymodbus.client")
        client_module.AsyncModbusTcpClient = build
        exceptions_module = types.ModuleType("pymodbus.exceptions")
        exceptions_module.ModbusIOException = ModbusIOException
        exceptions_module.ConnectionException = ConnectionException
        package.client = client_module
        package.exceptions = exceptions_module
        monkeypatch.setitem(sys.modules, "pymodbus", package)
        monkeypatch.setitem(sys.modules, "pymodbus.client", client_module)
        monkeypatch.setitem(sys.modules, "pymodbus.exceptions", exceptions_module)
        return self
