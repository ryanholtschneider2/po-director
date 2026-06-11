"""Gather workspace state and render the Director / Reflector prompts.

This is the minimal slice of nanoc's `talk` that the Director needs: read the
goal doc, snapshot the beads board + `po status`, surface the latest handoff
note, and feed it all as `{{var}}` values into po's `render_template`.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path

from prefect_orchestration.templates import render_template

from po_director.config import DirectorConfig

logger = logging.getLogger(__name__)

AGENTS_DIR = Path(__file__).parent / "agents"

# Where the Claude CLI stores per-session transcripts. The cwd is slugged by
# replacing every non-alphanumeric run with a single dash (so
# `/home/u/Desktop/Code` -> `-home-u-Desktop-Code`). Used by the dreamer to find
# the day's sessions to consolidate.
_CLAUDE_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def project_transcripts_dir(workspace_dir: str) -> Path:
    """The `~/.claude/projects/<slug>` dir holding this workspace's transcripts."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(workspace_dir))
    return _CLAUDE_PROJECTS_ROOT / slug

# Board snapshot commands, run rooted at the workspace dir. Kept small and
# read-only; the agent can run more itself.
_BOARD_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("ready", ["bd", "ready"]),
    ("in_progress", ["bd", "list", "--status", "in_progress"]),
    ("open_p012", ["bd", "list", "--status", "open", "--priority", "0,1,2"]),
    ("human_gates", ["bd", "list", "--label", "human", "--status", "open"]),
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


