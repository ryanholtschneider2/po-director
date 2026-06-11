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
    "rigs": "(no rigs configured — you operate this workspace directly.)",
    "work_source": "ideate",
    "work_ask": "gate",
    "merge_mode": "auto",
    "merge_strategy": "pr",
    "memory": "(no prior handoff)",
}
_REPORTER_VARS = {
    "workspace_dir": "/tmp/ws",
    "goal": "Ship the roadmap.",
    "north_star": "open issues burned down",
    "board": "bd ready: (none)",
}
_ROADMAPPER_VARS = {
    "workspace_dir": "/tmp/ws",
    "persona": "director",
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
_IMPROVER_VARS = {
    "workspace_dir": "/tmp/ws",
    "goal": "Ship the roadmap.",
    "north_star": "open issues burned down",
    "board": "bd ready: (none)",
    "audit_dir": "/tmp/ws/.director/loop-audit/latest",
    "audit_summary": '{"sessions": 12, "total_turns": 340}',
    "match_terms": "ws, courtpro, director",
    "since_days": "30",
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
    assert (AGENTS_DIR / "roadmapper" / "prompt.md").is_file()
    assert (AGENTS_DIR / "reporter" / "prompt.md").is_file()
    assert (AGENTS_DIR / "dreamer" / "prompt.md").is_file()
    assert (AGENTS_DIR / "improver" / "prompt.md").is_file()
    assert (AGENTS_DIR / "pr-sheriff" / "prompt.md").is_file()
    # The reflector prompt was renamed to reporter — it must be gone.
    assert not (AGENTS_DIR / "reflector" / "prompt.md").is_file()


def test_roadmapper_prompt_fully_renders() -> None:
    out = render_template(AGENTS_DIR, "roadmapper", **_ROADMAPPER_VARS)
    assert not _LEFTOVER.search(out), f"unrendered placeholders: {_LEFTOVER.findall(out)}"
    # Behavioral anchors: maintains ROADMAP.md, decomposes to beads, writes the
    # TL;DR, and does NOT dispatch builds (that's the pulse).
    assert "ROADMAP.md" in out
    assert "roadmap-tldr.md" in out
    assert "director" in out  # persona substituted in
    lowered = out.lower()
    assert "do not" in lowered and "dispatch" in lowered


def test_dreamer_prompt_fully_renders() -> None:
    out = render_template(AGENTS_DIR, "dreamer", **_DREAMER_VARS)
    assert not _LEFTOVER.search(out), f"unrendered placeholders: {_LEFTOVER.findall(out)}"
    # Behavioral anchors: the two memory tiers + the transcript material.
    assert "STATE.md" in out
    assert ".director/memory/" in out
    assert "abc.jsonl" in out


def test_improver_prompt_fully_renders() -> None:
    out = render_template(AGENTS_DIR, "improver", **_IMPROVER_VARS)
    assert not _LEFTOVER.search(out), f"unrendered placeholders: {_LEFTOVER.findall(out)}"
    # Behavioral anchors: the ratchet, the dump dir, dispatch + Sheriff-merges.
    assert "/tmp/ws/.director/loop-audit/latest" in out
    assert "software-dev-agentic" in out
    assert "PR Sheriff" in out
    assert "docs/loop-audits/" in out


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


def test_director_prompt_reporting_honesty_guardrail() -> None:
    """Reporting status is gated on the critic's verdict, not a worker's self-claim."""
    out = render_template(AGENTS_DIR, "director", **_DIRECTOR_VARS)
    lowered = out.lower()
    assert "commit hash" in lowered
    assert "pass verdict" in lowered
    # The anti-pattern it forbids: relaying a worker's unverified "done".
    assert "self-claimed" in lowered


def test_reporter_prompt_fully_renders() -> None:
    out = render_template(AGENTS_DIR, "reporter", **_REPORTER_VARS)
    assert not _LEFTOVER.search(out), f"unrendered placeholders: {_LEFTOVER.findall(out)}"
    assert "open issues burned down" in out
    # New report behavior: what I did + what needs the operator (open gates).
    lowered = out.lower()
    assert "what i did" in lowered
    assert "--label human --status open" in out
