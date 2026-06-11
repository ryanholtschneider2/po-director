# po-director

The **Director** — a po formula pack that adds a standing, proactive chief on
top of `po`. It watches the directory you start it in, reads the goal and the
beads work board, proposes the next highest-leverage software-dev work, gates on
**your** approval (via `bd human` + a Slack ping), and dispatches approved work
through `po run`.

No `nanoc` dependency — the minimal slice of nanoc's gateway (prompt render,
state gather, agent spawn, Slack notify) is reimplemented on top of
`prefect-orchestration` core.

## Quick start

```bash
# Install alongside core (editable dev install)
po packs install --editable ../prefect-orchestration
po packs install --editable .

# Start the Director watching the current directory.
# First run prompts for the goal + North Star, writes .director.toml + goal.md,
# and registers five cron deployments (pulse 20m, roadmap hourly, report nightly,
# dream nightly, improve nightly).
po director start

# Inspect / stop
po director status
po director stop
```

You also need a Prefect worker running so the crons execute:

```bash
prefect worker start --pool po
```

## Standing up a workspace

A new corp dir needs exactly two things: a `goal.md` and a short
`.ade/settings.toml`. Everything else falls back to the persona's defaults and
the built-in defaults, so the file stays to the few knobs that actually differ
per corp.

```toml
# <corp-dir>/.ade/settings.toml — the minimal contract
[persona]
name = "ceo"                       # which standing agent runs (default: director)

[goals]
north_star = "MRR > $10k/mo"       # the metric the persona holds

[notify]
slack_channel = "C08LB4V9ZJ8"      # where proposals + reports post

[schedule]                         # optional — omit to keep the persona/built-in crons
pulse_cron = "*/15 * * * *"
roadmap_cron = "0 * * * *"
report_cron = "0 21 * * *"         # legacy reflect_cron still migrates to this
```

Pair it with a one-line goal:

```bash
mkdir -p mycorp/.ade
$EDITOR mycorp/.ade/settings.toml        # the file above
printf '# Goal\n\nShip the thing.\n' > mycorp/goal.md
cd mycorp && po director start           # reads .ade/settings.toml, no re-prompt
```

`po director start` does NOT re-prompt when `.ade/settings.toml` (or a legacy
`.director.toml`) already exists — the file is the source of truth.

### Config precedence

Every knob resolves through the same layering. Highest wins:

| Layer | Source | Example |
|---|---|---|
| **CLI flags** | `po director start --persona … --channel …` | one-off overrides |
| **Workspace file** | `.ade/settings.toml`, then legacy `.director.toml` under it | per-corp settings |
| **Persona defaults** | the persona's `config.toml` (shipped by its pack) | `ceo` runs `issues`/`auto` by default |
| **Built-in defaults** | `DirectorConfig` field defaults | `director`, `ideate`/`gate`, 10-min pulse |

So a persona pack sets sensible defaults for its kind of corp, an individual
corp dir overrides only what differs in its `.ade/settings.toml`, and a CLI
flag wins for a one-off run. The `.ade/settings.toml` tables map onto config
keys as: `[persona].name`, `[goals].{north_star, goal_path}`,
`[involvement].{work_source, work_ask, merge_mode}`,
`[merge].{strategy, ci_cmd}`, `[notify].slack_channel`, and
`[schedule].{pulse_cron, roadmap_cron, report_cron, dream_cron, improve_cron}`
(a legacy `[schedule].reflect_cron` migrates to `report_cron`). Unknown
tables/keys are ignored, so a newer config never crashes an older pack.

### Why no `extends =` (shared org-level base config)

Considered for `po-director-gt9`: an `extends = "../shared/base.toml"` key so
many corp dirs could inherit one org-level overrides file. **Decided against it.**
Cross-corp shared defaults already have a home — the **persona's `config.toml`**.
A SoloCo org standardizing on, say, `auto` work-ask and a 15-minute pulse for
all its corps ships those as one persona pack; every corp that selects that
persona inherits them and overrides only its own `north_star` / `slack_channel`.
That covers the real shared-base use case without a second inheritance
mechanism, a file-path resolution story (relative to what?), or cycle /
precedence ambiguity between `extends` and persona defaults. If a future need
genuinely can't be expressed as a persona default (per-corp values that must be
shared but aren't persona-shaped), revisit then; until then persona defaults are
the one layering seam.

## How it works

