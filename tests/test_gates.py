"""Unit tests for the conditional gate decision functions."""

from __future__ import annotations

from pathlib import Path

from po_director.config import DirectorConfig
from po_director.gates import entry_auto_pass, exit_auto_pass


def _cfg(tmp_path: Path, **kw: object) -> DirectorConfig:
    return DirectorConfig(workspace_dir=str(tmp_path), **kw)  # type: ignore[arg-type]


# ─── ENTRY gate ──────────────────────────────────────────────────────────


def test_entry_empty_allowlist_always_gates(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)  # default: empty
    assert entry_auto_pass(cfg, "docs") is False
    assert entry_auto_pass(cfg, "feature") is False


def test_entry_allowlisted_class_passes(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, entry_auto_pass="lint,docs")
    assert entry_auto_pass(cfg, "docs") is True
    assert entry_auto_pass(cfg, "LINT") is True  # case-insensitive
    assert entry_auto_pass(cfg, "feature") is False  # not on list


def test_entry_never_auto_pass_class_blocked(tmp_path: Path) -> None:
    # Even if a dangerous class were somehow requested, it is never eligible.
    cfg = _cfg(tmp_path, entry_auto_pass="docs")
    assert entry_auto_pass(cfg, "schema") is False
    assert entry_auto_pass(cfg, "migration") is False


# ─── EXIT gate ───────────────────────────────────────────────────────────


def test_exit_empty_allowlist_always_gates(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert exit_auto_pass(cfg, "docs", diff_lines=1, ci_green=True) is False


def test_exit_requires_ci_green(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, exit_auto_pass="docs")
    assert exit_auto_pass(cfg, "docs", diff_lines=1, ci_green=False) is False
    assert exit_auto_pass(cfg, "docs", diff_lines=1, ci_green=True) is True


def test_exit_size_cap_enforced(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, exit_auto_pass="docs", exit_max_diff_lines=40)
    assert exit_auto_pass(cfg, "docs", diff_lines=40, ci_green=True) is True
    assert exit_auto_pass(cfg, "docs", diff_lines=41, ci_green=True) is False


def test_exit_no_cap_when_zero(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, exit_auto_pass="docs")  # cap defaults to 0 = no cap
    assert exit_auto_pass(cfg, "docs", diff_lines=10_000, ci_green=True) is True


def test_exit_class_must_be_allowlisted(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, exit_auto_pass="docs")
    assert exit_auto_pass(cfg, "feature", diff_lines=1, ci_green=True) is False
