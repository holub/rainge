---
name: rainge
description: Run a native OMP roundtable where configured task agents discuss work, share decisions, and remain observable through the custom panel and Agent Hub.
---

# Rainge — native OMP roundtable

Rainge is an OMP extension, not a second agent runner. It uses OMP's native task agents, session history, `/todo`, and Agent Hub.

## Start

Run `/rainge` in an interactive OMP session. It enters rainge mode: a `◈ rainge` status badge appears in the OMP chrome and the input line stays yours. The same command accepts a subcommand:

```text
/rainge panel
/rainge exit
/rainge invite <agent|role> [alias]
/rainge kick <alias>
/rainge pause
/rainge resume
/rainge status
/rainge decide <decision>
```

`<agent|role>` is either an OMP task-agent name (discovered from `.omp/agents`, `~/.omp/agent/agents`, an enabled extension package, or a bundled agent) or a `modelRoles` key from the user config (e.g. `smol`, `designer`). The alias is the stable participant identity shown in Rainge and passed as the native task `name`. Tab-completion on `/rainge` offers subcommands, known agents, configured roles, and invited aliases for `/rainge kick`.

Link this repo as the `rainge` plugin (`omp plugin link /path/to/rainge`; `./install.sh` does this). The manifest registers the extension and the sibling `skills/` directory provides this skill; `omp plugin list` shows `rainge`. Restart OMP or reload extensions after linking.

## Participant contract

When Rainge sends a control message, use the built-in `task` tool. Do not launch external harness subprocesses (`omp -p`, Claude Code, Copilot, Codex, tmux panes), Python coordinators, or external registries.

For an invite, Rainge pre-resolves `<agent|role>` to one installed agent and names it in the queued instruction:

- exact agent name (bundled or custom) — used verbatim;
- `modelRoles` key with a same-named custom agent — that agent;
- `modelRoles` key without one — Rainge provisions a project role-backed agent (`.omp/agents/<role>.md` with `model: "@<role>"`) and dispatches it by name, so the role's model applies;
- anything else is rejected at invite time and never reaches you.

Use the named agent verbatim; do not remap or validate it yourself. If the instruction notes a provisioned role-backed agent, state it aloud once. An invite is roster-only: never spawn for it — no join task, no readiness ack, no hub check, no DM. The first real assignment spawns the participant fresh with `name` set to the Rainge alias and the participant rules inline in the task text. The provisioned file is ordinary project config: delete it to unpin the role.

For multiple independent participants, use one native task batch with shared `context`. Agent Hub then provides their live status, transcripts, steering, revival, and kill controls. Open it with `Alt+A`. Invitees show in the Rainge panel from invite time but get Hub rows only once first assigned real work.

Session scope: a new session means new participants. At first assignment, check the `hub` list — same-named live or parked rows left over from other sessions are stale. Never message or revive a stale row as a roundtable participant: leave it alone or pick a fresh alias (kill it only when its own session is gone — it may still belong to a live sibling session), then always spawn fresh with the task tool. Preferring existing agents over fresh spawns applies only to follow-ups within the current session's own roster.

Invite delivery is queued, not steered: Rainge stores each invite and injects it with the next user prompt, so inviting never starts a model turn on its own. A queued roster line needs no action and no reply beyond the user prompt at hand — take no tool calls for it, and never message a participant except to assign work or forward a user DM.

Each participant must:

1. do not inspect the repository, run tools, or start work until the moderator assigns a task;
2. use tools when evidence is required;
3. report concrete findings, decisions, and unresolved risks concisely;
4. address another participant with `@alias` only when a response is needed, keeping hub messages to one or two lines and batching all follow-ups for one peer into a single message;
5. avoid repeating settled points;
6. never claim agreement unless the decision is recorded.
7. keep every hub (IRC) message to one or two lines — message-card chrome is fixed overhead, so brevity is the only compaction.
8. in a joint discussion, talk peer-to-peer until the assignment's end condition, then let the named reporter send the outcome to Main — never dribble intermediate turns to the moderator.
9. answer a moderator hub DM directly in hub with the requested reply — never open a side discussion, spawn, or report turn for it.
10. start every hub message with `to: @alias|@all|Main` and `status: done|waiting|working|playing` header lines, then the 1-2 line body — the router delivers on headers: `@alias` goes only to that participant, `@all` reaches every participant through the moderator's parallel collection, `Main` is the moderator relay path.