- **`director-pulse`** (every 20 min) — gather goal + board + memory → render
  the Director persona → run one agent turn → it dispatches or proposes work
  (per `work_ask`); the flow posts any new proposal gate to Slack. Its board
  snapshot includes a **Plan update** section carrying the latest roadmap TL;DR
  (if refreshed in the last ~2h), so a pulse reacts to a changed plan
  automatically.
- **`director-roadmap`** (hourly) — the **planning pass**. Runs as the same
  persona doing a dedicated planning turn (it does NOT dispatch builds or merge):
  assess progress since the last pass, maintain `ROADMAP.md` at the workspace
  root (the durable higher-level plan derived from the goal + North Star), and
  decompose its current focus into dependency-wired, prioritized, dispatch-ready
  beads (coalescing against existing ones). It writes a timestamped TL;DR of what
  changed to `.director/roadmap-tldr.md`, which the flow posts to Slack titled
  "Plan updated" and the next pulse picks up in its board snapshot.
- **`director-report`** (nightly, ~21:00) — the end-of-day report. Summarizes
  what the Director *did* since the last report and bubbles up what needs the
  operator: open `human`-labeled gates, decisions awaited, and blockers. Posted
  to Slack. (Renamed from `director-reflect`; a legacy `reflect_cron` migrates.)
- **`director-improve`** (nightly) — the **autonomy ratchet**. Mines the
  operator's recent corrections / nudges / setup-help / taste complaints out of
  the session transcripts (this workspace + its businesses), turns the recurring
  ones into concrete system fixes, writes a dated audit to `docs/loop-audits/`,
  files beads for the top fixes, dispatches the safe well-scoped ones via
  `software-dev-agentic` (the **PR Sheriff owns the merge decision** — the flow
  never merges), and posts a digest. The point: each pass the system needs the
  operator a little less and creeps toward his in-the-loop quality. The flow is
  transport (gather the operator's turns); the agent owns the judgment.
- **`director-dream`** (daily, off-peak) — nightly memory consolidation. Reads
  the day's session transcripts (`~/.claude/projects/<workspace-slug>/*.jsonl`),
  distils durable facts / decisions / lessons into the curated company brain at
  `.director/STATE.md` plus a dated `.director/memory/<date>.md`, and updates docs
  where the day's work made them stale. This is what lets a fresh pulse (every
  session starts cold) carry forward settled decisions and standing guidance
  instead of forgetting them. Posts a short consolidation digest to Slack. The
  flow is transport (schedule + spawn + post); the agent owns the judgment of
  what's worth remembering.

## Configuration reference

