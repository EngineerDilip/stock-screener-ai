"""Architecture boundaries for dependency-injection wiring."""

from pathlib import Path


def test_use_case_factories_do_not_import_bootstrap_facade() -> None:
    source = (Path(__file__).parents[2] / "app/wiring/use_case_factories.py").read_text(
        encoding="utf-8"
    )

    assert "app.wiring.bootstrap" not in source
