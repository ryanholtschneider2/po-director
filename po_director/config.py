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

DEFAULT_PULSE_CRON = "*/10 * * * *"
DEFAULT_REFLECT_CRON = "0 13 * * *"  # daily at 13:00 local
DEFAULT_GOAL_PATH = "goal.md"
DEFAULT_NORTH_STAR = "open issues burned down"
DEFAULT_APPROVAL_MODE = "always"

APPROVAL_MODES = ("always", "batches", "consequential")


@dataclass(slots=True)
class DirectorConfig:
    """Resolved Director settings for one workspace."""

    workspace_dir: str
    goal_path: str = DEFAULT_GOAL_PATH
    north_star: str = DEFAULT_NORTH_STAR
    slack_channel: str | None = None
    pulse_cron: str = DEFAULT_PULSE_CRON
    reflect_cron: str = DEFAULT_REFLECT_CRON
    approval_mode: str = DEFAULT_APPROVAL_MODE

    def __post_init__(self) -> None:
        if self.approval_mode not in APPROVAL_MODES:
            raise ValueError(
                "approval_mode must be one of "
                + ", ".join(APPROVAL_MODES)
                + "; got "
                + repr(self.approval_mode)
            )

    @property
    def goal_file(self) -> Path:
        return Path(self.workspace_dir) / self.goal_path

    @property
    def memory_dir(self) -> Path:
        return Path(self.workspace_dir) / ".director"


def config_path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir) / CONFIG_NAME


def load_config(workspace_dir: str | Path) -> DirectorConfig:
    """Load `.director.toml` from a workspace, falling back to defaults.

    Unknown keys in the file are ignored so a newer config can't crash an older
    pack. `workspace_dir` from the file is overridden by the caller's path (the
    file may have been moved/copied).
    """
    path = config_path(workspace_dir)
    data: dict[str, object] = {}
    if path.is_file():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    known = {f for f in DirectorConfig.__dataclass_fields__}  # noqa: C416
    kwargs = {key: val for key, val in data.items() if key in known}
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
