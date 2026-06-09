"""Phase-1 unit tests: the flattened prompts exist and fully render.

No `nanoc`, no Jinja — po's `{{var}}` renderer must substitute every placeholder
in the Director and Reflector prompts with no leftover `{{...}}`.
"""

from __future__ import annotations

import re
from pathlib import Path

import po_director
from prefect_orchestration.templates import render_template

AGENTS_DIR = Path(po_director.__file__).parent / "agents"

# The full var set the flows will pass. Keep in sync with render.build_prompt.
_DIRECTOR_VARS = {
    "workspace_dir": "/tmp/ws",
    "goal": "Ship the roadmap.",
    "north_star": "open issues burned down",
    "board": "bd ready: (none)",
    "work_source": "ideate",
    "work_ask": "gate",
    "merge_mode": "auto",
    "merge_strategy": "pr",
    "memory": "(no prior handoff)",
}
_REFLECTOR_VARS = {
    "workspace_dir": "/tmp/ws",
    "goal": "Ship the roadmap.",
    "north_star": "open issues burned down",
    "board": "bd ready: (none)",
}
_DREAMER_VARS = {
    "workspace_dir": "/tmp/ws",
    "goal": "Ship the roadmap.",
    "north_star": "open issues burned down",
    "board": "bd ready: (none)",
    "transcripts": "- `/home/u/.claude/projects/-tmp-ws/abc.jsonl` (12 KB, 2026-06-09 03:00)",
}
_SHERIFF_VARS = {
    "feature_id": "ws-42",
    "workspace_dir": "/tmp/ws",
    "merge_mode": "auto",
    "merge_strategy": "pr",
    "ci_cmd": "make test",
}

_LEFTOVER = re.compile(r"\{\{.*?\}\}")


def test_prompt_files_exist() -> None:
    assert (AGENTS_DIR / "director" / "prompt.md").is_file()
    assert (AGENTS_DIR / "reflector" / "prompt.md").is_file()
    assert (AGENTS_DIR / "dreamer" / "prompt.md").is_file()
    assert (AGENTS_DIR / "pr-sheriff" / "prompt.md").is_file()


def test_dreamer_prompt_fully_renders() -> None:
    out = render_template(AGENTS_DIR, "dreamer", **_DREAMER_VARS)
    assert not _LEFTOVER.search(out), f"unrendered placeholders: {_LEFTOVER.findall(out)}"
    # Behavioral anchors: the two memory tiers + the transcript material.
    assert "STATE.md" in out
    assert ".director/memory/" in out
    assert "abc.jsonl" in out


def test_pr_sheriff_prompt_fully_renders() -> None:
    out = render_template(AGENTS_DIR, "pr-sheriff", **_SHERIFF_VARS)
    assert not _LEFTOVER.search(out), f"unrendered placeholders: {_LEFTOVER.findall(out)}"
    # Behavioral anchors: feature id, the four verdicts, dispatcher discipline.
    for anchor in ("ws-42", "fix-merge", "needs-human", "needs-rewrite"):
        assert anchor in out
    assert "never write application code" in out.lower()


def test_director_prompt_fully_renders() -> None:
    out = render_template(AGENTS_DIR, "director", **_DIRECTOR_VARS)
    assert not _LEFTOVER.search(out), f"unrendered placeholders: {_LEFTOVER.findall(out)}"
    # Key behavioral anchors survived the flatten.
    assert "work_ask = gate" in out
    assert "work_source = ideate" in out
    assert "bd human" in out
    assert "/tmp/ws" in out


def test_reflector_prompt_fully_renders() -> None:
    out = render_template(AGENTS_DIR, "reflector", **_REFLECTOR_VARS)
    assert not _LEFTOVER.search(out), f"unrendered placeholders: {_LEFTOVER.findall(out)}"
    assert "open issues burned down" in out
