"""Name what keeps Modbus away, after a connection has failed (roadmap L12).

pymodbus hides how a connection failed: ``connect()`` answers False on any
OSError, and a reset that follows the first request surfaces as a timeout. So
a port that refuses, a controller whose one Modbus place another client holds,
and a passing hiccup all look the same from the integration's own client, and
the set-up flow and the entry said the wrong thing about the commonest of
them: a ``modbus:`` block left in configuration.yaml looked like a dead pump.

A raw socket tells them apart, with one read of the outdoor register:

* the port refuses, or nothing answers the handshake in time: CLOSED. Modbus
  TCP is off at the panel, the address is wrong, or the unit is out of reach;
* the handshake is accepted and reset at once, before the request or after it,
  or the request is met with the end of the stream: BUSY. The controller takes
  one Modbus client at a time and another one holds the place. A reset that
  comes before asyncio has told the connection made surfaces from the connect
  itself, as "Connect call failed [Errno 104]", the very line the houses logged
  while their place was taken; a port that is off refuses the handshake instead;
* the request is answered with the register: ANSWERED. Whatever failed a
  moment ago has passed;
* something answers, but not with the register: a Modbus exception, an answer
  to another request, or bytes that are no Modbus at all, such as a web
  server on the port typed for Modbus: REJECTED. The port or the Modbus address
  is wrong, and trying again changes nothing;
* the request goes unanswered while the line stays up: SILENT, nothing to name.

The probe is a second client, so it runs only after the integration's own
client has failed and been closed, never while a working session exists:
against a controller that is talking to the integration it would be reset
itself or take the place. It waits out the controller's settle time before it
knocks, because within that time the controller still believes the closed
session is there and resets anyone else, which would read as BUSY; and it
notes its own close, so the next real connection waits in its turn. Free of
Home Assistant, so the suite can drive it against a server on loopback.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time

from .modbus_api import PROBE_REGISTER, note_close, settle_wait

_LOGGER = logging.getLogger(__name__)

CLOSED = "closed"
BUSY = "busy"
ANSWERED = "answered"
SILENT = "silent"
REJECTED = "rejected"

#: The shortest Modbus TCP answer: the seven bytes of the MBAP header, the
#: function code, and one byte more, an exception code or a byte count.
SHORTEST_ANSWER = 9

#: How long the handshake and the answer may each take.
PROBE_TIMEOUT = 5.0

#: How long the set-up reuses a verdict rather than knock again. Home Assistant
#: retries a set-up that is not ready after 5, 10, 20 and 40 seconds and up to
#: every ten minutes; a probe on each of those would knock on a controller that
#: is already turning a client away, as often again as the retries themselves.
PROBE_EVERY = 600.0

#: The latest verdict per unit, with the moment it was reached on the monotonic
#: clock, kept per address like the settle time in modbus_api.
_VERDICTS: dict[tuple[str, int], tuple[float, str]] = {}


def read_request(transaction: int, unit: int, address: int = PROBE_REGISTER) -> bytes:
    """A Modbus TCP request to read one holding register, function code 3."""
    pdu = struct.pack(">BHH", 3, address, 1)
    return struct.pack(">HHHB", transaction, 0, len(pdu) + 1, unit) + pdu


def verdict_of_answer(answer: bytes, transaction: int) -> str:
    """What the bytes that came back to read_request(transaction, ...) say.

    Nothing, the end of the stream at the request: BUSY, as a reset is. A
    Modbus TCP answer to this very request with function code 3: ANSWERED.
    Anything else is REJECTED: an exception (function code 0x83), an answer to
    another transaction, which the integration's own client throws away as
    well, and anything that is no Modbus answer at all. The unit byte is not
    asked: a controller that answers the read under another unit number has
    still answered it, and calling that wrong would send the owner to change
    a Modbus address that works.
    """
    if not answer:
        return BUSY
    if len(answer) < SHORTEST_ANSWER:
        return REJECTED
    echoed, protocol, _length, _unit, function = struct.unpack(">HHHBB", answer[:8])
    if echoed == transaction and protocol == 0 and function == 3:
        return ANSWERED
    return REJECTED


async def _async_read_answer(reader: asyncio.StreamReader, timeout: float) -> bytes:
    """The bytes that answer the request, enough of them to judge.

    The first must come within ``timeout``, or asyncio's TimeoutError says the
    line is silent. An answer split over segments is read on until it holds
    the shortest Modbus answer or the stream ends, within the same timeout
    counted from the start, so a trickle is not waited on for long.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    answer = await asyncio.wait_for(reader.read(260), timeout)
    while answer and len(answer) < SHORTEST_ANSWER:
        left = deadline - loop.time()
        if left <= 0:
            break
        try:
            more = await asyncio.wait_for(reader.read(260 - len(answer)), left)
        except asyncio.TimeoutError:
            break
        if not more:
            break
        answer += more
    return answer


async def _async_knock(host: str, port: int, unit: int, timeout: float) -> str:
    """Open the port, ask for the outdoor register once, and say what happened."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (ConnectionResetError, ConnectionAbortedError):
        # The handshake went through and the controller reset it before asyncio
        # had told the connection made: a taken place, as a reset at the request
        # is. A port that is off refuses the handshake (ConnectionRefusedError).
        # The controller saw a connection, so it is owed its settle time.
        note_close(host, port, time.monotonic())
        return BUSY
    except (OSError, asyncio.TimeoutError):
        return CLOSED
    try:
        writer.write(read_request(1, unit))
        await writer.drain()
        answer = await _async_read_answer(reader, timeout)
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        return BUSY
    except asyncio.TimeoutError:
        return SILENT
    except OSError:
        return SILENT
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1.0)
        except (OSError, asyncio.TimeoutError):
            pass
        # A connection was made, so the controller is owed its settle time
        # before the integration's own client knocks again.
        note_close(host, port, time.monotonic())
    return verdict_of_answer(answer, 1)


async def async_classify(
    host: str, port: int = 502, unit: int = 1, timeout: float = PROBE_TIMEOUT
) -> str:
    """Say why Modbus did not answer: CLOSED, BUSY, ANSWERED, REJECTED or SILENT.

    Waits out the settle time of a connection to the same unit that was closed
    just now, the integration's own failed one as a rule, and records the
    verdict for async_classify_cached.
    """
    waiting = settle_wait(host, port, time.monotonic())
    if waiting:
        await asyncio.sleep(waiting)
    verdict = await _async_knock(host, port, unit, timeout)
    _VERDICTS[(host, port)] = (time.monotonic(), verdict)
    _LOGGER.debug("The Modbus port of %s:%s, probed after a failure: %s", host, port, verdict)
    return verdict


def recent_verdict(host: str, port: int, now: float, every: float = PROBE_EVERY) -> str | None:
    """The verdict on this unit reached within ``every`` seconds, if there is one."""
    found = _VERDICTS.get((host, port))
    if found is None or now - found[0] >= every:
        return None
    return found[1]


async def async_classify_cached(
    host: str, port: int = 502, unit: int = 1, every: float = PROBE_EVERY
) -> str:
    """As async_classify, but a verdict younger than ``every`` seconds is reused.

    For the set-up, which Home Assistant retries on its own: the reason it
    gives stays named, and the controller is knocked on at most once in that
    time by the probe.
    """
    known = recent_verdict(host, port, time.monotonic(), every)
    if known is not None:
        return known
    return await async_classify(host, port, unit)
