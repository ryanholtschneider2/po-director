"""Prefect deployments for the Director.

Two paths:
- `register()` (the `po.deployments` entry point, used by `po deploy`) ships
  rig-agnostic no-schedule `*-manual` deployments.
- `build_workspace_deployments(cfg)` builds the *scheduled*, workspace-stamped
  pulse + roadmap + report + dream + improve deployments that `po director
  --start` applies. The workspace dir is baked into `parameters` because a cron
  run has no interactive CWD.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from prefect.client.schemas.schedules import CronSchedule
from prefect.deployments.runner import EntrypointType

from po_director.config import DEFAULT_PERSONA, DirectorConfig
from po_director.coordinator import (
    director_dream,
    director_improve,
    director_pulse,
    director_report,
    director_roadmap,
)
from po_director.sheriff import pr_sheriff

_MODULE_PATH = {"entrypoint_type": EntrypointType.MODULE_PATH}

# Merge modes where the AI/Sheriff is expected to land merges without a human
# click — these are the modes that get a standing, PR-triggered pr-sheriff
# deployment applied for the workspace. `human` (gate each) and `approve-all`
# (human click lands it) deliberately don't.
AUTO_MERGE_MODES = ("auto", "ai-approve-all")


def register() -> list:
    """Rig-agnostic manual deployments (for `po deploy` / `po run --at`)."""
    return [
        director_pulse.to_deployment(name="director-pulse-manual", **_MODULE_PATH),
        director_roadmap.to_deployment(name="director-roadmap-manual", **_MODULE_PATH),
        director_report.to_deployment(name="director-report-manual", **_MODULE_PATH),
        director_dream.to_deployment(name="director-dream-manual", **_MODULE_PATH),
        director_improve.to_deployment(name="director-improve-manual", **_MODULE_PATH),
        pr_sheriff.to_deployment(name="pr-sheriff-manual", **_MODULE_PATH),
    ]


def workspace_slug(workspace_dir: str) -> str:
    """Stable, filename-safe slug for a workspace dir (name + short hash)."""
    base = re.sub(r"[^a-zA-Z0-9]+", "-", workspace_dir.rstrip("/").split("/")[-1]).strip("-")
    digest = hashlib.sha1(workspace_dir.encode("utf-8")).hexdigest()[:6]
    return (base or "ws") + "-" + digest


def _persona_slug(persona: str) -> str:
    """Filename-safe slug for a persona name (for deployment-name suffixes)."""
    return re.sub(r"[^a-zA-Z0-9]+", "-", persona).strip("-") or "persona"


def sheriff_deployment_name(cfg: DirectorConfig) -> str:
    """The workspace's PR-Sheriff deployment name.

    Workspace-scoped (one PR queue per repo) — no persona fold, since the
    Sheriff triages the repo's merges regardless of which persona dispatched
    it. ``pr_sheriff`` is run PR-by-PR with ``feature_id`` supplied per run.
    """
    return "pr-sheriff-" + workspace_slug(cfg.workspace_dir)


def build_sheriff_deployment(cfg: DirectorConfig) -> Any:
    """The standing, event-triggered PR-Sheriff deployment for this workspace.

    No schedule — it's fired per PR via ``sheriff_dispatch.on_pr_opened`` (the
    software-dev agentic flow calls it when a worker's PR is opened). The
    workspace dir is baked into ``parameters``; ``feature_id`` is supplied per
    run by the dispatcher.
    """
    return pr_sheriff.to_deployment(
        name=sheriff_deployment_name(cfg),
        parameters={"workspace_dir": cfg.workspace_dir},
        tags=["po-director", "pr-sheriff"],
        description="PR Sheriff merge triage for " + cfg.workspace_dir,
        work_pool_name="po",
        **_MODULE_PATH,
    )


def _persona_workspace_slug(cfg: DirectorConfig) -> str:
    """Workspace slug, persona-folded for non-default personas (shared helper)."""
    slug = workspace_slug(cfg.workspace_dir)
    if cfg.persona != DEFAULT_PERSONA:
        slug = _persona_slug(cfg.persona) + "-" + slug
    return slug


def deployment_names(cfg: DirectorConfig) -> tuple[str, str, str, str, str]:
    """Scheduled pulse + roadmap + report + dream + improve deployment names.

    The persona is folded into the slug when it isn't the default `director`,
    so several personas can run against one workspace without colliding. Names
    stay byte-identical for the default persona (existing deployments survive).
    """
    slug = _persona_workspace_slug(cfg)
    return (
        "director-pulse-" + slug,
        "director-roadmap-" + slug,
        "director-report-" + slug,
        "director-dream-" + slug,
        "director-improve-" + slug,
    )


def legacy_reflect_deployment_name(cfg: DirectorConfig) -> str:
    """The pre-rename `director-reflect-<slug>` deployment name.

    `director-reflect` was renamed to `director-report`. `po director stop`
    targets this so an upgraded workspace's stale reflect deployment is cleaned
    up rather than orphaned on the server.
    """
    return "director-reflect-" + _persona_workspace_slug(cfg)


def build_workspace_deployments(cfg: DirectorConfig) -> list[Any]:
    """Scheduled pulse + roadmap + report + dream + improve deployments for this workspace."""
    pulse_name, roadmap_name, report_name, dream_name, improve_name = deployment_names(cfg)
    params = {"workspace_dir": cfg.workspace_dir}
    deployments: list[Any] = [
        director_pulse.to_deployment(
            name=pulse_name,
            schedule=CronSchedule(cron=cfg.pulse_cron),
            parameters=params,
            tags=["po-director", "director-pulse"],
            description="Director pulse for " + cfg.workspace_dir,
            work_pool_name="po",
            **_MODULE_PATH,
        ),
        director_roadmap.to_deployment(
            name=roadmap_name,
            schedule=CronSchedule(cron=cfg.roadmap_cron),
            parameters=params,
            tags=["po-director", "director-roadmap"],
            description="Director hourly roadmap planning for " + cfg.workspace_dir,
            work_pool_name="po",
            **_MODULE_PATH,
        ),
        director_report.to_deployment(
            name=report_name,
            schedule=CronSchedule(cron=cfg.report_cron),
            parameters=params,
            tags=["po-director", "director-report"],
            description="Director nightly report for " + cfg.workspace_dir,
            work_pool_name="po",
            **_MODULE_PATH,
        ),
        director_dream.to_deployment(
            name=dream_name,
            schedule=CronSchedule(cron=cfg.dream_cron),
            parameters=params,
            tags=["po-director", "director-dream"],
            description="Director nightly consolidation for " + cfg.workspace_dir,
            work_pool_name="po",
            **_MODULE_PATH,
        ),
        director_improve.to_deployment(
            name=improve_name,
            schedule=CronSchedule(cron=cfg.improve_cron),
            parameters=params,
            tags=["po-director", "director-improve"],
            description="Director autonomy audit for " + cfg.workspace_dir,
            work_pool_name="po",
            **_MODULE_PATH,
        ),
    ]
    # Auto merge modes get a standing PR-Sheriff deployment, fired per PR by
    # the agentic flow (see sheriff_dispatch.on_pr_opened). Human/approve-all
    # modes keep the merge a human decision, so no standing Sheriff.
    if cfg.merge_mode in AUTO_MERGE_MODES:
        deployments.append(build_sheriff_deployment(cfg))
    return deployments
