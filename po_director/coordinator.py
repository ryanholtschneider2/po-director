"""The Director flows: `director-pulse` (heartbeat) and `director-reflect`.

Both render a persona prompt from gathered workspace state, run one agent turn
via `prefect_orchestration.AgentSession`, and post to Slack via the flow (not
the agent) so delivery is deterministic.

The pulse's approval gate is enforced *agent-side* (the prompt tells it to file
a `bd human` gate per `approval_mode` instead of dispatching). The flow's only
job around the gate is to **post any newly-filed gate to Slack** — detected as
the set-difference of `bd human list` lines across the turn, which is robust
to bd's exact output format.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from prefect import flow, get_run_logger
from prefect.exceptions import MissingContextError
from prefect_orchestration.agent_session import AgentSession
from prefect_orchestration.backend_select import select_default_backend

from po_director.config import DEFAULT_PERSONA, DirectorConfig, load_config
from po_director.notify import post_slack
from po_director.render import persona_prompt, reflect_prompt

logger = logging.getLogger(__name__)

_PROPOSAL_TITLE = "Director proposal — needs your approval"
_REFLECTION_TITLE = "Director — daily reflection"


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

    Uses `bd human list --json` so the diff is by gate id (not by rendered
    line), giving an accurate new-gate count and a clean Slack body.
    """
    try:
        proc = subprocess.run(
            ["bd", "human", "list", "--json"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        logger.exception("bd human list --json failed")
        return {}
    try:
        # bd v1.0.4 emits `null` (not `[]`) when there are no gates.
        rows = json.loads(proc.stdout or "[]") or []
    except ValueError:
        logger.warning("bd human list --json returned non-JSON output")
        return {}
    return {str(r["id"]): str(r.get("title", "")) for r in rows if isinstance(r, dict) and "id" in r}


def _make_session(cfg: DirectorConfig, role: str, backend: object | None) -> AgentSession:
    be = backend if backend is not None else select_default_backend()()
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
            + '\n\nApprove with: `bd human respond <id> -r "yes, go"`'
            + " (or dismiss: `bd human dismiss <id>`)."
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


@flow(name="director-reflect")
def director_reflect(
    workspace_dir: str,
    *,
    dry_run: bool = False,
    backend: object | None = None,
) -> dict[str, object]:
    """Daily one-page reflection, posted to Slack."""
    log = _log()
    cfg = load_config(workspace_dir)

    if dry_run:
        log.info("director-reflect dry-run: skipping agent turn for %s", cfg.workspace_dir)
        return {"dry_run": True, "posted": 0}

    session = _make_session(cfg, persona_role(cfg, "reflector"), backend)
    result = session.prompt(reflect_prompt(cfg))

    posted = 0
    if result.strip() and post_slack(cfg.slack_channel, _REFLECTION_TITLE, result):
        posted = 1
    return {
        "dry_run": False,
        "posted": posted,
        "chars": len(result),
        "session_id": session.session_id,
    }
