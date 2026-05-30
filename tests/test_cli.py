"""Phase-4 unit tests: launcher config + deployment specs (no Prefect server).

`_start`/`_stop` touch a server / `prefect` CLI and are exercised manually
(see plan e2e). Here we test the pure parts: first-run config creation,
workspace-stamped deployment specs, and status output.
"""

from __future__ import annotations

from pathlib import Path

from po_director.cli import _ensure_config, director
from po_director.config import DEFAULT_NORTH_STAR, load_config
from po_director.deployments import (
    build_workspace_deployments,
    deployment_names,
    workspace_slug,
)


def test_first_run_creates_config_and_goal(tmp_path: Path) -> None:
    # stdin is non-tty under pytest, so prompts fall back to defaults.
    cfg = _ensure_config(
        str(tmp_path),
        channel="C08LB4V9ZJ8",
        approval_mode="batches",
        pulse_cron=None,
        reflect_cron=None,
        north_star=None,
    )
    assert (tmp_path / ".director.toml").is_file()
    assert (tmp_path / "goal.md").is_file()
    assert cfg.north_star == DEFAULT_NORTH_STAR
    assert cfg.slack_channel == "C08LB4V9ZJ8"
    assert cfg.approval_mode == "batches"
    # persisted
    assert load_config(tmp_path).approval_mode == "batches"


def test_workspace_slug_stable_and_safe(tmp_path: Path) -> None:
    s1 = workspace_slug("/home/me/My Repo!")
    s2 = workspace_slug("/home/me/My Repo!")
    assert s1 == s2
    assert "!" not in s1 and " " not in s1


def test_build_deployments(tmp_path: Path) -> None:
    cfg = _ensure_config(
        str(tmp_path), channel=None, approval_mode=None,
        pulse_cron="*/5 * * * *", reflect_cron="0 9 * * *", north_star=None,
    )
    deps = build_workspace_deployments(cfg)
    assert len(deps) == 2
    pulse_name, reflect_name = deployment_names(cfg)
    names = {d.name for d in deps}
    assert names == {pulse_name, reflect_name}
    for d in deps:
        assert d.parameters == {"workspace_dir": cfg.workspace_dir}


def test_status_default_action(tmp_path: Path) -> None:
    out = director(dir=str(tmp_path))
    assert "Director status" in out
    assert str(tmp_path.resolve()) in out
