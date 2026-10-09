"""Modbus TCP client for CTC's BMS interface.

CTC documents this in the BMS manual (162 600 16). Everything is a holding
register: function code 3 to read, 16 to write, offset 0, and at most 100
registers per transaction.

The controller accepts exactly one master at a time. It answers the TCP
handshake for a second client and then resets the connection as soon as that
client sends a PDU, which looks like the unit being offline. One connection is
therefore held for the lifetime of the entry and every request is serialised
behind a lock.

That single slot also means the integration has to own the session outright.
pymodbus reconnects by itself as a default, so a client that was merely
abandoned would keep knocking on the controller's one slot and lock the live
client out; here the library is told never to reconnect, the old client is
closed before a new one is built, and two never exist at the same time.

Two things can go wrong with a request, and they are told apart because they
call for opposite responses. Silence is the controller keeping quiet within the
timeout while the line stays up: that is how it answers a register the model
does not implement, so the connection is kept and only that request is lost. A
transport failure is the connection being gone, refused, reset or dropped under
a request: the client is let go at once, so that the next request starts over
after the controller has had its settle time.

CTC also sets a pace. The BMS documentation gives an update rate of 1000 ms and
the controller cannot pipeline, so exactly one request may be outstanding and
requests are spaced out rather than sent back to back. It also needs a moment
after the socket opens before it will answer. Both are enforced here rather than
left to the caller, because getting them wrong looks like an unreliable network
instead of a client that is talking too fast.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .const import CONTROL_ADDRESSES, SENTINELS, ModbusSensor, enum_label

_LOGGER = logging.getLogger(__name__)

MAX_BLOCK = 100

#: One timer for connecting and for waiting on an answer. pymodbus has a single
#: setting for both, and it is the only timer in play: a second one outside the
#: library would fire first and cut the transaction short halfway, which newer
#: pymodbus reports as a cancellation rather than as the silence it was.
REQUEST_TIMEOUT = 10

# Shortest gap between two transactions. CTC documents an update rate of one
# second for the BMS interface; the community Modbus configurations that work
# use tens of milliseconds between messages. Sixty is a compromise that keeps a
# full poll brisk without crowding the controller.
MESSAGE_WAIT = 0.06

# The controller needs a moment after the socket opens before it answers. The
# widely used YAML packages wait five seconds; three is enough in practice and
# only costs anything on the first poll after a reconnect.
CONNECT_DELAY = 3.0

# And a moment after the socket closes before it hands its single client slot
# back. Home Assistant reloads an entry by unloading and setting it up again
# within the same breath, which is a new connection knocking while the
# controller still believes the old one is there: it answers with a reset, and
# the knocking appears to keep that belief alive. So a connection waits out the
# last close on the same unit, which costs nothing at all on a fresh start.
CLOSE_SETTLE = 10.0

#: When each unit's socket was last closed, kept per address rather than per
#: client, since a reload builds a new client for the same pump.
_CLOSED_AT: dict[tuple[str, int], float] = {}

#: The outdoor temperature, the one register every CTC model answers: what the
#: set-up flow asks for to see that Modbus is there, and what a poll round asks
#: for first, so that a controller that answers nothing at all is found out at
#: once rather than one timeout per block.
PROBE_REGISTER = 62000

#: Whether the library's version has been written to the log in this process.
#: It is said once, since the version decides how a silent register is handled
#: (see :func:`client_options`) and is the first thing to ask for in a report.
_SAID_VERSION = False


def note_close(host: str, port: int, now: float) -> None:
    """Remember that this unit's socket has just been closed."""
    _CLOSED_AT[(host, port)] = now


def settle_wait(host: str, port: int, now: float) -> float:
    """How long a new connection to this unit should wait before knocking."""
    closed = _CLOSED_AT.get((host, port))
    if closed is None:
        return 0.0
    return max(0.0, CLOSE_SETTLE - (now - closed))


#: The logger pymodbus writes to, and the start of the line it writes, at
#: ERROR, for a request nobody answered. That is how CTC says a register the
#: model lacks, which the poll round handles and says on debug; with the
#: integration's retries=0 the line names zero retries, which no other client
#: in the process is likely to produce, so the match is as narrow as it can be.
LIBRARY_LOGGER = "pymodbus.logging"
LIBRARY_SILENCE_LINE = "No response received after 0 retries"


