"""The entry carries its runtime before the panel is first touched.

"Read the menu again" in the options borrows the loaded entry's web client,
so that it takes the same panel lock as the harvest, the menu re-read and the
walk to the system information page (R10). Home Assistant takes runtime_data
away when an entry is unloaded, and the set-up used to put it back only after
the first harvest had walked the panel. An options dialog open across a
reload could therefore send its rescan into that window, find no runtime,
build a client of its own with a free lock, and walk the same panel the
harvest was walking; the routes recorded then were saved for good. The run
under a real core is in test_homeassistant_rescan.py; this holds the order in
the source, where the ordinary suite can see it.
"""

from __future__ import annotations

import pathlib

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"


def test_the_runtime_is_on_the_entry_before_the_first_harvest():
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    setup = source.split("async def async_setup_entry")[1].split("\nasync def ")[0]
    assert setup.count("entry.runtime_data = runtime") == 1, "runtime sätts på ett ställe"
    assert setup.index("entry.runtime_data = runtime") < setup.index("await web.async_refresh()"), (
        "runtime ska ligga på posten innan första skörden vandrar panelen"
    )


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
