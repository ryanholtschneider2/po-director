"""Per-workspace Director configuration (`<workspace>/.director.toml`).

The Director has no rig concept — it watches the directory it was started in.
`po director start` captures that directory here and stamps it into the cron
deployments so a scheduled run (which has no interactive CWD) still knows which
workspace it owns.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_NAME = ".director.toml"
ADE_CONFIG_NAME = ".ade/settings.toml"  # consolidated, agent-writable

DEFAULT_PULSE_CRON = "*/10 * * * *"
DEFAULT_REFLECT_CRON = "0 13 * * *"  # daily at 13:00 local
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
    reflect_cron: str = DEFAULT_REFLECT_CRON
    # Involvement axes.
    work_source: str = DEFAULT_WORK_SOURCE
    work_ask: str = DEFAULT_WORK_ASK
    merge_mode: str = DEFAULT_MERGE_MODE
    merge_strategy: str = DEFAULT_MERGE_STRATEGY
    ci_cmd: str | None = None  # repo CI command; agents may detect + write this

    def __post_init__(self) -> None:
        self._check("work_source", self.work_source, WORK_SOURCES)
        self._check("work_ask", self.work_ask, WORK_ASKS)
        self._check("merge_mode", self.merge_mode, MERGE_MODES)
        self._check("merge_strategy", self.merge_strategy, MERGE_STRATEGIES)

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

    [goals].north_star, [involvement].{work_source,work_ask,merge_mode},
    [merge].{strategy->merge_strategy, ci_cmd}.
    """
    out: dict[str, object] = {}
    goals = data.get("goals")
    if isinstance(goals, dict) and "north_star" in goals:
        out["north_star"] = goals["north_star"]
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
    return out


def load_config(workspace_dir: str | Path) -> DirectorConfig:
    """Resolve workspace config from `.ade/settings.toml` (preferred) layered
    over legacy `.director.toml`.

    Precedence: `.ade/settings.toml` (nested tables) wins; `.director.toml`
    (flat, legacy) fills gaps. A legacy `approval_mode` migrates to `work_ask`.
    Unknown keys are ignored so a newer config can't crash an older pack.
    `workspace_dir` from a file is overridden by the caller's path.
    """
    known = set(DirectorConfig.__dataclass_fields__)
    kwargs: dict[str, object] = {}

    # Legacy flat .director.toml first (lowest precedence).
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

    # .ade/settings.toml overrides legacy.
    ade = ade_config_path(workspace_dir)
    if ade.is_file():
        with ade.open("rb") as fh:
            ade_data = tomllib.load(fh)
        for key, val in _flatten_ade(ade_data).items():
            if key in known:
                kwargs[key] = val

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
        if val is None:
            lines.append("# " + key + " = (unset)")
            continue
        lines.append(key + " = " + _toml_value(val))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
