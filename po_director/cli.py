"""`po director` utility verb (dispatched via the `po.commands` entry point).

Subcommand-as-flag because po's command dispatch only parses `--key value`:

    po director --start [--dir .] [--channel C…] [--approval-mode always]
    po director --status
    po director --stop

`--start` ensures `<dir>/.director.toml` (first run prompts for goal + North
Star on a TTY, else uses defaults), writes `goal.md`, and applies the two
workspace-stamped cron deployments. `--stop` deletes them. Default (no flag) is
`--status`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from po_director.config import (
    DEFAULT_NORTH_STAR,
    DirectorConfig,
    config_path,
    load_config,
    save_config,
)
from po_director.deployments import build_workspace_deployments, deployment_names


def _prompt(label: str, default: str) -> str:
    if not sys.stdin.isatty():
        return default
    try:
        ans = input(label + " [" + default + "]: ").strip()
    except EOFError:
        return default
    return ans or default


def _ensure_config(
    workspace_dir: str,
    *,
    channel: str | None,
    approval_mode: str | None,
    pulse_cron: str | None,
    reflect_cron: str | None,
    north_star: str | None,
) -> DirectorConfig:
    """Load-or-create config, prompting for goal/North Star on first run."""
    existing = config_path(workspace_dir).is_file()
    cfg = load_config(workspace_dir)

    if channel is not None:
        cfg.slack_channel = channel
    if approval_mode is not None:
        cfg.approval_mode = approval_mode
    if pulse_cron is not None:
        cfg.pulse_cron = pulse_cron
    if reflect_cron is not None:
        cfg.reflect_cron = reflect_cron
    if north_star is not None:
        cfg.north_star = north_star

    if not existing:
        # First run: capture goal + North Star.
        if north_star is None:
            cfg.north_star = _prompt("North Star (the metric to hold)", DEFAULT_NORTH_STAR)
        goal_file = cfg.goal_file
        if not goal_file.is_file():
            goal = _prompt(
                "Goal (one line; expand later in " + cfg.goal_path + ")",
                "Burn down the open issue queue for " + Path(workspace_dir).name + ".",
            )
            goal_file.parent.mkdir(parents=True, exist_ok=True)
            goal_file.write_text("# Goal\n\n" + goal + "\n", encoding="utf-8")

    DirectorConfig(**{k: getattr(cfg, k) for k in DirectorConfig.__dataclass_fields__})  # validate
    save_config(cfg)
    return cfg


def _start(cfg: DirectorConfig) -> str:
    from prefect_orchestration.deployments import apply_deployment

    applied = []
    for dep in build_workspace_deployments(cfg):
        apply_deployment(dep)
        applied.append(dep.name)
    lines = [
        "Director started for " + cfg.workspace_dir,
        "  config:       " + str(config_path(cfg.workspace_dir)),
        "  approval_mode: " + cfg.approval_mode,
        "  slack_channel: " + (cfg.slack_channel or "(none — set with --channel)"),
        "  deployments:  " + ", ".join(applied),
        "",
        "Ensure a worker is running:  prefect worker start --pool po",
    ]
    return "\n".join(lines)


def _stop(cfg: DirectorConfig) -> str:
    pulse_name, reflect_name = deployment_names(cfg)
    results = []
    for flow_name, dep_name in (
        ("director-pulse", pulse_name),
        ("director-reflect", reflect_name),
    ):
        target = flow_name + "/" + dep_name
        proc = subprocess.run(
            ["prefect", "deployment", "delete", target],
            capture_output=True,
            text=True,
            check=False,
        )
        results.append(target + (" — deleted" if proc.returncode == 0 else " — not found"))
    return "Director stopped:\n  " + "\n  ".join(results)


def _status(cfg: DirectorConfig) -> str:
    pulse_name, reflect_name = deployment_names(cfg)
    return "\n".join(
        [
            "Director status for " + cfg.workspace_dir,
            "  config:        " + str(config_path(cfg.workspace_dir)),
            "  north_star:    " + cfg.north_star,
            "  approval_mode: " + cfg.approval_mode,
            "  slack_channel: " + (cfg.slack_channel or "(none)"),
            "  pulse_cron:    " + cfg.pulse_cron,
            "  reflect_cron:  " + cfg.reflect_cron,
            "  deployments:   " + pulse_name + ", " + reflect_name,
        ]
    )


_ACTIONS = ("start", "stop", "status")


def director(
    action: str = "status",
    *,
    dir: str = ".",
    channel: str | None = None,
    approval_mode: str | None = None,
    pulse_cron: str | None = None,
    reflect_cron: str | None = None,
    north_star: str | None = None,
) -> str:
    """Start/stop/inspect the Director for a workspace directory.

    Examples:
        po director start
        po director start --dir . --channel C08LB4V9ZJ8 --approval-mode batches
        po director status
        po director stop
    """
    if action not in _ACTIONS:
        raise ValueError("action must be one of " + ", ".join(_ACTIONS) + "; got " + repr(action))
    workspace_dir = str(Path(dir).resolve())

    if action == "start":
        cfg = _ensure_config(
            workspace_dir,
            channel=channel,
            approval_mode=approval_mode,
            pulse_cron=pulse_cron,
            reflect_cron=reflect_cron,
            north_star=north_star,
        )
        return _start(cfg)
    if action == "stop":
        return _stop(load_config(workspace_dir))
    return _status(load_config(workspace_dir))
