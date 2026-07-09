"""`linkedos status` and `linkedos --version` smoke tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from linkedos import __version__
from linkedos.cli import main


def test_status_on_fresh_checkout(isolated_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["status"]) == 0

    out = capsys.readouterr().out
    assert f"linkedos {__version__}" in out
    assert "data/linkedos.db" in out
    assert "exists: False" in out
    assert out.strip().endswith("OK")


def test_status_does_not_create_the_database(isolated_env: Path) -> None:
    main(["status"])

    assert not (isolated_env / "data" / "linkedos.db").exists()


def test_status_reports_heartbeats(temp_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from linkedos.services.status import record_heartbeat

    record_heartbeat(source="test")
    capsys.readouterr()

    assert main(["status"]) == 0

    out = capsys.readouterr().out
    assert "exists: True" in out
    assert "heartbeat: 1 row(s)" in out


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"linkedos {__version__}"


def test_no_command_is_an_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == 2
