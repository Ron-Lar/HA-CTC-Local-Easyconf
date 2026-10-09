"""The heat pump's transitions: starts, run length, defrosts and what changed when.

Modbus register 62017 says what the heat pump is doing, every half minute, and
the derived binary sensors show that as a state. What a state cannot show is
the change: when the compressor last started, how many times it has started
today, how long the last run lasted, how often and how long an air to water
unit defrosts, which is the difference between a healthy one and an iced up
one. This module watches the codes from one poll to the next and keeps that
bookkeeping, and the transitions it finds are what the sensors read.

Everything is judged on the controller's codes, never on the Swedish labels,
as the binary sensors do (const.HP_RUNNING_CODES and friends). A run is any
stretch in which the compressor turns: heating, cooling, hot water and
defrosting, so a defrost in the middle of a run is neither a stop nor a start.
The codes that say nothing about the compressor (not defined, not available,
communication error, function test) and a register that did not answer leave
the last known state in place: a communication error that comes and goes must
not be counted as starts.

The first sample sets the baseline and raises nothing, so a restart of Home
Assistant never reports the state it finds as a change. A poll that failed is
no sample; after an outage the next sample is compared with the last one
before it, so a stop that happened in between is dated at the recovery.
Nothing is stored: the counts since midnight start over with Home Assistant,
and the entities say from when they count.

Free of Home Assistant, like keepalive.py and patience.py: the clock is handed
in as an aware datetime in local time, which is what midnight is judged on.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable

from .catalogue import is_period_count
from .const import (
    HP_ALARM_CODE,
    HP_DEFROST_CODE,
    HP_RUNNING_CODES,
    SG_MODE,
    STATUS_HEATPUMP,
    STATUS_SYSTEM,
    STATUS_UNKNOWN,
)

#: The codes in which the compressor turns. A defrost reverses the circuit but
#: the compressor keeps running, so it belongs to the run it happens in.
HP_RUN_CODES: frozenset[int] = HP_RUNNING_CODES | {HP_DEFROST_CODE}

#: Codes that say nothing about the compressor: the heat pump is not defined,
#: not available or out of contact with the controller, or an installer is
#: running parts by hand in a function test. A sample with one of these leaves
#: the last known state in place, like a register that did not answer.
HP_SILENT_CODES: frozenset[int] = frozenset({8, 30, 31, 32})

EVENT_COMPRESSOR_START = "kompressor_start"
EVENT_COMPRESSOR_STOP = "kompressor_stopp"
EVENT_DEFROST_START = "avfrostning_start"
EVENT_DEFROST_END = "avfrostning_slut"
EVENT_ALARM = "larm"
EVENT_ALARM_CLEARED = "larm_borta"
EVENT_SMARTGRID_CHANGED = "smartgrid_andrad"
EVENT_SYSTEM_STATUS_CHANGED = "systemstatus_andrad"

#: Every kind of transition the watch reports, in the order they are listed
#: for an event entity.
EVENT_TYPES: tuple[str, ...] = (
    EVENT_COMPRESSOR_START,
    EVENT_COMPRESSOR_STOP,
    EVENT_DEFROST_START,
    EVENT_DEFROST_END,
    EVENT_ALARM,
    EVENT_ALARM_CLEARED,
    EVENT_SMARTGRID_CHANGED,
    EVENT_SYSTEM_STATUS_CHANGED,
)

#: The Modbus key the heat pump's status is read under, and the keys the
#: other two codes and the outdoor temperature come under. See sample_of.
HP_STATUS_KEY = "hp1_status"
SYSTEM_STATUS_KEY = "system_status"
SG_MODE_KEY = "sg_mode"
OUTDOOR_KEY = "outdoor_temp"
#: Modbus 62234, the compressor's minutes over the last day, for the mean run.
MINUTES_24H_KEY = "compressor_hours_24h"


@dataclass(frozen=True)
class Sample:
    """What one poll said, as far as the watch is concerned.

    ``at`` is an aware datetime in local time. A code is None when the register
    did not answer or the reading is not trusted; the outdoor temperature is
    carried along for the attributes.
    """

    at: datetime
    hp_status: int | None = None
    system_status: int | None = None
    sg_mode: int | None = None
    outdoor: float | None = None


@dataclass(frozen=True)
class Transition:
    """One change between two samples, with what it changed from and to."""

    kind: str
    at: datetime
    from_code: int | None
    to_code: int | None
    from_label: str | None
    to_label: str | None
    outdoor: float | None
    #: How long the run or defrost that just ended lasted, where its start was seen.
    minutes: float | None = None

    def attributes(self) -> dict[str, Any]:
        """The change as entity attributes: both codes, their labels, the weather."""
        found: dict[str, Any] = {
            "från": self.from_label,
            "till": self.to_label,
            "från kod": self.from_code,
            "till kod": self.to_code,
            "utetemperatur": self.outdoor,
        }
        if self.minutes is not None:
            found["längd"] = self.minutes
        return found


@dataclass(frozen=True)
class Run:
    """A completed run of the compressor."""

    #: None when the run was already under way at the first sample.
    started: datetime | None
    ended: datetime
    minutes: float | None


@dataclass(frozen=True)
class Defrost:
    """A defrost whose start was seen, and its end once that is seen too."""

    started: datetime
    #: Modbus 62000 at the start: a defrost at plus five is not one at minus ten.
    outdoor: float | None
    ended: datetime | None = None
    minutes: float | None = None


def _label(table: dict[int, str], code: int | None) -> str | None:
    """The table's label for a code, or the code itself where the table has none."""
    if code is None:
        return None
    return table.get(code) or f"{STATUS_UNKNOWN} ({code})"


