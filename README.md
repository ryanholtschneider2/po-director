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
# and registers two cron deployments (pulse every 10m, reflect daily).
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
slack_channel = "C08LB4V9ZJ8"      # where proposals + reflections post

[schedule]                         # optional — omit to keep the persona/built-in crons
pulse_cron = "*/15 * * * *"
reflect_cron = "0 9 * * *"
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
`[schedule].{pulse_cron, reflect_cron}`. Unknown tables/keys are ignored, so a
newer config never crashes an older pack.

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

- **`director-pulse`** (every 10 min) — gather goal + board + memory → render
  the Director persona → run one agent turn → it proposes work by filing a
  `bd human` gate; the flow posts the proposal to Slack. When you answer the
  gate "yes", the next pulse dispatches it via `po run`.
- **`director-reflect`** (daily) — a one-page written reflection on the goal,
  posted to Slack.

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
| `pulse_cron` | `*/10 * * * *` | pulse schedule |
| `reflect_cron` | `0 13 * * *` | reflection schedule (daily) |
| `approval_mode` | `always` | `always` \| `batches` \| `consequential` |

`approval_mode`:
- **`always`** — every dispatch needs your yes (file `bd human`, wait).
- **`batches`** — single safe beads auto-dispatch; epics/batches gate.
- **`consequential`** — dispatch freely; gate only force-push, prod deploy,
  schema migration, spend, irreversible actions, or large parallel fan-outs.

## Personas

A **persona** is the standing agent's identity for a workspace — by default
`director` (the builtin), but any installed pack can ship its own (`ceo`, `pm`,
…). Selecting a persona swaps the pulse prompt and, optionally, the reflection
prompt and a set of per-persona config defaults.

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
  `pulse_cron`, `reflect_cron`, `merge_mode`, `merge_strategy` (overridden by
  workspace settings and CLI flags).
- `reflector/prompt.md` — a persona-specific reflection prompt; when absent the
  builtin reflector is used.

Prompts use po's `{{var}}` substitution. The pulse prompt may reference
`{{workspace_dir}}`, `{{goal}}`, `{{north_star}}`, `{{work_source}}`,
`{{work_ask}}`, `{{merge_mode}}`, `{{merge_strategy}}`, `{{board}}`, and
`{{memory}}`. Run `po packs update` after registering the entry point so
`importlib.metadata` sees it.

EP-registered personas take precedence over po_director's builtin
`agents/<name>/`, so a pack can override `director` itself if it wants.

## Plan

Design + decisions: `~/.agents/plans/po-director-plan.md`.
