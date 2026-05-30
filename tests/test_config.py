"""Phase-2 unit tests: config defaults, TOML round-trip, validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from po_director.config import (
    DEFAULT_APPROVAL_MODE,
    DEFAULT_PULSE_CRON,
    DirectorConfig,
    load_config,
    save_config,
)


def test_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.workspace_dir == str(tmp_path.resolve())
    assert cfg.approval_mode == DEFAULT_APPROVAL_MODE
    assert cfg.pulse_cron == DEFAULT_PULSE_CRON
    assert cfg.slack_channel is None


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    cfg = DirectorConfig(
        workspace_dir=str(tmp_path),
        north_star="ship the thing",
        slack_channel="C08LB4V9ZJ8",
        approval_mode="batches",
    )
    path = save_config(cfg)
    assert path.is_file()

    loaded = load_config(tmp_path)
    assert loaded.north_star == "ship the thing"
    assert loaded.slack_channel == "C08LB4V9ZJ8"
    assert loaded.approval_mode == "batches"


def test_unset_slack_channel_not_written(tmp_path: Path) -> None:
    save_config(DirectorConfig(workspace_dir=str(tmp_path)))
    text = (tmp_path / ".director.toml").read_text(encoding="utf-8")
    # No *active* (uncommented) assignment; a `# slack_channel = (unset)` note is fine.
    active = [ln for ln in text.splitlines() if ln.strip().startswith("slack_channel")]
    assert active == []
    assert load_config(tmp_path).slack_channel is None


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    (tmp_path / ".director.toml").write_text(
        'north_star = "x"\nfuture_key = "ignored"\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.north_star == "x"


def test_bad_approval_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="approval_mode"):
        DirectorConfig(workspace_dir=str(tmp_path), approval_mode="nope")
