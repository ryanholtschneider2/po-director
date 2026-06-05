"""Persona engine: EP discovery, per-persona defaults, loud failure, naming.

A persona pack is faked by monkeypatching `persona._persona_entry_points` to
point a name at a tmp directory shipping `prompt.md` (+ optional `config.toml`,
`reflector/prompt.md`). No real pack install needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import po_director.persona as persona
import po_director.render as render
from po_director.config import (
    DEFAULT_PULSE_CRON,
    DEFAULT_WORK_SOURCE,
    DirectorConfig,
    load_config,
)
from po_director.deployments import deployment_names


class _FakeEP:
    """Stand-in for importlib.metadata.EntryPoint."""

    def __init__(self, name: str, persona_dir: Path) -> None:
        self.name = name
        self.value = "fake_pack.personas:get_" + name
        self._dir = persona_dir

    def load(self):
        return lambda: self._dir


def _install_persona(
    monkeypatch,
    name: str,
    persona_dir: Path,
    *,
    prompt: str = "# {{north_star}} persona for {{workspace_dir}}\n",
    config_toml: str | None = None,
    reflector_prompt: str | None = None,
) -> Path:
    """Materialise a fake persona dir and register it as an EP."""
    persona_dir.mkdir(parents=True, exist_ok=True)
    (persona_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    if config_toml is not None:
        (persona_dir / "config.toml").write_text(config_toml, encoding="utf-8")
    if reflector_prompt is not None:
        refl = persona_dir / "reflector"
        refl.mkdir(parents=True, exist_ok=True)
        (refl / "prompt.md").write_text(reflector_prompt, encoding="utf-8")
    monkeypatch.setattr(
        persona, "_persona_entry_points", lambda: {name: _FakeEP(name, persona_dir)}
    )
    return persona_dir


# ─── resolution / discovery ─────────────────────────────────────────────


def test_builtin_director_resolves() -> None:
    d = persona.resolve_persona_dir("director")
    assert d == persona.BUILTIN_AGENTS_DIR / "director"
    assert (d / "prompt.md").is_file()


def test_ep_persona_wins_and_resolves(tmp_path: Path, monkeypatch) -> None:
    pdir = _install_persona(monkeypatch, "ceo", tmp_path / "ceo")
    assert persona.resolve_persona_dir("ceo") == pdir
    assert "ceo" in persona.available_personas()


def test_unknown_persona_fails_loudly_listing_available(monkeypatch) -> None:
    monkeypatch.setattr(persona, "_persona_entry_points", lambda: {})
    with pytest.raises(persona.PersonaError) as ei:
        persona.resolve_persona_dir("nope")
    msg = str(ei.value)
    assert "nope" in msg
    # builtin director is always listed as available
    assert "director" in msg


def test_ep_without_promptmd_raises(tmp_path: Path, monkeypatch) -> None:
    empty = tmp_path / "ceo"
    empty.mkdir()
    monkeypatch.setattr(
        persona, "_persona_entry_points", lambda: {"ceo": _FakeEP("ceo", empty)}
    )
    with pytest.raises(persona.PersonaError, match="no prompt.md"):
        persona.resolve_persona_dir("ceo")


# ─── per-persona defaults + precedence ──────────────────────────────────


def test_persona_defaults_apply_when_workspace_unset(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _install_persona(
        monkeypatch,
        "ceo",
        tmp_path / "ceo",
        config_toml='work_source = "issues"\npulse_cron = "*/5 * * * *"\n',
    )
    cfg = load_config(ws, persona_override="ceo")
    assert cfg.persona == "ceo"
    assert cfg.work_source == "issues"  # from persona config.toml
    assert cfg.pulse_cron == "*/5 * * * *"


def test_workspace_setting_overrides_persona_default(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    # workspace pins work_source=ideate; persona default is issues → workspace wins.
    (ws / ".director.toml").write_text('work_source = "ideate"\n', encoding="utf-8")
    _install_persona(
        monkeypatch,
        "ceo",
        tmp_path / "ceo",
        config_toml='work_source = "issues"\npulse_cron = "*/5 * * * *"\n',
    )
    cfg = load_config(ws, persona_override="ceo")
    assert cfg.work_source == "ideate"  # workspace beats persona default
    assert cfg.pulse_cron == "*/5 * * * *"  # persona default still fills the gap


def test_persona_override_beats_file_persona(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".director.toml").write_text('persona = "director"\n', encoding="utf-8")
    _install_persona(monkeypatch, "ceo", tmp_path / "ceo")
    assert load_config(ws, persona_override="ceo").persona == "ceo"
    # without override, the file persona stands
    assert load_config(ws).persona == "director"


def test_persona_from_ade_settings(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    (ws / ".ade").mkdir(parents=True)
    (ws / ".ade" / "settings.toml").write_text(
        '[persona]\nname = "ceo"\n', encoding="utf-8"
    )
    _install_persona(monkeypatch, "ceo", tmp_path / "ceo")
    assert load_config(ws).persona == "ceo"


def test_default_persona_unchanged_when_nothing_set(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.persona == "director"
    assert cfg.work_source == DEFAULT_WORK_SOURCE
    assert cfg.pulse_cron == DEFAULT_PULSE_CRON


# ─── deployment + session naming ────────────────────────────────────────


def test_deployment_names_byte_identical_for_default(tmp_path: Path) -> None:
    cfg = DirectorConfig(workspace_dir=str(tmp_path))  # persona defaults to director
    pulse, reflect = deployment_names(cfg)
    # No persona component — exactly the legacy shape.
    slug = pulse[len("director-pulse-"):]
    assert pulse == "director-pulse-" + slug
    assert reflect == "director-reflect-" + slug
    assert "ceo" not in pulse


def test_deployment_names_persona_suffixed_and_distinct(tmp_path: Path) -> None:
    default = DirectorConfig(workspace_dir=str(tmp_path))
    ceo = DirectorConfig(workspace_dir=str(tmp_path), persona="ceo")
    d_pulse, _ = deployment_names(default)
    c_pulse, c_reflect = deployment_names(ceo)
    assert c_pulse != d_pulse  # personas don't collide on one workspace
    assert "ceo" in c_pulse and "ceo" in c_reflect
    assert c_pulse.startswith("director-pulse-")


# ─── prompt rendering ───────────────────────────────────────────────────


def test_persona_prompt_default_byte_identical(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "(none)")
    cfg = DirectorConfig(workspace_dir=str(tmp_path), north_star="velocity")
    assert render.persona_prompt(cfg) == render.build_prompt(cfg, "director")


def test_persona_prompt_renders_pack_persona(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "(none)")
    _install_persona(
        monkeypatch,
        "ceo",
        tmp_path / "ceo",
        prompt="# CEO persona — hold {{north_star}}\nwork_ask = {{work_ask}}\n",
    )
    cfg = DirectorConfig(workspace_dir=str(tmp_path), persona="ceo", north_star="MRR")
    out = render.persona_prompt(cfg)
    assert "CEO persona" in out
    assert "MRR" in out
    assert "{{" not in out


def test_reflect_prompt_prefers_persona_reflector(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "(none)")
    _install_persona(
        monkeypatch,
        "ceo",
        tmp_path / "ceo",
        reflector_prompt="# CEO reflection on {{north_star}}\n",
    )
    cfg = DirectorConfig(workspace_dir=str(tmp_path), persona="ceo", north_star="MRR")
    out = render.reflect_prompt(cfg)
    assert "CEO reflection" in out


def test_reflect_prompt_falls_back_to_builtin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "(none)")
    # persona ships no reflector → builtin reflector renders.
    _install_persona(monkeypatch, "ceo", tmp_path / "ceo")
    cfg = DirectorConfig(workspace_dir=str(tmp_path), persona="ceo")
    builtin = render.build_prompt(cfg, "reflector")
    assert render.reflect_prompt(cfg) == builtin


def test_unknown_persona_in_config_fails_loudly(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(persona, "_persona_entry_points", lambda: {})
    save = tmp_path / ".director.toml"
    save.write_text('persona = "ghost"\n', encoding="utf-8")
    with pytest.raises(persona.PersonaError, match="ghost"):
        load_config(tmp_path)
