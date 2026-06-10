"""Unit tests for the loop-audit transport (operator-turn extraction)."""

from __future__ import annotations

import json
from pathlib import Path

import po_director.loop_audit as la


def test_match_terms_includes_basename_businesses_and_director(tmp_path: Path) -> None:
    ws = tmp_path / "HoltschneiderLLC"
    (ws / "businesses" / "courtpro").mkdir(parents=True)
    (ws / "businesses" / "storybook").mkdir(parents=True)
    terms = la.match_terms_for(str(ws))
    assert "HoltschneiderLLC" in terms
    assert "courtpro" in terms and "storybook" in terms
    assert "director" in terms


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_dump_operator_turns_extracts_only_human_text(tmp_path: Path, monkeypatch) -> None:
    projects = tmp_path / "projects"
    proj = projects / "-home-u-Desktop-courtpro"
    proj.mkdir(parents=True)
    _write_jsonl(
        proj / "sess.jsonl",
        [
            {"type": "user", "message": {"role": "user", "content": "fix the 404 page"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "ok"}},
            # tool_result block is not human-typed
            {"type": "user", "message": {"role": "user",
             "content": [{"type": "tool_result", "content": "x"}]}},
            {"type": "user", "message": {"role": "user", "content": "no chips please"}},
        ],
    )
    monkeypatch.setattr(la, "PROJECTS_ROOT", projects)
    out = tmp_path / "out"
    summary = la.dump_operator_turns(
        ["courtpro"], since_days=0, min_turns=1, out_dir=str(out)
    )
    assert summary["sessions"] == 1
    assert summary["total_turns"] == 2  # the two human turns, not the tool_result
    assert summary["by_bucket"] == {"courtpro": 1}
    dump = next(out.glob("courtpro__*.txt")).read_text()
    assert "fix the 404 page" in dump and "no chips please" in dump
    assert "ok" not in dump  # assistant turn excluded


def test_dump_respects_min_turns(tmp_path: Path, monkeypatch) -> None:
    projects = tmp_path / "projects"
    proj = projects / "-x-director-y"
    proj.mkdir(parents=True)
    _write_jsonl(
        proj / "s.jsonl",
        [{"type": "user", "message": {"role": "user", "content": "one"}}],
    )
    monkeypatch.setattr(la, "PROJECTS_ROOT", projects)
    summary = la.dump_operator_turns(
        ["director"], since_days=0, min_turns=4, out_dir=str(tmp_path / "o")
    )
    assert summary["sessions"] == 0  # below the floor
