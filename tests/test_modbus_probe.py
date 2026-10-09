"""The raw probe that names what keeps Modbus away (L12), against servers on loopback.

A controller whose one Modbus place another client holds accepts the handshake
and resets the connection as soon as it is asked anything. pymodbus turns that
into a timeout, which looks like a dead pump. The probe asks with a raw socket
and tells four cases apart; here each is played by a small server on
127.0.0.1, and the two rules around it are pinned: the probe waits out the
settle time of a connection closed just before, and it notes its own close so
the next real client waits in turn.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import time

import pytest

from conftest import load

modbus_api = load("modbus_api")
probe = load("modbus_probe")


@pytest.fixture(autouse=True)
def loopback(request):
    """Sockets on 127.0.0.1, also where a plugin in the run blocks them by default.

    pytest-homeassistant-custom-component, loaded in the Home Assistant run,
    switches sockets off for every test and lets only 127.0.0.1 through once
    they are switched back on; its own fixture for that is used where it exists.
    """
    try:
        request.getfixturevalue("socket_enabled")
    except pytest.FixtureLookupError:
        pass
    modbus_api._CLOSED_AT.clear()
    probe._VERDICTS.clear()
    yield
    modbus_api._CLOSED_AT.clear()
    probe._VERDICTS.clear()


class Controller:
    """A stand-in for the controller's Modbus port, behaving as told.

    ``reset_at_accept``: resets the connection the moment it is made.
    ``reset``: reads the request and resets, as a CTC does for a second client.
    ``eof``: reads the request and closes cleanly.
    ``answer``: answers the read with one register.
    ``silent``: reads the request and says nothing, keeping the line up.
    """

    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self.server: asyncio.AbstractServer | None = None
        self.writers: list[asyncio.StreamWriter] = []
        self.requests: list[bytes] = []

    async def start(self) -> int:
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    @staticmethod
    def _reset(writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        writer.transport.abort()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        if self.behaviour == "reset_at_accept":
            self._reset(writer)
            return
        try:
            request = await reader.readexactly(12)
        except (asyncio.IncompleteReadError, ConnectionError):
            return
        self.requests.append(request)
        if self.behaviour == "reset":
            self._reset(writer)
        elif self.behaviour == "eof":
            writer.close()
        elif self.behaviour == "answer":
            tid, _protocol, _length, unit, function = struct.unpack(">HHHBB", request[:8])
            pdu = struct.pack(">BBBH", unit, function, 2, 72)
            writer.write(struct.pack(">HHH", tid, 0, len(pdu)) + pdu)
            await writer.drain()
        # "silent" holds the line and says nothing.

    async def stop(self) -> None:
        assert self.server is not None
        self.server.close()
        for writer in self.writers:
            writer.transport.abort()
        await asyncio.sleep(0.05)


def _classify(behaviour: str, timeout: float = 1.0) -> tuple[str, Controller]:
    async def scenario():
        controller = Controller(behaviour)
        port = await controller.start()
        try:
            verdict = await probe.async_classify("127.0.0.1", port, unit=1, timeout=timeout)
        finally:
            await controller.stop()
        return verdict, controller

    return asyncio.run(scenario())


# ------------------------------------------------------------- the four cases


def test_a_reset_after_the_handshake_is_a_busy_place():
    verdict, controller = _classify("reset")
    assert verdict == probe.BUSY
    # It asked the one register every model answers, with function code 3.
    (request,) = controller.requests
    assert request == probe.read_request(1, 1)
    assert struct.unpack(">BH", request[7:10]) == (3, modbus_api.PROBE_REGISTER)


def test_a_reset_the_moment_it_is_accepted_is_a_busy_place_too():
    verdict, _ = _classify("reset_at_accept")
    assert verdict == probe.BUSY


def test_the_end_of_the_stream_after_the_request_is_a_busy_place():
    verdict, _ = _classify("eof")
    assert verdict == probe.BUSY


def test_an_answer_means_the_failure_has_passed():
    verdict, _ = _classify("answer")
    assert verdict == probe.ANSWERED


def test_silence_with_the_line_up_names_nothing():
    verdict, _ = _classify("silent", timeout=0.3)
    assert verdict == probe.SILENT


def test_a_port_that_refuses_is_closed():
    spare = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    spare.bind(("127.0.0.1", 0))
    port = spare.getsockname()[1]
    spare.close()
    verdict = asyncio.run(probe.async_classify("127.0.0.1", port, timeout=1.0))
    assert verdict == probe.CLOSED
    # Nothing was connected, so nothing is owed a settle time.
    assert ("127.0.0.1", port) not in modbus_api._CLOSED_AT


# ------------------------------------------------- the settle time, both ways


def test_the_probe_waits_out_the_settle_of_a_connection_closed_just_before(monkeypatch):
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def sleep(seconds, *args, **kwargs):
        if seconds >= 1:
            slept.append(seconds)
            return None
        return await real_sleep(seconds, *args, **kwargs)

    monkeypatch.setattr(probe.asyncio, "sleep", sleep)

    async def scenario():
        controller = Controller("answer")
        port = await controller.start()
        # The integration's own client, closed a moment ago.
        modbus_api.note_close("127.0.0.1", port, time.monotonic())
        try:
            return await probe.async_classify("127.0.0.1", port, timeout=1.0), port
        finally:
            await controller.stop()

    verdict, port = asyncio.run(scenario())
    assert verdict == probe.ANSWERED
    (waited,) = slept
    assert modbus_api.CLOSE_SETTLE - 1 < waited <= modbus_api.CLOSE_SETTLE
    # And its own close is noted, so the next real client settles in turn.
    assert modbus_api.settle_wait("127.0.0.1", port, time.monotonic()) > modbus_api.CLOSE_SETTLE - 1


# --------------------------------------------- the set-up reuses a fresh verdict


def test_the_set_up_reuses_a_verdict_younger_than_the_interval(monkeypatch):
    knocks: list[str] = []

    async def knock(host, port, unit, timeout):
        knocks.append(host)
        return probe.BUSY

    monkeypatch.setattr(probe, "_async_knock", knock)
    assert asyncio.run(probe.async_classify_cached("192.0.2.55", 502)) == probe.BUSY
    assert asyncio.run(probe.async_classify_cached("192.0.2.55", 502)) == probe.BUSY
    assert knocks == ["192.0.2.55"], "en dom yngre än intervallet återanvänds"
    # Another unit is a question of its own.
    asyncio.run(probe.async_classify_cached("192.0.2.56", 502))
    assert knocks == ["192.0.2.55", "192.0.2.56"]


def test_an_old_verdict_is_asked_again():
    probe._VERDICTS[("192.0.2.55", 502)] = (100.0, probe.BUSY)
    assert probe.recent_verdict("192.0.2.55", 502, now=100.0 + probe.PROBE_EVERY - 1) == probe.BUSY
    assert probe.recent_verdict("192.0.2.55", 502, now=100.0 + probe.PROBE_EVERY) is None
    assert probe.recent_verdict("192.0.2.99", 502, now=100.0) is None
    assert probe.PROBE_EVERY == 600.0


# ------------------------------------- only after a failure, never beside a session


def _source(name: str) -> str:
    from conftest import COMPONENT

    return (COMPONENT / name).read_text(encoding="utf-8")


def test_the_probe_is_called_from_the_two_failure_paths_and_nowhere_else():
    from conftest import COMPONENT

    users = sorted(
        path.name
        for path in COMPONENT.glob("*.py")
        if path.name != "modbus_probe.py" and "modbus_probe" in path.read_text(encoding="utf-8")
    )
    assert users == ["__init__.py", "config_flow.py"]


def test_the_flow_probes_only_once_its_own_client_is_closed():
    flow = _source("config_flow.py")
    connect = flow.split("async def async_step_connect(")[1].split("\n    def ")[0]
    assert connect.index("await client.async_close()") < connect.index("self._async_modbus_failed()")
    failed = flow.split("async def _async_modbus_failed(")[1].split("\n    async def ")[0]
    assert "await async_classify(" in failed


def test_the_set_up_probes_only_once_its_own_client_is_shut():
    init = _source("__init__.py")
    setup = init.split("async def async_setup_entry(")[1]
    failing = setup.split("except Exception as err:")[1].split("raise\n")[0]
    assert failing.index("await modbus_client.async_shutdown()") < failing.index(
        "_async_name_the_failure("
    )
    # And the notice goes at the first reading that works.
    after = setup.split("raise\n", 1)[1]
    first = next(line.strip() for line in after.splitlines() if line.strip() and not line.strip().startswith("#"))
    assert first == 'ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_{ISSUE_MODBUS_BUSY}")'


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json", "translations/sv.json"])
def test_every_text_the_probe_needs_is_there(name):
    import json

    texts = json.loads(_source(name))
    assert "{host}" in texts["config"]["step"]["modbus_busy"]["description"]
    assert "modbus:" in texts["config"]["step"]["modbus_busy"]["description"]
    for key in ("modbus_busy", "modbus_transient"):
        assert texts["config"]["error"][key]
    assert texts["issues"]["modbus_busy"]["title"]
    for key in ("modbus_busy", "modbus_closed"):
        assert texts["exceptions"][key]["message"]
