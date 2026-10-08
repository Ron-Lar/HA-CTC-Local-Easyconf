"""One panel, one walker: "read the menu again" shares the harvester's lock.

The options flow used to build a web client of its own for the rescan, with a
panel lock of its own, and walk up to 26 taps while the entry was loaded and
its harvester might be replaying a route on the same display. Two walkers on a
shared panel record routes that are wrong, and those routes are saved in the
options for good. These tests hold the rescan to taking the entry's own lock,
giving way at once when it is taken, and never leaving it held.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"


def run(coro):
    return asyncio.run(coro)


class CountingLock:
    """A panel lock that counts who holds it, and falls over at two."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.owners = 0
        self.most = 0

    def locked(self) -> bool:
        return self._lock.locked()

    async def __aenter__(self):
        await self._lock.acquire()
        self.owners += 1
        self.most = max(self.most, self.owners)
        assert self.owners == 1, "två vandrare på samma panel"
        return self

    async def __aexit__(self, *exc) -> None:
        self.owners -= 1
        self._lock.release()


def _client():
    return SimpleNamespace(panel=CountingLock())


# ------------------------------------------------------------- the lock


def test_a_rescan_walks_under_the_panel_lock(catalogue, monkeypatch):
    client = _client()
    seen: list[int] = []

    async def walk(c, require_root=False):
        # The walk itself must find the lock held, by this walker alone.
        seen.append(c.panel.owners)
        return ["a page"]

    monkeypatch.setattr(catalogue, "async_discover_pages", walk)
    assert run(catalogue.async_rescan_pages(client)) == ["a page"]
    assert seen == [1]
    assert not client.panel.locked()


def test_a_rescan_gives_way_to_a_harvest_that_holds_the_panel(catalogue, monkeypatch):
    client = _client()
    walked = 0

    async def walk(c, require_root=False):
        nonlocal walked
        walked += 1
        return ["a page"]

    monkeypatch.setattr(catalogue, "async_discover_pages", walk)

    async def scenario():
        release = asyncio.Event()

        async def harvest():
            async with client.panel:
                await release.wait()

        harvester = asyncio.create_task(harvest())
        await asyncio.sleep(0)  # the harvester takes the panel
        assert client.panel.locked()
        # The form is answered at once rather than queued behind the harvest.
        with pytest.raises(catalogue.PanelBusy):
            await asyncio.wait_for(catalogue.async_rescan_pages(client), timeout=0.2)
        release.set()
        await harvester
        # And once the panel is free again the same call walks.
        return await catalogue.async_rescan_pages(client)

    assert run(scenario()) == ["a page"]
    assert walked == 1
    assert client.panel.most == 1


def test_a_walk_that_fails_lets_go_of_the_panel(catalogue, web_api, monkeypatch):
    client = _client()

    async def walk(c, require_root=False):
        raise web_api.CtcWebError("the display went quiet")

    monkeypatch.setattr(catalogue, "async_discover_pages", walk)
    with pytest.raises(web_api.CtcWebError):
        run(catalogue.async_rescan_pages(client))
    assert not client.panel.locked()


# --------------------------------------------------------- the call sites


def _calls_in(source: str, class_name: str, method: str) -> set[str]:
    """The names called inside one method, read from the source."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == method:
                    return {
                        getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                        for call in ast.walk(item)
                        if isinstance(call, ast.Call)
                    }
    raise AssertionError(f"{class_name}.{method} saknas")


def test_the_options_flow_walks_with_the_guarded_call():
    # config_flow.py needs Home Assistant and cannot be imported here, so the
    # rescan step is read as source: it walks through async_rescan_pages, which
    # takes the lock, and never through the bare discovery.
    source = (ROOT / "config_flow.py").read_text(encoding="utf-8")
    calls = _calls_in(source, "CtcOptionsFlow", "async_step_rescan")
    assert "async_rescan_pages" in calls
    assert "async_discover_pages" not in calls, "omsökningen går förbi panellåset"
    assert "CtcWebClient" not in calls, "omsökningen bygger en egen klient med eget lås"


def test_the_options_flow_borrows_the_entrys_own_client():
    # The lock only means something when it is the same object the harvester
    # holds, which lives on the entry's runtime data.
    source = (ROOT / "config_flow.py").read_text(encoding="utf-8")
    calls = _calls_in(source, "CtcOptionsFlow", "async_step_rescan")
    assert "_web_client" in calls
    assert 'getattr(self._entry, "runtime_data", None)' in source
    assert 'getattr(runtime, "web_client", None)' in source


def test_the_set_up_keeps_the_client_on_runtime_even_without_pages():
    source = (ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "web_client=web_client" in source, "runtime får inte webbklienten"
    # The runtime field is declared without a default: it is always there,
    # pages or no pages, which is what the options flow relies on.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "CtcRuntime":
            fields = [
                item for item in node.body
                if isinstance(item, ast.AnnAssign) and item.target.id == "web_client"
            ]
            assert fields and fields[0].value is None, "web_client ska vara obligatoriskt"
            return
    raise AssertionError("CtcRuntime saknas")


def test_the_busy_panel_has_its_texts():
    # Without the strings Home Assistant shows the bare key in the dialog.
    for name in ("strings.json", "translations/en.json", "translations/sv.json"):
        texts = json.loads((ROOT / name).read_text(encoding="utf-8"))
        message = texts["options"].get("abort", {}).get("panel_busy")
        assert message, f"panel_busy saknas i {name}"
        assert "–" not in message and " - " not in message
    swedish = json.loads((ROOT / "translations/sv.json").read_text(encoding="utf-8"))
    assert "Försök om en stund" in swedish["options"]["abort"]["panel_busy"]
