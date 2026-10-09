"""A heat pump whose display does not answer is set up on Modbus alone (R11).

The address form used to require /settings/name to answer before Modbus was
even tried, so Web switched off at the panel, an older display, or a firewall
that lets 502 through and nothing else stopped the set-up as "not a CTC",
though the runtime does well without the web. Now a typed address whose web
port is silent is tried on Modbus, and the entry made says so in its data, so
that nothing that needs the display runs for it and the repairs view does not
ask for what cannot be done.

What is pinned here runs without Home Assistant: the rule that reads the data,
the probe that tells a silent web port from one that answers with something
else, and the places in the package that must respect the rule.
test_homeassistant_config_flow.py drives the flow and the set-up under a real
core.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import aiohttp
import pytest

from conftest import COMPONENT

INIT = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
FLOW = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    """One function or method, up to the next definition at its own depth."""
    head, body = source.split(f"def {name}(", 1)
    indent = head[head.rindex("\n") + 1 :].replace("async ", "")
    return re.split(rf"\n{indent}(?:async )?def |\n{indent}@", body)[0]


# ------------------------------------------------------------- the data says so


def test_only_an_entry_that_says_false_is_without_a_display(const):
    assert const.CONF_DISPLAY == "display"
    assert const.has_display({}) is True, "en post från före nyckeln har alltid en display"
    assert const.has_display({"display": True}) is True
    assert const.has_display({"display": False}) is False


def test_the_model_of_a_unit_nobody_named_is_the_family(discovery):
    # The device is called "CTC EcoZenith" rather than "CTC CTC".
    assert discovery.FAMILY == "EcoZenith"


# ------------------------------------------------- silence and a no, told apart


class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body


class _Session:
    """Answers one GET the way it was told to, or fails it with the given error."""

    def __init__(self, status: int = 200, body: str = "", error: BaseException | None = None):
        self._response = _Response(status, body)
        self._error = error
        self.urls: list[str] = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        session = self

        class _Context:
            async def __aenter__(self):
                if session._error is not None:
                    raise session._error
                return session._response

            async def __aexit__(self, *exc):
                return False

        return _Context()


def _probe(discovery, session):
    return asyncio.run(discovery.async_probe_web(session, "192.0.2.55", 80))


def test_a_settings_file_is_a_display(discovery):
    probe = _probe(discovery, _Session(body="settings_ezi3xx.bin\n"))
    assert probe.answered and probe.display is not None
    assert probe.display.model == "EcoZenith i360"


@pytest.mark.parametrize(
    "error",
    [
        aiohttp.ClientConnectorError(
            SimpleNamespace(host="192.0.2.55", port=80, ssl=None), ConnectionRefusedError(61, "refused")
        ),
        asyncio.TimeoutError(),
    ],
    ids=["refused", "timeout"],
)
def test_a_refused_or_silent_web_port_is_silence(discovery, error):
    probe = _probe(discovery, _Session(error=error))
    assert probe.display is None and probe.answered is False


@pytest.mark.parametrize(
    "session",
    [
        _Session(status=400, body="Bad request"),
        _Session(body="<html>a router</html>"),
        _Session(error=aiohttp.ServerDisconnectedError()),
    ],
    ids=["status", "body", "dropped"],
)
def test_anything_else_that_answers_is_something_that_is_not_a_ctc(discovery, session):
    probe = _probe(discovery, session)
    assert probe.display is None and probe.answered is True


def test_the_old_probe_still_gives_the_display_or_nothing(discovery):
    assert asyncio.run(discovery.async_probe_host(_Session(body="settings_ezi2xx.bin"), "h")).model == (
        "EcoZenith i255"
    )
    assert asyncio.run(discovery.async_probe_host(_Session(status=400), "h")) is None


# --------------------------------------------- the flow makes it only by hand


def test_only_a_typed_address_can_make_an_entry_without_a_display():
    given = _function(FLOW, "_async_address_given")
    assert "async_probe_web(" in given and "self._display = False" in given
    assert "elif probe.answered:" in given, "något annat som svarar på webbporten är ingen CTC"
    # The search and DHCP keep asking for the display.
    assert "async_probe_web(" not in _function(FLOW, "async_step_dhcp")
    assert FLOW.count("self._display = False") == 1


def test_an_entry_on_modbus_alone_has_no_pages_and_no_menu():
    create = _function(FLOW, "_create_without_display")
    assert "CONF_SLOW_PAGES: []" in create
    assert "CONF_MENU" not in create
    data = _function(FLOW, "_entry_data")
    assert "CONF_DISPLAY: self._display" in data


def test_read_again_that_finds_pages_gives_the_entry_its_display():
    rescan = FLOW.split("async def async_step_rescan(")[1]
    assert "if self._found and not has_display(self._entry.data):" in rescan
    found = _function(FLOW, "_async_display_found")
    assert "CONF_DISPLAY: True" in found
    # Data and options in one write, so the entry reloads once.
    save = _function(FLOW, "_async_save")
    assert "async_update_entry(self._entry, data=data, options=options)" in save


# ---------------------------------------- and the runtime leaves the display be


def test_the_repairs_view_asks_first_whether_the_display_notices_apply():
    review = _function(INIT, "_async_review_issues")
    code = review.split('"""')[2]
    first = next(line.strip() for line in code.splitlines() if line.strip())
    assert first == "if not _display_notices_apply(hass, entry):"
    rule = _function(INIT, "_display_notices_apply")
    assert "has_display(entry.data)" in rule
    for key in ("ISSUE_PAGES", "ISSUE_HISTORY_PAGE", "ISSUE_IDENTITY"):
        assert key in rule


def test_catching_up_reads_neither_menu_nor_identity_without_a_display():
    catch_up = _function(INIT, "_async_catch_up")
    code = catch_up.split('"""')[2]
    guard = code.index("if not has_display(entry.data):")
    assert guard < code.index("while True:")
    assert code.index("_async_check_release(") < guard, "releasekollen gäller alla poster"


def test_no_link_to_a_web_interface_that_did_not_answer():
    setup = _function(INIT, "async_setup_entry")
    assert "configuration_url=web_interface_url(host, web_port) if has_display(entry.data) else None" in setup
