"""`jarvis test`: el comando unico de validacion esta registrado y es coherente."""

from __future__ import annotations

from click.testing import CliRunner

from openjarvis.cli import cli, selftest_cmd


def test_command_is_registered_and_documented():
    out = CliRunner().invoke(cli, ["test", "--help"]).output
    for token in ("--suite", "--env", "--no-lint", "quick", "voice", "full"):
        assert token in out


def test_suites_and_lint_paths_exist():
    root = selftest_cmd._project_root()
    assert root is not None
    for suite, args in selftest_cmd.SUITES.items():
        for a in args:
            if a.startswith("tests/"):
                assert (root / a).exists(), f"{suite}: falta {a}"
    for p in selftest_cmd.LINT_PATHS:
        assert (root / p).exists(), f"lint: falta {p}"


def test_exit_code_reflects_failures(monkeypatch):
    calls = []

    def fake_run(console, title, cmd, cwd):
        calls.append(title)
        return "ruff" not in title.lower()  # el lint "falla"

    monkeypatch.setattr(selftest_cmd, "_run", fake_run)
    res = CliRunner().invoke(cli, ["test", "--suite", "quick"])
    assert res.exit_code == 1 and len(calls) == 2  # ruff + pytest se ejecutan siempre

    monkeypatch.setattr(selftest_cmd, "_run", lambda *a, **k: True)
    assert CliRunner().invoke(cli, ["test", "--suite", "quick"]).exit_code == 0
    assert CliRunner().invoke(cli, ["test", "--no-lint"]).exit_code == 0


def test_runs_outside_a_checkout_fail_cleanly(monkeypatch):
    monkeypatch.setattr(selftest_cmd, "_project_root", lambda: None)
    res = CliRunner().invoke(cli, ["test"])
    assert res.exit_code == 2
