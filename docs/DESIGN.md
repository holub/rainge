# Rainge native design

## Scope

Rainge is an OMP extension that adds a roundtable control surface to an existing OMP session. It coordinates OMP task agents; it does not replace OMP's scheduler, session store, Agent Hub, or todo system.

## Runtime ownership

```text
user prompt / /rainge control
          |
          v
  Rainge extension ------> OMP session entries
          |
          v
  native task tool ------> child task agents
          |
          +---------------> Agent Hub / task lifecycle
```

The main OMP agent remains the moderator. A Rainge control command records state and delivers an instruction: immediately by steering (stop semantics: kick, pause, resume, decide) or queued with the next user prompt (start semantics: invite, which therefore costs no model turn of its own). The main agent uses the native `task` tool to perform the action. This keeps dispatch, concurrency, cancellation, child sessions, model resolution, and output delivery in OMP.

## Participant identity

A participant has:

```ts
type Participant = {
  alias: string;
  agent: string;
  status: "invited" | "paused" | "kicked";
  joinedAt: string;
};
```

`agent` is an OMP task-agent name. `alias` is the stable user-facing identity and is passed to the task item `name` field, so Agent Hub can show the same identity. Duplicate aliases and reserved names (`all`, `user`, `moderator`) are rejected.

There is no static participant registry. OMP discovers agents from project/user `.omp/agents`, extension-package agents, Claude marketplace agent roots where enabled, and bundled agents. Model selection remains in agent frontmatter and `modelRoles`.

## Durable state

The extension appends `dev.rainge.state.v1` custom entries to the OMP session. On `session_start`, switch, branch, or tree navigation it rebuilds state from the active branch's latest entry. Decisions append `dev.rainge.decision.v1` entries. Both names are extension-owned and namespaced.

OMP's session JSONL is the source of truth. No `~/.rainge` data home, lock files, bus, output directory, or duplicate transcript exists.

## Dispatch contract

An invite is roster-only: it records alias-to-agent in state and queues a roster line for the next user prompt. The moderator must never spawn for it. At first assignment, the moderator must:

