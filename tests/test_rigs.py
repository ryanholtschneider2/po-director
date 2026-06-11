"""Unit tests for generic rigs — named workspaces a director manages."""

from __future__ import annotations

from pathlib import Path

import po_director.render as render
from po_director.config import DirectorConfig, load_config, save_config
from po_director.deployments import (
    build_workspace_deployments,
    sheriff_deployment_name_for,
    sheriff_targets,
)


def _ade(tmp_path: Path, body: str) -> None:
    ade = tmp_path / ".ade"
    ade.mkdir(parents=True, exist_ok=True)
    (ade / "settings.toml").write_text(body, encoding="utf-8")


def test_rigs_parse_and_resolve_paths(tmp_path: Path) -> None:
    _ade(
        tmp_path,
        '[[rigs]]\nname = "app"\npath = "courtpro"\ncode = true\n'
        '[[rigs]]\nname = "gtm"\npath = "gtm"\n',  # code defaults false
    )
    cfg = load_config(tmp_path)
    assert len(cfg.rigs) == 2
    resolved = cfg.resolved_rigs()
    app = next(r for r in resolved if r["name"] == "app")
    assert app["path"] == str((tmp_path / "courtpro").resolve())  # resolved to abs
    assert app["code"] is True
    gtm = next(r for r in resolved if r["name"] == "gtm")
    assert gtm["code"] is False
    # code_rigs filters to code-only
    assert [r["name"] for r in cfg.code_rigs()] == ["app"]


def test_rigs_absolute_path_preserved(tmp_path: Path) -> None:
    _ade(tmp_path, f'[[rigs]]\nname = "x"\npath = "{tmp_path}/abs"\ncode = true\n')
    cfg = load_config(tmp_path)
    assert cfg.resolved_rigs()[0]["path"] == f"{tmp_path}/abs"


def test_save_config_skips_rigs(tmp_path: Path) -> None:
    cfg = DirectorConfig(workspace_dir=str(tmp_path), rigs=[{"name": "a", "path": "p", "code": True}])
    path = save_config(cfg)
    # `rigs` isn't round-tripped through the flat toml (no `rigs = …` assignment).
    # (Note: the workspace path itself may contain the substring "rigs".)
    assert "rigs = " not in path.read_text()
    assert "rigs =" not in path.read_text()


def test_sheriff_targets_per_code_rig_under_auto(tmp_path: Path) -> None:
    cfg = DirectorConfig(
        workspace_dir=str(tmp_path),
        merge_mode="auto",
        rigs=[
            {"name": "app", "path": "code1", "code": True},
            {"name": "api", "path": "code2", "code": True},
            {"name": "gtm", "path": "gtm", "code": False},
        ],
    )
    targets = sheriff_targets(cfg)
    assert targets == [str((tmp_path / "code1").resolve()), str((tmp_path / "code2").resolve())]
    # one sheriff deployment per code rig, none for the non-code rig or workspace
    deps = build_workspace_deployments(cfg)
    sheriffs = [d.name for d in deps if d.name.startswith("pr-sheriff-")]
    assert sheriffs == [sheriff_deployment_name_for(t) for t in targets]


def test_sheriff_targets_workspace_when_no_rigs(tmp_path: Path) -> None:
    cfg = DirectorConfig(workspace_dir=str(tmp_path), merge_mode="auto")
    assert sheriff_targets(cfg) == [cfg.workspace_dir]


def test_sheriff_targets_empty_for_human_mode(tmp_path: Path) -> None:
    cfg = DirectorConfig(
        workspace_dir=str(tmp_path),
        merge_mode="human",
        rigs=[{"name": "app", "path": "code1", "code": True}],
    )
    assert sheriff_targets(cfg) == []


def test_build_rigs_lists_rigs_and_boards(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "bd-1 fix the thing")
    cfg = DirectorConfig(
        workspace_dir=str(tmp_path),
        rigs=[
            {"name": "app", "path": "code1", "code": True},
            {"name": "gtm", "path": "gtm", "code": False},
        ],
    )
    out = render.build_rigs(cfg)
    assert "app" in out and "gtm" in out
    assert "code rig" in out and "non-code rig" in out
    assert "bd-1 fix the thing" in out


def test_build_rigs_empty(tmp_path: Path) -> None:
    out = render.build_rigs(DirectorConfig(workspace_dir=str(tmp_path)))
    assert "no rigs configured" in out