Full list of resolved knobs. For a new corp dir prefer the consolidated
`.ade/settings.toml` (see [Standing up a workspace](#standing-up-a-workspace));
the flat `.director.toml` below is the legacy/agent-written form and sits one
layer *under* `.ade/settings.toml`.

| Key | Default | Meaning |
|---|---|---|
| `workspace_dir` | start dir | directory the Director watches/dispatches into |
| `goal_path` | `goal.md` | strategy doc, relative to workspace |
| `north_star` | (asked at start) | the metric to hold |
| `persona` | `director` | the standing agent's identity (see [Personas](#personas)) |
| `slack_channel` | `None` | Slack channel id for posts; no posting when unset |
| `pulse_cron` | `*/20 * * * *` | pulse schedule |
| `roadmap_cron` | `0 * * * *` | roadmap planning schedule (hourly) |
| `report_cron` | `0 21 * * *` | nightly report schedule (legacy `reflect_cron` migrates here) |
| `dream_cron` | `0 4 * * *` | nightly consolidation schedule (daily, off-peak) |
| `improve_cron` | `0 5 * * *` | autonomy-audit schedule (nightly, 05:00) |
| `approval_mode` | `always` | `always` \| `batches` \| `consequential` |

`approval_mode`:
- **`always`** — every dispatch needs your yes (file `bd human`, wait).
- **`batches`** — single safe beads auto-dispatch; epics/batches gate.
- **`consequential`** — dispatch freely; gate only force-push, prod deploy,
  schema migration, spend, irreversible actions, or large parallel fan-outs.

## Rigs — the workspaces a director manages

A director doesn't only operate its own workspace. It manages a set of named
**rigs** — arbitrary workspaces it dispatches work into (a product codebase, a
second codebase, a marketing rig, a gtm rig, …), each with its own `bd` board.
The director's `{{workspace_dir}}` is where *it* lives — its roadmap, its `human`
gates to the operator (so they show in the Orchestra Reviews panel), its memory —
not necessarily where the work lands. This is the standard SoloCo shape: a CEO at
the business level whose code is just one of the rigs it runs.

Declare rigs as `[[rigs]]` tables in `.ade/settings.toml`:

```toml
[[rigs]]
name = "courtpro-app"   # how the director refers to it
path = "courtpro"        # relative to the workspace dir (or absolute)
code = true              # code rigs get a standing PR-Sheriff + software-dev-agentic

[[rigs]]
name = "gtm"
path = "gtm"
code = false             # non-code: the director works it directly / files beads
```

- The director's pulse prompt is fed each rig's board (`build_rigs`), so it sees
  the work and dispatches into the rig's **own path** (`--rig-path <rig path>`),
  never `--rig-path {{workspace_dir}}`.
- Each **`code = true`** rig gets its own standing **PR-Sheriff** (keyed to the
  rig's path), so its PRs auto-merge under an auto `merge_mode`. `po director
  start` drops a minimal merge-mode marker into a code rig that has no config of
  its own, so its sheriff fires. Non-code rigs get no sheriff.
- With no rigs configured, the workspace itself is assumed to be the repo (the
  legacy workspace == code-repo case) and gets the single workspace sheriff.

## Personas

A **persona** is the standing agent's identity for a workspace — by default
`director` (the builtin), but any installed pack can ship its own (`ceo`, `pm`,
…). Selecting a persona swaps the pulse prompt and, optionally, the task prompts
(report / roadmap / dream / improve) and a set of per-persona config defaults.

```bash
po director start --persona ceo     # pulse with the ceo persona's prompt
po director status --persona ceo
po director stop   --persona ceo
```

Set it in `.ade/settings.toml` (`[persona]\nname = "ceo"`), legacy
`.director.toml` (`persona = "ceo"`), or the `--persona` flag. Precedence is the
same as every other knob: CLI flag > workspace files > the persona's own
defaults > builtin defaults. An unknown persona fails loudly, listing what's
available.

For a non-default persona the deployment and tmux session names are suffixed
with the persona slug (e.g. `director-pulse-ceo-<ws-slug>`), so several personas
can run against one workspace without colliding. The default `director` persona
keeps the legacy names byte-for-byte, so existing deployments/sessions survive.

### Shipping a persona from a pack

Register a `po.personas` entry point whose value is a zero-arg callable returning
the absolute path to the persona directory:

```toml
# your-pack/pyproject.toml
[project.entry-points."po.personas"]
ceo = "your_pack.personas:get_persona_dir"
```

```python
# your_pack/personas.py
from importlib.resources import files
from pathlib import Path

def get_persona_dir() -> Path:
    return Path(str(files("your_pack") / "personas" / "ceo"))
```

The directory must contain `prompt.md` (the pulse persona prompt) and may contain:

- `config.toml` — per-persona defaults for any of `work_source`, `work_ask`,
  `pulse_cron`, `roadmap_cron`, `report_cron`, `dream_cron`, `improve_cron`,
  `merge_mode`, `merge_strategy` (overridden by workspace settings and CLI flags).
- `roadmapper/prompt.md` — a persona-specific hourly-planning prompt; when absent
  the builtin roadmapper is used (it reads the persona name as `{{persona}}` so
  it runs as that persona doing a planning pass).
- `reporter/prompt.md` — a persona-specific nightly-report prompt; when absent
  the builtin reporter is used.
- `dreamer/prompt.md` — a persona-specific nightly-consolidation prompt; when
  absent the builtin dreamer is used. It additionally receives `{{transcripts}}`
  (the day's session files to consolidate).

Prompts use po's `{{var}}` substitution. The pulse prompt may reference
`{{workspace_dir}}`, `{{persona}}`, `{{goal}}`, `{{north_star}}`,
`{{work_source}}`, `{{work_ask}}`, `{{merge_mode}}`, `{{merge_strategy}}`,
`{{board}}`, and `{{memory}}`. Run `po packs update` after registering the entry point so
`importlib.metadata` sees it.

EP-registered personas take precedence over po_director's builtin
`agents/<name>/`, so a pack can override `director` itself if it wants.

## Plan

Design + decisions: `~/.agents/plans/po-director-plan.md`.
