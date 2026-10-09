""""Read the menu again" from the options: the count starts over, the form is kept (R13).

Three things the options flow used to get wrong. A tick in "read the menu
again" jumped straight to the rescan step and threw the rest of the form away.
The background's three tries at the menu were counted once per run, so after
three misses a reading could be ordered from the options but, if it missed too,
nothing read the menu again until a restart. And a missed reading still wrote
the stored menu back, built from the fallback where no menu was stored. The
run under a real core is in test_homeassistant_options.py; here the order is
held in the source, which the ordinary suite can read without Home Assistant.
"""

from __future__ import annotations

import pathlib

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"


def _flow() -> str:
    return (COMPONENT / "config_flow.py").read_text(encoding="utf-8")


def _method(source: str, name: str) -> str:
    return source.split(f"async def {name}")[1].split("\n    async def ")[0].split("\n    def ")[0]


def test_a_reading_from_the_options_starts_the_background_tries_over():
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    helper = source.split("def menu_read_from_the_options")[1].split("\ndef ")[0].split("\nasync def ")[0]
    assert "_MENU_TRIES.pop(entry_id, None)" in helper, "räknaren nollställs"
    assert "_MENU_LAST[entry_id] = now" in helper, "formulärets läsning är senaste försöket"
    # The reading in the background still asks menu_is_due with the count,
    # so a count started over is three new tries after the save.
    assert "_MENU_TRIES.get(entry.entry_id, 0)" in source.split("def _menu_is_due")[1].split("\n\n")[0]


def test_the_form_books_its_reading_after_walking_and_not_at_a_busy_panel():
    rescan = _method(_flow(), "async_step_rescan")
    walked, after = rescan.split("await async_rescan_pages(self._web_client())")
    assert "menu_read_from_the_options(" not in walked
    busy = after.split("except PanelBusy:")[1].split("except CtcWebError")[0]
    assert "menu_read_from_the_options(" not in busy, "ingen läsning skedde, så inget bokförs"
    assert 'return self.async_abort(reason="panel_busy")' in busy
    booked = after.split("except CtcWebError")[1]
    # The reading rides along since L6, so how the form's walk went is what
    # the report and the diagnostics say.
    assert (
        "menu_read_from_the_options(self._entry.entry_id, self.hass.loop.time(), reading)"
        in booked
    )
    assert booked.index("menu_read_from_the_options(") < booked.index("menu_after_rescan(")


def test_a_missed_reading_writes_neither_the_menu_nor_the_version():
    rescan = _method(_flow(), "async_step_rescan")
    save = rescan.split("if user_input is not None:")[1].split("\n        try:")[0]
    before, guarded = save.split("if self._fresh:")
    assert "CONF_MENU:" not in before and "CONF_MENU]" not in before, "menyn skrivs inte ovillkorligt"
    assert "changes[CONF_MENU] = pages_to_storage(self._pages)" in guarded
    assert "changes[CONF_MENU_VERSION] = await _async_version(self.hass)" in guarded
    # The tick boxes are the person's choice and are saved either way.
    assert "CONF_SLOW_PAGES: pages_to_storage(keep)" in before


def test_the_first_form_rides_along_to_the_rescan_step_and_is_saved_with_it():
    flow = _flow()
    init = _method(flow, "async_step_init")
    submitted = init.split("if user_input is not None:")[1].split("\n        options = self._entry.options")[0]
    assert "self._pending = {" in submitted
    pending_at = submitted.index("self._pending = {")
    rescan_at = submitted.index("return await self.async_step_rescan()")
    assert pending_at < rescan_at, "fälten sparas undan innan omsökningssteget visas"
    # Nothing is written before the walk: a write reloads the entry, and the
    # walk must not meet the reload's first harvest on the panel.
    assert "async_create_entry" not in submitted and "async_update_entry" not in submitted
    assert "return await self._async_save(self._pending)" in submitted
    for field in (
        "CONF_FAST_INTERVAL",
        "CONF_SLOW_INTERVAL",
        "CONF_RESTORE_PAGE",
        "CONF_ENABLE_CONTROL",
        "CONF_VISIT_SYSTEM_INFO",
        "CONF_CHECK_UPDATES",
        "CONF_SEND_STATISTICS",
    ):
        assert f"{field}:" in submitted[pending_at:rescan_at], field

    rescan = _method(flow, "async_step_rescan")
    save = rescan.split("if user_input is not None:")[1].split("\n        try:")[0]
    assert "**self._pending," in save
    assert save.index("**self._pending,") < save.index("CONF_SLOW_PAGES: pages_to_storage(keep)"), (
        "omsökningsformulärets egna fält vinner över det första"
    )
    # And the rescan form is offered as the first form left things.
    offered = rescan.split("menu_after_rescan(")[1]
    assert "settings = {**self._entry.options, **self._pending}" in offered
    assert "settings.get(CONF_SLOW_INTERVAL" in offered and "settings.get(CONF_RESTORE_PAGE" in offered


def test_switching_the_statistics_off_is_acted_on_at_the_save_only():
    flow = _flow()
    save = flow.split("async def _async_save")[1].split("\n    def ")[0]
    assert "async_forget_install(self.hass, self._entry, DOMAIN)" in save
    assert "async_forget_install" not in _method(flow, "async_step_init")
    assert "async_forget_install" not in _method(flow, "async_step_rescan")
