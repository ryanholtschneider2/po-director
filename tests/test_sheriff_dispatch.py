"""Unit tests for the PR-Sheriff deployment wiring + event dispatch.

Covers the deterministic parts with no Prefect server / Claude:
- `register()` ships a `pr-sheriff-manual` deployment.
- `build_workspace_deployments` includes the Sheriff iff merge_mode is auto.
- `sheriff_deployment_name` shape.
- `on_pr_opened` gates on merge_mode and is best-effort (never raises).
"""

from __future__ import annotations

from pathlib import Path

import po_director.sheriff_dispatch as dispatch
from po_director.config import DirectorConfig, save_config
from po_director.deployments import (
    AUTO_MERGE_MODES,
    build_workspace_deployments,
    register,
    sheriff_deployment_name,
)


def _cfg(tmp_path: Path, **kw: object) -> DirectorConfig:
    return DirectorConfig(workspace_dir=str(tmp_path), **kw)  # type: ignore[arg-type]


def _ws(tmp_path: Path, **kw: object) -> str:
    save_config(_cfg(tmp_path, **kw))
    return str(tmp_path)


# ── deployment registration ──────────────────────────────────────────


def test_register_includes_pr_sheriff_manual() -> None:
    names = {dep.name for dep in register()}
    assert "pr-sheriff-manual" in names


def test_sheriff_deployment_name_shape(tmp_path: Path) -> None:
    name = sheriff_deployment_name(_cfg(tmp_path))
    assert name.startswith("pr-sheriff-")
    # workspace_slug = <dirname>-<6 hex>
    assert name.rsplit("-", 1)[-1].isalnum()


def test_auto_modes_get_sheriff_deployment(tmp_path: Path) -> None:
    for mode in AUTO_MERGE_MODES:
        deps = build_workspace_deployments(_cfg(tmp_path, merge_mode=mode))
        names = [d.name for d in deps]
        assert sheriff_deployment_name(_cfg(tmp_path)) in names, mode
        # pulse/reflect/dream/improve + sheriff
        assert len(deps) == 5, mode


def test_human_modes_skip_sheriff_deployment(tmp_path: Path) -> None:
    for mode in ("human", "approve-all"):
        deps = build_workspace_deployments(_cfg(tmp_path, merge_mode=mode))
        names = [d.name for d in deps]
        assert sheriff_deployment_name(_cfg(tmp_path)) not in names, mode
        # pulse/reflect/dream/improve (no sheriff)
        assert len(deps) == 4, mode


# ── on_pr_opened gating + best-effort ────────────────────────────────


def test_on_pr_opened_dispatches_for_auto(tmp_path: Path, monkeypatch) -> None:
    ws = _ws(tmp_path, merge_mode="ai-approve-all")
    calls: list[dict] = []

    def fake_run_deployment(*, name, parameters, timeout):
        calls.append({"name": name, "parameters": parameters, "timeout": timeout})

    # on_pr_opened imports run_deployment lazily from prefect.deployments.
    import prefect.deployments as pd

    monkeypatch.setattr(pd, "run_deployment", fake_run_deployment)

    assert dispatch.on_pr_opened(ws, "feat-1") is True
    assert len(calls) == 1
    assert calls[0]["name"] == "pr-sheriff/" + sheriff_deployment_name(_cfg(tmp_path))
    assert calls[0]["parameters"] == {"feature_id": "feat-1"}
    assert calls[0]["timeout"] == 0


def test_on_pr_opened_skips_human_mode(tmp_path: Path, monkeypatch) -> None:
    ws = _ws(tmp_path, merge_mode="human")

    def boom(**_kw):  # pragma: no cover — must not be called
        raise AssertionError("run_deployment should not fire for human mode")

    import prefect.deployments as pd

    monkeypatch.setattr(pd, "run_deployment", boom)
    assert dispatch.on_pr_opened(ws, "feat-1") is False


def test_on_pr_opened_merge_mode_override(tmp_path: Path, monkeypatch) -> None:
    # Workspace is human, but the override forces an auto mode → dispatch.
    ws = _ws(tmp_path, merge_mode="human")
    calls: list = []
    import prefect.deployments as pd

    monkeypatch.setattr(pd, "run_deployment", lambda **kw: calls.append(kw))
    assert dispatch.on_pr_opened(ws, "feat-1", merge_mode="auto") is True
    assert len(calls) == 1


def test_on_pr_opened_swallows_run_deployment_error(
    tmp_path: Path, monkeypatch
) -> None:
    ws = _ws(tmp_path, merge_mode="auto")

    def boom(**_kw):
        raise RuntimeError("prefect server unreachable")

    import prefect.deployments as pd

    monkeypatch.setattr(pd, "run_deployment", boom)
    # Best-effort: returns False, never raises.
    assert dispatch.on_pr_opened(ws, "feat-1") is False


def test_on_pr_opened_swallows_config_error(tmp_path: Path, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise OSError("no config here")

    monkeypatch.setattr(dispatch, "load_config", boom)
    assert dispatch.on_pr_opened(str(tmp_path), "feat-1") is False