def _minutes(since: datetime | None, until: datetime) -> float | None:
    """Minutes from one moment to another, to the tenth, or None without a start."""
    if since is None:
        return None
    return round((until - since).total_seconds() / 60, 1)


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat(timespec="seconds")


def hp_code_known(code: int | None) -> bool:
    """Whether a heat pump status code says anything about the compressor."""
    return code is not None and code in STATUS_HEATPUMP and code not in HP_SILENT_CODES


class TransitionWatch:
    """The heat pump's state from sample to sample, and what changed between them."""

    def __init__(self) -> None:
        self._hp: int | None = None
        self._system: int | None = None
        self._sg: int | None = None
        #: When the run under way began, if its start was seen.
        self.running_since: datetime | None = None
        self.last_start: datetime | None = None
        self.last_start_system_status: int | None = None
        self.last_start_outdoor: float | None = None
        self.last_run: Run | None = None
        self.starts_today = 0
        #: When the defrost under way began, if its start was seen.
        self.defrosting_since: datetime | None = None
        self.last_defrost: Defrost | None = None
        self.defrosts_today = 0
        #: From when the day's counts count: midnight, or the first sample
        #: after a start of Home Assistant.
        self.counting_since: datetime | None = None

    @property
    def running(self) -> bool | None:
        """Whether the compressor turns, by the last known code; None before one."""
        return None if self._hp is None else self._hp in HP_RUN_CODES

    @property
    def defrosting(self) -> bool | None:
        """Whether the heat pump is defrosting, by the last known code; None before one."""
        return None if self._hp is None else self._hp == HP_DEFROST_CODE

    def observe(self, sample: Sample) -> list[Transition]:
        """Take in one poll and return what changed since the last one.

        The first sample, and the first known code of each kind, set a
        baseline and change nothing.
        """
        self._roll_day(sample.at)
        found = self._observe_heat_pump(sample)
        found.extend(self._observe_system_status(sample))
        found.extend(self._observe_smartgrid(sample))
        return found

    def _roll_day(self, at: datetime) -> None:
        """Start the day's counts over at the first sample of a new local day."""
        if self.counting_since is None:
            self.counting_since = at
            return
        if at.date() != self.counting_since.date():
            self.counting_since = at.replace(hour=0, minute=0, second=0, microsecond=0)
            self.starts_today = 0
            self.defrosts_today = 0

    def _observe_heat_pump(self, sample: Sample) -> list[Transition]:
        code = sample.hp_status
        if not hp_code_known(code):
            return []
        before, self._hp = self._hp, code
        if before is None or before == code:
            return []
        at = sample.at
        found: list[Transition] = []

        def add(kind: str, minutes: float | None = None) -> None:
            found.append(
                Transition(
                    kind, at, before, code,
                    _label(STATUS_HEATPUMP, before), _label(STATUS_HEATPUMP, code),
                    sample.outdoor, minutes,
                )
            )

        was_run, now_run = before in HP_RUN_CODES, code in HP_RUN_CODES
        was_defrost, now_defrost = before == HP_DEFROST_CODE, code == HP_DEFROST_CODE
        was_alarm, now_alarm = before == HP_ALARM_CODE, code == HP_ALARM_CODE
        # What ended first, then what began, so a stop and a start in the same
        # sample read in the order they happened. A defrost ends before the run
        # it was part of, and is no stop by itself: the compressor turns on.
        if was_defrost and not now_defrost:
            minutes = _minutes(self.defrosting_since, at)
            if self.last_defrost is not None and self.last_defrost.ended is None:
                self.last_defrost = replace(self.last_defrost, ended=at, minutes=minutes)
            self.defrosting_since = None
            add(EVENT_DEFROST_END, minutes)
        if was_run and not now_run:
            minutes = _minutes(self.running_since, at)
            self.last_run = Run(self.running_since, at, minutes)
            self.running_since = None
            add(EVENT_COMPRESSOR_STOP, minutes)
        if was_alarm and not now_alarm:
            add(EVENT_ALARM_CLEARED)
        if not was_run and now_run:
            self.running_since = at
            self.last_start = at
            self.last_start_system_status = sample.system_status
            self.last_start_outdoor = sample.outdoor
            self.starts_today += 1
            add(EVENT_COMPRESSOR_START)
        if not was_defrost and now_defrost:
            self.defrosting_since = at
            self.last_defrost = Defrost(at, sample.outdoor)
            self.defrosts_today += 1
            add(EVENT_DEFROST_START)
        if not was_alarm and now_alarm:
            add(EVENT_ALARM)
        return found

    def _observe_system_status(self, sample: Sample) -> list[Transition]:
        code = sample.system_status
        if code is None:
            return []
        before, self._system = self._system, code
        if before is None or before == code:
            return []
        return [
            Transition(
                EVENT_SYSTEM_STATUS_CHANGED, sample.at, before, code,
                _label(STATUS_SYSTEM, before), _label(STATUS_SYSTEM, code), sample.outdoor,
            )
        ]

    def _observe_smartgrid(self, sample: Sample) -> list[Transition]:
        code = sample.sg_mode
        if code is None:
            return []
        before, self._sg = self._sg, code
        if before is None or before == code:
            return []
        return [
            Transition(
                EVENT_SMARTGRID_CHANGED, sample.at, before, code,
                _label(SG_MODE, before), _label(SG_MODE, code), sample.outdoor,
            )
        ]


