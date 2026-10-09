"""pymodbus' own error line about a silent register stays out of the log while an entry is loaded (F1.5, R2).

With the library's timeout as the only timer, every register nobody answers
reaches the library's Log.error, "No response received after 0 retries,
continue with next request", on the logger pymodbus.logging at ERROR, visible
at Home Assistant's default level. On a model that lacks the stored block that
is twelve red lines per start about something the poll round handles and says
on debug. The integration holds a filter on that logger for as long as an entry
is loaded, matching that one line and nothing else.
"""

from __future__ import annotations

import logging

import pytest

LINE = "No response received after 0 retries, continue with next request"
FRAMES = " >>>>> send: 0x0 0x1 0x0 0x0 0x0 0x6 0x1 0x3 0xf0 0x34 0x0 0x16"


@pytest.fixture()
def library_logger(modbus_api):
    logger = logging.getLogger(modbus_api.LIBRARY_LOGGER)
    yield logger
    # Whatever a test left behind, the next starts clean.
    logger.removeFilter(modbus_api._QUIET)
    modbus_api._QUIET_HOLDS = 0


def _errors(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]


def test_without_a_hold_the_library_speaks_for_itself(library_logger, caplog):
    with caplog.at_level(logging.ERROR, logger=library_logger.name):
        library_logger.error(LINE + FRAMES)
    assert _errors(caplog) == [LINE + FRAMES]


def test_a_hold_keeps_that_one_line_out_and_nothing_else(modbus_api, library_logger, caplog):
    release = modbus_api.hold_library_quiet()
    with caplog.at_level(logging.ERROR, logger=library_logger.name):
        library_logger.error(LINE + FRAMES)
        library_logger.error("Connection lost: [Errno 104] Connection reset by peer")
        # Home Assistant's own modbus integration retries three times; its line is not ours.
        library_logger.error("No response received after 3 retries, continue with next request")
        library_logger.warning(LINE)
    release()
    assert _errors(caplog) == [
        "Connection lost: [Errno 104] Connection reset by peer",
        "No response received after 3 retries, continue with next request",
    ]
    assert [r.levelno for r in caplog.records if r.getMessage() == LINE] == [], (
        "the line is the match, whatever its level"
    )


def test_the_undo_lets_the_line_through_again(modbus_api, library_logger, caplog):
    release = modbus_api.hold_library_quiet()
    release()
    with caplog.at_level(logging.ERROR, logger=library_logger.name):
        library_logger.error(LINE)
    assert _errors(caplog) == [LINE]
    assert library_logger.filters == []


def test_two_entries_hold_it_until_the_last_lets_go(modbus_api, library_logger, caplog):
    first = modbus_api.hold_library_quiet()
    second = modbus_api.hold_library_quiet()
    assert library_logger.filters == [modbus_api._QUIET], "one filter however many holds"
    first()
    with caplog.at_level(logging.ERROR, logger=library_logger.name):
        library_logger.error(LINE)
    assert _errors(caplog) == [], "the second entry still holds it"
    second()
    with caplog.at_level(logging.ERROR, logger=library_logger.name):
        library_logger.error(LINE)
    assert _errors(caplog) == [LINE]


def test_an_undo_called_twice_releases_once(modbus_api, library_logger):
    first = modbus_api.hold_library_quiet()
    second = modbus_api.hold_library_quiet()
    first()
    first()
    assert library_logger.filters == [modbus_api._QUIET], "a double undo must not free another entry's hold"
    second()
    assert library_logger.filters == []
    assert modbus_api._QUIET_HOLDS == 0


def test_the_line_matched_is_the_one_the_library_writes_with_no_retries(modbus_api):
    """Pinned against the library's own text in transaction.py, for 3.8 to 3.15."""
    assert LINE.startswith(modbus_api.LIBRARY_SILENCE_LINE)
    assert modbus_api.client_options()["retries"] == 0, "the zero in the line is ours"
    assert modbus_api.LIBRARY_LOGGER == "pymodbus.logging"
