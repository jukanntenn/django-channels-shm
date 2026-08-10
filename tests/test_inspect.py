"""Unit tests for channels_shm.inspect (O4 CLI) and channels_shm.__main__.

Maps to src/channels_shm/inspect.py plus its `python -m channels_shm`
entrypoint. The CLI reads observability files under /dev/shm/{prefix}_obs;
tests redirect `_obs_dir` to a temp dir so no real shm state is touched.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from pathlib import Path

import pytest

from channels_shm import inspect


@pytest.fixture
def obs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI at a temp obs dir and return it."""

    def _fake_obs_dir(_prefix: str) -> Path:
        return tmp_path

    monkeypatch.setattr(inspect, "_obs_dir", _fake_obs_dir)
    return tmp_path


def _log_args(**kw: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "prefix": "test",
        "pid": None,
        "level": None,
        "grep": None,
    }
    return argparse.Namespace(**defaults | kw)


def _metric_args(**kw: object) -> argparse.Namespace:
    defaults: dict[str, object] = {"prefix": "test", "aggregate": False}
    return argparse.Namespace(**defaults | kw)


class TestObsDir:
    def test_path(self) -> None:
        assert inspect._obs_dir("myprefix") == Path("/dev/shm/myprefix_obs")


class TestAlivePids:
    def test_missing_dir(self, tmp_path: Path) -> None:
        assert inspect._alive_pids(tmp_path / "logs") == set()

    def test_skips_non_numeric_stems(self, tmp_path: Path) -> None:
        _ = (tmp_path / "abc.jsonl").write_text("")
        assert inspect._alive_pids(tmp_path) == set()

    def test_alive_current_pid(self, tmp_path: Path) -> None:
        _ = (tmp_path / f"{os.getpid()}.jsonl").write_text("")
        assert os.getpid() in inspect._alive_pids(tmp_path)

    def test_dead_pid_excluded(self, tmp_path: Path) -> None:
        _ = (tmp_path / "99999999.jsonl").write_text("")
        assert inspect._alive_pids(tmp_path) == set()


class TestCmdLogs:
    def test_no_logs_dir(self, obs: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert not (obs / "logs").exists()
        _ = inspect.cmd_logs(_log_args()) == 1
        assert "No logs dir" in capsys.readouterr().err

    def test_dumps_valid_lines_skips_garbage(
        self, obs: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log_dir = obs / "logs"
        log_dir.mkdir()
        _ = (log_dir / "1.jsonl").write_text(
            json.dumps({"event": "a"})
            + "\n"
            + "\n"
            + "not json\n"
            + json.dumps({"event": "b"})
        )
        _ = inspect.cmd_logs(_log_args()) == 0
        out = capsys.readouterr().out
        assert '"event": "a"' in out
        assert '"event": "b"' in out
        assert "not json" not in out

    def test_pid_filter(self, obs: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log_dir = obs / "logs"
        log_dir.mkdir()
        _ = (log_dir / "1.jsonl").write_text(json.dumps({"event": "one"}) + "\n")
        _ = (log_dir / "2.jsonl").write_text(json.dumps({"event": "two"}) + "\n")
        _ = inspect.cmd_logs(_log_args(pid=2)) == 0
        out = capsys.readouterr().out
        assert "two" in out
        assert "one" not in out

    def test_level_filter(self, obs: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log_dir = obs / "logs"
        log_dir.mkdir()
        _ = (log_dir / "1.jsonl").write_text(
            json.dumps({"event": "info", "level": "INFO"})
            + "\n"
            + json.dumps({"event": "warn", "level": "WARNING"})
        )
        _ = inspect.cmd_logs(_log_args(level="WARNING")) == 0
        out = capsys.readouterr().out
        assert "warn" in out
        assert "info" not in out

    def test_grep_filter(self, obs: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log_dir = obs / "logs"
        log_dir.mkdir()
        _ = (log_dir / "1.jsonl").write_text(
            json.dumps({"event": "match_me"}) + "\n" + json.dumps({"event": "skip_me"})
        )
        _ = inspect.cmd_logs(_log_args(grep="match")) == 0
        out = capsys.readouterr().out
        assert "match_me" in out
        assert "skip_me" not in out


class TestCmdMetrics:
    def test_no_metrics_dir(
        self, obs: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert not (obs / "metrics").exists()
        _ = inspect.cmd_metrics(_metric_args()) == 1
        assert "No metrics dir" in capsys.readouterr().err

    def test_dumps_files(self, obs: Path, capsys: pytest.CaptureFixture[str]) -> None:
        m = obs / "metrics"
        m.mkdir()
        _ = (m / "1.json").write_text(json.dumps({"counters": [{"name": "a"}]}))
        _ = inspect.cmd_metrics(_metric_args()) == 0
        out = capsys.readouterr().out
        assert "=== 1.json ===" in out
        assert '"name": "a"' in out

    def test_aggregate(self, obs: Path, capsys: pytest.CaptureFixture[str]) -> None:
        m = obs / "metrics"
        m.mkdir()
        _ = (m / "1.json").write_text(
            json.dumps(
                {"counters": [{"name": "c", "values": [{"value": 1}, {"value": 2}]}]}
            )
        )
        _ = (m / "2.json").write_text(
            json.dumps({"counters": [{"name": "c", "values": [{"value": 4}]}]})
        )
        _ = inspect.cmd_metrics(_metric_args(aggregate=True)) == 0
        out = capsys.readouterr().out
        assert '"aggregated_counters"' in out
        assert '"c": 7' in out


class TestCmdStatus:
    def test_status_with_alive_and_dead_pids(
        self, obs: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        logs = obs / "logs"
        logs.mkdir()
        _ = (logs / f"{os.getpid()}.jsonl").write_text("")
        _ = (logs / "99999999.jsonl").write_text("")
        metrics = obs / "metrics"
        metrics.mkdir()
        _ = (metrics / f"{os.getpid()}.json").write_text(
            json.dumps({"counters": [{"name": "send_total"}]})
        )
        _ = inspect.cmd_status(_metric_args()) == 0
        out = capsys.readouterr().out
        assert "prefix: test" in out
        assert f"pid {os.getpid()} (alive)" in out
        assert "send_total" in out

    def test_status_without_obs_dir(
        self, obs: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert not (obs / "logs").exists()
        _ = inspect.cmd_status(_metric_args()) == 0
        out = capsys.readouterr().out
        assert "alive_pids: (none)" in out


class TestMain:
    def test_main_invokes_subcommand(
        self,
        obs: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys, "argv", ["channels_shm.inspect", "--prefix", "px", "status"]
        )
        assert inspect.main() == 0  # return code: 0=ok
        out = capsys.readouterr().out
        assert "prefix: px" in out
        assert str(obs) in out  # the _obs_dir redirect took effect

    def test_module_entrypoint(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Executing __main__ as the entry module exercises main() (O4 CLI)."""
        monkeypatch.setattr(sys, "argv", ["python -m channels_shm", "status"])
        with pytest.raises(SystemExit) as ei:
            _ = runpy.run_module("channels_shm.__main__", run_name="__main__")
        assert ei.value.code == 0
        assert "prefix: channels_shm" in capsys.readouterr().out
