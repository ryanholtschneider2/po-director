"""Prefect deployments for the Director.

Two paths:
- `register()` (the `po.deployments` entry point, used by `po deploy`) ships
  rig-agnostic no-schedule `*-manual` deployments.
- `build_workspace_deployments(cfg)` builds the *scheduled*, workspace-stamped
  pulse + reflect deployments that `po director --start` applies. The workspace
  dir is baked into `parameters` because a cron run has no interactive CWD.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from prefect.client.schemas.schedules import CronSchedule
from prefect.deployments.runner import EntrypointType

from po_director.config import DEFAULT_PERSONA, DirectorConfig
from po_director.coordinator import director_pulse, director_reflect

_MODULE_PATH = {"entrypoint_type": EntrypointType.MODULE_PATH}


def register() -> list:
    """Rig-agnostic manual deployments (for `po deploy` / `po run --at`)."""
    return [
        director_pulse.to_deployment(name="director-pulse-manual", **_MODULE_PATH),
        director_reflect.to_deployment(name="director-reflect-manual", **_MODULE_PATH),
    ]


def workspace_slug(workspace_dir: str) -> str:
    """Stable, filename-safe slug for a workspace dir (name + short hash)."""
    base = re.sub(r"[^a-zA-Z0-9]+", "-", workspace_dir.rstrip("/").split("/")[-1]).strip("-")
    digest = hashlib.sha1(workspace_dir.encode("utf-8")).hexdigest()[:6]
    return (base or "ws") + "-" + digest


def _persona_slug(persona: str) -> str:
    """Filename-safe slug for a persona name (for deployment-name suffixes)."""
    return re.sub(r"[^a-zA-Z0-9]+", "-", persona).strip("-") or "persona"


def deployment_names(cfg: DirectorConfig) -> tuple[str, str]:
    """Scheduled pulse + reflect deployment names for this workspace.

    The persona is folded into the slug when it isn't the default `director`,
    so several personas can run against one workspace without colliding. Names
    stay byte-identical for the default persona (existing deployments survive).
    """
    slug = workspace_slug(cfg.workspace_dir)
    if cfg.persona != DEFAULT_PERSONA:
        slug = _persona_slug(cfg.persona) + "-" + slug
    return "director-pulse-" + slug, "director-reflect-" + slug


def build_workspace_deployments(cfg: DirectorConfig) -> list[Any]:
    """Scheduled pulse + reflect deployments stamped with this workspace."""
    pulse_name, reflect_name = deployment_names(cfg)
    params = {"workspace_dir": cfg.workspace_dir}
    return [
        director_pulse.to_deployment(
            name=pulse_name,
            schedule=CronSchedule(cron=cfg.pulse_cron),
            parameters=params,
            tags=["po-director", "director-pulse"],
            description="Director pulse for " + cfg.workspace_dir,
            work_pool_name="po",
            **_MODULE_PATH,
        ),
        director_reflect.to_deployment(
            name=reflect_name,
            schedule=CronSchedule(cron=cfg.reflect_cron),
            parameters=params,
            tags=["po-director", "director-reflect"],
            description="Director daily reflection for " + cfg.workspace_dir,
            work_pool_name="po",
            **_MODULE_PATH,
        ),
    ]
