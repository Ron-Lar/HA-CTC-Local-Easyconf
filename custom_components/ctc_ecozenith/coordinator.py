"""Update coordinators for the two transports.

The Modbus coordinator is the workhorse: it reads the documented registers in
contiguous blocks, it has no side effects and it runs on a short interval.

The web coordinator harvests the values Modbus does not expose. Reading a page
other than the one on the panel means navigating there, which moves the physical
display, so it runs rarely, it checks first whether somebody is using the panel,
and it puts the panel back when it is done.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .catalogue import numeric_value
from .const import (
    HARVEST_PATIENCE,
    RETRY_INTERVAL,
    CONTROL_EXPIRY_SECONDS,
    CONTROL_KEEPALIVE_SECONDS,
    DOMAIN,
    MODBUS_SENSORS,
    MODBUS_SETTINGS,
    ModbusSensor,
    SlowPage,
)
from .keepalive import Keepalive
from .patience import Patience
from .modbus_api import (
    REQUEST_TIMEOUT,
    CtcModbusClient,
    CtcModbusError,
    CtcModbusTransportError,
    decode_reading,
    plan_blocks,
)
from .poll import MissingBlocks, SlowRounds, read_round
from .web_api import CtcWebClient, CtcWebError

_LOGGER = logging.getLogger(__name__)

class CtcModbusCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the documented Modbus registers."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: CtcModbusClient,
        interval: int,
        include_settings: bool = True,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} modbus",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.descriptions: tuple[ModbusSensor, ...] = (
            MODBUS_SENSORS + MODBUS_SETTINGS if include_settings else MODBUS_SENSORS
        )
        self._blocks = plan_blocks(self.descriptions)
        #: Blocks this model has turned out not to have. Learnt per run, so a
        #: restart gives every block a fresh chance.
        self._missing = MissingBlocks()
        self._slow = SlowRounds()
        #: The code behind every enum reading, by sensor key, kept beside the
        #: label in ``data``. The derived binary sensors judge these, since a
        #: label is a string that may be reworded or translated while the code
        #: is what the controller said. Only trusted for a key that is in data.
        self.codes: dict[str, int] = {}
        #: The code behind an enum reading the table has no label for, by sensor
        #: key, so the number is still visible in the sensor's attributes.
        self.unknown_codes: dict[str, int] = {}
        self._said_codes: set[tuple[str, int]] = set()
        # Cumulative count of register blocks that did not answer, or rounds
        # that lost the connection. Only used by the optional daily report,
        # which sends the delta since it last ran.
        self.read_failures = 0

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            result = await read_round(self.client, self._blocks, self._missing.missing)
        except CtcModbusTransportError as err:
            self.read_failures += 1
            # Home Assistant logs the failure once, and the recovery, by itself.
            raise UpdateFailed(f"the heat pump was lost at register {err.address}: {err}") from err
        self.read_failures += len(result.unanswered) + len(result.dropped)

        for start in self._missing.note(result):
            count = next((n for s, n in self._blocks if s == start), 1)
            _LOGGER.info(
                "Registers %s to %s have not answered in %s rounds while the rest did, so "
                "this model is taken not to have them and they are left out until Home "
                "Assistant restarts",
                start,
                start + count - 1,
                self._missing.patience,
            )
        interval = self.update_interval.total_seconds() if self.update_interval else 0.0
        pace = self._slow.note(result.elapsed, interval)
        if pace == "slow":
            _LOGGER.warning(
                "A Modbus round took %.1f s against an interval of %.0f s, so readings "
                "are older than they look; a block that never answers costs %s s each "
                "round until it is learnt as missing",
                result.elapsed,
                interval,
                REQUEST_TIMEOUT,
            )
        elif pace == "recovered":
            _LOGGER.info("Modbus rounds are back within the interval (%.1f s)", result.elapsed)

        raw = result.raw
        if not raw:
            raise UpdateFailed("no Modbus register could be read")

        data: dict[str, Any] = {}
        for description in self.descriptions:
            value = self._decode(description, raw)
            if value is not None:
                data[description.key] = value
        return data

    def _decode(self, description: ModbusSensor, raw: dict[int, int]) -> Any:
        """The reading for one description, with the bookkeeping around it.

        The decoding itself is decode_reading in modbus_api, free of Home
        Assistant and tested on its own; this keeps the codes the table has no
        label for, so the sensor can carry the number as an attribute.
        """
        reading = decode_reading(description, raw)
        if reading.code is None:
            self.codes.pop(description.key, None)
        else:
            self.codes[description.key] = reading.code
        if description.enum is None or reading.value is None:
            return reading.value
        # A label of the integration's own making ("Okänd (12)") is not one of
        # the sensor's options, and Home Assistant answers a state that is not
        # with an exception on every write: the entity then stops updating at
        # all. VSH's i255 sat in system status 12 behind exactly that.
        if reading.unknown is None:
            self.unknown_codes.pop(description.key, None)
        else:
            self.unknown_codes[description.key] = reading.unknown
            if (description.key, reading.unknown) not in self._said_codes:
                self._said_codes.add((description.key, reading.unknown))
                _LOGGER.info(
                    "%s answered with code %s, which the table has no label for, so the "
                    "sensor reads %s and carries the code as an attribute",
                    description.key,
                    reading.unknown,
                    reading.value,
                )
        return reading.value


class CtcWebCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Harvest the display's own values for the pages the user selected."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: CtcWebClient,
        pages: list[SlowPage],
        interval: int,
        restore_page: bool = True,
        home_page: int | None = None,
        on_home_page_found: Any = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} display",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.pages = pages
        self.restore_page = restore_page
        #: The page the panel was showing before this integration first touched
        #: it. Kept across restarts so that one failed restore cannot make the
        #: wrong page the new normal.
        self.home_page = home_page
        self._on_home_page_found = on_home_page_found
        self._expected_page: int | None = None
        self.last_skip_reason: str | None = None
        #: When each value was last actually read off the panel. The data keeps
        #: a value through a skipped cycle, so this is the only way to tell a
        #: fresh reading from a carried one.
        self.read_at: dict[str, datetime] = {}
        #: A display that is merely slow should not take every reading with it.
        self.patience = Patience(interval, RETRY_INTERVAL, HARVEST_PATIENCE)

    async def _async_update_data(self) -> dict[str, Any]:
        if not self.pages:
            return {}
        async with self.client.panel:
            try:
                data = await self._async_harvest()
            except UpdateFailed as err:
                shown = self.patience.failed(bool(self.data))
                self.update_interval = timedelta(seconds=self.patience.seconds)
                if shown:
                    raise
                _LOGGER.debug("Display harvest failed (%s), keeping what we have: %s",
                              self.patience.failures, err)
                return dict(self.data or {})
        self.patience.worked()
        self.update_interval = timedelta(seconds=self.patience.seconds)
        return data

    async def _async_harvest(self) -> dict[str, Any]:
        data: dict[str, Any] = dict(self.data or {})
        try:
            origin = await self.client.async_current_page()
        except CtcWebError as err:
            raise UpdateFailed(f"could not read the panel state: {err}") from err

        # Somebody standing at the panel would be fighting us for it. If the page
        # is not where we left it, leave it alone this round.
        if (
            self._expected_page is not None
            and origin != self._expected_page
            and len(self.pages) > 1
        ):
            self.last_skip_reason = "panelen används av någon annan"
            _LOGGER.debug("Panel is on page %s, not %s; skipping this cycle", origin, self._expected_page)
            return data
        self.last_skip_reason = None

        if self.home_page is None and origin not in {p.page for p in self.pages}:
            # First time in: whatever the panel was showing is where it belongs.
            self.home_page = origin
            if self._on_home_page_found is not None:
                self._on_home_page_found(origin)

        restore_to = None
        if self.restore_page:
            # Restoring to the page this cycle started on is right until a
            # restore fails, after which that wrong page would become the new
            # reference. The remembered home page breaks that loop.
            restore_to = origin
            if origin in {p.page for p in self.pages} and self.home_page is not None:
                restore_to = self.home_page
        try:
            for page in self.pages:
                if await self.client.async_current_page() != page.page:
                    # The route was recorded during setup. Replaying it is the
                    # only reliable way in, since the menu layout differs between
                    # models and cannot be derived at poll time.
                    moved = await self.client.async_goto_page(page.page, page.route)
                    if not moved:
                        _LOGGER.debug("Could not reach page %s", page.page)
                        continue
                values_by_screen: dict[int, list[Any]] = {}
                for screen in page.screens:
                    try:
                        values_by_screen[screen] = await self.client.async_vars(screen)
                    except CtcWebError as err:
                        _LOGGER.debug("Screen %s unreadable: %s", screen, err)
                read_at = datetime.now(timezone.utc)
                for value in page.values:
                    number = numeric_value(value, values_by_screen.get(value.screen, []))
                    if number is not None:
                        data[value.key] = number
                        self.read_at[value.key] = read_at
        except CtcWebError as err:
            raise UpdateFailed(f"display read failed: {err}") from err
        finally:
            if restore_to is not None:
                try:
                    await self._async_restore(restore_to)
                except CtcWebError:
                    _LOGGER.debug("Could not restore the panel to page %s", restore_to)
            try:
                self._expected_page = await self.client.async_current_page()
            except CtcWebError:
                self._expected_page = None

        if not data:
            raise UpdateFailed("no value could be read from the display")
        return data

    async def async_restore_page(self, target: int) -> bool:
        """Put the panel back on ``target``, for callers outside the harvest.

        The caller holds ``client.panel``: this is handed to the walk that reads
        the system information page, which takes the lock for its whole trip.
        The lock is not reentrant, so taking it here would deadlock.
        """
        return await self._async_restore(target)

    async def _async_restore(self, target: int) -> bool:
        """Put the panel back, trying every way in that is known.

        A recorded route is the surest, stepping back works inside a submenu,
        and the home screen is the last resort so the panel is at least left
        somewhere sensible rather than deep in a menu.
        """
        route = next((p.route for p in self.pages if p.page == target and p.route), None)
        if await self.client.async_goto_page(target, route):
            return True
        if await self.client.async_step_back_to(target):
            return True
        if self.home_page is not None and target != self.home_page:
            home_route = next(
                (p.route for p in self.pages if p.page == self.home_page and p.route), None
            )
            if await self.client.async_goto_page(self.home_page, home_route):
                return True
        await self.client.async_goto_home()
        return await self.client.async_current_page() == target


class CtcControlManager:
    """Keep CTC's volatile control registers alive, and say truthfully which are.

    The 1000 block is write only and the controller forgets it roughly five
    minutes after the last write. Rewriting every minute keeps a wide margin, and
    stopping simply hands control back to the heat pump.

    An override counts as in force only from a write that reached the unit, and
    only while one has reached it within CONTROL_EXPIRY_SECONDS. The rule itself
    is in keepalive.py; this is the Home Assistant side of it: the timer, the
    log and the listeners.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: CtcModbusClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._hass = hass
        self._client = client
        self._state = Keepalive(CONTROL_EXPIRY_SECONDS)
        # Wall clock rather than monotonic, because the entities show these
        # moments as timestamps and one clock has to serve both.
        self._clock = clock
        self._unsub = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def active(self) -> dict[int, int]:
        return self._state.active

    def get(self, address: int) -> int | None:
        return self._state.get(address)

    def written_at(self, address: int) -> datetime | None:
        """When a write of this address last reached the controller."""
        return self._moment(self._state.written_at(address))

    def valid_until(self, address: int) -> datetime | None:
        """Until when the controller is known to hold this address."""
        return self._moment(self._state.valid_until(address))

    @staticmethod
    def _moment(seconds: float | None) -> datetime | None:
        if seconds is None:
            return None
        return datetime.fromtimestamp(seconds, tz=timezone.utc)

    def written_attributes(self, address: int) -> dict[str, str]:
        """The two moments as entity attributes, for the numbers and the selects.

        Empty while nothing is in force, so an idle entity carries no stale times.
        """
        written = self.written_at(address)
        until = self.valid_until(address)
        if written is None or until is None:
            return {}
        return {
            "senast skriven": written.isoformat(timespec="seconds"),
            "gäller till": until.isoformat(timespec="seconds"),
        }

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call back whenever an override is set or released; returns the undo."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def async_set(self, address: int, raw: int | None) -> None:
        """Set or release one control register.

        The value is recorded only once the write has reached the controller, so
        a failed write never shows as control in force.
        """
        if raw is None:
            self._state.release(address)
            if not self._state:
                self._stop_timer()
            self._notify()
            return
        try:
            await self._client.async_write(address, raw)
        except CtcModbusError:
            # A write that fails while an older value is in force counts against
            # that value's time, like a failed refresh would.
            if self._state.failed(address, self._clock()):
                self._said_released(address)
                if not self._state:
                    self._stop_timer()
                self._notify()
            raise
        recovered = self._state.written(address, raw, self._clock())
        _LOGGER.debug("Control register %s set to %s", address, raw)
        if recovered:
            _LOGGER.info("Control register %s reached the controller again", address)
        self._ensure_timer()
        self._notify()

    def async_release_all(self) -> None:
        """Stop overriding anything and hand the unit back to its own settings.

        Nothing is written: the controller forgets an override about five
        minutes after the last write, so it is enough to stop writing. A number
        has no release position of its own, which makes this the only way back
        from one short of restarting Home Assistant.
        """
        self._state.clear()
        self._stop_timer()
        self._notify()

    def _ensure_timer(self) -> None:
        if self._unsub is not None or not self._state:
            return
        from homeassistant.helpers.event import async_track_time_interval

        self._unsub = async_track_time_interval(
            self._hass,
            self._async_refresh,
            timedelta(seconds=CONTROL_KEEPALIVE_SECONDS),
        )

    def _stop_timer(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def _async_refresh(self, _now) -> None:
        """Write every override again, and give up the ones the unit has forgotten.

        A miss is a debug line, except the first of a run, which is a warning
        that says what happens next. The release is one warning; a write that
        reaches the unit again after misses is one info line.
        """
        changed = False
        for address, raw in self._state.active.items():
            try:
                await self._client.async_write(address, raw)
            except CtcModbusError as err:
                now = self._clock()
                if self._state.failed(address, now):
                    self._said_released(address)
                    changed = True
                elif self._state.failures(address) == 1:
                    until = self._moment(self._state.valid_until(address))
                    _LOGGER.warning(
                        "Could not refresh control register %s (%s). The controller holds "
                        "the last value until about %s and the override is released then "
                        "unless a write reaches it",
                        address,
                        err,
                        until.isoformat(timespec="seconds") if until else "?",
                    )
                else:
                    _LOGGER.debug("Could not refresh control register %s: %s", address, err)
                continue
            if self._state.written(address, raw, self._clock()):
                _LOGGER.info("Control register %s reached the controller again", address)
                changed = True
            else:
                _LOGGER.debug("Control register %s refreshed with %s", address, raw)
        if changed:
            if not self._state:
                self._stop_timer()
            self._notify()

    def _said_released(self, address: int) -> None:
        _LOGGER.warning(
            "Control register %s released: no write has reached the controller for %s s, "
            "so it has forgotten the value and Home Assistant no longer claims it",
            address,
            CONTROL_EXPIRY_SECONDS,
        )

    async def async_stop(self) -> None:
        self._stop_timer()
        self._state.clear()
