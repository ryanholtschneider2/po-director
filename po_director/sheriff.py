"""The `pr-sheriff` flow — outbound merge triage for a finished feature.

Mirrors the director-pulse shape: render the PR Sheriff persona from workspace +
feature state, run one agent turn via `AgentSession`, then do deterministic
post-processing (read the verdict file, post a needs-human gate to Slack). The
Sheriff is a triager + dispatcher — the agent itself runs CI, dispatches fixer
workers (`po run software-dev-fast`), and lands merges; the flow just sets up
context and surfaces the verdict.

Triggered per PR (default `merge_strategy = pr`) and swept by the director pulse
as a backstop. Honors `merge_mode` (auto | human | approve-all | ai-approve-all).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from prefect import flow

from po_director.config import load_config
from po_director.coordinator import _gate_map, _log, _make_session
from po_director.notify import post_slack
from po_director.render import build_prompt

logger = logging.getLogger(__name__)

_VALID_VERDICTS = ("merge", "fix-merge", "needs-human", "needs-rewrite")
_NEEDS_HUMAN_TITLE = "PR Sheriff — a merge needs your call"


def _verdict_path(workspace_dir: str, feature_id: str) -> Path:
    return Path(workspace_dir) / ".ade" / "sheriff" / (feature_id + ".json")


def _read_verdict(workspace_dir: str, feature_id: str) -> dict[str, str]:
    """Read `.ade/sheriff/<feature>.json`; empty dict if absent/garbage."""
    path = _verdict_path(workspace_dir, feature_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("unreadable PR Sheriff verdict at %s", path)
        return {}
    return data if isinstance(data, dict) else {}


@flow(name="pr-sheriff")
def pr_sheriff(
    workspace_dir: str,
    feature_id: str,
    *,
    dry_run: bool = False,
    backend: object | None = None,
) -> dict[str, object]:
    """Triage one feature's merge. Returns a small status dict incl. the verdict.

    On `dry_run` short-circuits before any agent turn (no Claude call).
    """
    log = _log()
    cfg = load_config(workspace_dir)

    if dry_run:
        log.info("pr-sheriff dry-run: skipping agent turn for %s", feature_id)
        return {"feature_id": feature_id, "verdict": None, "dry_run": True, "posted": 0}

    before = _gate_map(cfg.workspace_dir)
    session = _make_session(cfg, "pr-sheriff", backend)
    session.prompt(
        build_prompt(
            cfg,
            "pr-sheriff",
            feature_id=feature_id,
            merge_mode=cfg.merge_mode,
            merge_strategy=cfg.merge_strategy,
            ci_cmd=cfg.ci_cmd or "(detect from repo)",
        )
    )
    after = _gate_map(cfg.workspace_dir)

    verdict_doc = _read_verdict(cfg.workspace_dir, feature_id)
    verdict = verdict_doc.get("verdict")
    if verdict is not None and verdict not in _VALID_VERDICTS:
        log.warning("pr-sheriff returned unknown verdict %r for %s", verdict, feature_id)

    # Surface any newly-filed needs-human merge gate to Slack (set-diff of gates).
    new_ids = sorted(set(after) - set(before))
    posted = 0
    if new_ids:
        body = (
            "\n".join("`" + gid + "` — " + after[gid] for gid in new_ids)
            + '\n\nApprove with: `bd human respond <id> -r "yes, merge"`'
            + " (or dismiss: `bd human dismiss <id>`)."
        )
        if post_slack(cfg.slack_channel, _NEEDS_HUMAN_TITLE, body):
            posted = 1

    log.info(
        "pr-sheriff %s: verdict=%s, %d new gate(s), posted=%d",
        feature_id,
        verdict,
        len(new_ids),
        posted,
    )
    return {
        "feature_id": feature_id,
        "verdict": verdict,
        "reason": verdict_doc.get("reason"),
        "dry_run": False,
        "new_gates": len(new_ids),
        "posted": posted,
        "session_id": session.session_id,
    }
