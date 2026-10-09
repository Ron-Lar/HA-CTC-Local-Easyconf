"""A page that stopped answering is a page whose readings age, not a fresh one (R5).

The harvest used to start from a copy of the old data and pass over a page it
could not reach with a debug line: the readings of that page stood in the data
as if just read, Patience counted the round as a success, and "Avgiven värme"
from yesterday looked like today's for days. Now the coordinator keeps the
moment each key was read, a display sensor is available only while that moment
is recent, a page's failure costs that page alone, and only a walk that read no
page at all is a failure of the display, counted by Patience as before.
"""

from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

import ha_stub
from conftest import load

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"

INTERVAL = 1800
HOME = 1


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def coordinator():
    ha_stub.skip_unless_stubbed()
    return load("coordinator")


@pytest.fixture(scope="module")
def harvest():
    return load("harvest")


class FakeDisplay:
    """A display with two harvested pages, each one screen with one reading.

    ``unreachable`` pages answer the walk with False, ``erroring`` pages raise
    on the way there, and ``broken`` screens raise when read. The values a
    screen answers can be changed between rounds through ``values``.
    """

    def __init__(self, web_api, values, start=HOME):
        self.web_api = web_api
        self.panel = asyncio.Lock()
        self.page = start
        self.values = values
        self.unreachable: set[int] = set()
        self.erroring: set[int] = set()
        self.broken: set[int] = set()
        self.visits: list[int] = []

    async def async_current_page(self):
        return self.page

    async def async_goto_page(self, target, route=None):
        if target in self.erroring:
            raise self.web_api.CtcWebError(f"/click timed out on the way to {target}")
        if target in self.unreachable:
            return False
        self.page = target
        self.visits.append(target)
        return True

    async def async_vars(self, screen):
        if screen in self.broken:
            raise self.web_api.CtcWebError(f"/vars/{screen} timed out")
        return list(self.values.get(screen, []))

    async def async_step_back_to(self, target, hops=6):
        self.page = target
        return True

    async def async_goto_home(self, hops=6):
        self.page = HOME
        return HOME


def _page(const, number, raw_index=0):
    return const.SlowPage(
        page=number,
        title=f"Sida {number}",
        screens=[number * 10],
        values=[
            const.SlowValue(
                key=f"p{number}_v",
                label="Värde",
                page=number,
                screen=number * 10,
                fmt="%d",
                var_indices=[raw_index],
            )
        ],
        route=[(100, 250)],
    )


@pytest.fixture()
def harvester(coordinator, web_api, const):
    display = FakeDisplay(web_api, {210: [21], 220: [22]})
    pages = [_page(const, 21), _page(const, 22)]
    web = coordinator.CtcWebCoordinator(hass=object(), client=display, pages=pages, interval=INTERVAL)
    return web, display


def _round(web):
    web.data = run(web._async_update_data())
    return web.data


# ------------------------------------------------------------- the age


def test_a_page_not_reached_keeps_its_old_value_and_its_old_moment(harvester):
    web, display = harvester
    assert _round(web) == {"p21_v": 21.0, "p22_v": 22.0}
    first = dict(web.read_at)
    assert web.pages_read == [21, 22] and web.pages_missed == []

    display.values[210] = [31]
    display.values[220] = [32]
    display.unreachable.add(22)
    assert _round(web) == {"p21_v": 31.0, "p22_v": 22.0}, "sidan som inte nåddes behåller sitt värde"
    assert web.read_at["p21_v"] > first["p21_v"]
    assert web.read_at["p22_v"] == first["p22_v"], "men inte en ny lästid"
    assert web.pages_read == [21] and web.pages_missed == [22]
    # One page out of reach is not a failure of the display.
    assert web.patience.failures == 0
    assert web.last_failure is None


def test_a_reading_is_fresh_for_three_intervals_and_then_stale(harvester, const, harvest):
    web, _display = harvester
    _round(web)
    read = web.read_at["p21_v"]
    limit = timedelta(seconds=const.HARVEST_PATIENCE * INTERVAL)
    assert web.stale_after == limit
    assert web.is_fresh("p21_v")
    assert web.is_fresh("p21_v", now=read + limit), "på gränsen är den färsk"
    assert not web.is_fresh("p21_v", now=read + limit + timedelta(seconds=1))
    assert not web.is_fresh("p99_v"), "en nyckel utan värde är inte färsk"
    # The rule itself, free of the coordinator.
    assert harvest.is_fresh(read, read + limit, limit)
    assert not harvest.is_fresh(None, read, limit)