The main OMP agent is the moderator — a control plane, not a transport. It routes participant outputs through subsequent native `task` calls, each assignment self-contained with the participant rules inline. Never answer a topical question yourself while participants are live — neither first nor after: a plain topical question is roundtable-bound, so run the question protocol below instead of answering. Never broadcast `to:"all"` (the bus is process-global and wakes stale rows); address exact live ids. The moderator's own chat lines are for roundtable ops (controls, status, decisions), an explicit `moderator:` address, and relaying collected Q&A answers — relay is verbatim delivery, not a recap: after a collection, relay each collected answer verbatim exactly once as `alias: <text>`, and only text observed verbatim in a wait result or incoming body this turn — never compose, paraphrase, or invent a participant line; a relay line with no collected body behind it is a bug. Never close with an aggregate (`both confirmed ...`, `omp1 and omp2 ...`). Host rendering of peer messages is unreliable, so the moderator's relay is the guaranteed delivery path for collected answers. A milestone update (`status: playing`/`working` to Main) is never a turn to speak and never gets a relay — the durable record is the `/rainge panel` inbox and router log; speak only when the user asks for progress or the run ends. A background completion delivery is not a turn to speak either: post nothing about it — unless its participant has no collected answer at all, in which case DM it once asking to resend its answer via hub send to Main and relay the reply; if nothing arrives, post `alias: (no answer — see history://alias)`. Full detail stays in `agent://` artifacts — never paste full outputs into chat or hub messages. A direct user prompt or Rainge control message always takes precedence over participant traffic. While paused, do not start or chain participant turns; current work may finish.

## Autonomous discussion

Questions are not joint work. When the user asks the roundtable a question (`@all ...`, or a plain topical question while participants are live): if no live participant rows exist yet, assign it with one task call quoting this per-participant task verbatim: `Start with header lines 'to: Main' and 'status: done', then your 1-2 line answer, sent with exactly one hub send (op send, to Main). No other tool calls, no peer discussion, no synthesis.` — no owner, no reporter, no polling. If live rows exist, do not spawn: dispatch parallel hub DMs — one `hub` send per current live id (from `/rainge status`, the panel, or the latest spawn result), all sends issued as parallel tool calls in one message, never broadcast `to:"all"` — then drain with bare `hub` waits (`ids:` listing every asked id) until every live id has answered; answers attribute by sender, so out-of-order arrivals attach to their author and need no resend. After collection, relay each collected answer verbatim exactly once. Do not end your turn after dispatching: stay on the turn until every id has answered or, after one resend-DM (`resend its answer via hub send to Main`), is marked `alias: (no answer — see history://alias)`. Never hold answers to batch them beyond the collection. Reserve sequential chaining (round-robin, each send quoting the question plus every answer collected so far) for questions whose answers must build on each other — and prefer the reporter pattern there: one assignment, peers converse peer-to-peer, the named reporter sends the outcome to Main. Router rule for peer headers: a participant message with `to: @alias` is forwarded as `@sender: <body>` to that live id with one hub send and nothing else; `to: @all` from a participant runs the same parallel-DM collection as a user question.

1. Assign an owner with one task call naming the peers, the shared goal, and the end condition. Never do participant-assignable work yourself — the moderator hosts only when no participant can.
2. Tell participants to converse directly peer-to-peer via hub DMs until the end condition, without routing intermediate turns through you — and in exchanges longer than a few turns, to post one-line milestone updates to Main (`to: Main`, `status: playing|working`, one line each: what just resolved, what is next). Milestones land in the panel inbox/router log for the user; the moderator never replies to them. The user watches progress in `/rainge panel` or Agent Hub peer transcripts, not in moderator chat.
3. Name exactly one reporter to message Main with the outcome. Do not poll or wake peers mid-discussion; wait for the report.
4. `@alias ...` from the user is a direct message: forward it to that participant, never answer it yourself, and stay out of the reply path.
5. While the discussion runs, post nothing per turn — no recaps, no progress summaries, no relay of peer turns. The peers' own messages are the conversation. Speak only for the final outcome of joint work, or when the user addresses you directly about running the roundtable.
6. Switching sessions auto-pauses the roundtable; returning auto-resumes it. A manual pause is never auto-resumed.

A discussion that needs no bump per turn is the goal: one assignment in, one report out.

## Federated bus

For roundtables across separate omp processes, run `python3 router/bus_router.py serve` (default `127.0.0.1:7480`; stays headless). The curses panel is input-first: plain text broadcasts to the room (opening a round), `@alias text` direct-messages one participant; `/help`, `/new <id>`, `/join [id]` (seats stored sessions, revives participants), `/add [alias]` (seat one stored session), `/kick <alias>`, `/r[oster]`, `/rm [id]` (forget the stored session file), `/verbose` (transcript chrome), `/bye` (leave; kills only members of a room you joined — a watched room is left alone). Dead commands hide from completion and refuse with a pointer; `DEL` in a picker unseats without deleting (the join picker forgets the room and keeps members detached).

