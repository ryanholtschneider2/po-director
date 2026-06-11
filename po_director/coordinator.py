"""The Director flows: `director-pulse` (heartbeat), `director-roadmap`
(hourly planning), `director-report` (nightly), `director-dream`, `director-improve`.

Both render a persona prompt from gathered workspace state, run one agent turn
via `prefect_orchestration.AgentSession`, and post to Slack via the flow (not
the agent) so delivery is deterministic.

The pulse's approval gate is enforced *agent-side* (the prompt tells it to file
a `human`-labeled gate per `approval_mode` instead of dispatching). The flow's
only job around the gate is to **post any newly-filed gate to Slack** — detected
as the set-difference of open `human`-labeled beads across the turn, which is
robust to bd's exact output format.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from prefect import flow, get_run_logger
from prefect.exceptions import MissingContextError
from prefect_orchestration.agent_session import AgentSession
from prefect_orchestration.backend_select import select_default_backend

from po_director.config import DEFAULT_PERSONA, DirectorConfig, load_config
from po_director.loop_audit import dump_operator_turns, match_terms_for
from po_director.notify import post_slack
from po_director.render import (
    dream_prompt,
    improve_prompt,
    persona_prompt,
    report_prompt,
    roadmap_prompt,
)

logger = logging.getLogger(__name__)

_PROPOSAL_TITLE = "Director proposal — needs your approval"
_ROADMAP_TITLE = "Plan updated"
_REPORT_TITLE = "Director — nightly report"
_DREAM_TITLE = "Director — nightly consolidation"
_IMPROVE_TITLE = "Director — autonomy audit"


def persona_role(cfg: DirectorConfig, base: str) -> str:
    """Session/tmux role label for a builtin role under the active persona.

    Byte-identical to `base` for the default `director` persona (so existing
    sessions aren't orphaned); persona-prefixed otherwise so several personas
    can run against one workspace without colliding on session names.
    """
    if cfg.persona == DEFAULT_PERSONA:
        return base
    return cfg.persona + "-" + base


def _log() -> logging.Logger | logging.LoggerAdapter:
    try:
        return get_run_logger()
    except MissingContextError:
        return logger


def _gate_map(workspace_dir: str) -> dict[str, str]:
    """Snapshot open human-approval gates as `{issue_id: title}`.

    Gates are `human`-labeled beads; we list them by label/status (beads-rust
    has no `bd human` subcommand). The diff is by gate id (not by rendered
    line), giving an accurate new-gate count and a clean Slack body.
    """
    try:
        proc = subprocess.run(
            ["bd", "list", "--label", "human", "--status", "open", "--json"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        logger.exception("bd list --label human --status open --json failed")
        return {}
    try:
        # Tolerate `null`/empty output when there are no gates.
        rows = json.loads(proc.stdout or "[]") or []
    except ValueError:
        logger.warning("bd list --label human --json returned non-JSON output")
        return {}
    return {str(r["id"]): str(r.get("title", "")) for r in rows if isinstance(r, dict) and "id" in r}


def _fresh_roadmap_tldr(workspace_dir: str, *, since: float) -> str:
    """The roadmap TL;DR the agent wrote this run, or "" if it didn't refresh it.

    The roadmapper writes `.director/roadmap-tldr.md` after updating ROADMAP.md +
    beads. We only post when the file's mtime is at/after `since` (the turn's
    start), so a no-change pass — or a stale TL;DR from an earlier run — posts
    nothing instead of re-announcing an old plan update.
    """
    tldr = Path(workspace_dir) / ".director" / "roadmap-tldr.md"
    try:
        if not tldr.is_file() or tldr.stat().st_mtime < since:
            return ""
        return tldr.read_text(encoding="utf-8").strip()
    except OSError:
        logger.exception("reading roadmap-tldr.md failed")
        return ""


def _build_backend(factory: object, issue: str, role: str) -> object:
    """Instantiate a backend factory, plumbing `issue`/`role` when required.

    Backend constructors have different shapes: the tmux backends
    (`TmuxClaudeBackend`, `TmuxCodexBackend`) require `issue` and `role`
    positional args for their session names, while the stateless backends
    (`ClaudeCliBackend`, `CodexCliBackend`, `StubBackend`) take none. Try the
    issue+role shape first, fall back to zero-arg — mirrors core's
    `prompt_formula._make_backend`.
    """
    try:
        return factory(issue=issue, role=role)  # type: ignore[operator]
    except TypeError:
        return factory()  # type: ignore[operator]


def _make_session(
    cfg: DirectorConfig,
    role: str,
    backend: object | None,
    *,
    issue: str = "director",
) -> AgentSession:
    be = (
        backend
        if backend is not None
        else _build_backend(select_default_backend(), issue, role)
    )
    return AgentSession(
        role=role,
        repo_path=Path(cfg.workspace_dir),
        backend=be,  # type: ignore[arg-type]
        model="opus",
        skip_mail_inject=True,
        overlay=False,
        skills=False,
    )


@flow(name="director-pulse")
def director_pulse(
    workspace_dir: str,
    *,
    dry_run: bool = False,
    backend: object | None = None,
) -> dict[str, object]:
    """One forward-motion heartbeat. Returns a small status dict.

    On `dry_run` short-circuits before any agent turn (no Claude call).
    """
    log = _log()
    cfg = load_config(workspace_dir)

    if dry_run:
        log.info("director-pulse dry-run: skipping agent turn for %s", cfg.workspace_dir)
        return {"quiet": True, "dry_run": True, "new_gates": 0, "posted": 0}

    before = _gate_map(cfg.workspace_dir)
    session = _make_session(cfg, cfg.persona, backend)
    result = session.prompt(persona_prompt(cfg))
    after = _gate_map(cfg.workspace_dir)

    new_ids = sorted(set(after) - set(before))
    posted = 0
    if new_ids:
        body = (
            "\n".join("`" + gid + "` — " + after[gid] for gid in new_ids)
            + '\n\nApprove with: `bd close <id> -r "yes, go"`'
            + ' (or decline: `bd close <id> -r "dismissed"`).'
        )
        if post_slack(cfg.slack_channel, _PROPOSAL_TITLE, body):
            posted = 1

    quiet = not result.strip() and not new_ids
    if not quiet:
        log.info(
            "director-pulse: %d new gate(s), posted=%d, %d chars output",
            len(new_ids),
            posted,
            len(result),
        )
    return {
        "quiet": quiet,
        "dry_run": False,
        "new_gates": len(new_ids),
        "posted": posted,
        "session_id": session.session_id,
    }


@flow(name="director-roadmap")
def director_roadmap(
    workspace_dir: str,
    *,
    dry_run: bool = False,
    backend: object | None = None,
) -> dict[str, object]:
    """Hourly planning pass: maintain ROADMAP.md and decompose it into beads.

    Runs as the SAME persona doing a dedicated PLANNING turn (role
    `roadmapper`). The agent assesses progress since the last roadmap pass,
    updates `ROADMAP.md` at the workspace root (the durable higher-level plan),
    decomposes it into dependency-wired, prioritized beads (coalescing against
    existing ones), and writes a timestamped TL;DR of what changed this pass to
    `.director/roadmap-tldr.md`. It does NOT dispatch builds (the pulse does)
    and does NOT merge.

    Transport: the flow posts that TL;DR to Slack titled "Plan updated" when the
    agent refreshed it this run, so the operator sees the plan moved without
    reading the whole roadmap. On `dry_run` short-circuits before any agent turn.
    """
    log = _log()
    cfg = load_config(workspace_dir)

    if dry_run:
        log.info("director-roadmap dry-run: skipping agent turn for %s", cfg.workspace_dir)
        return {"dry_run": True, "posted": 0, "tldr": False}

    started = time.time()
    session = _make_session(cfg, persona_role(cfg, "roadmapper"), backend)
    result = session.prompt(roadmap_prompt(cfg))

    tldr = _fresh_roadmap_tldr(cfg.workspace_dir, since=started)
    posted = 0
    if tldr and post_slack(cfg.slack_channel, _ROADMAP_TITLE, tldr):
        posted = 1
    return {
        "dry_run": False,
        "posted": posted,
        "tldr": bool(tldr),
        "chars": len(result),
        "session_id": session.session_id,
    }


@flow(name="director-report")
def director_report(
    workspace_dir: str,
    *,
    dry_run: bool = False,
    backend: object | None = None,
) -> dict[str, object]:
    """Nightly report: what the Director DID since the last report, posted to Slack.

    One agent turn (role `reporter`) that summarizes the day's actions
    (dispatches, merges, decisions), then bubbles up what needs the operator:
    open `human`-labeled gates, decisions awaited, and blockers.
    """
    log = _log()
    cfg = load_config(workspace_dir)

    if dry_run:
        log.info("director-report dry-run: skipping agent turn for %s", cfg.workspace_dir)
        return {"dry_run": True, "posted": 0}

    session = _make_session(cfg, persona_role(cfg, "reporter"), backend)
    result = session.prompt(report_prompt(cfg))

    posted = 0
    if result.strip() and post_slack(cfg.slack_channel, _REPORT_TITLE, result):
        posted = 1
    return {
        "dry_run": False,
        "posted": posted,
        "chars": len(result),
        "session_id": session.session_id,
    }


@flow(name="director-dream")
def director_dream(
    workspace_dir: str,
    *,
    dry_run: bool = False,
    backend: object | None = None,
) -> dict[str, object]:
    """Nightly memory consolidation ('dreaming').

    One agent turn that reads the day's session transcripts, distils durable
    facts/decisions/lessons into `.director/STATE.md` + a dated
    `.director/memory/<date>.md`, and updates docs where the day's work changed
    reality. The flow is transport (schedule + spawn + post the digest); the
    agent owns the judgment of what's worth remembering. Posts a short digest to
    Slack. On `dry_run` short-circuits before any agent turn.
    """
    log = _log()
    cfg = load_config(workspace_dir)

    if dry_run:
        log.info("director-dream dry-run: skipping agent turn for %s", cfg.workspace_dir)
        return {"dry_run": True, "posted": 0}

    session = _make_session(cfg, persona_role(cfg, "dreamer"), backend)
    result = session.prompt(dream_prompt(cfg))

    posted = 0
    if result.strip() and post_slack(cfg.slack_channel, _DREAM_TITLE, result):
        posted = 1
    return {
        "dry_run": False,
        "posted": posted,
        "chars": len(result),
        "session_id": session.session_id,
    }


@flow(name="director-improve")
def director_improve(
    workspace_dir: str,
    *,
    since_days: float = 30.0,
    min_turns: int = 4,
    dry_run: bool = False,
    backend: object | None = None,
) -> dict[str, object]:
    """The autonomy ratchet: mine operator interventions, ship fixes.

    Turns the operator's recent corrections / nudges / setup-help / taste
    complaints into concrete system changes, so the system needs the human less
    each cycle and creeps toward in-the-loop performance. Transport: gather the
    operator's human turns from the workspace's (and its businesses') session
    transcripts into a run dir. Judgment: the agent classifies the interventions,
    writes a dated audit report, files beads for the top fixes, dispatches the
    safe well-scoped ones via `software-dev-agentic` (merge decisions are left to
    the PR Sheriff), and posts a digest. On `dry_run` short-circuits before any
    agent turn.
    """
    log = _log()
    cfg = load_config(workspace_dir)

    if dry_run:
        log.info("director-improve dry-run: skipping agent turn for %s", cfg.workspace_dir)
        return {"dry_run": True, "posted": 0, "sessions": 0}

    terms = match_terms_for(cfg.workspace_dir)
    audit_dir = str(Path(cfg.workspace_dir) / ".director" / "loop-audit" / "latest")
    summary = dump_operator_turns(
        terms, since_days=since_days, min_turns=min_turns, out_dir=audit_dir
    )
    log.info(
        "director-improve: %d sessions, %d operator turns from %s",
        summary["sessions"],
        summary["total_turns"],
        terms,
    )

    session = _make_session(cfg, persona_role(cfg, "improver"), backend)
    result = session.prompt(
        improve_prompt(
            cfg,
            audit_dir=audit_dir,
            audit_summary=json.dumps(summary, indent=2),
            match_terms=", ".join(terms),
            since_days=int(since_days),
        )
    )

    posted = 0
    if result.strip() and post_slack(cfg.slack_channel, _IMPROVE_TITLE, result):
        posted = 1
    return {
        "dry_run": False,
        "posted": posted,
        "sessions": summary["sessions"],
        "chars": len(result),
        "session_id": session.session_id,
    }
