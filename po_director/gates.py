"""Conditional gate decisions — does a change auto-pass a human gate?

The Director has two human-approval gates:

- **ENTRY** — a Director-invented idea becomes a dispatch / new issue. Fires
  *before* any code exists, so the only signal available is the idea's *change
  class* (is this a docs tweak, a lint fix, or a real feature?).
- **EXIT** — a finished PR reaches `main` (the PR Sheriff). Fires on a real PR,
  so the diff size and CI state are also known.

Each gate may *auto-pass* below a per-project threshold expressed as an opt-in
allowlist of change classes (`DirectorConfig.entry_auto_pass` /
`exit_auto_pass`), plus an optional diff-size cap on EXIT
(`exit_max_diff_lines`). An empty allowlist (the default) means the gate always
fires — identical to pre-threshold behavior.

These functions are the *canonical* semantics. The Director / PR-Sheriff prompts
restate them in prose (the agent applies the judgement), and they are surfaced
in `po director status`; a future mechanical Sheriff can call them directly.
"""

from __future__ import annotations

from po_director.config import CHANGE_CLASSES, NEVER_AUTO_PASS, DirectorConfig

__all__ = ["entry_auto_pass", "exit_auto_pass", "CHANGE_CLASSES", "NEVER_AUTO_PASS"]


def _eligible(change_class: str, allow: tuple[str, ...]) -> bool:
    cls = (change_class or "").strip().lower()
    # NEVER_AUTO_PASS short-circuits even if (somehow) present in the allowlist;
    # normalize_classes already strips those, this is defense in depth.
    if cls in NEVER_AUTO_PASS:
        return False
    return cls in allow


def entry_auto_pass(cfg: DirectorConfig, change_class: str) -> bool:
    """True iff the ENTRY gate may be skipped for a change of `change_class`.

    The idea is dispatched directly (no `bd human` gate) when its class is on the
    project's `entry_auto_pass` allowlist. Empty allowlist -> always False.
    """
    return _eligible(change_class, cfg.entry_auto_pass)


def exit_auto_pass(
    cfg: DirectorConfig,
    change_class: str,
    *,
    diff_lines: int,
    ci_green: bool,
) -> bool:
    """True iff the EXIT gate may be skipped (auto-merge) for this PR.

    All of: class on `exit_auto_pass`, CI green, and (when a cap is set) the diff
    is within `exit_max_diff_lines`. CI must always be green to auto-merge — an
    auto-pass never lands a red PR. Empty allowlist -> always False.
    """
    if not ci_green:
        return False
    if not _eligible(change_class, cfg.exit_auto_pass):
        return False
    cap = cfg.exit_max_diff_lines
    if cap and diff_lines > cap:
        return False
    return True
