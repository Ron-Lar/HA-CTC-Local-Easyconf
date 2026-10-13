"""What Home Assistant holds in the controller, and what the pump says beside it (roadmap R49).

A control register in CTC's 1000 block is held by writing it every minute,
and the controller forgets it five minutes after the last write that reached
it, so a keepalive that stops lets go of everything at once. The page and the
entities used to say little of it: a held number looked like one that only
mirrored the pump, a select showed "Släpp styrningen" while idle although the
pump's own hot water mode was right there in 61500, and nothing on the
overview said that Home Assistant was holding anything at all.

Two things are worked out here, from the control manager and the Modbus
coordinator as they stand. What is held, one entry per register in the order
of the control tables, for the sensor Aktiva överstyrningar. And the pump's own
value for a control that has a mirror among the stored settings, the
register the controller reads its own setting from, which a number shows
while idle and a select shows as its mode while idle, and both carry as
"pumpens värde" whether or not Home Assistant holds the register. Only a
register with a mirror has one: 61542 (Inställt värmeläge) does not mirror
1015, whose modes are another set, and 62001 is the hot water stop
temperature, not the setpoint 1033 writes.

Free of Home Assistant, like keepalive.py, so the rules can be tested on
their own.
"""

from __future__ import annotations

from typing import Any, Iterable

from .const import CONTROL_NUMBERS, CONTROL_SELECTS, ControlRegister


def shown_value(register: ControlRegister, raw: int) -> float | str | int:
    """A raw register value as the entity shows it: the mode's name, or the scaled number."""
    if register.enum is not None:
        return register.enum.get(raw, raw)
    return round(raw * register.scale, 3)


def held(
    control: Any,
    registers: Iterable[ControlRegister] = CONTROL_NUMBERS + CONTROL_SELECTS,
) -> list[dict[str, Any]]:
    """Every register Home Assistant holds right now, under the sensor's Swedish keys.

    Name, register, the value as the entity shows it and when a write of it
    last reached the controller. Only what the manager counts as in force, a
    write that reached the unit within its five minutes, so the list says
    what the controller holds rather than what was asked of it.
    """
    active = control.active
    found: list[dict[str, Any]] = []
    for register in registers:
        raw = active.get(register.address)
        if raw is None:
            continue
        written = control.written_at(register.address)
        found.append(
            {
                "namn": register.name,
                "register": str(register.address),
                "värde": shown_value(register, raw),
                "senast skriven": written.isoformat(timespec="seconds") if written else None,
            }
        )
    return found


def _mirror_key(modbus: Any, register: ControlRegister) -> str | None:
    """The key of the stored setting a control mirrors, or None without one."""
    if register.mirror is None:
        return None
    for description in getattr(modbus, "descriptions", ()) or ():
        if description.address == register.mirror:
            return description.key
    return None


def _answered(modbus: Any) -> bool:
    """Whether the coordinator's last round got through, so its data is the pump's now."""
    return bool(getattr(modbus, "last_update_success", True))


def pump_number(modbus: Any, register: ControlRegister) -> float | None:
    """The pump's own setting for a number control, from the register it mirrors."""
    key = _mirror_key(modbus, register)
    if key is None or not _answered(modbus):
        return None
    value = (getattr(modbus, "data", None) or {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def pump_option(modbus: Any, register: ControlRegister) -> str | None:
    """The pump's own mode for a select control, by the code its mirror answered.

    Judged on the code, not on the stored setting's label, so a label that is
    reworded or translated cannot make the select show a mode it does not
    have. A code the control's table has no name for gives None.
    """
    key = _mirror_key(modbus, register)
    if key is None or not _answered(modbus) or register.enum is None:
        return None
    if key not in (getattr(modbus, "data", None) or {}):
        return None
    code = (getattr(modbus, "codes", None) or {}).get(key)
    return register.enum.get(code) if isinstance(code, int) else None