def recent_transcripts(cfg: DirectorConfig, *, within_hours: float = 24.0) -> str:
    """List the workspace's session transcripts touched in the last `within_hours`.

    Returns a prompt block of `path (size, modified)` lines for the dreamer to
    read and consolidate, plus the projects dir and the slug convention so the
    agent can locate sessions itself if the computed dir is empty (e.g. a
    worktree subdir got its own slug). Transport only — the agent does the
    reading and the judgment of what's worth remembering.
    """
    proj_dir = project_transcripts_dir(cfg.workspace_dir)
    cutoff = time.time() - within_hours * 3600.0
    lines: list[str] = [
        "Projects dir for this workspace: `" + str(proj_dir) + "`",
        "(Claude slugs the workspace path by replacing non-alphanumerics with `-`."
        " If the dir below is empty or missing, glob"
        " `~/.claude/projects/*<workspace-name>*/**/*.jsonl` yourself.)",
        "",
    ]
    if not proj_dir.is_dir():
        lines.append("(no transcripts dir found yet — locate sessions yourself)")
        return "\n".join(lines)
    recent = sorted(
        (p for p in proj_dir.glob("**/*.jsonl") if p.is_file() and p.stat().st_mtime >= cutoff),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not recent:
        lines.append("(no sessions in the last " + str(int(within_hours)) + "h)")
        return "\n".join(lines)
    lines.append("Sessions touched in the last " + str(int(within_hours)) + "h (newest first):")
    for p in recent:
        st = p.stat()
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        lines.append("- `" + str(p) + "` (" + str(st.st_size // 1024) + " KB, " + mtime + ")")
    return "\n".join(lines)


# How fresh a roadmap TL;DR must be to surface in the pulse's board snapshot.
# The roadmapper runs hourly, so a 2h window covers "the plan changed since the
# last pulse saw it" without resurfacing a stale plan-update indefinitely.
_ROADMAP_TLDR_NAME = "roadmap-tldr.md"
_ROADMAP_TLDR_FRESH_HOURS = 2.0


def _plan_update(cfg: DirectorConfig, *, within_hours: float = _ROADMAP_TLDR_FRESH_HOURS) -> str:
    """The roadmapper's latest TL;DR, if it was refreshed within `within_hours`.

    The hourly `director-roadmap` pass writes `.director/roadmap-tldr.md` after
    it updates ROADMAP.md + beads. Surfacing it in the pulse board means the next
    pulse sees that the plan changed without re-deriving it. Returns "" when the
    file is missing or stale, so `build_board` only adds the section when there's
    something fresh to show.
    """
    tldr = cfg.memory_dir / _ROADMAP_TLDR_NAME
    try:
        if not tldr.is_file() or tldr.stat().st_mtime < time.time() - within_hours * 3600.0:
            return ""
        body = tldr.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return body


def build_board(cfg: DirectorConfig) -> str:
    """Snapshot the work board + execution signals as a single prompt block.

    When the hourly roadmapper has refreshed `.director/roadmap-tldr.md` within
    the last couple of hours, a leading "Plan update" section carries that TL;DR
    so a pulse reacts to a changed plan automatically.
    """
    sections: list[str] = []
    plan_update = _plan_update(cfg)
    if plan_update:
        sections.append("### Plan update (latest roadmap pass)\n" + plan_update)
    for label, cmd in _BOARD_COMMANDS:
        out = _run(cmd, cfg.workspace_dir)
        sections.append("### " + label + "\n" + (out or "(none)"))
    return "\n\n".join(sections)


def build_prompt(
    cfg: DirectorConfig,
    role: str,
    *,
    agents_dir: Path | None = None,
    **extra: object,
) -> str:
    """Render `<agents_dir>/<role>/prompt.md` with the gathered workspace state.

    `agents_dir` defaults to po_director's builtin prompts; the persona-aware
    flows pass a resolved persona directory so a pack-shipped persona's
    `prompt.md` (and optional `reporter/prompt.md`, `roadmapper/prompt.md`)
    renders instead. `extra` overrides any computed var.
    """
    base = agents_dir if agents_dir is not None else AGENTS_DIR
    vars_: dict[str, object] = {
        "workspace_dir": cfg.workspace_dir,
        "persona": cfg.persona,
        "goal": _read_goal(cfg),
        "north_star": cfg.north_star,
        "work_source": cfg.work_source,
        "work_ask": cfg.work_ask,
        "merge_mode": cfg.merge_mode,
        "merge_strategy": cfg.merge_strategy,
        "board": build_board(cfg),
        "memory": _latest_handoff(cfg),
    }
    vars_.update(extra)
    return render_template(base, role, **vars_)


def persona_prompt(cfg: DirectorConfig, **extra: object) -> str:
    """Render the persona's pulse prompt (`<persona_dir>/prompt.md`).

    Resolves `cfg.persona` via the `po.personas` entry points / builtins. For
    the default `director` persona this is byte-identical to
    `build_prompt(cfg, "director")`.
    """
    from po_director.persona import resolve_persona_dir

    persona_dir = resolve_persona_dir(cfg.persona)
    return build_prompt(cfg, persona_dir.name, agents_dir=persona_dir.parent, **extra)


def roadmap_prompt(cfg: DirectorConfig, **extra: object) -> str:
    """Render the hourly planning ('roadmap') prompt.

    Runs as the SAME persona doing a planning pass (not a separate identity):
    prefers a persona-shipped `<persona_dir>/roadmapper/prompt.md`, else the
    builtin roadmapper. The persona name is available as `{{persona}}` so a
    pack's roadmapper (e.g. the CEO's) reads in that persona's voice.
    """
    from po_director.persona import resolve_persona_dir

    persona_dir = resolve_persona_dir(cfg.persona)
    if (persona_dir / "roadmapper" / "prompt.md").is_file():
        return build_prompt(cfg, "roadmapper", agents_dir=persona_dir, **extra)
    return build_prompt(cfg, "roadmapper", **extra)


def report_prompt(cfg: DirectorConfig, **extra: object) -> str:
    """Render the nightly report prompt, preferring a persona-shipped reporter.

    Uses `<persona_dir>/reporter/prompt.md` when the persona ships one;
    otherwise falls back to po_director's builtin reporter.
    """
    from po_director.persona import resolve_persona_dir

    persona_dir = resolve_persona_dir(cfg.persona)
    if (persona_dir / "reporter" / "prompt.md").is_file():
        return build_prompt(cfg, "reporter", agents_dir=persona_dir, **extra)
    return build_prompt(cfg, "reporter", **extra)


def dream_prompt(cfg: DirectorConfig, **extra: object) -> str:
    """Render the daily consolidation ('dream') prompt.

    Prefers a persona-shipped `<persona_dir>/dreamer/prompt.md`, else falls back
    to po_director's builtin dreamer. Injects the recent-transcripts block so the
    agent knows which sessions to read and distil into memory.
    """
    from po_director.persona import resolve_persona_dir

    extra.setdefault("transcripts", recent_transcripts(cfg))
    persona_dir = resolve_persona_dir(cfg.persona)
    if (persona_dir / "dreamer" / "prompt.md").is_file():
        return build_prompt(cfg, "dreamer", agents_dir=persona_dir, **extra)
    return build_prompt(cfg, "dreamer", **extra)


def improve_prompt(cfg: DirectorConfig, **extra: object) -> str:
    """Render the loop-audit / autonomy-ratchet ('improve') prompt.

    Prefers a persona-shipped `<persona_dir>/improver/prompt.md`, else the
    builtin improver. The caller (the flow) injects `audit_dir` (where the
    operator-turn dumps live) and `audit_summary` (counts) after running the
    extractor — this keeps the transcript-gathering in transport.
    """
    from po_director.persona import resolve_persona_dir

    persona_dir = resolve_persona_dir(cfg.persona)
    if (persona_dir / "improver" / "prompt.md").is_file():
        return build_prompt(cfg, "improver", agents_dir=persona_dir, **extra)
    return build_prompt(cfg, "improver", **extra)