def sample_of(coordinator: Any, at: datetime) -> Sample:
    """A sample out of the Modbus coordinator's last round.

    A code is only trusted for a reading that is in the data, the rule the
    binary sensors follow: the codes are a dictionary kept beside the
    readings, and the readings decide what answered this round.
    """
    data = coordinator.data or {}
    codes = getattr(coordinator, "codes", None) or {}

    def code(key: str) -> int | None:
        return codes.get(key) if key in data else None

    outdoor = data.get(OUTDOOR_KEY)
    if isinstance(outdoor, bool) or not isinstance(outdoor, (int, float)):
        outdoor = None
    return Sample(
        at,
        hp_status=code(HP_STATUS_KEY),
        system_status=code(SYSTEM_STATUS_KEY),
        sg_mode=code(SG_MODE_KEY),
        outdoor=outdoor,
    )


# ---------------------------------------------------------------- the sensors


@dataclass(frozen=True)
class TransitionSensor:
    """One sensor read off the watch: what it is, and how to read it.

    ``kind`` is timestamp, count or minutes; sensor.py turns that into the
    device and state classes. The value and the attributes are functions of the
    watch, so the table can be read without Home Assistant.
    """

    key: str
    name: str
    kind: str
    icon: str
    value: Callable[[TransitionWatch], Any]
    attributes: Callable[[TransitionWatch], dict[str, Any]]


