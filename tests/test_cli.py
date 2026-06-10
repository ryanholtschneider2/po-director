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
    sheriff_deployment_name,
    workspace_slug,
)


def test_first_run_creates_config_and_goal(tmp_path: Path) -> None:
    # stdin is non-tty under pytest, so prompts fall back to defaults.
    cfg = _ensure_config(
        str(tmp_path),
        channel="C08LB4V9ZJ8",
        work_source="issues",
        work_ask="auto",
        merge_mode="human",
        merge_strategy=None,
        approval_mode=None,
        pulse_cron=None,
        reflect_cron=None,
        north_star=None,
    )
    assert (tmp_path / ".director.toml").is_file()
    assert (tmp_path / "goal.md").is_file()
    assert cfg.north_star == DEFAULT_NORTH_STAR
    assert cfg.slack_channel == "C08LB4V9ZJ8"
    assert cfg.work_source == "issues"
    assert cfg.work_ask == "auto"
    assert cfg.merge_mode == "human"
    assert cfg.merge_strategy == "pr"  # default
    # persisted
    persisted = load_config(tmp_path)
    assert persisted.work_source == "issues"
    assert persisted.work_ask == "auto"
    assert persisted.merge_mode == "human"


def test_legacy_approval_mode_migrates_to_work_ask(tmp_path: Path) -> None:
    # A pre-2x2 .director.toml with approval_mode migrates to work_ask=gate.
    (tmp_path / ".director.toml").write_text(
        'workspace_dir = "x"\napproval_mode = "consequential"\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.work_ask == "gate"


def test_ade_settings_overrides_legacy(tmp_path: Path) -> None:
    # .ade/settings.toml (nested) wins over .director.toml (flat legacy).
    (tmp_path / ".director.toml").write_text(
        'work_source = "ideate"\nwork_ask = "gate"\n', encoding="utf-8"
    )
    ade = tmp_path / ".ade"
    ade.mkdir()
    (ade / "settings.toml").write_text(
        '[involvement]\nwork_source = "issues"\nwork_ask = "auto"\nmerge_mode = "human"\n'
        '[merge]\nstrategy = "direct"\nci_cmd = "make ci"\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.work_source == "issues"
    assert cfg.work_ask == "auto"
    assert cfg.merge_mode == "human"
    assert cfg.merge_strategy == "direct"
    assert cfg.ci_cmd == "make ci"


def test_workspace_slug_stable_and_safe(tmp_path: Path) -> None:
    s1 = workspace_slug("/home/me/My Repo!")
    s2 = workspace_slug("/home/me/My Repo!")
    assert s1 == s2
    assert "!" not in s1 and " " not in s1


def test_build_deployments(tmp_path: Path) -> None:
    cfg = _ensure_config(
        str(tmp_path), channel=None, work_source=None, work_ask=None,
        merge_mode=None, merge_strategy=None, approval_mode=None,
        pulse_cron="*/5 * * * *", reflect_cron="0 9 * * *", north_star=None,
    )
    deps = build_workspace_deployments(cfg)
    # Default merge_mode is auto → pulse/reflect/dream + the PR-Sheriff.
    assert len(deps) == 4
    pulse_name, reflect_name, dream_name = deployment_names(cfg)
    names = {d.name for d in deps}
    assert names == {pulse_name, reflect_name, dream_name, sheriff_deployment_name(cfg)}
    for d in deps:
        assert d.parameters == {"workspace_dir": cfg.workspace_dir}


def test_status_default_action(tmp_path: Path) -> None:
    out = director(dir=str(tmp_path))
    assert "Director status" in out
    assert str(tmp_path.resolve()) in out


def test_render_action_prints_persona_prompt(tmp_path: Path) -> None:
    out = director("render", dir=str(tmp_path))
    # Default persona: the builtin director pulse prompt, rendered with the
    # workspace dir substituted in.
    assert "You are the **Director**" in out
    assert str(tmp_path.resolve()) in out


def test_render_action_unknown_persona_fails_loudly(tmp_path: Path) -> None:
    import pytest

    from po_director.persona import PersonaError

    with pytest.raises(PersonaError):
        director("render", dir=str(tmp_path), persona="no-such-persona")
