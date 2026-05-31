"""Unit tests for the PR Sheriff flow's pure parts (no Prefect server / Claude).

The agent turn + git/merge are exercised manually (see ade-merge-refinery-plan).
Here: verdict-file location + parsing, and the dry-run short-circuit.
"""

from __future__ import annotations

import json
from pathlib import Path

from po_director.sheriff import _read_verdict, _verdict_path, pr_sheriff


def test_verdict_path(tmp_path: Path) -> None:
    p = _verdict_path(str(tmp_path), "ws-42")
    assert p == tmp_path / ".ade" / "sheriff" / "ws-42.json"


def test_read_verdict_missing(tmp_path: Path) -> None:
    assert _read_verdict(str(tmp_path), "ws-42") == {}


def test_read_verdict_ok(tmp_path: Path) -> None:
    p = _verdict_path(str(tmp_path), "ws-42")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"feature_id": "ws-42", "verdict": "merge", "reason": "green"}))
    doc = _read_verdict(str(tmp_path), "ws-42")
    assert doc["verdict"] == "merge"
    assert doc["reason"] == "green"


def test_read_verdict_garbage(tmp_path: Path) -> None:
    p = _verdict_path(str(tmp_path), "ws-42")
    p.parent.mkdir(parents=True)
    p.write_text("not json{")
    assert _read_verdict(str(tmp_path), "ws-42") == {}


def test_dry_run_short_circuits(tmp_path: Path) -> None:
    # No agent turn, no verdict required.
    out = pr_sheriff(str(tmp_path), "ws-42", dry_run=True)
    assert out["dry_run"] is True
    assert out["feature_id"] == "ws-42"
    assert out["verdict"] is None
