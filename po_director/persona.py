"""Persona discovery: resolve a persona name to its prompt/config directory.

A *persona* is the standing agent's identity for one workspace — by default
`director`, but any installed pack may ship its own (e.g. `ceo`, `pm`). A
persona is a directory containing `prompt.md` (the pulse persona prompt) and
optionally `config.toml` (per-persona defaults) and a `reflector/prompt.md`.

Resolution order (mirrors po core's last-write-wins entry-point convention,
but here EP-shipped personas take precedence so a pack can override a builtin):

1. The `po.personas` entry-point group. Each entry resolves to a zero-arg
   callable returning the absolute `Path` to the persona directory:

       [project.entry-points."po.personas"]
       ceo = "my_pack.personas:get_persona_dir"

2. po_director's builtin `agents/<name>/` directory.

Unknown persona fails loudly via `PersonaError`, listing what *is* available.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import entry_points
from pathlib import Path

BUILTIN_AGENTS_DIR = Path(__file__).parent / "agents"
PERSONA_EP_GROUP = "po.personas"
DEFAULT_PERSONA = "director"

# Config-toml keys a persona may ship as per-persona defaults. These mirror the
# DirectorConfig knobs that make sense to vary by persona; workspace settings
# and CLI flags override them (see config.load_config).
PERSONA_DEFAULT_KEYS = (
    "work_source",
    "work_ask",
    "pulse_cron",
    "reflect_cron",
    "merge_mode",
    "merge_strategy",
)


class PersonaError(ValueError):
    """Raised when a persona name can't be resolved or is malformed."""


def _persona_entry_points() -> dict[str, object]:
    """Map `po.personas` entry-point name -> EntryPoint. Empty if none installed."""
    return {ep.name: ep for ep in entry_points(group=PERSONA_EP_GROUP)}


def _builtin_personas() -> list[str]:
    """Builtin persona names — any `agents/<name>/` dir shipping a `prompt.md`."""
    if not BUILTIN_AGENTS_DIR.is_dir():
        return []
    return sorted(
        p.name for p in BUILTIN_AGENTS_DIR.iterdir() if (p / "prompt.md").is_file()
    )


def available_personas() -> list[str]:
    """All resolvable persona names: EP-registered first, then builtins."""
    eps = list(_persona_entry_points())
    builtin = [name for name in _builtin_personas() if name not in eps]
    return sorted(eps) + builtin


def resolve_persona_dir(name: str) -> Path:
    """Return the directory holding `<persona>/prompt.md`.

    EP-registered personas win over builtins. Raises `PersonaError` (listing
    available personas) when `name` resolves to nothing, or when an EP entry
    returns a directory without a `prompt.md`.
    """
    eps = _persona_entry_points()
    ep = eps.get(name)
    if ep is not None:
        get_dir = ep.load()
        persona_dir = Path(get_dir()).resolve()
        if not (persona_dir / "prompt.md").is_file():
            raise PersonaError(
                "persona " + repr(name) + " (from entry point " + ep.value + ") "
                "returned " + str(persona_dir) + " which has no prompt.md"
            )
        return persona_dir

    builtin = BUILTIN_AGENTS_DIR / name
    if (builtin / "prompt.md").is_file():
        return builtin

    raise PersonaError(
        "unknown persona " + repr(name) + "; available personas: "
        + (", ".join(available_personas()) or "(none)")
    )


def load_persona_defaults(name: str) -> dict[str, str]:
    """Read the persona's `config.toml`, returning only the recognised keys.

    Missing file -> empty dict. Unknown / non-string keys are ignored so a
    newer persona config can't crash an older po-director.
    """
    persona_dir = resolve_persona_dir(name)
    cfg_path = persona_dir / "config.toml"
    if not cfg_path.is_file():
        return {}
    with cfg_path.open("rb") as fh:
        data = tomllib.load(fh)
    return {
        key: data[key]
        for key in PERSONA_DEFAULT_KEYS
        if isinstance(data.get(key), str)
    }


__all__ = [
    "DEFAULT_PERSONA",
    "PERSONA_DEFAULT_KEYS",
    "PERSONA_EP_GROUP",
    "PersonaError",
    "available_personas",
    "load_persona_defaults",
    "resolve_persona_dir",
]
