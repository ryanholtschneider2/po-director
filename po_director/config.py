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

from po_director.persona import DEFAULT_PERSONA, load_persona_defaults

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

# Conditional gate thresholds (see README "Conditional gates"). Each gate —
# ENTRY (idea -> dispatch) and EXIT (merge) — may auto-pass below a per-project
# threshold. The unifying primitive is the *change class*: ENTRY fires before
# any code exists, so a diff-size threshold is meaningless there; a class is the
# only signal available at both gate times. `*_auto_pass` is an opt-in allowlist
# of classes that skip the human gate; the default empty allowlist means every
# gate fires (identical to pre-threshold behavior — this is the whole safety
# story for projects that don't opt in).
CHANGE_CLASSES = ("lint", "format", "docs", "chore", "test", "refactor", "fix", "feature")
# Classes that are NEVER eligible for auto-pass, stripped from any allowlist.
# Mirrors the prompt's standing "always gate regardless" list.
NEVER_AUTO_PASS = ("schema", "migration", "public-api", "force-push", "deploy", "spend")
DEFAULT_ENTRY_AUTO_PASS: tuple[str, ...] = ()
DEFAULT_EXIT_AUTO_PASS: tuple[str, ...] = ()
DEFAULT_EXIT_MAX_DIFF_LINES = 0  # 0 = no size cap; class allowlist + CI green only


def normalize_classes(value: object) -> tuple[str, ...]:
    """Coerce an auto-pass setting into a clean tuple of known change classes.

    Accepts a comma-separated string (``"lint, docs"``) or a list/tuple. Lower-
    cases and strips each entry, keeps only members of `CHANGE_CLASSES`, drops
    anything in `NEVER_AUTO_PASS`, and de-dupes preserving first-seen order.
    Unknown tokens are silently dropped so a typo can never *widen* a gate — it
    only ever fails closed (keeps gating). Returns ``()`` for None/empty.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw = [str(v) for v in value]
    else:
        return ()
    out: list[str] = []
    for token in raw:
        cls = token.strip().lower()
        if cls and cls in CHANGE_CLASSES and cls not in NEVER_AUTO_PASS and cls not in out:
            out.append(cls)
    return tuple(out)


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
    # Conditional gate thresholds — opt-in allowlists of change classes that may
    # skip the human gate. Empty (default) = every gate fires (no behavior
    # change). See `gates.py` for the decision semantics.
    entry_auto_pass: tuple[str, ...] = DEFAULT_ENTRY_AUTO_PASS
    exit_auto_pass: tuple[str, ...] = DEFAULT_EXIT_AUTO_PASS
    exit_max_diff_lines: int = DEFAULT_EXIT_MAX_DIFF_LINES  # 0 = no size cap
    # The standing agent's identity. 'director' is the builtin; packs ship more
    # via the `po.personas` entry-point group (see persona.py).
    persona: str = DEFAULT_PERSONA

    def __post_init__(self) -> None:
        self._check("work_source", self.work_source, WORK_SOURCES)
        self._check("work_ask", self.work_ask, WORK_ASKS)
        self._check("merge_mode", self.merge_mode, MERGE_MODES)
        self._check("merge_strategy", self.merge_strategy, MERGE_STRATEGIES)
        # Normalize the auto-pass allowlists (string/list -> clean class tuple)
        # and clamp the size cap so a hand-edited config can't poison a gate.
        self.entry_auto_pass = normalize_classes(self.entry_auto_pass)
        self.exit_auto_pass = normalize_classes(self.exit_auto_pass)
        self.exit_max_diff_lines = max(0, int(self.exit_max_diff_lines or 0))

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
    [gates.entry].auto_pass->entry_auto_pass,
    [gates.exit].{auto_pass->exit_auto_pass, max_diff_lines->exit_max_diff_lines},
    [notify].slack_channel, [schedule].{pulse_cron, reflect_cron}.

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
    gates = data.get("gates")
    if isinstance(gates, dict):
        entry = gates.get("entry")
        if isinstance(entry, dict) and "auto_pass" in entry:
            out["entry_auto_pass"] = entry["auto_pass"]
        exit_ = gates.get("exit")
        if isinstance(exit_, dict):
            if "auto_pass" in exit_:
                out["exit_auto_pass"] = exit_["auto_pass"]
            if "max_diff_lines" in exit_:
                out["exit_max_diff_lines"] = exit_["max_diff_lines"]
    notify = data.get("notify")
    if isinstance(notify, dict) and "slack_channel" in notify:
        out["slack_channel"] = notify["slack_channel"]
    schedule = data.get("schedule")
    if isinstance(schedule, dict):
        for key in ("pulse_cron", "reflect_cron"):
            if key in schedule:
                out[key] = schedule[key]
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
    if isinstance(val, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in val) + "]"
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