The protocol is pure chat — no coordinator participant. Every room owns a numbered, append-only transcript of every message; each participant holds a cursor and receives a diff of what they missed only when pinged. A broadcast opens a round: every online participant answers (one `bus op=send to='*'` each, in your own words); answers stream to the asker only — the group stays unaware. When all answers arrive, the router picks a decider (least messages in the round, never the last responder) and pings them the full diff with a final marker — they are the decider and the round ends when they end it: one `bus op=send` with `action='close'` files the verdict and the divider follows it, or a DM to a member opens a one-by-one loop with the full backlog attached. The exchange reaches the local session as context only. Chat answers are never delivered to the room; only explicit bus sends are. An answer cites the seed it answers (`ref` = its #); a stale cite files as chat and the answer stays owed. A participant who decides another turn is genuinely needed initiates it himself: one `bus op=send` with his follow-up (`to='*'` opens the next round, `to='<alias>'` answers one participant directly). Raw-socket clients may still `action=close` with a one-line conclusion or `action=round` with a target; chains cap at 3 rounds. Rounds time out; an operator message while a round is open nests — the outer suspends, the nested round completes first, and the outer resumes with the verdict attached. Only an explicit operator close kills a round.
Seed gate: answer a `[round]` ping only when it asks a question or assigns a task — greetings, acks, and thanks get no send. A participant broadcast without a question lands as chat: visible in the transcript, no round, no pings. Every operator seed pings everyone — greetings included, so no member misses work addressed to the room — and every operator seed wants one content answer from everyone, never an empty ack: if it reads as a greeting the hello IS the answer, if it reads as noise answer what it most likely means. An all-greetings round carries no goal: the decider closes it empty (`action='close'`, no message) and the router files only the divider. A continuation seed (the final-pinged decider moving the last round forward) always wants one answer — never an empty ack.
Presence is transcript chrome: joins and leaves land as `~~~ alias joined ~~~` / `~~~ alias left ~~~` system lines — visible everywhere, pinging nobody and opening no rounds.
Round closes land as a `--------------` divider line in the transcript.

Incoming prompts render as `Bus messages for '<you>'` with tagged transcript lines. A round ping (`[round #N]`) means: reply once via `bus op=send to='*', ref=N` — one line, your own words, never echo the question — then end your turn. A `round #N is closed` error means the round died before your answer landed — end the turn silently, never re-send. Exception: an operator greeting wants one short visible hello line from everyone (never an empty ack) — the decider closes the greet round with a close send carrying its hello. Otherwise, the moment you decide to stay silent, send an empty ack (`bus op=send to='*'`, message='', action='ack') — to the room, never as a DM: an empty DM files nothing so your answer is consumed — the round itself ends only on the decider's close send; silence with no signal parks the room on timeout, never do that. A conflicting-answers tie-break counts as concrete: the final-ping recipient decides and delivers it once (DM for role assignments, broadcast if the whole room must hear it). A direct message from another participant (`[msg]`) is a private conversation: reply with `bus op=send to='<sender>'` — a chat answer alone is never delivered; one message at a time, wait for the reply. Loop traffic is stamped: a stamped DM to you from the decider wants one direct answer back, no decision, no broadcast; a stamped answer back to you as decider hands you the call — DM the verdict with the close flag (`action='close'`; that one send files the verdict and the divider follows), close empty (`action='close'`, no message) when nothing is worth filing, or DM the next member to continue. A direct message from the operator is the exception: answer in chat and delivery back is automatic. Never poll (`bus status`/`list` loops), never read docs to answer bus chat, never inspect code to answer chat.

Converged split, same duty: if the answers agree on who does what next but nobody moved, the final-ping recipient names a member who moves first — never the operator (he only reads; a move parked on him stalls the room) — DM the actor to open a loop (you stay decider across answers); broadcast only if the room must hear it. Your answers reached the asker only — if you await a member's move, DM them; they haven't seen it. If no member can move yet, close the round instead of deferring. Agreement with a pending first move is concrete, not courtesy — and writing the plan into a closing verdict instead of DMing stalls the room: the close ends the mandate and nobody is pinged.

Manual connect from any omp: `/rainge bus <alias> 127.0.0.1:7480 [room]`; spawned participants auto-connect via `RAINGE_BUS`/`RAINGE_BUS_ROOM` env and resume from their stored cursor.

## Shared work state

- Use the built-in `/todo` command or `todo` tool for the shared task list. Do not create `tasks.md`, a side-panel plugin, or a second task database.
- Record accepted decisions with `/rainge decide <text>`. Decisions persist as namespaced OMP session entries.
- Use Agent Hub for live participant state and transcripts. The Rainge panel shows membership and dispatch state; it is not a replacement for Hub.
- tmux is optional terminal hosting only. Rainge does not create, inspect, or control tmux sessions.

## User stance

The user is not a participant — never address him as one, never narrate to him, never ask him to weigh in. His messages are goals: decompose them, assign the work, report the outcome. Step in only when you need him: stuck on a decision only he can make, a goal ambiguous in a way that changes the work, or the outcome is ready. A question to the user is a failure to decide — prefer the boring reversible option and state what you chose.

## Priority and safety

User messages are authoritative. If user intent conflicts with a participant suggestion, follow the user and record the decision when useful. Never infer an unavailable agent name or model. Task dispatch sets only the agent name: a role invite arrives with its role-backed agent already provisioned, so use the named agent verbatim and the role's model applies. Let the native task tool report unknown/disabled agents. Never use full-auto external subprocesses or recreate OMP's persistence, scheduling, or agent registry.
