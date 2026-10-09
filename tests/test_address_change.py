"""A new address goes through DHCP, ports and Modbus address through remove and add (R17).

There is no reconfigure step, by decision: an address changes every few years,
and with R20 the DHCP flow moves the entry to the unit's new address with its
device and entities; port 80, port 502 and Modbus address 1 never change on a
CTC. A reconfigure step would also have to unload the entry before its Modbus
probe, since the pump keeps a single session. The README says what to do
instead, and this keeps the two in step.
"""

from __future__ import annotations

from conftest import COMPONENT, ROOT


def test_there_is_no_reconfigure_step():
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    assert "async_step_reconfigure" not in flow
    # The address moves by DHCP instead, with the key pinned (R20).
    assert "moved_data(entry.data, host)" in flow


def test_the_readme_says_how_an_address_port_or_modbus_address_is_changed():
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "Give the display a fixed address, a DHCP reservation in your router" in readme
    assert "recognises the unit by the MAC address" in readme
    assert "remove the integration and add it again at the new address" in readme
    assert "The ports and the Modbus address are changed the same way" in readme
