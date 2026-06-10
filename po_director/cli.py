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
    ade_config_path,
    config_path,
    load_config,
    save_config,
)
from po_director.deployments import (
    AUTO_MERGE_MODES,
    build_workspace_deployments,
    deployment_names,
    sheriff_deployment_name,
)


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
    work_source: str | None,
    work_ask: str | None,
    merge_mode: str | None,
    merge_strategy: str | None,
    approval_mode: str | None,
    pulse_cron: str | None,
    reflect_cron: str | None,
    north_star: str | None,
    persona: str | None = None,
) -> DirectorConfig:
    """Load-or-create config, prompting for goal/North Star on first run.

    A workspace counts as already-configured if EITHER `.director.toml` or an
    `.ade/settings.toml` exists — the latter is the orc-managed source of truth
    (goal/north_star/involvement live there and override `.director.toml`), so
    when it's present we must NOT re-prompt or we'd shadow the operator's
    settings with a freshly-prompted value.

    A `--persona` flag wins over any persona set in the workspace files and
    seeds that persona's `config.toml` defaults (under workspace settings).
    """
    existing = config_path(workspace_dir).is_file() or ade_config_path(workspace_dir).is_file()
    cfg = load_config(workspace_dir, persona_override=persona)

    if channel is not None:
        cfg.slack_channel = channel
    if work_source is not None:
        cfg.work_source = work_source
    if work_ask is not None:
        cfg.work_ask = work_ask
    if merge_mode is not None:
        cfg.merge_mode = merge_mode
    if merge_strategy is not None:
        cfg.merge_strategy = merge_strategy
    # Legacy alias: --approval-mode maps onto the inbound work_ask axis.
    if approval_mode is not None and work_ask is None:
        cfg.work_ask = "gate"
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
        "  config:        " + str(config_path(cfg.workspace_dir)),
        "  work:          " + cfg.work_source + " / " + cfg.work_ask,
        "  merge_mode:    " + cfg.merge_mode + " (" + cfg.merge_strategy + ")",
        "  slack_channel: " + (cfg.slack_channel or "(none — set with --channel)"),
        "  deployments:   " + ", ".join(applied),
        "",
        "Ensure a worker is running:  prefect worker start --pool po",
    ]
    return "\n".join(lines)


def _stop(cfg: DirectorConfig) -> str:
    pulse_name, reflect_name, dream_name = deployment_names(cfg)
    results = []
    # The Sheriff deployment is only applied for auto merge modes, but always
    # attempt its delete — `prefect deployment delete` no-ops ("not found")
    # when it was never applied, so stop stays idempotent across mode changes.
    for flow_name, dep_name in (
        ("director-pulse", pulse_name),
        ("director-reflect", reflect_name),
        ("director-dream", dream_name),
        ("pr-sheriff", sheriff_deployment_name(cfg)),
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
    pulse_name, reflect_name, dream_name = deployment_names(cfg)
    deploy_line = pulse_name + ", " + reflect_name + ", " + dream_name
    if cfg.merge_mode in AUTO_MERGE_MODES:
        deploy_line += ", " + sheriff_deployment_name(cfg) + " (PR-triggered)"
    return "\n".join(
        [
            "Director status for " + cfg.workspace_dir,
            "  config:        " + str(config_path(cfg.workspace_dir)),
            "  persona:       " + cfg.persona,
            "  north_star:    " + cfg.north_star,
            "  work:          " + cfg.work_source + " / " + cfg.work_ask
            + "  (source / ask)",
            "  merge_mode:    " + cfg.merge_mode + " (" + cfg.merge_strategy + ")",
            "  ci_cmd:        " + (cfg.ci_cmd or "(unset — agent will detect)"),
            "  slack_channel: " + (cfg.slack_channel or "(none)"),
            "  pulse_cron:    " + cfg.pulse_cron,
            "  reflect_cron:  " + cfg.reflect_cron,
            "  dream_cron:    " + cfg.dream_cron,
            "  deployments:   " + deploy_line,
        ]
    )


_ACTIONS = ("start", "stop", "status", "render")


def director(
    action: str = "status",
    *,
    dir: str = ".",
    channel: str | None = None,
    work_source: str | None = None,
    work_ask: str | None = None,
    merge_mode: str | None = None,
    merge_strategy: str | None = None,
    approval_mode: str | None = None,  # deprecated alias -> work_ask=gate
    pulse_cron: str | None = None,
    reflect_cron: str | None = None,
    north_star: str | None = None,
    persona: str | None = None,
) -> str:
    """Start/stop/inspect the Director for a workspace directory.

    Involvement is two independent axes (see .ade/settings.toml):
      inbound  --work-source ideate|issues  --work-ask gate|auto
      outbound --merge-mode auto|human|approve-all|ai-approve-all  --merge-strategy pr|direct

    `--persona <name>` selects the standing agent's identity (default
    `director`; packs ship more via the `po.personas` entry-point group). A
    non-default persona suffixes the deployment + tmux session names so several
    personas can run against one workspace without colliding.

    Examples:
        po director start
        po director start --dir . --channel C08LB4V9ZJ8 --work-source issues --work-ask auto
        po director start --persona ceo
        po director start --merge-mode human
        po director status --persona ceo
        po director stop --persona ceo
    """
    if action not in _ACTIONS:
        raise ValueError("action must be one of " + ", ".join(_ACTIONS) + "; got " + repr(action))
    workspace_dir = str(Path(dir).resolve())

    if action == "start":
        cfg = _ensure_config(
            workspace_dir,
            channel=channel,
            work_source=work_source,
            work_ask=work_ask,
            merge_mode=merge_mode,
            merge_strategy=merge_strategy,
            approval_mode=approval_mode,
            pulse_cron=pulse_cron,
            reflect_cron=reflect_cron,
            north_star=north_star,
            persona=persona,
        )
        return _start(cfg)
    if action == "stop":
        return _stop(load_config(workspace_dir, persona_override=persona))
    if action == "render":
        # Print the persona's rendered prompt to stdout — for external session
        # runners (e.g. orchestra's persistent persona sessions) that want the
        # exact prompt a pulse would use without going through Prefect.
        from po_director.render import persona_prompt

        return persona_prompt(load_config(workspace_dir, persona_override=persona))
    return _status(load_config(workspace_dir, persona_override=persona))
