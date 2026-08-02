from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

REQUIREMENT_FILES = (
    Path(__file__).parents[2] / "requirements-runtime.txt",
    Path(__file__).parents[2] / "requirements-server.txt",
)


def _exact_pin(path: Path, package_name: str) -> Version:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "-r")):
            continue
        requirement = Requirement(line)
        if requirement.name.lower() != package_name.lower():
            continue
        pins = [
            specifier.version
            for specifier in requirement.specifier
            if specifier.operator == "=="
        ]
        if len(pins) == 1:
            return Version(pins[0])
    raise AssertionError(f"{path} does not exactly pin {package_name}")


def test_numpy_pin_satisfies_provider_calendar_policy():
    for requirement_file in REQUIREMENT_FILES:
        assert _exact_pin(requirement_file, "exchange-calendars") == Version("4.11.1")
        assert _exact_pin(requirement_file, "numpy") >= Version("1.26.4")
