"""Transport for the `director-improve` flow: gather the operator's human turns.

The improve flow mines past session transcripts for where the operator had to
step INTO the loop (corrections, nudges, setup, taste) and turns them into
system fixes. This module is the transport half — it locates the relevant
`~/.claude/projects/<slug>/*.jsonl` transcripts and dumps the operator-typed
turns to a run dir. ALL judgment (classifying interventions, ranking fixes,
deciding what to dispatch) is the agent's, done from these dumps.

Mirrors the standalone `loop-audit` skill's extractor so the scheduled flow and
the on-demand skill share one method.
"""

from __future__ import annotations

import glob
import json
import os
import time
from collections import Counter
from pathlib import Path

PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def match_terms_for(workspace_dir: str) -> list[str]:
    """Best-effort corpus match terms for a workspace.

    The workspace basename, every immediate child under `businesses/` (so a
    holdco workspace mines all its businesses), and `director` (to pull in
    standing-director sessions that live under their own orchestra slugs).
    """
    ws = Path(workspace_dir)
    terms = {ws.name, "director"}
    biz = ws / "businesses"
    if biz.is_dir():
        terms.update(p.name for p in biz.iterdir() if p.is_dir())
    return sorted(t for t in terms if t)


def _human_text(msg: dict) -> str | None:
    if msg.get("type") != "user":
        return None
    m = msg.get("message", {})
    if not isinstance(m, dict) or m.get("role") != "user":
        return None
    content = m.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
    text = "\n".join(parts).strip()
    if not text:
        return None
    if text.startswith(("<command-", "<local-command", "Caveat:")):
        return None
    if "<system-reminder>" in text and len(text) < 200:
        return None
    return text


def _bucket(path: str) -> str:
    low = path.lower()
    for key in ("courtpro", "storybook", "frontdesk", "books", "soloco", "director"):
        if key in low:
            return key
    return "other"


def dump_operator_turns(
    match_terms: list[str],
    *,
    since_days: float,
    min_turns: int,
    out_dir: str,
) -> dict[str, object]:
    """Dump operator turns from matching transcripts to `out_dir`.

    Writes one `<bucket>__<NNN>__<sid>.txt` per qualifying session plus
    `MANIFEST.txt`, and returns a small summary dict (counts + the top sessions)
    for the flow to log and the agent to orient on.
    """
    os.makedirs(out_dir, exist_ok=True)
    cutoff = 0.0 if since_days <= 0 else time.time() - since_days * 86400.0

    proj_dirs = [
        d
        for d in glob.glob(str(PROJECTS_ROOT / "*"))
        if any(t.lower() in os.path.basename(d).lower() for t in match_terms)
    ]

    sessions: list[tuple[str, list[str]]] = []
    for d in sorted(proj_dirs):
        for f in glob.glob(os.path.join(d, "**", "*.jsonl"), recursive=True):
            try:
                if cutoff and os.path.getmtime(f) < cutoff:
                    continue
            except OSError:
                continue
            turns: list[str] = []
            try:
                with open(f, errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except ValueError:
                            continue
                        t = _human_text(msg)
                        if t:
                            turns.append(t)
            except OSError:
                continue
            if len(turns) >= min_turns:
                sessions.append((f, turns))

    sessions.sort(key=lambda x: -len(x[1]))
    counts: Counter[str] = Counter()
    manifest: list[str] = []
    for f, turns in sessions:
        bucket = _bucket(f)
        counts[bucket] += 1
        sid = os.path.basename(f).replace(".jsonl", "")[:8]
        with open(os.path.join(out_dir, f"{bucket}__{len(turns):03d}__{sid}.txt"), "w") as of:
            of.write(f"SOURCE: {f}\nHUMAN TURNS: {len(turns)}\nBUCKET: {bucket}\n\n")
            for i, t in enumerate(turns, 1):
                of.write(f"\n===== HUMAN TURN {i} =====\n{t}\n")
        manifest.append(f"{len(turns)}\t{f}")

    with open(os.path.join(out_dir, "MANIFEST.txt"), "w") as mf:
        mf.write("\n".join(manifest) + "\n")

    return {
        "sessions": len(sessions),
        "total_turns": sum(len(t) for _, t in sessions),
        "by_bucket": dict(counts),
        "out_dir": out_dir,
        "top": [(os.path.basename(f), len(t)) for f, t in sessions[:10]],
    }
