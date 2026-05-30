"""po-director: the Director chief pack.

A heartbeat coordinator for software development. It watches the directory it
was started in (`po director start`), reads the goal and the beads work board,
proposes the next highest-leverage work, and — gated on human approval via
`bd human` + Slack — dispatches it through `po run`.

No `nanoc` dependency: the minimal slice of nanoc's `talk` gateway (prompt
render, state gather, agent spawn, Slack notify) is reimplemented on top of
`prefect_orchestration` core.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
