"""Just enough of Home Assistant for the modules that import it to load.

The coordinator and the entity platforms import Home Assistant at module level,
and the suite runs without it. What they touch at import time is small: a
coordinator base class, its failure exception, the entity base classes and the
device class enum. These stand-ins cover exactly that, and only when Home
Assistant itself is not installed, so a run inside a real HA environment uses
the real thing.

The interval timer is recorded instead of started, which is what lets a test
drive the control manager's keepalive by hand.
"""

from __future__ import annotations

import enum
import sys
import types
from typing import Generic, TypeVar

T = TypeVar("T")

#: Every (callback, interval) the control manager asked to have run.
tracked: list[tuple] = []


def _module(name: str, package: bool = False) -> types.ModuleType:
    module = types.ModuleType(name)
    if package:
        module.__path__ = []
    sys.modules[name] = module
    return module


def install() -> None:
    """Put the stand-ins in place, unless Home Assistant is really there."""
    if "homeassistant" in sys.modules:
        return
    try:
        import homeassistant  # noqa: F401

        return
    except ImportError:
        pass

    _module("homeassistant", package=True)
    core = _module("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda func: func

    _module("homeassistant.helpers", package=True)
    coordinator = _module("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator(Generic[T]):
        def __init__(self, hass, logger, *, name="", update_interval=None, **_kwargs):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None
            self.last_update_success = True

    class UpdateFailed(Exception):
        pass

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

    coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    coordinator.UpdateFailed = UpdateFailed
    coordinator.CoordinatorEntity = CoordinatorEntity

    event = _module("homeassistant.helpers.event")

    def async_track_time_interval(hass, action, interval):
        entry = (action, interval)
        tracked.append(entry)

        def _unsub() -> None:
            if entry in tracked:
                tracked.remove(entry)

        return _unsub

    event.async_track_time_interval = async_track_time_interval

    platform = _module("homeassistant.helpers.entity_platform")
    platform.AddEntitiesCallback = object

    _module("homeassistant.components", package=True)
    binary_sensor = _module("homeassistant.components.binary_sensor")

    class BinarySensorDeviceClass(enum.Enum):
        RUNNING = "running"
        PROBLEM = "problem"

    class BinarySensorEntity:
        pass

    binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass
    binary_sensor.BinarySensorEntity = BinarySensorEntity

    # The platforms do ``from . import CtcConfigEntry``, which would pull the
    # package's own __init__ and all of Home Assistant with it. The package is
    # a stub here (see conftest), so the name is simply given to it.
    package = sys.modules.get("ctc_ecozenith")
    if package is not None and not hasattr(package, "CtcConfigEntry"):
        package.CtcConfigEntry = object