1. use the pre-resolved agent named in the roster line verbatim — resolution (exact agent, same-named custom agent, or newly provisioned project role-backed agent pinning the role's model) and unknown-value rejection happen in code at invite time;
2. use the requested alias as the task item's `name`;
3. include the current user goal and roundtable rules in the assignment task;
4. use a task batch for independent participants;
5. route follow-up turns only while Rainge is running;
6. stop routing when paused or kicked;
7. treat every direct user message/control as higher priority than participant output;
8. never answer participant-addressed questions itself, and never re-summarize hub-surfaced outcomes in chat;
9. route follow-up questions to live rows by exact spawned id with `hub` sends, not fresh task spawns — a fresh spawn happens only when no live row exists for the alias; never broadcast, since the bus is process-global and `to:"all"` wakes stale same-named rows from other sessions.
10. stay on the turn after any Q&A dispatch with bare `hub` waits (`ids:` listing every asked id) until every answer arrives — an idle moderator's mailbox only drains on the next user prompt; verbatim-relay every collected answer as `alias: <text>` exactly once, and only text observed verbatim in a wait result or incoming body this turn — never compose, paraphrase, or invent a participant line — since host card rendering is unreliable; resend-DM fallback when a participant completes answerless. Dispatch independent questions as parallel hub DMs — one send per current live id, issued as parallel tool calls in one message; never broadcast. Reserve sequential chaining (round-robin, one live id at a time in roster order, each send quoting the question plus every answer collected so far) for questions whose answers must build on each other — and prefer the reporter pattern there.

11. route peer messages on envelope headers (`to:`/`status:` first lines): `@alias` → one forward quoting the sender (`@sender: <body>`) and nothing else; `@all` → the same parallel-DM collection, wait, and relay as a user question. The extension taps hub send/wait traffic into a bounded (30) router log in state and renders the tail in the panel; taps log regardless of roster — they reconcile unauthored in-memory state from the branch before persisting, so an improvised session with bare task spawns still gets router logging without ever writing empty-over-authored state. Milestone updates (`status: playing|working` to Main) are the durable progress record: idle-time arrivals persist as `irc:incoming` entries (panel inbox), wait-time arrivals surface through wait results (router log); neither is ever moderator-relayed.

12. keep the transport native: the extension shadow-registers `hub` only to rewrite a send addressed to a rostered alias into that participant's exact live id; every call delegates through `ctx.invokeTool` byte-identical otherwise — waits, lists, jobs, and process ops pass through untouched, and no collection, gating, or turn logic lives in code. Q&A collection is native: parallel hub DMs plus multi-id waits (item 10), or the reporter pattern for joint work. An `irc_message` probe logs realtime arrivals when the host delivers that event to extensions.

The extension never shells out to `omp`, Claude Code, Copilot, Codex, or tmux. Child sessions are observable through Agent Hub and persisted through OMP's normal task/session artifacts.

## UI

`/rainge` with no arguments enters rainge mode instead of stealing the editor:

- `ctx.ui.setStatus("rainge", ...)` renders a `◈ rainge` badge in the OMP chrome (running/paused, participant count), re-applied on session restore, `turn_start`, and `turn_end`, cleared on `/rainge exit`;
- the input line stays live for prompts and further `/rainge` subcommands;
- `/rainge panel` opens the same width-safe roster as a `{ overlay: true }` custom surface, so the editor survives underneath and `q` returns to intact input. The panel renders a durable inbox tail extracted from the session's `irc:incoming` entries — host irc cards flash and vanish, but the entries persist, so participant replies to Main stay visible here — beside the bounded router log. Host-rendered hub tool cards are transient chrome the extension cannot suppress.

The badge is intentionally a mode indicator, not a second live-agent renderer. Agent Hub already owns live status, transcripts, steering, revive, and kill. tmux may host the OMP terminal but is not inspected or controlled by Rainge.

Task tool calls and background-job completions render as host cards the skill cannot suppress — hence one spawn per participant per session, then `hub` DMs for follow-ups.

## Completion

The extension registers an autocomplete factory that wraps the built-in provider: built-in suggestions win whenever present off-command, and Rainge candidates appear only where the built-ins return nothing (on `/rainge` lines Rainge answers first). `/rainge` completes subcommands; `/rainge invite` completes bundled agents, custom agents read from project and user agent directories, and `modelRoles` keys parsed from the user config; `/rainge kick` completes invited aliases. The wrapper must call inner methods on their receiver — the built-in reads private state via `this`, so detached calls throw, the catch swallows them, and native completion silently dies. Built-in items apply through the bound inner provider; Rainge items are applied locally by token replacement, and the wrapper never returns null (the host dereferences the result unconditionally). Extension-package and marketplace agents are not enumerated (no host API exposes them); the native task tool remains authoritative at dispatch.

## User priority
Stop-semantics commands (`kick`, `pause`, `resume`, `decide`) and federated bus messages use `sendUserMessage(..., { deliverAs: "steer" })`: control and bus traffic interrupt/steer the current main turn and are persisted through OMP's normal prompt flow. Panel prompts include the latest router presence snapshot instead of forcing a model-side roster lookup. `invite` uses `{ deliverAs: "nextTurn" }`: stored and injected on the next user prompt, so several roster lines accumulate with zero thinking at invite time and batching happens at first assignment. The skill instructs the moderator to stop participant fan-out while paused and to follow the newest user intent over queued participant traffic.

## Tasks and decisions

The built-in `/todo` command/tool owns task phases, transitions, rendering, and persistence. Rainge only tells the moderator to use it. Decisions use the extension's namespaced custom entry and are included in the next moderator turn through the control message.

## Federation (bus)

The hub is process-global, so whole omp instances federate through a standalone router: `router/bus_router.py`, stdlib asyncio TCP speaking NDJSON — central, non-LLM code. There is no coordinator participant: the protocol is pure chat over a per-room, numbered, append-only transcript (bounded at 500 entries, persisted with the roster under `~/.rainge/rooms/`). Every participant holds a cursor and receives a diff of what they missed only when pinged: at a broadcast seed, a DM to them, a synthesizer final ping, or hello with `lastSeq` (every client send piggybacks `seen`). A broadcast opens a round: online participants except the asker and the operator are expected to answer — one short bus reply each, no judgment phrased into the prompt (hidden reasoning tracks decision-shaped language, so footers are unconditional one-liners and every bus prompt arrives on the trusted input channel, `attribution: "user"`; fresh spawns get the whole convention as an initial `-p` prompt instead of reading docs); answers stream to the asker only, and an unanswered round closes at RESPONSE_TIMEOUT. When all expected answers arrive, the router picks a synthesizer — least messages in the round, never the last responder, roster order tie-break — and pings them the missed diff with a final marker; the recipient is the decider and the round ends when they end it: one bus send with `action='close'` files the verdict and the divider follows it, or a DM to a member opens a one-by-one loop carrying the full unknown diff (the model's chat output is context-only and never delivered; a further turn is an explicit bus send by its initiator, taught to fire only on concrete new questions or tasks, or a genuine tie-break among conflicting answers — never courtesy). Peer-to-peer conversation runs over DMs (`to='<alias>'`, kind chat, ping the recipient only, no round machinery): participants reply to a peer DM with a bus DM — only the operator's DMs are answered in chat with automatic relay, because the operator is a human in a TUI who cannot call tools. Raw-socket clients may answer the final ping with `action=close` (body kept as conclusion) or `action=round,target='*'|alias` (chains, capped at MAX_CHAIN=3); both waits have timeouts and the candidate timeout closes without synthesis. The operator's panel messages pre-empt any open round. `room_resume` re-spawns every offline roster participant — `omp -r <session>` (or a fresh `omp -p <convention>` seeded with the bus convention) with `RAINGE_BUS=<alias>@<host>:<port>` and `RAINGE_BUS_ROOM=<room>`; `room_stop` (panel `/bye`) stops them and `/join` revives them; DEL in the join picker forgets a room, transcript and all. Router writes survive broken pipes; a dead peer drops only its own connection. The panel (`router/bus_panel.py`) is an input-first live observer under the operator's login name: plain text broadcasts, `@alias` DMs, slash commands for rooms/seating/kick/roster/rm/bye, spinners on awaited participants, and it reconnects when the router restarts under it.
Seed gate (courtesy-control as mechanism): a participant broadcast without a question opens no round — it lands as chat, visible with no pings — so courtesy replies can never re-seed a round into a ping-pong loop.
Answers cite the round they answer (`ref=<seed #>`): members cite the seed, the router preserves the cite on the entry, and the seed gate files uncited member sends as chat — a reply can never land in the wrong round. The operator panel (`router/bus_panel.py`) is input-first and context-gated: dead commands hide from completion and refuse with a pointer. `/add` seats one stored member session, `/rm` forgets a stored session, `/bye` leaves (killing only members of a room you joined), `/verbose` toggles transcript chrome, `DEL` in a picker unseats without deleting, and the join picker forgets rooms while keeping members detached. Stored member sessions under `~/.rainge/omp-sessions/` let `/join` revive participants, each resuming from its own transcript cursor.


## Failure boundaries

- Unknown or disabled participant names fail through native task resolution; Rainge does not guess or inspect PATH.
- A kicked child may still finish an in-flight task; the moderator must not route new work to it. Agent Hub is the supported kill/abort surface.
- Headless/RPC contexts receive command notifications but do not open the panel; state and control messages remain usable.
- Extension load/runtime errors are contained by OMP's extension runner.