class _QuietSilence(logging.Filter):
    """Keeps the library's own line about a silent register out of the log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.getMessage().startswith(LIBRARY_SILENCE_LINE)


_QUIET = _QuietSilence()
_QUIET_HOLDS = 0


def hold_library_quiet() -> Callable[[], None]:
    """Keep pymodbus' error line about a silent register out of the log; returns the undo.

    Held for as long as an entry is loaded and let go at its unload, so the
    library speaks for itself again once no heat pump is set up. On a model
    that lacks the stored block the line would otherwise come twelve times
    per start, in red, for something the round handles and says on debug.
    Only that one line is kept out: anything else the library has to say,
    a lost connection included, still reaches the log.
    """
    global _QUIET_HOLDS
    logger = logging.getLogger(LIBRARY_LOGGER)
    if _QUIET_HOLDS == 0:
        logger.addFilter(_QUIET)
    _QUIET_HOLDS += 1
    released = False

    def _release() -> None:
        nonlocal released
        global _QUIET_HOLDS
        if released:
            return
        released = True
        _QUIET_HOLDS -= 1
        if _QUIET_HOLDS == 0:
            logger.removeFilter(_QUIET)

    return _release


def client_options() -> dict[str, Any]:
    """How the library client is built.

    ``retries=0``: CTC answers a register the model lacks with silence, and
    asking three more times costs thirty seconds and tells nothing new.
    ``reconnect_delay=0``: the library must never reconnect on its own, since an
    abandoned client knocking on the controller's single slot is exactly what
    keeps the live one out. Before pymodbus 3.8 the library also closes the
    connection after a silent request (3.6.9, the floor, does; 3.8.6 does
    not); from 3.8 it keeps it up and only gives up after several silences in
    a row. Both are handled: a client found closed is let go and replaced,
    with the settle time in between, and the poll round holds the silence
    against the register either way.
    """
    return {"timeout": REQUEST_TIMEOUT, "retries": 0, "reconnect_delay": 0}


class CtcModbusError(Exception):
    """Raised when the Modbus side cannot be used."""

    def __init__(self, message: str, address: int | None = None) -> None:
        super().__init__(message)
        #: The register the failing request was for, when there was one.
        self.address = address


class CtcModbusSilence(CtcModbusError):
    """A request went unanswered within the timeout.

    This is how the controller says a register does not exist on this model, so
    the connection is kept. ``line_up`` is False when the library found the
    line gone by the time the timeout ran out, which pymodbus before 3.8 does
    after any silence and every version does when the peer resets under a
    request; the client has then already been let go, and the next request
    starts over.
    """

    def __init__(self, message: str, address: int | None = None, line_up: bool = True) -> None:
        super().__init__(message, address)
        self.line_up = line_up


class CtcModbusTransportError(CtcModbusError):
    """The connection is gone: refused, reset, or dropped under a request.

    Nothing further can be read on it, and the client has been let go so that
    the next request starts over once the controller has settled.
    """


def is_silence(err: BaseException) -> bool:
    """Whether a failed request was the controller keeping quiet.

    pymodbus reports a request nobody answered as ModbusIOException; a plain
    timeout is included for the sake of anything standing in for the library.
    Everything else means the line itself is in question.
    """
    if isinstance(err, (asyncio.TimeoutError, TimeoutError)):
        return True
    try:
        from pymodbus.exceptions import ModbusIOException
    except ImportError:  # pragma: no cover - dependency is declared
        return False
    return isinstance(err, ModbusIOException)


def decode_signed(value: int) -> int:
    """Interpret a 16 bit register as two's complement."""
    return value - 65536 if value > 32767 else value


def decode_pair(low: int, high: int) -> int:
    """Combine a 32 bit value. CTC sends the least significant word first."""
    return (high << 16) | low


def is_sentinel(value: int) -> bool:
    """True when the controller means "no sensor fitted"."""
    return value in SENTINELS


def _connected(client: Any) -> bool:
    return bool(getattr(client, "connected", False))


def _library_client() -> Any:
    """The library's client class, saying which pymodbus this is the first time."""
    global _SAID_VERSION
    try:
        import pymodbus
        from pymodbus.client import AsyncModbusTcpClient
    except ImportError as err:  # pragma: no cover - dependency is declared
        raise CtcModbusTransportError("pymodbus is not available") from err
    if not _SAID_VERSION:
        _SAID_VERSION = True
        _LOGGER.info("Using pymodbus %s", getattr(pymodbus, "__version__", "of unknown version"))
    return AsyncModbusTcpClient


#: What a 32 bit counter reads when it is not there: both words all ones. The
#: single word sentinels are not applied to a pair, since a counter that has
#: reached 9 999 or 10 000 kWh is a real reading, not a missing sensor.
PAIR_SENTINEL = 0xFFFFFFFF


@dataclass(frozen=True)
class Reading:
    """What one register decoded to.

    ``value`` is None when the register was not read or marks a missing sensor.
    ``code`` is the number behind an enum reading, whatever label it got, and
    ``unknown`` repeats it when the table has no label for it.
    """

    value: Any
    code: int | None = None
    unknown: int | None = None


