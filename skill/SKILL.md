---
name: paseo-fleet
description: |
  Steve's Paseo fleet butler — read and control Paseo (app.paseo.sh) Claude/Codex
  sessions across ALL his machines (XPU pod, MacBook, Airacle, GPU server, paw-mac)
  from any one machine. Each machine runs a Paseo daemon reachable via an E2EE
  offer URL over relay.paseo.sh; this skill wraps the local `paseo` CLI with
  `--host <offer>` to list sessions, read transcripts, triage what's running, and
  send messages / launch new agents remotely.
  USE THIS SKILL when the user says: "list my paseo sessions", "paseo overview",
  "what's running on my machines", "我各个机器上 paseo 在跑什么", "show my fleet",
  "哪个 session 在跑", "read the session on GPU/MBP/...", "send to that paseo
  session", "派活到 paseo 上的机器", "paseo triage", or pastes an
  `https://app.paseo.sh/#offer=...` pairing link.
triggers:
  - paseo
  - paseo fleet
  - paseo overview
  - 各个机器 session
  - app.paseo.sh
  - paseo triage
  - 哪个 session 在跑
---

# paseo-fleet — Claude Code skill

Omnara-fleet's sibling, for **Paseo** (https://app.paseo.sh). Where omnara-fleet
talks to the Omnara cloud REST API, paseo-fleet talks to **each machine's local
Paseo daemon** through the relay tunnel — Paseo is local-first, so there is no
cloud DB; the daemon must be online.

## Hybrid addressing — local vs remote (READ THIS FIRST)

Paseo has TWO native surfaces, both **local-only**, plus our fleet layer for remote:

| Surface | Scope | Use it for |
|---|---|---|
| **Paseo MCP** `mcp__paseo__*` (`list_agents`, `send_agent_prompt`, `get_agent_status`, `get_agent_activity`, `create_agent`, `cancel_agent`, `set_agent_mode`…) | **local daemon only** (127.0.0.1) | the machine you're ON — fast, structured, no relay linger |
| **Paseo orchestration skills** (`/paseo`, `/paseo-handoff`, `/paseo-loop`, `/paseo-committee`, `/paseo-advisor`, installed in `~/.agents/skills/`) | **local** | spawning/looping/handing-off agents on this machine |
| **paseo-fleet** (this skill) | **ALL machines** via offer URL + relay | cross-machine read & control |

**Rule of thumb:**
- Target is the current pod → prefer the native `mcp__paseo__*` tools (or `paseo` CLI directly). No relay, sub-second.
- Target is another machine → use paseo-fleet (`--host <offer>` under the hood).
- `overview`/`triage`/`find` always span the whole fleet (including local, via its own offer).

Our command verbs intentionally mirror the official MCP verbs (`send`≈`send_agent_prompt`,
`sessions`≈`list_agents`, `logs`≈`get_agent_activity`, `run`≈`create_agent`) so the two
surfaces feel identical whether local or remote.

## Mental model (how it works)

| Concept | Omnara | **Paseo** |
|---|---|---|
| Credential | PAT (account-wide) | **offer URL** — per-daemon, E2EE (`serverId`+`daemonPublicKeyB64`+relay endpoint) |
| Transport | Omnara cloud REST | `wss://relay.paseo.sh` WebSocket tunnel (does NOT persist data) |
| Unit | account | **daemon** (one per machine; key = `server_id` `srv_xxx`) |
| Offline behavior | history still in cloud | daemon offline ⇒ that machine is invisible |

The transport primitive is just:
```bash
paseo <subcommand> --host '<offer-url>'      # also accepts PASEO_HOST env
```
`--host` accepts a full `https://app.paseo.sh/#offer=...` URL; the CLI decodes it,
connects via relay, and does E2EE with the daemon's public key. Verified working
v0.1.96.

## Registry

`~/.paseo/paseo-fleet.json` by default (override with `PASEO_FLEET_REGISTRY`; put
it on a shared disk if you want every machine to see the same fleet). chmod 600.
One entry per machine: `{name, note, server_id, offer}`. The offer is not a
private key but CAN connect to your daemons — never echo it into chat/logs/commits.

To add/refresh a machine: on that machine run `paseo daemon pair` (or Paseo app →
pairing link), copy the `#offer=` URL into the registry.

## Commands

Driver: `python3 ~/.claude/skills/paseo-fleet/paseo-fleet.py <cmd>`

| Command | What |
|---|---|
| `overview` | concurrent `paseo ls -a -g --json` scan → all active/completed/stopped agents across all directories |
| `machines` | registered machines + reachability |
| `triage` | only agents that are running or `requiresAttention` (who needs you) |
| `find <query>` | **cross-machine** agent search by name / shortId / provider / cwd substring |
| `sessions <machine>` | full agent list of one machine (JSON) |
| `logs <machine> <agent> [--tail N] [--filter tools\|text\|errors\|permissions]` | timeline of one agent. `<agent>` = id, shortId, OR a substring of the session name |
| `send <machine> <agent> "msg"` | send a follow-up prompt (WRITE — confirm first) |
| `run <machine> "prompt" [--cwd P --provider claude --mode bypass]` | launch a new detached agent (WRITE — confirm first) |
| `raw <machine> -- <paseo args...>` | escape hatch: run any `paseo` subcommand against that daemon |

`<machine>` matches by name (case-insensitive, prefix ok) or by `server_id`.

## Usage decision tree

```
"what's running everywhere" / overview   → overview   (or triage for just active)
"what's on GPU / MBP"                     → sessions <machine>
"read that session" / 看看它在干嘛          → logs <machine> "<name-substr>" --filter text
"哪些等我"                                 → triage
"send X to that session"                  → send <machine> <agent> "X"   (confirm!)
"派活到某台机器"                            → run <machine> "<prompt>" --mode bypass (confirm!)
anything exotic (stop/archive/worktree)   → raw <machine> -- <paseo subcommand>
```

## Pitfalls / notes

1. **Relay-linger hang (the big one)**: a oneshot `paseo --host <offer>` returns
   its data in ~1.5s but then **hangs ~20-30s on TCP socket linger** before the
   node process exits (a Paseo CLI bug — `close()` doesn't `destroy()` the relay
   socket; relay RTT itself is only ~5ms). The driver works around this by
   **streaming stdout and killing the process the instant the result is complete**
   (valid JSON for `ls`, or `PASEO_FLEET_QUIET`=1.5s of silence for text). Net
   effect: full `overview` ~2s instead of ~32s. If you ever call `paseo --host`
   raw, wrap it in `timeout` or expect the hang.
2. **Daemon offline ⇒ invisible.** No cloud fallback. `machines` shows `❌`.
3. **`logs <agent>` resolves names locally** by first doing an `ls` on that
   machine, so it costs 2 round-trips — fine, but pass an id/shortId to skip.
4. **Offer = live credential.** Keep registry chmod 600; don't print offers.
5. **WRITE ops** (`send`/`run`) hit a real session on a real machine — always
   confirm with Steve before sending.
6. **Local machine (XPU)** is also reachable via its own offer through relay; if
   you're already ON that pod you can skip the fleet and use the `paseo` /
   Paseo MCP tools directly.
7. `PASEO_FLEET_REGISTRY` and `PASEO_FLEET_TIMEOUT` env vars override defaults.

## Relation to other tools

- **omnara-fleet** — same butler pattern for the Omnara product. If Steve's
  session is on Omnara (`omnara.com/dashboard/...`), use that skill instead.
- **Paseo MCP** (`mcp__paseo__*`) — only sees the *local* daemon. Use it for the
  current pod; use paseo-fleet for cross-machine.
