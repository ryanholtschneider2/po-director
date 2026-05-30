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
    # If the agent turn were attempted, build_prompt/_make_session would run;
    # assert they are never touched.
    called = {"prompt": False}
    monkeypatch.setattr(coord, "build_prompt", lambda *a, **k: called.__setitem__("prompt", True) or "x")
    out = coord.director_pulse.fn(_ws(tmp_path), dry_run=True)
    assert out["dry_run"] is True and out["quiet"] is True
    assert called["prompt"] is False


def test_quiet_path_posts_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(coord, "build_prompt", lambda *a, **k: "PROMPT")
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
    monkeypatch.setattr(coord, "build_prompt", lambda *a, **k: "PROMPT")
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
    monkeypatch.setattr(coord, "build_prompt", lambda *a, **k: "PROMPT")
    snapshots = iter([{}, {"director-1": "proposal"}])
    monkeypatch.setattr(coord, "_gate_map", lambda ws: next(snapshots))
    posts: list[tuple] = []
    monkeypatch.setattr(coord, "post_slack",
                        lambda ch, *a, **k: posts.append(ch) or bool(ch))

    # no slack_channel configured -> post_slack returns False -> posted 0
    out = coord.director_pulse.fn(_ws(tmp_path), backend=FakeBackend("x"))
    assert out["new_gates"] == 1
    assert out["posted"] == 0


def test_reflect_posts_when_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(coord, "build_prompt", lambda *a, **k: "PROMPT")
    posts: list[tuple] = []
    monkeypatch.setattr(coord, "post_slack", lambda *a, **k: posts.append(a) or True)
    out = coord.director_reflect.fn(
        _ws(tmp_path, slack_channel="C123"), backend=FakeBackend("today we shipped X")
    )
    assert out["posted"] == 1
    assert len(posts) == 1