def decode_reading(description: ModbusSensor, raw: Mapping[int, int]) -> Reading:
    """Turn the raw words of one register into its reading.

    The sentinels are judged after the sign is applied, because CTC's negative
    markers arrive as large unsigned words: 55537 is -9999, which read as raw
    would pass and become -999.9 degrees on a temperature. A 32 bit counter is
    judged as a whole against its own marker, so that a missing counter cannot
    become 4 294 967 295 kWh in a total_increasing statistic, which no reset
    cleans up by itself.
    """
    first = raw.get(description.address)
    if first is None:
        return Reading(None)
    if description.count == 2:
        second = raw.get(description.address + 1)
        if second is None:
            return Reading(None)
        combined = decode_pair(first, second)
        if combined == PAIR_SENTINEL:
            return Reading(None)
        return Reading(round(combined * description.scale, 3))
    value = decode_signed(first) if description.signed else first
    if is_sentinel(value):
        return Reading(None)
    if description.enum is not None:
        label, unknown = enum_label(description.enum, value)
        return Reading(label, code=value, unknown=unknown)
    return Reading(round(value * description.scale, 3))


class CtcModbusClient:
    """A single, serialised Modbus TCP connection to the controller.

    The connection is let go in two ways that must not be confused. A poll
    round that found the line dead calls :meth:`async_close`: the library
    client goes, and the next request builds a new one after the settle time.
    The entry on its way out calls :meth:`async_shutdown`: the client goes for
    good, and no request on this object ever connects again. Without that
    second kind, a keepalive write that was already queued when the entry was
    unloaded would open a connection of its own after the close, and nothing
    would ever close it: the controller's single slot would then be held
    against the new entry's client until Home Assistant was restarted.
    """

    def __init__(self, host: str, port: int = 502, slave: int = 1) -> None:
        self._host = host
        self._port = port
        self._slave = slave
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        #: Set by async_shutdown and never cleared: this object is finished.
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether :meth:`async_shutdown` has been called."""
        return self._closed

    async def _ensure_client(self) -> Any:
        if self._closed:
            raise CtcModbusTransportError(
                f"the Modbus client for {self._host}:{self._port} has been shut down"
            )
        if self._client is not None:
            if _connected(self._client):
                return self._client
            # The library saw the line go while nobody was asking, or gave it up
            # after a silence. That client is finished with before another is
            # built, so two never exist at once and the controller gets its
            # settle time between them.
            await self._drop_client()

        waiting = settle_wait(self._host, self._port, time.monotonic())
        if waiting:
            await asyncio.sleep(waiting)
        library_client = _library_client()
        client = library_client(self._host, port=self._port, **client_options())
        # Held from before connect() so that whatever happens during it, a
        # cancellation included, there is a client to close rather than one left
        # behind with a socket of its own.
        self._client = client
        try:
            connected = await client.connect()
        except Exception as err:  # noqa: BLE001 - the library raises broadly
            await self._drop_client()
            raise CtcModbusTransportError(
                f"could not connect to {self._host}:{self._port}: {err}"
            ) from err
        except BaseException:
            await self._drop_client()
            raise
        if not connected or not _connected(client):
            await self._drop_client()
            raise CtcModbusTransportError(f"could not connect to {self._host}:{self._port}")
        await asyncio.sleep(CONNECT_DELAY)
        self._last_request = time.monotonic()
        return client

    async def _drop_client(self) -> None:
        """Let go of the library client, closed, and remember when for the settle."""
        client, self._client = self._client, None
        if client is None:
            return
        close = getattr(client, "close", None)
        if close is not None:
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 - closing a dead socket may complain
                _LOGGER.debug("Closing the Modbus client raised", exc_info=True)
        note_close(self._host, self._port, time.monotonic())

    async def _pace(self) -> None:
        """Hold the documented gap between two transactions."""
        gap = MESSAGE_WAIT - (time.monotonic() - self._last_request)
        if gap > 0:
            await asyncio.sleep(gap)

    async def async_close(self) -> None:
        """Let the connection go, to be built again by the next request.

        This is a round giving up a dead line so that the next round starts
        over after the settle time, not the end of the client.
        """
        async with self._lock:
            await self._drop_client()

    async def async_shutdown(self) -> None:
        """Let the connection go for good: no request on this object connects again.

        The flag is set before the lock is waited for, so a write that is
        already queued behind the one in flight finds the door shut when its
        turn comes, instead of building a connection that nobody would close.
        A request already in flight is let finish; the connection it used is
        closed as soon as it has.
        """
        self._closed = True
        async with self._lock:
            await self._drop_client()

    async def _failed(self, err: Exception, address: int, what: str) -> CtcModbusError:
        """Sort a failed request into silence or a lost line, and act on it.

        Silence keeps the client, unless the library already found the line
        gone, in which case the client is let go now so the settle counts from
        the moment it happened. Anything else is the line, and the client goes.

        A cancellation is none of these. pymodbus from 3.13 catches the
        CancelledError that lands inside a transaction and raises
        ModbusIOException in its place, which would pass for silence here and
        let a round carry on through its remaining blocks after Home Assistant
        told it to stop. The task's own cancelling flag, or the cause chained
        on the exception, says what really happened, and the cancellation is
        raised again with the client let go.
        """
        task = asyncio.current_task()
        if (task is not None and task.cancelling()) or isinstance(
            err.__cause__, asyncio.CancelledError
        ):
            await self._drop_client()
            raise asyncio.CancelledError() from err
        line_up = _connected(self._client)
        if is_silence(err):
            self._last_request = time.monotonic()
            if line_up:
                return CtcModbusSilence(
                    f"register {address} did not answer within {REQUEST_TIMEOUT} s, "
                    "which is how the controller says this model lacks it",
                    address,
                )
            await self._drop_client()
            return CtcModbusSilence(
                f"register {address} did not answer, and the connection went with it",
                address,
                line_up=False,
            )
        await self._drop_client()
        return CtcModbusTransportError(f"{what} of {address} lost the connection: {err}", address)

    def _slave_kwargs(self, client: Any, method: str) -> dict[str, int]:
        """pymodbus renamed the unit argument; support both spellings."""
        import inspect

        try:
            params = inspect.signature(getattr(client, method)).parameters
        except (TypeError, ValueError):  # pragma: no cover
            return {"slave": self._slave}
        if "device_id" in params:
            return {"device_id": self._slave}
        return {"slave": self._slave}

    async def async_read(self, address: int, count: int = 1) -> list[int]:
        """Read holding registers, splitting anything over the block limit."""
        if count < 1:
            return []
        out: list[int] = []
        async with self._lock:
            client = await self._ensure_client()
            kwargs = self._slave_kwargs(client, "read_holding_registers")
            offset = 0
            while offset < count:
                chunk = min(MAX_BLOCK, count - offset)
                at = address + offset
                await self._pace()
                try:
                    result = await client.read_holding_registers(at, count=chunk, **kwargs)
                except Exception as err:  # noqa: BLE001 - pymodbus raises broadly
                    raise await self._failed(err, at, "read") from err
                self._last_request = time.monotonic()
                if result is None or getattr(result, "isError", lambda: True)():
                    raise CtcModbusError(f"read of {at} returned an error", at)
                out.extend(result.registers)
                offset += chunk
        return out

    async def async_write(self, address: int, value: int) -> None:
        """Write one holding register with function code 16, as CTC specifies.

        Nothing but the volatile control registers may be written, whoever is
        asking. The check comes before the lock, so a refused write never waits
        for the connection and never touches it: this is the one hard guard
        against a write landing in the 61500 block, whose EEPROM wears out.
        """
        if address not in CONTROL_ADDRESSES:
            raise CtcModbusError(
                f"refusing to write register {address}: only CTC's volatile control "
                "registers in the 1000 block may be written"
            )
        async with self._lock:
            client = await self._ensure_client()
            kwargs = self._slave_kwargs(client, "write_registers")
            raw = value & 0xFFFF if value >= 0 else (value + 65536) & 0xFFFF
            await self._pace()
            try:
                result = await client.write_registers(address, [raw], **kwargs)
            except Exception as err:  # noqa: BLE001
                raise await self._failed(err, address, "write") from err
            self._last_request = time.monotonic()
            if result is None or getattr(result, "isError", lambda: True)():
                raise CtcModbusError(f"write to {address} returned an error", address)

    async def async_probe(self) -> bool:
        """Confirm the controller answers on the documented outdoor register."""
        values = await self.async_read(PROBE_REGISTER, 1)
        return bool(values)


# Registers closer together than this are fetched in one transaction. Reading a
# few unused registers costs nothing; a separate round trip costs a lot, and the
# controller only allows one master.
BLOCK_GAP = 16


def plan_blocks(sensors: tuple[ModbusSensor, ...]) -> list[tuple[int, int]]:
    """Group register addresses into as few reads as the block limit allows."""
    if not sensors:
        return []
    wanted = sorted({(s.address, s.count) for s in sensors})
    blocks: list[tuple[int, int]] = []
    start = wanted[0][0]
    end = wanted[0][0] + wanted[0][1]
    for address, count in wanted[1:]:
        finish = address + count
        if address - end <= BLOCK_GAP and finish - start <= MAX_BLOCK:
            end = max(end, finish)
        else:
            blocks.append((start, end - start))
            start, end = address, finish
    blocks.append((start, end - start))
    return blocks
