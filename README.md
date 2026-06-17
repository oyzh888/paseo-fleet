# paseo-fleet

Read and control [Paseo](https://app.paseo.sh) agent sessions across **all your
machines** from any one of them — a fleet butler in the spirit of
[omnara-fleet](https://github.com/oyzh888/omnara-fleet), but for Paseo.

Paseo is local-first: each machine runs its own daemon and there's no cloud DB.
Paseo's native [MCP tools](https://paseo.sh/docs/mcp) and
[orchestration skills](https://paseo.sh/docs/skills) are excellent but **only see
the local daemon**. paseo-fleet adds the missing **cross-machine** layer by
connecting to each remote daemon over the relay tunnel (`relay.paseo.sh`, E2EE)
using its pairing **offer URL** — analogous to an Omnara PAT, but per-daemon.

```
$ paseo-fleet overview
━━━ laptop ━━━
  🟢 running  Finance research            | claude/claude-opus-4-8
  ⚪ idle     Disk cleanup plan            | claude/claude-sonnet-4-6
━━━ gpu-box ━━━
  🟢 running  Iroko strategy workspace     | codex/gpt-5.5
═══ FLEET: 30 agents, 3 running across 2/2 reachable ═══
```

## Why not just the official tools?

| Surface | Scope | Strength |
|---|---|---|
| Paseo MCP (`mcp__paseo__*`) | **local daemon** | structured, fast, no relay |
| Paseo skills (`/paseo`, `/paseo-handoff`, `/paseo-loop`, …) | **local** | spawn/loop/handoff on this box |
| **paseo-fleet** | **all machines** | one pane of glass across the fleet |

Use the native tools for the machine you're on; use paseo-fleet for everything else.
The command verbs mirror the official MCP verbs so it feels the same either way.

## Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/oyzh888/paseo-fleet/main/install.sh)
```

or from a clone:

```bash
git clone https://github.com/oyzh888/paseo-fleet && cd paseo-fleet && ./install.sh
```

This installs the CLI to `~/.local/bin/paseo-fleet`, the Claude Code skill to
`~/.claude/skills/paseo-fleet/`, and seeds an example registry.

### Configure the fleet

Edit `~/.paseo/paseo-fleet.json` (chmod 600). For each machine, get its offer URL
by running `paseo daemon pair` on that machine (or the Paseo app → pairing link),
and paste it in:

```json
{
  "fleet": [
    {"name": "laptop",  "server_id": "srv_...", "offer": "https://app.paseo.sh/#offer=..."},
    {"name": "gpu-box", "server_id": "srv_...", "offer": "https://app.paseo.sh/#offer=..."}
  ]
}
```

> ⚠️ The offer URL is a live credential. Keep the registry private; it's gitignored.

## Commands

| Command | What |
|---|---|
| `overview` | scan every machine → table of all agents (concurrent) |
| `machines` | registered machines + reachability |
| `triage` | only agents that are running or need attention |
| `find <query>` | cross-machine search by name / shortId / provider / cwd |
| `sessions <machine>` | full agent list of one machine (JSON) |
| `logs <machine> <agent> [--tail N] [--filter tools\|text\|errors\|permissions]` | agent timeline (agent = id or name substring) |
| `send <machine> <agent> "msg"` | send a follow-up prompt to a session |
| `run <machine> "prompt" [--cwd P --provider claude --mode bypass]` | launch a new detached agent |
| `raw <machine> -- <paseo args...>` | run any `paseo` subcommand against that daemon |

`<machine>` matches by name (prefix ok) or `server_id`.

### Env

| Var | Default | Meaning |
|---|---|---|
| `PASEO_FLEET_REGISTRY` | `~/.paseo/paseo-fleet.json` | fleet config path |
| `PASEO_FLEET_TIMEOUT` | `45` | per-machine hard timeout (s) |
| `PASEO_FLEET_QUIET` | `1.5` | idle gap that marks a text stream "done" (s) |

## The relay-linger speedup

A oneshot `paseo --host <offer>` returns its data in ~1.5s but then **hangs
~20-30s on TCP socket linger** before the node process exits (the relay RTT itself
is only ~5ms — it's a client-side close bug, not the network). paseo-fleet streams
stdout and **kills the process the instant the result is complete** (valid JSON, or
a quiet period for text streams), turning a ~32s `overview` into ~2s.

## Requirements

- `paseo` CLI on PATH (`@getpaseo/cli`), v0.1.96+
- Python 3.8+
- each target machine's daemon online + relay enabled

## License

MIT
