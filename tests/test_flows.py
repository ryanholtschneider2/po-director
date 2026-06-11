"""Phase-3 unit tests: pulse/reflect flow logic with a fake backend + mocked bd.

The approval-gate *decision* lives in the prompt (agent-side) and is exercised
in e2e; here we test the flow's deterministic behavior: dry-run short-circuit,
quiet path posts nothing, and a newly-filed gate posts exactly once.
"""

from __future__ import annotations

from pathlib import Path

import po_director.coordinator as coord
from po_director.config import DirectorConfig, save_config


class FakeBackend:
    """Minimal SessionBackend: returns canned text, never calls Claude."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    def run(self, prompt, *, session_id=None, cwd=None, fork=False,
            model="opus", effort=None, extra_env=None):
        return self._text, session_id or "sid-fake"


def _ws(tmp_path: Path, **kw: object) -> str:
    save_config(DirectorConfig(workspace_dir=str(tmp_path), **kw))  # type: ignore[arg-type]
    return str(tmp_path)


def test_gate_map_parses_json(monkeypatch) -> None:
    class P:
        stdout = '[{"id": "d-1", "title": "Dispatch X?"}, {"id": "d-2", "title": "Y?"}]'

    monkeypatch.setattr(coord.subprocess, "run", lambda *a, **k: P())
    gates = coord._gate_map("/tmp/ws")
    assert gates == {"d-1": "Dispatch X?", "d-2": "Y?"}


def test_gate_map_bad_json_is_empty(monkeypatch) -> None:
    class P:
        stdout = "Error: not json"

    monkeypatch.setattr(coord.subprocess, "run", lambda *a, **k: P())
    assert coord._gate_map("/tmp/ws") == {}


def test_dry_run_short_circuits(tmp_path: Path, monkeypatch) -> None:
    # If the agent turn were attempted, persona_prompt/_make_session would run;
    # assert they are never touched.
    called = {"prompt": False}
    monkeypatch.setattr(coord, "persona_prompt", lambda *a, **k: called.__setitem__("prompt", True) or "x")
    out = coord.director_pulse.fn(_ws(tmp_path), dry_run=True)
    assert out["dry_run"] is True and out["quiet"] is True
    assert called["prompt"] is False


def test_quiet_path_posts_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(coord, "persona_prompt", lambda *a, **k: "PROMPT")
    # same gate present before and after -> nothing new
    monkeypatch.setattr(coord, "_gate_map", lambda ws: {"director-9": "existing gate"})
    posts: list[tuple] = []
    monkeypatch.setattr(coord, "post_slack", lambda *a, **k: posts.append(a) or True)

    out = coord.director_pulse.fn(
        _ws(tmp_path, slack_channel="C123"), backend=FakeBackend("")
    )
    assert out["quiet"] is True
    assert out["new_gates"] == 0
    assert posts == []


def test_new_gate_posts_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(coord, "persona_prompt", lambda *a, **k: "PROMPT")
    # before: empty; after: one new gate (a multi-line title must still count as 1)
    snapshots = iter([{}, {"director-1": "Dispatch the auth fix via software-dev-full?"}])
    monkeypatch.setattr(coord, "_gate_map", lambda ws: next(snapshots))
    posts: list[tuple] = []
    monkeypatch.setattr(coord, "post_slack", lambda *a, **k: posts.append(a) or True)

    out = coord.director_pulse.fn(
        _ws(tmp_path, slack_channel="C123"), backend=FakeBackend("filed a gate")
    )
    assert out["new_gates"] == 1
    assert out["posted"] == 1
    assert len(posts) == 1
    # body carries the gate id + title
    assert "director-1" in posts[0][2] and "auth fix" in posts[0][2]


def test_new_gate_no_channel_no_post(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(coord, "persona_prompt", lambda *a, **k: "PROMPT")
    snapshots = iter([{}, {"director-1": "proposal"}])
    monkeypatch.setattr(coord, "_gate_map", lambda ws: next(snapshots))
    posts: list[tuple] = []
    monkeypatch.setattr(coord, "post_slack",
                        lambda ch, *a, **k: posts.append(ch) or bool(ch))

    # no slack_channel configured -> post_slack returns False -> posted 0
    out = coord.director_pulse.fn(_ws(tmp_path), backend=FakeBackend("x"))
    assert out["new_gates"] == 1
    assert out["posted"] == 0


def test_report_posts_when_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(coord, "report_prompt", lambda *a, **k: "PROMPT")
    posts: list[tuple] = []
    monkeypatch.setattr(coord, "post_slack", lambda *a, **k: posts.append(a) or True)
    out = coord.director_report.fn(
        _ws(tmp_path, slack_channel="C123"), backend=FakeBackend("today I dispatched X")
    )
    assert out["posted"] == 1
    assert len(posts) == 1
    assert posts[0][1] == coord._REPORT_TITLE


def test_report_dry_run_short_circuits(tmp_path: Path, monkeypatch) -> None:
    called = {"prompt": False}
    monkeypatch.setattr(
        coord, "report_prompt", lambda *a, **k: called.__setitem__("prompt", True) or "x"
    )
    out = coord.director_report.fn(_ws(tmp_path), dry_run=True)
    assert out["dry_run"] is True and out["posted"] == 0
    assert called["prompt"] is False


def test_roadmap_dry_run_short_circuits(tmp_path: Path, monkeypatch) -> None:
    called = {"prompt": False}
    monkeypatch.setattr(
        coord, "roadmap_prompt", lambda *a, **k: called.__setitem__("prompt", True) or "x"
    )
    out = coord.director_roadmap.fn(_ws(tmp_path), dry_run=True)
    assert out["dry_run"] is True and out["posted"] == 0 and out["tldr"] is False
    assert called["prompt"] is False


def test_roadmap_posts_tldr_when_agent_writes_it(tmp_path: Path, monkeypatch) -> None:
    # The agent (FakeBackend) "writes" the TL;DR by us creating the file during
    # the prompt render; the flow must read it and post under the Plan-updated title.
    monkeypatch.setattr(coord, "roadmap_prompt", lambda *a, **k: "PROMPT")
    posts: list[tuple] = []
    monkeypatch.setattr(coord, "post_slack", lambda *a, **k: posts.append(a) or True)

    class WritingBackend(FakeBackend):
        def run(self, prompt, **kw):
            mem = Path(tmp_path) / ".director"
            mem.mkdir(parents=True, exist_ok=True)
            (mem / "roadmap-tldr.md").write_text(
                "# Roadmap update\n\n- filed epic X\n", encoding="utf-8"
            )
            return super().run(prompt, **kw)

    out = coord.director_roadmap.fn(
        _ws(tmp_path, slack_channel="C123"), backend=WritingBackend("planned")
    )
    assert out["tldr"] is True
    assert out["posted"] == 1
    assert posts and posts[0][1] == coord._ROADMAP_TITLE
    assert "filed epic X" in posts[0][2]


def test_roadmap_no_tldr_posts_nothing(tmp_path: Path, monkeypatch) -> None:
    # A pass that writes no TL;DR (or only a stale one) posts nothing.
    monkeypatch.setattr(coord, "roadmap_prompt", lambda *a, **k: "PROMPT")
    posts: list[tuple] = []
    monkeypatch.setattr(coord, "post_slack", lambda *a, **k: posts.append(a) or True)
    out = coord.director_roadmap.fn(
        _ws(tmp_path, slack_channel="C123"), backend=FakeBackend("planned, no change")
    )
    assert out["tldr"] is False
    assert out["posted"] == 0
    assert posts == []


def test_dream_dry_run_short_circuits(tmp_path: Path, monkeypatch) -> None:
    called = {"prompt": False}
    monkeypatch.setattr(
        coord, "dream_prompt", lambda *a, **k: called.__setitem__("prompt", True) or "x"
    )
    out = coord.director_dream.fn(_ws(tmp_path), dry_run=True)
    assert out["dry_run"] is True and out["posted"] == 0
    assert called["prompt"] is False


def test_dream_posts_digest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(coord, "dream_prompt", lambda *a, **k: "PROMPT")
    posts: list[tuple] = []
    monkeypatch.setattr(coord, "post_slack", lambda *a, **k: posts.append(a) or True)
    out = coord.director_dream.fn(
        _ws(tmp_path, slack_channel="C123"), backend=FakeBackend("consolidated 2 sessions")
    )
    assert out["posted"] == 1
    assert len(posts) == 1
    assert posts[0][1] == coord._DREAM_TITLE


def test_improve_dry_run_short_circuits(tmp_path: Path, monkeypatch) -> None:
    # Must NOT run the extractor or build the prompt on a dry run.
    touched = {"dump": False, "prompt": False}
    monkeypatch.setattr(
        coord, "dump_operator_turns",
        lambda *a, **k: touched.__setitem__("dump", True) or {},
    )
    monkeypatch.setattr(
        coord, "improve_prompt",
        lambda *a, **k: touched.__setitem__("prompt", True) or "x",
    )
    out = coord.director_improve.fn(_ws(tmp_path), dry_run=True)
    assert out["dry_run"] is True and out["posted"] == 0
    assert touched == {"dump": False, "prompt": False}


def test_improve_mines_then_posts(tmp_path: Path, monkeypatch) -> None:
    summary = {"sessions": 3, "total_turns": 90, "by_bucket": {}, "out_dir": "x", "top": []}
    monkeypatch.setattr(coord, "dump_operator_turns", lambda *a, **k: summary)
    monkeypatch.setattr(coord, "improve_prompt", lambda *a, **k: "PROMPT")
    posts: list[tuple] = []
    monkeypatch.setattr(coord, "post_slack", lambda *a, **k: posts.append(a) or True)
    out = coord.director_improve.fn(
        _ws(tmp_path, slack_channel="C123"), backend=FakeBackend("filed 4, dispatched 1")
    )
    assert out["posted"] == 1 and out["sessions"] == 3
    assert posts and posts[0][1] == coord._IMPROVE_TITLE


def test_gate_map_handles_null(monkeypatch) -> None:
    # bd v1.0.4 emits `null` (not []) when there are no human gates.
    class P:
        stdout = "null"
    monkeypatch.setattr(coord.subprocess, "run", lambda *a, **k: P())
    assert coord._gate_map("/tmp/ws") == {}


def test_build_backend_passes_issue_role_to_tmux_factory() -> None:
    # Regression for po-director-3f5: the auto-selected tmux backend requires
    # (issue, role); _build_backend must supply them rather than calling the
    # factory with zero args.
    captured: dict[str, object] = {}

    class TmuxLike:
        def __init__(self, *, issue: str, role: str) -> None:
            captured["issue"] = issue
            captured["role"] = role

    be = coord._build_backend(TmuxLike, "feat-1", "pr-sheriff")
    assert isinstance(be, TmuxLike)
    assert captured == {"issue": "feat-1", "role": "pr-sheriff"}


def test_build_backend_falls_back_to_zero_arg_factory() -> None:
    # Stateless backends (ClaudeCliBackend / StubBackend) take no args;
    # _build_backend must fall through to zero-arg construction.
    class StatelessLike:
        def __init__(self) -> None:
            self.made = True

    be = coord._build_backend(StatelessLike, "feat-1", "persona")
    assert isinstance(be, StatelessLike)
    assert be.made is True


def test_make_session_builds_default_backend_when_none(tmp_path: Path, monkeypatch) -> None:
    # When no backend is injected, _make_session must construct the
    # auto-selected factory without crashing on the (issue, role) signature.
    seen: dict[str, object] = {}

    class TmuxLike:
        def __init__(self, *, issue: str, role: str) -> None:
            seen["issue"] = issue
            seen["role"] = role

    monkeypatch.setattr(coord, "select_default_backend", lambda: TmuxLike)
    sess = coord._make_session(_make_cfg(tmp_path), "persona", None, issue="feat-9")
    assert isinstance(sess.backend, TmuxLike)
    assert seen == {"issue": "feat-9", "role": "persona"}


def _make_cfg(tmp_path: Path) -> DirectorConfig:
    return DirectorConfig(workspace_dir=str(tmp_path))
