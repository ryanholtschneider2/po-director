"""Phase-2 unit tests: config defaults, TOML round-trip, validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from po_director.config import (
    DEFAULT_MERGE_MODE,
    DEFAULT_PULSE_CRON,
    DEFAULT_WORK_ASK,
    DEFAULT_WORK_SOURCE,
    DirectorConfig,
    load_config,
    save_config,
)


def test_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.workspace_dir == str(tmp_path.resolve())
    assert cfg.work_source == DEFAULT_WORK_SOURCE
    assert cfg.work_ask == DEFAULT_WORK_ASK
    assert cfg.merge_mode == DEFAULT_MERGE_MODE
    assert cfg.pulse_cron == DEFAULT_PULSE_CRON
    assert cfg.slack_channel is None


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    cfg = DirectorConfig(
        workspace_dir=str(tmp_path),
        north_star="ship the thing",
        slack_channel="C08LB4V9ZJ8",
        work_source="issues",
        work_ask="auto",
        merge_mode="human",
    )
    path = save_config(cfg)
    assert path.is_file()

    loaded = load_config(tmp_path)
    assert loaded.north_star == "ship the thing"
    assert loaded.slack_channel == "C08LB4V9ZJ8"
    assert loaded.work_source == "issues"
    assert loaded.work_ask == "auto"
    assert loaded.merge_mode == "human"


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


def test_bad_work_ask_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="work_ask"):
        DirectorConfig(workspace_dir=str(tmp_path), work_ask="nope")


def test_bad_merge_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="merge_mode"):
        DirectorConfig(workspace_dir=str(tmp_path), merge_mode="nope")


# ─── minimal corp-dir contract: full .ade/settings.toml mapping ──────────


def _write_ade(workspace: Path, body: str) -> None:
    ade = workspace / ".ade"
    ade.mkdir(parents=True, exist_ok=True)
    (ade / "settings.toml").write_text(body, encoding="utf-8")


def test_ade_minimal_corp_dir_contract(tmp_path: Path) -> None:
    """A new corp dir needs only goal.md plus a short .ade/settings.toml
    expressing persona, north_star, slack_channel, and optional cron overrides.
    """
    _write_ade(
        tmp_path,
        "\n".join(
            [
                "[goals]",
                'north_star = "MRR > 10k"',
                "[notify]",
                'slack_channel = "C08LB4V9ZJ8"',
                "[schedule]",
                'pulse_cron = "*/15 * * * *"',
                'roadmap_cron = "*/30 * * * *"',
                'report_cron = "0 22 * * *"',
            ]
        )
        + "\n",
    )
    cfg = load_config(tmp_path)
    assert cfg.north_star == "MRR > 10k"
    assert cfg.slack_channel == "C08LB4V9ZJ8"
    assert cfg.pulse_cron == "*/15 * * * *"
    assert cfg.roadmap_cron == "*/30 * * * *"
    assert cfg.report_cron == "0 22 * * *"


def test_ade_legacy_reflect_cron_migrates_to_report(tmp_path: Path) -> None:
    # A pre-rename .ade [schedule].reflect_cron migrates to report_cron.
    _write_ade(tmp_path, '[schedule]\nreflect_cron = "0 9 * * *"\n')
    assert load_config(tmp_path).report_cron == "0 9 * * *"


def test_legacy_director_toml_reflect_cron_migrates_to_report(tmp_path: Path) -> None:
    # A pre-rename flat .director.toml reflect_cron migrates to report_cron.
    (tmp_path / ".director.toml").write_text(
        'reflect_cron = "0 8 * * *"\n', encoding="utf-8"
    )
    assert load_config(tmp_path).report_cron == "0 8 * * *"


def test_ade_goal_path_override(tmp_path: Path) -> None:
    _write_ade(tmp_path, '[goals]\ngoal_path = "strategy.md"\n')
    assert load_config(tmp_path).goal_path == "strategy.md"


def test_ade_slack_and_cron_default_when_absent(tmp_path: Path) -> None:
    # Only persona/north_star set → slack stays None, crons keep dataclass defaults.
    _write_ade(tmp_path, '[goals]\nnorth_star = "x"\n')
    cfg = load_config(tmp_path)
    assert cfg.slack_channel is None
    assert cfg.pulse_cron == DEFAULT_PULSE_CRON


def test_ade_overrides_legacy_director_toml(tmp_path: Path) -> None:
    (tmp_path / ".director.toml").write_text(
        'north_star = "legacy"\nslack_channel = "OLD"\n', encoding="utf-8"
    )
    _write_ade(
        tmp_path, '[goals]\nnorth_star = "new"\n[notify]\nslack_channel = "NEW"\n'
    )
    cfg = load_config(tmp_path)
    assert cfg.north_star == "new"  # .ade wins over .director.toml
    assert cfg.slack_channel == "NEW"