def test_last_read_is_the_moment_the_key_was_read(harvester):
    web, display = harvester
    assert web.last_read("p21_v") is None
    _round(web)
    assert web.last_read("p21_v") == web.read_at["p21_v"]
    assert web.last_read("p21_v").tzinfo is not None, "tidsstämplad med tidszon"
    assert web.last_harvest is not None
    harvested = web.last_harvest
    # A round that reads nothing moves neither.
    display.unreachable.update({21, 22})
    _round(web)
    assert web.last_read("p21_v") == web.read_at["p21_v"]
    assert web.last_harvest == harvested


# ----------------------------------------------------- one page's trouble


def test_one_pages_error_on_the_way_costs_that_page_alone(harvester):
    web, display = harvester
    _round(web)
    display.values[210] = [41]
    display.erroring.add(22)
    assert _round(web)["p21_v"] == 41.0
    assert web.pages_missed == [22]
    assert web.patience.failures == 0


def test_a_page_whose_screens_cannot_be_read_counts_as_missed(harvester):
    web, display = harvester
    _round(web)
    display.broken.add(220)
    _round(web)
    assert web.pages_read == [21] and web.pages_missed == [22]


def test_a_value_the_screen_stopped_giving_keeps_its_old_moment(harvester, const):
    # The page is read, but the row holds CTC's marker for a missing sensor:
    # the old value stands, with its old time, and ages like a missed page.
    web, display = harvester
    _round(web)
    before = web.read_at["p22_v"]
    display.values[220] = [next(iter(const.SENTINELS))]
    assert _round(web)["p22_v"] == 22.0
    assert web.read_at["p22_v"] == before
    assert web.pages_read == [21, 22], "sidan nåddes, raden lämnade inget tal"


# ----------------------------------------------------- the whole display


def test_no_page_read_at_all_is_a_failure_counted_by_patience(harvester):
    web, display = harvester
    _round(web)
    display.unreachable.update({21, 22})
    # Two rounds keep what we have, the third is shown as a failure.
    assert _round(web) == {"p21_v": 21.0, "p22_v": 22.0}
    assert web.patience.failures == 1
    assert web.last_failure and "no selected page" in web.last_failure
    _round(web)
    with pytest.raises(Exception) as raised:
        run(web._async_update_data())
    assert raised.type.__name__ == "UpdateFailed"
    assert web.patience.failures == 3
    # The retry pace, as before.
    assert web.update_interval == timedelta(seconds=web.patience.seconds)


def test_the_last_error_is_carried_in_the_failure(harvester):
    web, display = harvester
    display.erroring.update({21, 22})
    with pytest.raises(Exception) as raised:
        run(web._async_update_data())
    assert "timed out" in str(raised.value)
    assert web.last_failure == str(raised.value)


def test_a_harvest_that_worked_clears_the_failure(harvester):
    web, display = harvester
    display.unreachable.update({21, 22})
    with pytest.raises(Exception):
        run(web._async_update_data())
    display.unreachable.clear()
    _round(web)
    assert web.last_failure is None
    assert web.patience.failures == 0


def test_the_panel_is_put_back_even_when_a_page_errors(harvester):
    web, display = harvester
    display.erroring.add(22)
    _round(web)
    assert display.page == HOME


# ----------------------------------------------------------- the sensor


def test_the_display_sensor_is_available_by_the_age_of_its_reading():
    # sensor.py imports Home Assistant's sensor platform, which the suite does
    # not stub, so the rule is read as source: availability asks the
    # coordinator whether the key is fresh, and the moment is an attribute.
    source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    body = source.split("class CtcDisplaySensor")[1].split("\nclass ")[0]
    available = body.split("def available")[1].split("def ")[0]
    assert "is_fresh(" in available, "tillgängligheten ska gå på lästiden"
    assert "last_update_success" in available, "och på att displayen svarar alls"
    attributes = body.split("def extra_state_attributes")[1]
    assert '"senast läst"' in attributes
    assert "last_read(" in attributes
    assert 'isoformat(timespec="seconds")' in attributes


def test_the_harvest_is_a_few_named_steps(coordinator):
    # Other work hooks into the loop (the alarm text per page, the skip rule),
    # so the walk stays readable: one method per step, called from the harvest.
    web = coordinator.CtcWebCoordinator
    for step in (
        "_async_origin",
        "_someone_at_the_panel",
        "_note_home",
        "_restore_target",
        "_async_read_page",
        "_async_leave",
    ):
        assert callable(getattr(web, step)), step
