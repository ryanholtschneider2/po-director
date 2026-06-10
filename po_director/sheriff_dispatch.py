"""Event dispatch for the PR Sheriff.

The software-dev agentic flow calls :func:`on_pr_opened` when a worker opens a
PR for a feature. This module decides — based on the workspace's ``merge_mode``
— whether to fire the standing ``pr-sheriff`` deployment for that feature.

ZFC split: the agentic flow is transport (it just announces "a PR was opened
for feature X in workspace Y"); the *judgment* of whether the Sheriff should
run (the merge-mode gate) and *which* deployment to hit lives here, so
``merge_mode`` + the deployment slug stay single-sourced in po-director.

Best-effort by contract: never raises. A non-auto merge mode, an unreadable
config, a missing deployment, or an unreachable Prefect server all return
``False`` — a PR opening must never break the flow that opened it.
"""

from __future__ import annotations

import logging

from po_director.config import load_config
from po_director.deployments import AUTO_MERGE_MODES, sheriff_deployment_name

logger = logging.getLogger(__name__)


def on_pr_opened(
    workspace_dir: str,
    feature_id: str,
    *,
    merge_mode: str | None = None,
) -> bool:
    """Fire the workspace's pr-sheriff deployment for ``feature_id`` if auto.

    Returns ``True`` iff a deployment run was dispatched. ``merge_mode``
    overrides the workspace default for this call (parallels ``pr_sheriff``'s
    own override). The deployment run is fire-and-forget (``timeout=0``) so the
    caller never blocks on the Sheriff's agent turn.
    """
    try:
        cfg = load_config(workspace_dir)
    except Exception:
        logger.warning(
            "pr-sheriff dispatch: could not load config at %s — skip",
            workspace_dir,
            exc_info=True,
        )
        return False

    effective_mode = merge_mode or cfg.merge_mode
    if effective_mode not in AUTO_MERGE_MODES:
        logger.info(
            "pr-sheriff dispatch: merge_mode=%s is not auto — skip (feature=%s)",
            effective_mode,
            feature_id,
        )
        return False

    name = sheriff_deployment_name(cfg)
    try:
        from prefect.deployments import run_deployment

        run_deployment(
            name="pr-sheriff/" + name,
            parameters={"feature_id": feature_id},
            timeout=0,  # fire-and-forget — don't wait for the Sheriff to finish
        )
    except Exception:
        logger.warning(
            "pr-sheriff dispatch: run_deployment failed for %s (feature=%s) — skip",
            name,
            feature_id,
            exc_info=True,
        )
        return False

    logger.info("pr-sheriff dispatched: deployment=%s feature=%s", name, feature_id)
    return True
