"""Gather workspace state and render the Director / Reflector prompts.

This is the minimal slice of nanoc's `talk` that the Director needs: read the
goal doc, snapshot the beads board + `po status`, surface the latest handoff
note, and feed it all as `{{var}}` values into po's `render_template`.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from prefect_orchestration.templates import render_template

from po_director.config import DirectorConfig

logger = logging.getLogger(__name__)

AGENTS_DIR = Path(__file__).parent / "agents"

# Board snapshot commands, run rooted at the workspace dir. Kept small and
# read-only; the agent can run more itself.
_BOARD_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("ready", ["bd", "ready"]),
    ("in_progress", ["bd", "list", "--status", "in_progress"]),
    ("open_p012", ["bd", "list", "--status", "open", "--priority", "0,1,2"]),
    ("human_gates", ["bd", "human", "list"]),
    ("po_status", ["po", "status"]),
)


def _run(cmd: list[str], cwd: str) -> str:
    """Run a read-only command, returning stdout (empty string on failure)."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        logger.exception("director state command failed: %s", " ".join(cmd))
        return ""
    return (proc.stdout or "").strip()


def _read_goal(cfg: DirectorConfig) -> str:
    try:
        return cfg.goal_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "(no goal.md yet — infer the goal from the workspace and roadmap.)"


def _latest_handoff(cfg: DirectorConfig) -> str:
    mem_dir = cfg.memory_dir
    if not mem_dir.is_dir():
        return "(no prior handoff)"
    notes = sorted(
        (p for p in mem_dir.glob("handoff-*.md") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not notes:
        return "(no prior handoff)"
    latest = notes[0]
    try:
        body = latest.read_text(encoding="utf-8").strip()
    except OSError:
        return "(no prior handoff)"
    return "Latest handoff (`" + latest.name + "`):\n\n" + body


def build_board(cfg: DirectorConfig) -> str:
    """Snapshot the work board + execution signals as a single prompt block."""
    sections: list[str] = []
    for label, cmd in _BOARD_COMMANDS:
        out = _run(cmd, cfg.workspace_dir)
        sections.append("### " + label + "\n" + (out or "(none)"))
    return "\n\n".join(sections)


def build_prompt(cfg: DirectorConfig, role: str, **extra: object) -> str:
    """Render `<agents>/<role>/prompt.md` with the gathered workspace state.

    `role` is "director" or "reflector". `extra` overrides any computed var.
    """
    vars_: dict[str, object] = {
        "workspace_dir": cfg.workspace_dir,
        "goal": _read_goal(cfg),
        "north_star": cfg.north_star,
        "approval_mode": cfg.approval_mode,
        "board": build_board(cfg),
        "memory": _latest_handoff(cfg),
    }
    vars_.update(extra)
    return render_template(AGENTS_DIR, role, **vars_)
