"""Native Windows regression for gateway recovery after a watchdog exit (#108486)."""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from gateway.restart import GATEWAY_FATAL_CONFIG_EXIT_CODE, GATEWAY_SERVICE_RESTART_EXIT_CODE
from hermes_cli import gateway_windows
from tests.waiting import wait_until


@pytest.mark.platforms("windows")
@pytest.mark.parametrize(
    ("first_exit", "expected_attempts"),
    [
        pytest.param(GATEWAY_SERVICE_RESTART_EXIT_CODE, 2, id="watchdog-then-clean"),
        pytest.param(GATEWAY_FATAL_CONFIG_EXIT_CODE, 1, id="fatal-config"),
    ],
)
def test_hidden_gateway_launcher_restarts_only_recoverable_exit(tmp_path, monkeypatch, first_exit, expected_attempts):
    work_dir = tmp_path / "launcher sandbox"
    work_dir.mkdir()
    stub_path = work_dir / "stub_gateway.py"
    stub_path.write_text(
        textwrap.dedent(f"""\
            import sys
            from pathlib import Path

            assert sys.argv[1:] == ["gateway", "run"]
            attempts = Path(__file__).with_name("attempts.txt")
            exit_code = 0 if attempts.exists() else {first_exit}
            with attempts.open("a", encoding="ascii") as trace:
                print(exit_code, file=trace)
            sys.exit(exit_code)
        """),
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway_windows, "_LAUNCHER_RESTART_DELAY_MS", 10, raising=False)
    monkeypatch.setattr(
        gateway_windows,
        "_gateway_run_argv",
        lambda python_path, _profile_arg: [python_path, "-I", str(stub_path), "gateway", "run"],
    )
    monkeypatch.setenv("HERMES_HOME", str(work_dir))
    launcher = gateway_windows._write_task_script().with_suffix(".vbs")

    wscript = Path(os.environ["SYSTEMROOT"]) / "System32" / "wscript.exe"
    try:
        result = subprocess.run(
            [str(wscript), "//B", "//Nologo", str(launcher)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"Windows Script Host did not exit in {exc.timeout}s. stderr={exc.stderr!r}")

    assert result.returncode == 0, result.stderr
    attempts = work_dir / "attempts.txt"
    observed = wait_until(
        lambda: attempts.exists() and attempts.read_text(encoding="ascii").splitlines(),
        what="the gateway stub to record its exit code",
    )
    expected = [str(first_exit)] + ["0"] * (expected_attempts - 1)
    assert observed == expected
