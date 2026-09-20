# RaiNGE

A roundtable for your AI crew. You operate; members — separate `omp` instances in tmux windows — talk, answer, and decide in one room with one numbered transcript. A broadcast opens a round, everyone answers (citing the round), a synthesizer files the verdict, a divider closes it. Side threads run as direct messages; your mid-game question nests and folds back. No second model, no coordinator agent, no judgment in the plumbing: a stdlib-TCP bus plus a curses panel.

## The shape of a session

```text
you:      have we fixed the flaky test?
aaa  ↳#7  yes — retry with backoff, landed yesterday
bbb  ↳#7  yes, green three nights running
✓bbb      verdict: fixed, stop watching it
          --------------
```

- **Rounds.** Plain text to the room opens one; `@alias` whispers to one member. Every answer cites its seed (`ref=#N`); a cite past the divider errors instead of landing in a dead round.
- **Courtesy stays quiet.** Greetings and acks land as chat — no round, no pings, no ping-pong loops.
- **Verdicts, not summaries.** When all answers arrive the least-heard member synthesizes: one send carries the verdict and the close flag, and the divider follows.
- **Nesting.** Your question mid-round suspends the outer, runs first, and the outer resumes with the verdict attached. Only your explicit close kills.
- **Memory.** Rooms persist under `~/.rainge/rooms/`; each member keeps its own session under `~/.rainge/omp-sessions/` and resumes from its own transcript cursor.

## Requirements

- `omp` on `PATH` (members are `omp` instances; the extension needs a host that loads it).
- Python 3.10+ — stdlib only (`asyncio`, `curses`, `json`, …), no pip packages.
- `tmux` — member seating spawns one window per alias in a shared session; observing and seeding from an existing room work without it.

## Quick start

```sh
./install.sh                              # link this repo as the rainge plugin
python3 router/bus_router.py serve        # detached bus, default 127.0.0.1:7480
python3 router/bus_router.py              # operator panel (spawns a throwaway bus if none runs)
```

In the panel: `/new <id>` creates a room, `/join` enters one (reviving its members), `/add` seats a stored member session, plain text broadcasts, `/bye` leaves. Any `omp` joins from its own terminal:

```text
/rainge bus <alias> <host:port> [room]
```

Verify with `omp plugin list` and `omp plugin doctor`; `./tests/verify.sh` pins every protocol and panel gate offline. Restart OMP (or reload extensions) after linking.

## The other half: native `/rainge`

Inside a single `omp` session, `/rainge` coordinates task agents without leaving the editor — status badge on, input stays yours:

| Command | Effect |
|---|---|
| `/rainge panel` | Open the overlay panel (q closes, editor text intact). |
| `/rainge exit` | Leave rainge mode and clear the badge. |
| `/rainge invite <agent\|role> [alias]` | Add a task agent — or a model role like `smol`, `designer` — to the roundtable. Tab-completion offers both. |
| `/rainge kick <alias>` | Stop routing new work to that participant; use Agent Hub to kill an active child. |
| `/rainge pause` / `/rainge resume` | Gate new participant turns. |
| `/rainge status` | Show membership and dispatch state. |
| `/rainge decide <text>` | Persist a namespaced decision and steer the moderator. |

Invitees are OMP task agents (`.omp/agents`, `~/.omp/agent/agents`, enabled extension packages, bundled agents) or `modelRoles` keys from the user config. A role resolves to the same-named agent when one exists, otherwise RaiNGE provisions a project role-backed agent (`.omp/agents/<role>.md` with `model: "@<role>"`) so the role's model applies. Use `Alt+A` for Agent Hub, `/todo` for the shared task list. The full moderator and member contract lives in `skills/rainge/SKILL.md`.

## Agents

An agent card is a Markdown file with YAML frontmatter:

```md
---
name: default
description: one-line human summary
model: "@default"
---

System prompt body — shipped inline into the task text at first assignment.
```

- **Identity:** the `name:` key wins; without one the filename (minus `.md`) is the name.
- **Model:** `model: "@<role>"` pins a `modelRoles` key from the user config, so a role invite runs on the role's model.
- **Discovery:** bundled agents, then project `.omp/agents/`, then the user agent dir — project-local shadows global and travels with the repo.
- Keep the body short: it doubles as the member's bus convention (`bus op=send`, ack-on-silence, operator-out by default). See `.omp/agents/default.md`.

## Layout

```text
extension/
  rainge.ts       OMP extension: commands, tool, panel, session state
router/
  bus_router.py   the bus: rooms, rounds, nesting, transcript — plus panel/serve/list/kill CLI
  bus_panel.py    curses operator surface (context-gated commands)
skills/
  rainge/
    SKILL.md      OMP-facing coordination contract
package.json       plugin manifest (omp.extensions entry)
install.sh         links this repo as the rainge plugin
tests/
  verify.sh       offline contract checks
docs/
  DESIGN.md       architecture record
  PLAN.md         implementation plan
```

## Boundaries

- **RaiNGE extension:** membership intent, pause/resume state, decisions, custom panel, and the model-facing control tool.
- **Bus router:** transport only — rooms, rounds, transcript. No model, no judgment, no second registry.
- **OMP task:** participant creation, concurrency, output delivery, persistence, and lifecycle.
- **Agent Hub:** observation and direct participant control.
- **OMP session:** durable native-side state and transcript.
- **OMP todo:** shared task workflow.

tmux is optional terminal hosting only. RaiNGE does not create, inspect, or control tmux sessions.
