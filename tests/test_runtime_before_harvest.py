"""The entry carries its runtime before the panel is first touched.

"Read the menu again" in the options borrows the loaded entry's web client,
so that it takes the same panel lock as the harvest, the menu re-read and the
walk to the system information page (R10). Home Assistant takes runtime_data
away when an entry is unloaded, and the set-up used to put it back only after
the first harvest had walked the panel. An options dialog open across a
reload could therefore send its rescan into that window, find no runtime,
build a client of its own with a free lock, and walk the same panel the
harvest was walking; the routes recorded then were saved for good.

Since R6 the set-up walks nothing at all: the first harvest is scheduled by
the coordinator, one interval after the last stored one, and the identity is
read in the background. The runtime still has to be on the entry before the
coordinator exists, since the coordinator's timer is what starts the first
walk. The run under a real core is in test_homeassistant_rescan.py; this holds
the order in the source, where the ordinary suite can see it.
"""

from __future__ import annotations

import pathlib

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"


def _setup_source() -> str:
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    return source.split("async def async_setup_entry")[1].split("\nasync def ")[0]


def test_the_runtime_is_on_the_entry_before_the_harvester_exists():
    setup = _setup_source()
    assert setup.count("entry.runtime_data = runtime") == 1, "runtime sätts på ett ställe"
    assert setup.index("entry.runtime_data = runtime") < setup.index("CtcWebCoordinator("), (
        "runtime ska ligga på posten innan skördaren, vars klocka startar första vandringen, finns"
    )


def test_the_set_up_neither_harvests_nor_reads_the_display():
    # A restart costs the panel nothing: no harvest in set-up, with or without
    # a stored one, and no reading of the identity; both belong to the
    # background (see _async_catch_up and the coordinator's own schedule).
    setup = _setup_source()
    assert "web.async_refresh()" not in setup, "uppsättningen får inte skörda"
    assert "web.async_config_entry_first_refresh()" not in setup
    assert "async_read_identity(" not in setup, "identiteten läses i bakgrunden"


def test_the_options_flow_builds_its_own_client_only_without_a_runtime():
    # The borrowing itself: the flow asks the entry for its runtime and takes
    # the client from there, and only an entry without one gets a new client.
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    helper = source.split("def _web_client")[1].split("\n    async def ")[0]
    assert 'getattr(self._entry, "runtime_data", None)' in helper
    assert 'getattr(runtime, "web_client", None)' in helper
    borrowed, built = helper.split("return client")
    assert "CtcWebClient(" not in borrowed
    assert "CtcWebClient(" in built
