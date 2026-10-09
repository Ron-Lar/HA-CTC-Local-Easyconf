"""Load the integration's Home Assistant free modules without installing HA.

The package's ``__init__`` pulls in Home Assistant, which the unit tests do not
need and should not require. The modules under test are therefore loaded
directly into a stub package so that their relative imports still resolve.
"""

import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ctc_ecozenith"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

if "ctc_ecozenith" not in sys.modules:
    stub = types.ModuleType("ctc_ecozenith")
    stub.__path__ = [str(COMPONENT)]
    sys.modules["ctc_ecozenith"] = stub


def load(name: str):
    full = f"ctc_ecozenith.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, COMPONENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # A module that failed halfway must not be found whole by the next
        # test: that showed as "module has no attribute" far from the cause.
        sys.modules.pop(full, None)
        raise
    return module


def pytest_configure(config):
    """Run pytest-asyncio in auto mode wherever it is loaded.

    The suite itself is synchronous throughout and never notices the mode. But
    pytest-homeassistant-custom-component, which test_homeassistant.py needs,
    brings autouse fixtures that are async, and in strict mode every test in the
    session errors at set-up over them. Switching the mode here, rather than in
    pytest.ini, keeps an environment without pytest-asyncio free of a warning
    about an unknown option. A mode given on the command line, or anything but
    the plugin's default in the ini or through -o, is left as it is.
    """
    if not hasattr(config.option, "asyncio_mode"):
        return  # pytest-asyncio is not loaded; nothing registered the option
    if config.option.asyncio_mode is not None or config.getini("asyncio_mode") != "strict":
        return
    config.option.asyncio_mode = "auto"


@pytest.fixture(scope="session")
def const():
    return load("const")


@pytest.fixture(scope="session")
def web_api():
    return load("web_api")


@pytest.fixture(scope="session")
def modbus_api():
    return load("modbus_api")


@pytest.fixture(scope="session")
def catalogue():
    return load("catalogue")


@pytest.fixture(scope="session")
def discovery():
    return load("discovery")


@pytest.fixture(scope="session")
def stats_extra():
    return load("stats_extra")


@pytest.fixture(scope="session")
def wp118() -> str:
    return (FIXTURES / "wp_118.js").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def vars118() -> str:
    return (FIXTURES / "vars_118.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def sm_all() -> str:
    return (FIXTURES / "sm_all.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def cop():
    return load("cop")


@pytest.fixture(scope="session")
def identity():
    return load("identity")


@pytest.fixture(scope="session")
def updates():
    return load("updates")


@pytest.fixture(scope="session")
def patience():
    return load("patience")


@pytest.fixture(scope="session")
def dashboard_views():
    return load("dashboard_views")


@pytest.fixture(scope="session")
def explanations():
    return load("explanations")


@pytest.fixture(scope="session")
def seen():
    return load("seen")


@pytest.fixture()
def pumps() -> dict:
    """The two houses' heat pumps as dashboard.py hands them over.

    Built from the entity registries of an i255 with an EcoAir 720M and control
    switched on, and of an i550 Pro without control, with display pages ticked.
    """
    import json

    return json.loads((FIXTURES / "pumps.json").read_text(encoding="utf-8"))


@pytest.fixture()
def page():
    """A real page of a display, as the panel's own definition describes it.

    Captured from the two houses: an i255 and an i550 Pro, their operation data
    and history pages, with every caption resolved through the display's text
    catalogue. The layouts differ enough that one pairing rule has to serve both.
    """
    import json

    def load_page(name: str) -> dict:
        return json.loads((FIXTURES / f"widgets_{name}.json").read_text(encoding="utf-8"))

    return load_page
