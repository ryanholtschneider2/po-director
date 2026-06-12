"""Phase-2 unit tests: state gather (bd/po mocked) + Slack notify (HTTP mocked)."""

from __future__ import annotations

from pathlib import Path

import po_director.notify as notify
import po_director.render as render
from po_director.config import DirectorConfig


def _cfg(tmp_path: Path, **kw: object) -> DirectorConfig:
    return DirectorConfig(workspace_dir=str(tmp_path), **kw)  # type: ignore[arg-type]


def test_project_transcripts_dir_slugs_path() -> None:
    d = render.project_transcripts_dir("/home/u/Desktop/Code/personal/HoltschneiderLLC")
    assert d.name == "-home-u-Desktop-Code-personal-HoltschneiderLLC"
    assert d.parent.name == "projects"


def test_recent_transcripts_lists_fresh_only(tmp_path: Path, monkeypatch) -> None:
    import os
    import time

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fresh.jsonl").write_text("{}\n", encoding="utf-8")
    stale = proj / "stale.jsonl"
    stale.write_text("{}\n", encoding="utf-8")
    old = time.time() - 100 * 3600  # well outside the window
    os.utime(stale, (old, old))
    monkeypatch.setattr(render, "project_transcripts_dir", lambda ws: proj)
    block = render.recent_transcripts(_cfg(tmp_path), within_hours=24)
    assert "fresh.jsonl" in block
    assert "stale.jsonl" not in block


def test_dream_prompt_injects_transcripts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "(none)")
    monkeypatch.setattr(render, "recent_transcripts", lambda cfg, **k: "TRANSCRIPT-BLOCK")
    out = render.dream_prompt(_cfg(tmp_path))
    assert "TRANSCRIPT-BLOCK" in out
    assert "STATE.md" in out


def test_build_board_uses_command_output(tmp_path: Path, monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: str) -> str:
        seen.append(cmd)
        return "OUT:" + cmd[1]

    monkeypatch.setattr(render, "_run", fake_run)
    board = render.build_board(_cfg(tmp_path))
    assert "### ready" in board and "OUT:ready" in board
    assert ["bd", "list", "--label", "human", "--status", "open"] in seen
    assert ["po", "status"] in seen
    # No fresh roadmap-tldr.md → no Plan update section.
    assert "Plan update" not in board


def test_build_board_includes_fresh_plan_update(tmp_path: Path, monkeypatch) -> None:
    mem = tmp_path / ".director"
    mem.mkdir()
    (mem / "roadmap-tldr.md").write_text(
        "# Roadmap update\n\n- filed epic FOO\n", encoding="utf-8"
    )
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "(none)")
    board = render.build_board(_cfg(tmp_path))
    assert "### Plan update" in board
    assert "filed epic FOO" in board


def test_build_board_injects_roadmap_body(tmp_path: Path, monkeypatch) -> None:
    """The settled ROADMAP.md body is injected into the board every pulse so a
    persona steers by the plan in-context."""
    (tmp_path / "ROADMAP.md").write_text(
        "# Roadmap: Acme\n\n## North Star\nMRR\n\n## Roadmap\n| R1 | Landing | now | x |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "(none)")
    board = render.build_board(_cfg(tmp_path))
    assert "### Roadmap (current)" in board
    assert "Landing" in board and "North Star" in board


def test_build_board_roadmap_absent_placeholder(tmp_path: Path, monkeypatch) -> None:
    """No ROADMAP.md is legitimate (pre-first-pulse) — placeholder, never raises."""
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "(none)")
    board = render.build_board(_cfg(tmp_path))
    assert "### Roadmap (current)" in board
    assert "no ROADMAP.md yet" in board


def test_roadmap_section_truncates_on_line_boundary(tmp_path: Path) -> None:
    long_body = "# Roadmap\n" + "".join(f"| R{i} | item {i} | now | y |\n" for i in range(200))
    (tmp_path / "ROADMAP.md").write_text(long_body, encoding="utf-8")
    out = render._roadmap_section(_cfg(tmp_path), max_chars=200)
    assert out.endswith("see ROADMAP.md)")
    visible = out[: out.index("\n\n… (truncated")]
    stripped = long_body.strip()
    assert stripped.startswith(visible)
    assert stripped[len(visible)] == "\n"  # cut landed on a line boundary


def test_build_board_skips_stale_plan_update(tmp_path: Path, monkeypatch) -> None:
    import os
    import time

    mem = tmp_path / ".director"
    mem.mkdir()
    tldr = mem / "roadmap-tldr.md"
    tldr.write_text("- old plan\n", encoding="utf-8")
    old = time.time() - 5 * 3600  # older than the 2h freshness window
    os.utime(tldr, (old, old))
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "(none)")
    board = render.build_board(_cfg(tmp_path))
    assert "Plan update" not in board


def test_roadmap_prompt_renders_with_persona(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "goal.md").write_text("Win.", encoding="utf-8")
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "(none)")
    out = render.roadmap_prompt(_cfg(tmp_path, north_star="velocity"))
    assert "ROADMAP.md" in out
    assert "roadmap-tldr.md" in out
    assert "{{" not in out


def test_build_prompt_renders_with_state(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "goal.md").write_text("Win.", encoding="utf-8")
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "(none)")
    out = render.build_prompt(_cfg(tmp_path, north_star="velocity"), "director")
    assert "Win." in out
    assert "velocity" in out
    assert "{{" not in out


def test_latest_handoff_picks_newest(tmp_path: Path, monkeypatch) -> None:
    mem = tmp_path / ".director"
    mem.mkdir()
    (mem / "handoff-2026-05-01.md").write_text("old", encoding="utf-8")
    newest = mem / "handoff-2026-05-29.md"
    newest.write_text("NEWEST NOTE", encoding="utf-8")
    # make newest actually newer by mtime
    import os
    import time

    os.utime(newest, (time.time() + 10, time.time() + 10))
    monkeypatch.setattr(render, "_run", lambda cmd, cwd: "")
    out = render.build_prompt(_cfg(tmp_path), "director")
    assert "NEWEST NOTE" in out


def test_notify_noop_without_channel(tmp_path: Path) -> None:
    assert notify.post_slack(None, "t", "b", token="xoxb-fake") is False


def test_notify_noop_without_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    assert notify.post_slack("C123", "t", "b") is False


def test_notify_posts_when_configured(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["auth"] = req.headers.get("Authorization")
        return FakeResp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    ok = notify.post_slack("C123", "Proposal", "do the thing", token="xoxb-fake")
    assert ok is True
    assert b"C123" in captured["body"]  # type: ignore[operator]
    assert captured["auth"] == "Bearer xoxb-fake"
