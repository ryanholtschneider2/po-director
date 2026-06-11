"""Per-workspace Director configuration (`<workspace>/.director.toml`).

The Director has no rig concept — it watches the directory it was started in.
`po director start` captures that directory here and stamps it into the cron
deployments so a scheduled run (which has no interactive CWD) still knows which
workspace it owns.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from po_director.persona import DEFAULT_PERSONA, load_persona_defaults

CONFIG_NAME = ".director.toml"
ADE_CONFIG_NAME = ".ade/settings.toml"  # consolidated, agent-writable

DEFAULT_PULSE_CRON = "*/20 * * * *"  # every 20 min — 10 min was too aggressive; override per-workspace in .director.toml
DEFAULT_ROADMAP_CRON = "0 * * * *"  # hourly — the planning pass that maintains ROADMAP.md + decomposes into beads
DEFAULT_REPORT_CRON = "0 21 * * *"  # nightly at 21:00 local — end-of-day report
DEFAULT_DREAM_CRON = "0 4 * * *"  # daily at 04:00 local — off-peak consolidation
DEFAULT_IMPROVE_CRON = "0 5 * * *"  # nightly at 05:00 local — autonomy ratchet
DEFAULT_GOAL_PATH = "goal.md"
DEFAULT_NORTH_STAR = "open issues burned down"

# Involvement — two independent axes (see ade-merge-refinery-plan.md).
# INBOUND (what the director takes on): work_source x work_ask, a 2x2.
DEFAULT_WORK_SOURCE = "ideate"  # ideate (from goal) | issues (existing backlog only)
DEFAULT_WORK_ASK = "gate"  # gate (propose, wait) | auto (dispatch autonomously)
WORK_SOURCES = ("ideate", "issues")
WORK_ASKS = ("gate", "auto")
# OUTBOUND (how a green PR reaches main): the PR Sheriff.
DEFAULT_MERGE_MODE = "auto"  # auto | human | approve-all | ai-approve-all
MERGE_MODES = ("auto", "human", "approve-all", "ai-approve-all")
DEFAULT_MERGE_STRATEGY = "pr"  # pr (push + open PR) | direct (merge to main)
MERGE_STRATEGIES = ("pr", "direct")

# Legacy single-axis approval_mode (pre-2x2). All three values were "ask"
# variants, so they migrate to work_ask="gate".
DEFAULT_APPROVAL_MODE = "always"
APPROVAL_MODES = ("always", "batches", "consequential")
_LEGACY_APPROVAL_TO_ASK = {"always": "gate", "batches": "gate", "consequential": "gate"}


@dataclass(slots=True)
class DirectorConfig:
    """Resolved Director settings for one workspace."""

    workspace_dir: str
    goal_path: str = DEFAULT_GOAL_PATH
    north_star: str = DEFAULT_NORTH_STAR
    slack_channel: str | None = None
    pulse_cron: str = DEFAULT_PULSE_CRON
    roadmap_cron: str = DEFAULT_ROADMAP_CRON
    report_cron: str = DEFAULT_REPORT_CRON
    dream_cron: str = DEFAULT_DREAM_CRON
    improve_cron: str = DEFAULT_IMPROVE_CRON
    # Involvement axes.
    work_source: str = DEFAULT_WORK_SOURCE
    work_ask: str = DEFAULT_WORK_ASK
    merge_mode: str = DEFAULT_MERGE_MODE
    merge_strategy: str = DEFAULT_MERGE_STRATEGY
    ci_cmd: str | None = None  # repo CI command; agents may detect + write this
    # The standing agent's identity. 'director' is the builtin; packs ship more
    # via the `po.personas` entry-point group (see persona.py).
    persona: str = DEFAULT_PERSONA
    # Named rigs this director manages — arbitrary workspaces it dispatches work
    # into (code1, code2, marketing, gtm, …), NOT just code. Each entry is
    # {name, path, code}: `path` is relative to workspace_dir (or absolute);
    # `code` is a bool — only code rigs produce PRs and get a standing PR-Sheriff.
    # Authored as `[[rigs]]` in .ade/settings.toml; not round-tripped through the
    # flat .director.toml. See `resolved_rigs`.
    rigs: list[dict[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._check("work_source", self.work_source, WORK_SOURCES)
        self._check("work_ask", self.work_ask, WORK_ASKS)
        self._check("merge_mode", self.merge_mode, MERGE_MODES)
        self._check("merge_strategy", self.merge_strategy, MERGE_STRATEGIES)

    def resolved_rigs(self) -> list[dict[str, object]]:
        """The configured rigs with `path` resolved to an absolute path.

        A rig `path` is taken relative to `workspace_dir` unless already
        absolute. Entries missing a `name` or `path` are skipped; `code`
        defaults to False. Returns `{name, path (abs), code}` dicts.
        """
        base = Path(self.workspace_dir)
        out: list[dict[str, object]] = []
        for rig in self.rigs:
            if not isinstance(rig, dict):
                continue
            name, path = rig.get("name"), rig.get("path")
            if not isinstance(name, str) or not isinstance(path, str):
                continue
            abs_path = path if Path(path).is_absolute() else str((base / path).resolve())
            out.append({"name": name, "path": abs_path, "code": bool(rig.get("code", False))})
        return out

    def code_rigs(self) -> list[dict[str, object]]:
        """Resolved rigs flagged `code = true` (the ones that get a PR-Sheriff)."""
        return [r for r in self.resolved_rigs() if r["code"]]

    @staticmethod
    def _check(name: str, val: str, allowed: tuple[str, ...]) -> None:
        if val not in allowed:
            raise ValueError(
                name + " must be one of " + ", ".join(allowed) + "; got " + repr(val)
            )

    @property
    def goal_file(self) -> Path:
        return Path(self.workspace_dir) / self.goal_path

    @property
    def memory_dir(self) -> Path:
        return Path(self.workspace_dir) / ".director"


def config_path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir) / CONFIG_NAME


def ade_config_path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir) / ADE_CONFIG_NAME


def _flatten_ade(data: dict[str, object]) -> dict[str, object]:
    """Map `.ade/settings.toml` nested tables onto DirectorConfig kwargs.

    [persona].name, [goals].{north_star, goal_path},
    [involvement].{work_source,work_ask,merge_mode},
    [merge].{strategy->merge_strategy, ci_cmd},
    [notify].slack_channel, [schedule].{pulse_cron, roadmap_cron, report_cron,
    dream_cron, improve_cron} (legacy [schedule].reflect_cron migrates to report_cron).

    Together these let a corp dir express the whole minimal contract — persona,
    north_star, slack_channel, and optional cron overrides — in one short file
    (see the README "Standing up a workspace" section). Unknown tables/keys are
    left alone; load_config filters to known DirectorConfig fields anyway.
    """
    out: dict[str, object] = {}
    persona = data.get("persona")
    if isinstance(persona, dict) and "name" in persona:
        out["persona"] = persona["name"]
    goals = data.get("goals")
    if isinstance(goals, dict):
        for key in ("north_star", "goal_path"):
            if key in goals:
                out[key] = goals[key]
    inv = data.get("involvement")
    if isinstance(inv, dict):
        for key in ("work_source", "work_ask", "merge_mode"):
            if key in inv:
                out[key] = inv[key]
    merge = data.get("merge")
    if isinstance(merge, dict):
        if "strategy" in merge:
            out["merge_strategy"] = merge["strategy"]
        if "ci_cmd" in merge:
            out["ci_cmd"] = merge["ci_cmd"]
    notify = data.get("notify")
    if isinstance(notify, dict) and "slack_channel" in notify:
        out["slack_channel"] = notify["slack_channel"]
    schedule = data.get("schedule")
    if isinstance(schedule, dict):
        for key in ("pulse_cron", "roadmap_cron", "report_cron", "dream_cron", "improve_cron"):
            if key in schedule:
                out[key] = schedule[key]
        # Legacy: reflect_cron was renamed to report_cron. Migrate when the new
        # key isn't already set.
        if "reflect_cron" in schedule and "report_cron" not in schedule:
            out["report_cron"] = schedule["reflect_cron"]
    # `[[rigs]]` — named workspaces the director manages (name/path/code).
    rigs = data.get("rigs")
    if isinstance(rigs, list):
        out["rigs"] = [
            {"name": r["name"], "path": r["path"], "code": bool(r.get("code", False))}
            for r in rigs
            if isinstance(r, dict) and isinstance(r.get("name"), str) and isinstance(r.get("path"), str)
        ]
    return out


def load_config(
    workspace_dir: str | Path, *, persona_override: str | None = None
) -> DirectorConfig:
    """Resolve workspace config from `.ade/settings.toml` (preferred) layered
    over legacy `.director.toml`, with per-persona defaults underneath.

    Precedence (highest wins): `persona_override` (a CLI `--persona` flag) for
    the persona field; then for every other knob — CLI flags (applied by the
    caller after this returns) > workspace files (`.ade/settings.toml` over
    `.director.toml`) > the persona's own `config.toml` defaults > dataclass
    defaults. A legacy `approval_mode` migrates to `work_ask`. Unknown keys
    are ignored so a newer config can't crash an older pack. `workspace_dir`
    from a file is overridden by the caller's path.

    An unknown persona (from a file or `persona_override`) fails loudly via
    `persona.PersonaError`, listing the available personas.
    """
    known = set(DirectorConfig.__dataclass_fields__)
    kwargs: dict[str, object] = {}

    # Legacy flat .director.toml first (lowest precedence among files).
    legacy = config_path(workspace_dir)
    if legacy.is_file():
        with legacy.open("rb") as fh:
            data = tomllib.load(fh)
        for key, val in data.items():
            if key in known:
                kwargs[key] = val
        approval = data.get("approval_mode")
        if isinstance(approval, str) and "work_ask" not in data:
            kwargs["work_ask"] = _LEGACY_APPROVAL_TO_ASK.get(approval, DEFAULT_WORK_ASK)
        # Legacy: reflect_cron was renamed to report_cron. Preserve the
        # operator's configured value when the new key isn't present.
        if "reflect_cron" in data and "report_cron" not in data:
            kwargs["report_cron"] = data["reflect_cron"]

    # .ade/settings.toml overrides legacy.
    ade = ade_config_path(workspace_dir)
    if ade.is_file():
        with ade.open("rb") as fh:
            ade_data = tomllib.load(fh)
        for key, val in _flatten_ade(ade_data).items():
            if key in known:
                kwargs[key] = val

    # Resolve the effective persona (CLI override > files > default), then layer
    # its config.toml defaults *under* the workspace settings already gathered.
    persona = persona_override or kwargs.get("persona") or DEFAULT_PERSONA
    kwargs["persona"] = persona
    for key, val in load_persona_defaults(str(persona)).items():
        kwargs.setdefault(key, val)  # workspace files win over persona defaults

    kwargs.pop("workspace_dir", None)
    kwargs["workspace_dir"] = str(Path(workspace_dir).resolve())
    return DirectorConfig(**kwargs)  # type: ignore[arg-type]


def _toml_value(val: object) -> str:
    if val is None:
        # TOML has no null; we represent "unset" by omitting the key, so this
        # is only reached for explicit empty strings.
        return '""'
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    return '"' + str(val).replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_config(cfg: DirectorConfig) -> Path:
    """Write `<workspace>/.director.toml`. Omits `slack_channel` when None."""
    path = config_path(cfg.workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# po-director workspace config — see `po director status`", ""]
    for key, val in asdict(cfg).items():
        # `rigs` is a list of tables authored as `[[rigs]]` in .ade/settings.toml;
        # it isn't round-tripped through the flat .director.toml.
        if key == "rigs":
            continue
        if val is None:
            lines.append("# " + key + " = (unset)")
            continue
        lines.append(key + " = " + _toml_value(val))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
