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

## How it works

- **`director-pulse`** (every 10 min) — gather goal + board + memory → render
  the Director persona → run one agent turn → it proposes work by filing a
  `bd human` gate; the flow posts the proposal to Slack. When you answer the
  gate "yes", the next pulse dispatches it via `po run`.
- **`director-reflect`** (daily) — a one-page written reflection on the goal,
  posted to Slack.

## Configuration (`<workspace>/.director.toml`)

| Key | Default | Meaning |
|---|---|---|
| `workspace_dir` | start dir | directory the Director watches/dispatches into |
| `goal_path` | `goal.md` | strategy doc, relative to workspace |
| `north_star` | (asked at start) | the metric to hold |
| `slack_channel` | `None` | Slack channel id for posts; no posting when unset |
| `pulse_cron` | `*/10 * * * *` | pulse schedule |
| `reflect_cron` | `0 13 * * *` | reflection schedule (daily) |
| `approval_mode` | `always` | `always` \| `batches` \| `consequential` |

`approval_mode`:
- **`always`** — every dispatch needs your yes (file `bd human`, wait).
- **`batches`** — single safe beads auto-dispatch; epics/batches gate.
- **`consequential`** — dispatch freely; gate only force-push, prod deploy,
  schema migration, spend, irreversible actions, or large parallel fan-outs.

## Plan

Design + decisions: `~/.agents/plans/po-director-plan.md`.