def _last_start_attributes(watch: TransitionWatch) -> dict[str, Any]:
    return {
        "systemstatus vid starten": _label(STATUS_SYSTEM, watch.last_start_system_status),
        "utetemperatur vid starten": watch.last_start_outdoor,
        "körning pågår": watch.running,
    }


def _last_run_attributes(watch: TransitionWatch) -> dict[str, Any]:
    run = watch.last_run
    return {
        "startade": _iso(run.started) if run else None,
        "slutade": _iso(run.ended) if run else None,
    }


def _counting_since(watch: TransitionWatch) -> dict[str, Any]:
    return {"räknas sedan": _iso(watch.counting_since)}


def _last_defrost_attributes(watch: TransitionWatch) -> dict[str, Any]:
    defrost = watch.last_defrost
    return {
        # Empty while the defrost is still going; the length comes with its end.
        "längd": defrost.minutes if defrost else None,
        "utetemperatur vid starten": defrost.outdoor if defrost else None,
        "avslutad": _iso(defrost.ended) if defrost else None,
        "pågår": watch.defrosting,
    }


TRANSITION_SENSORS: tuple[TransitionSensor, ...] = (
    TransitionSensor(
        "last_start", "Senaste start", "timestamp", "mdi:play-circle-outline",
        lambda watch: watch.last_start, _last_start_attributes,
    ),
    TransitionSensor(
        "starts_today", "Starter i dag", "count", "mdi:counter",
        lambda watch: watch.starts_today, _counting_since,
    ),
    TransitionSensor(
        "last_run", "Senaste körning", "minutes", "mdi:timer-outline",
        lambda watch: watch.last_run.minutes if watch.last_run else None, _last_run_attributes,
    ),
    TransitionSensor(
        "defrosts_today", "Avfrostningar i dag", "count", "mdi:snowflake-melt",
        lambda watch: watch.defrosts_today, _counting_since,
    ),
    TransitionSensor(
        "last_defrost", "Senaste avfrostning", "timestamp", "mdi:snowflake-melt",
        lambda watch: watch.last_defrost.started if watch.last_defrost else None,
        _last_defrost_attributes,
    ),
)


# ------------------------------------------------- the mean run over a day

#: The key of the sensor that divides the day's compressor minutes by the
#: display's starts per day. Only where the history page is harvested.
MEAN_RUN_KEY = "mean_run_24h"
MEAN_RUN_NAME = "Medelkörning senaste dygnet"


def find_starts_per_day(pages: list[Any]) -> Any | None:
    """The display row that counts compressor starts over the last day, if harvested.

    "Antal starter /24 h" on the history page, matched the way the catalogue
    tells a period count from the lifetime counter beside it.
    """
    for page in pages:
        for value in page.values:
            if is_period_count(value.label):
                return value
    return None


def mean_run_minutes(minutes_24h: Any, starts_24h: Any) -> float | None:
    """The day's compressor minutes divided by its starts, or None without both.

    No figure at all for zero starts rather than a division by zero, and none
    for a negative or non numeric reading, which the registers never give but
    a sentinel that slipped through might.
    """
    for value in (minutes_24h, starts_24h):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
    if starts_24h <= 0 or minutes_24h < 0:
        return None
    return round(minutes_24h / starts_24h, 1)
