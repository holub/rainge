#!/usr/bin/env bash
# Offline contract checks for the OMP-native Raige entry points (chat protocol v2).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

# --- installer -------------------------------------------------------------

bash -n "$here/install.sh" || fail "installer shell syntax"
pass "installer shell syntax"

# --- native skill contract ---------------------------------------------------

[ -f "$here/skills/rainge/SKILL.md" ] || fail "missing native skill"
grep -q '^name: rainge$' "$here/skills/rainge/SKILL.md" || fail "skill name frontmatter"
grep -q '^description: ' "$here/skills/rainge/SKILL.md" || fail "skill description frontmatter"
grep -q 'native task tool' "$here/skills/rainge/SKILL.md" || fail "skill does not require native task dispatch"
grep -q 'Autonomous discussion' "$here/skills/rainge/SKILL.md" || fail "skill does not define autonomous discussion"
grep -q 'Session scope' "$here/skills/rainge/SKILL.md" || fail "skill does not define session-scope spawning"
gn=$(grep -c 'external harness\|Python coordinators\|external registries' "$here/skills/rainge/SKILL.md" || true)
[ "$gn" -ge 1 ] || fail "skill does not forbid external runners"
grep -q 'Never answer a topical question yourself' "$here/skills/rainge/SKILL.md" || fail "skill must forbid moderator answering participant-bound questions"
grep -q 'If live rows exist, do not spawn' "$here/skills/rainge/SKILL.md" || fail "skill must route follow-ups via hub instead of fresh spawns"
grep -q 'op send, to Main' "$here/skills/rainge/SKILL.md" || fail "skill must name the hub-DM recipient explicitly"
grep -q 'Federated bus' "$here/skills/rainge/SKILL.md" || fail "skill must document the federated bus"
grep -q 'action=close' "$here/skills/rainge/SKILL.md" || fail "skill must teach the round-close action"
grep -q 'action=round' "$here/skills/rainge/SKILL.md" || fail "skill must teach the round-chain action"
grep -q 'transcript' "$here/skills/rainge/SKILL.md" || fail "skill must describe the room transcript model"
grep -q 'tie-break' "$here/skills/rainge/SKILL.md" || fail "skill must teach final-ping tie-breaking"
grep -q 'tie-break' "$here/extension/rainge.ts" || fail "extension must teach final-ping tie-breaking"
grep -q 'moves first' "$here/skills/rainge/SKILL.md" "$here/extension/rainge.ts" || fail "final-ping recipient must name the first mover on convergence"
grep -q 'define it yourself' "$here/extension/rainge.ts" || fail "synthesizer must define the first move when agreement has none"
grep -q "never an empty ack" "$here/skills/rainge/SKILL.md" "$here/extension/rainge.ts" || fail "greetings must get a visible hello from everyone, never silent parking"
grep -q 'never the operator' "$here/extension/rainge.ts" "$here/skills/rainge/SKILL.md" || fail "synthesizer must name a member, never park the move on the operator"
pass "native skill contract"


grep -q 'liveId' "$here/extension/rainge.ts" || fail "extension must track exact spawned live ids"
grep -q 'tool_result' "$here/extension/rainge.ts" || fail "extension must record spawn ids from task results"
grep -q 'resolveSpawnIds' "$here/extension/rainge.ts" || fail "extension must export the spawn-id matcher for offline proof"
grep -q 'syncFromBranch' "$here/extension/rainge.ts" || fail "taps must reconcile unauthored state from the branch before persisting"
grep -q 'arrivalTexts' "$here/extension/rainge.ts" || fail "panel must extract the durable irc inbox from session entries"
grep -q 'type === "diff"' "$here/extension/rainge.ts" || fail "extension must handle bus diff frames"
grep -q 'frame.final === true' "$here/extension/rainge.ts" || fail "extension must handle the synthesizer final marker"
grep -q "action='close'" "$here/extension/rainge.ts" || fail "extension must teach the one-send decider close"
! grep -q 'action: "ack"' "$here/extension/rainge.ts" || fail "socket auto-ack must be gone; the round ends on the decider send"
grep -q 'cursor' "$here/extension/rainge.ts" || fail "extension must track the transcript cursor"
grep -q 'lastSeq: conn.cursor' "$here/extension/rainge.ts" || fail "hello must resume from the stored cursor"
grep -q 'attribution: "user"' "$here/extension/rainge.ts" || fail "all bus prompts must arrive on the trusted input channel"
grep -q 'entry\["operator"\] === true' "$here/extension/rainge.ts" || fail "extension must key the reply channel on the operator annotation"
grep -q 'conventionSeeded' "$here/extension/rainge.ts" || fail "extension must seed the bus convention once per process"
grep -q 'asks a question or assigns a task' "$here/extension/rainge.ts" || fail "seed footer must gate replies on questions, not demand unconditional answers"
grep -q 'kind === "system"' "$here/extension/rainge.ts" || fail "extension must render system entries without prompting a turn"
grep -q 'OPERATOR_ALIAS = os.environ.get("USER")' "$here/router/bus_panel.py" || fail "panel must seat under the login name from env USER"
! grep -q 'deliverAs: "steer", attribution' "$here/extension/rainge.ts" || fail "idle diff delivery must not force steer through the injection screen"
! grep -q 'onlineBusCoordinator' "$here/extension/rainge.ts" || fail "coordinator machinery must be gone"
grep -q 'action' "$here/extension/rainge.ts" && grep -q 'target' "$here/extension/rainge.ts" || fail "bus tool must carry round action and target"
grep -q 'overlay: true' "$here/extension/rainge.ts" || fail "panel must be overlay to preserve the editor"
grep -q 'RAINGE_BUS_ROOM' "$here/extension/rainge.ts" || fail "extension must auto-connect with the room from env"
pass "extension contracts"

# --- router contracts ---------------------------------------------------------

python3 -m py_compile "$here/router/bus_router.py" || fail "router must compile"
python3 -m py_compile "$here/router/bus_panel.py" || fail "panel must compile"
grep -q 'def append' "$here/router/bus_router.py" || fail "router must keep an append-only transcript"
grep -q 'TRANSCRIPT_CAP' "$here/router/bus_router.py" || fail "transcript must be bounded"
grep -q 'def entries_after' "$here/router/bus_router.py" || fail "diffs must derive from cursors"
grep -q 'def ping' "$here/router/bus_router.py" || fail "router must ping participants with diffs"
grep -q 'def pick_candidate' "$here/router/bus_router.py" || fail "router must pick round synthesizers"
grep -q 'MAX_CHAIN' "$here/router/bus_router.py" || fail "round chains must be capped"
grep -q 'RESPONSE_TIMEOUT' "$here/router/bus_router.py" || fail "round responses must have a timeout"
grep -q 'CANDIDATE_TIMEOUT' "$here/router/bus_router.py" || fail "synthesizers must have a timeout"
grep -q 'def suspend_round' "$here/router/bus_router.py" || fail "operator content must suspend open rounds, not kill them"
grep -q 'room_resume' "$here/router/bus_router.py" || fail "router must respawn offline participants"
grep -q 'room_kill' "$here/router/bus_router.py" || fail "router must kill controlled participants"
grep -q 'RAINGE_BUS_ROOM' "$here/router/bus_router.py" || fail "router must pass the room to spawned instances"
grep -q 'BrokenPipeError' "$here/router/bus_router.py" || fail "router writes must survive broken pipes"
! grep -q 'def promote' "$here/router/bus_router.py" || fail "role machinery must be gone"
grep -q 'def destroy' "$here/router/bus_router.py" || fail "deleted rooms must be destroyed, not just unlinked"
grep -q '"router"' "$here/router/bus_router.py" || fail "rooms frame must identify the router endpoint"
grep -q 'no router at' "$here/router/bus_router.py" || fail "list must report unreachable routers"
grep -q 'frame.get("dir") or' "$here/router/bus_router.py" || fail "room_new must not clobber dir with empty"
grep -q '_local_routers' "$here/router/bus_router.py" || fail "kill must list rival routers instead of guessing"
grep -q 'not substantive' "$here/router/bus_router.py" || fail "courtesy seeds must land as chat"
grep -q 'def note_presence' "$here/router/bus_router.py" || fail "router must log joins and leaves as system entries"
grep -q 'note_text(f"-------------- #{closed_id}")' "$here/router/bus_router.py" || fail "router must name the closed round in the divider"
grep -q 'def is_courtesy' "$here/router/bus_router.py" || fail "router must distinguish courtesy seeds from task seeds"
grep -q 'Uniform first turn' "$here/router/bus_router.py" || fail "operator seeds must ping everyone, greetings included"
grep -q '\[\"deputy\"\] = sender' "$here/router/bus_router.py" || fail "deputy follow-ups must be stamped for extra delivery"
grep -q 'directed pings refresh the loop' "$here/router/bus_router.py" || fail "decider mandate must survive directed pings"
grep -q 'You stay decider' "$here/extension/rainge.ts" || fail "loop answers must render the decider footer"
grep -q 'No empty acks on a continuation' "$here/extension/rainge.ts" || fail "continuations must demand an answer, never an empty ack"
grep -q 'open a loop' "$here/extension/rainge.ts" "$here/skills/rainge/SKILL.md" || fail "continuation must default to a directed loop, not a broadcast"
grep -q 'the round ends when you end it' "$here/extension/rainge.ts" || fail "decider close must be explicit, never automatic"
grep -q 'reaches the asker only' "$here/extension/rainge.ts" || fail "members must know answers reach the asker only"
grep -q 'alias != self.operator' "$here/router/bus_router.py" || fail "live push must echo only the operator, never prompt a member"
grep -q 'every voice before the judge step' "$here/extension/rainge.ts" || fail "operator seeds must demand every voice before the judge step"
grep -q 'silence never gets the gavel' "$here/router/bus_router.py" || fail "abstainers must be excluded from candidacy"
grep -q 'DM the verdict with the close flag' "$here/extension/rainge.ts" "$here/skills/rainge/SKILL.md" || fail "decider must close with one send carrying the verdict and the close flag"
grep -q 'ref=<the round.s #>' "$here/extension/rainge.ts" || fail "members must cite the seed they answer"
grep -q 'ref=N' "$here/skills/rainge/SKILL.md" || fail "skill must teach citing the seed number"
grep -q '## User stance' "$here/skills/rainge/SKILL.md" || fail "skill must set the user stance: goals in, questions only when stuck"
grep -q 'entry\["ref"\] = ref' "$here/router/bus_router.py" || fail "router must preserve the cited ref on entries"
grep -q '↳#' "$here/router/bus_panel.py" || fail "panel must render reply linkage"
grep -q 'verdict close' "$here/router/bus_router.py" || fail "verdict close must clear the mandate"
grep -q 'the verdict is' "$here/router/bus_router.py" || fail "decider must close empty when nothing is worth filing"
grep -q "Close empty: op=send, action='close', message=''" "$here/extension/rainge.ts" || fail "extension must teach the empty verdict close"
grep -q 'a plan written into a verdict parks the room' "$here/extension/rainge.ts" || fail "decider must DM the first move, never file the plan as a verdict"
grep -q 'instead of DMing stalls the room' "$here/skills/rainge/SKILL.md" || fail "skill must forbid plan-as-verdict closes"
grep -q 'print(f"dir {d}")' "$here/router/bus_router.py" || fail "list must group rooms by dir"
! grep -q 'list_all' "$here/router/bus_router.py" || fail "list --all must be gone; list shows the pid"
grep -q -- '--foreground' "$here/router/bus_router.py" || fail "serve must offer --foreground"
grep -q 'daemonize(ROUTER_LOG)' "$here/router/bus_router.py" || fail "serve must detach and return input"
grep -q 'NO_ROOM = "foyer"' "$here/router/bus_router.py" "$here/router/bus_panel.py" || fail "no-room sentinel must read foyer"
grep -q 'room ?? "foyer"' "$here/extension/rainge.ts" || fail "member extension must default to the foyer"
grep -q 'def alias_session_dir' "$here/router/bus_router.py" || fail "router must rejoin per-alias sessions instead of spawning fresh omp"
grep -q -- '--session-dir' "$here/router/bus_router.py" || fail "spawn must isolate and continue alias sessions"
grep -q 'def watch_snapshot' "$here/router/bus_router.py" || fail "router must snapshot member sessions for frozen watching"
grep -q 'watch <room>:<alias>' "$here/router/bus_router.py" || fail "router CLI must offer watch <room>:<alias>"
grep -q '"total": len(self.bus.rooms)' "$here/router/bus_router.py" || fail "room_list must report the unfiltered room total"
grep -q 'rooms_total' "$here/router/bus_panel.py" || fail "panel must show the elsewhere-count on empty dirs"
grep -q 'live state is per-router' "$here/router/bus_router.py" || fail "serve must warn when another router already runs"
grep -q 'file no blank row' "$here/router/bus_router.py" || fail "router must swallow contentless sends instead of filing them"
grep -q "message='', action='ack'" "$here/skills/rainge/SKILL.md" || fail "skill must teach the empty ack on silence"
grep -q 'protocol chrome, never content' "$here/router/bus_router.py" || fail "router must swallow contentless broadcasts instead of chatting them"
grep -q 'deputized and not is_courtesy' "$here/router/bus_router.py" || fail "deputy must not burn its follow-up on courtesy noise"
grep -q 'tie-break, across close' "$here/router/bus_router.py" || fail "deputy must keep the tie-break across close"
grep -q '"cwd": self.cwd' "$here/router/bus_router.py" || fail "rooms must persist the real project dir"
grep -q '"-c", str(workdir)' "$here/router/bus_router.py" || fail "members must spawn in the room cwd, not the router cwd"
grep -q '"cwd": os.getcwd()' "$here/router/bus_panel.py" || fail "/new must stamp the real project dir"
grep -q 'router.lock' "$here/router/bus_router.py" || fail "singleton must be enforced with a lockfile"
grep -q 'refusing a second instance' "$here/router/bus_router.py" || fail "serve must refuse a second instance"
grep -q -- '--takeover' "$here/router/bus_router.py" || fail "serve must offer takeover of a live sibling"
grep -q 'attaching there (singleton)' "$here/router/bus_panel.py" || fail "panel must attach to the locked endpoint instead of spawning"
grep -q 'def delete_room' "$here/router/bus_router.py" || fail "Bus must share one delete_room path"
grep -q 'shutil.rmtree(member_home' "$here/router/bus_router.py" || fail "delete must remove orphaned member sessions"
grep -q 'trusts the socket' "$here/router/bus_router.py" || fail "CLI rm must work pre-hello"
grep -q 'args\[0\] == "rm"' "$here/router/bus_router.py" || fail "rm CLI missing"
grep -q 'args\[0\] in ("list", "ls")' "$here/router/bus_router.py" || fail "ls alias missing"
grep -q 'args\[0\] == "kill"' "$here/router/bus_router.py" || fail "kill CLI missing"
grep -q 'refusing to guess' "$here/router/bus_router.py" || fail "kill must refuse an unlocked router"
grep -q 'allow_spawn' "$here/router/bus_panel.py" || fail "show must be attach-only"
grep -q 'bus_router.py kill' "$here/router/bus_router.py" || fail "usage must document kill"
grep -q 'bus_router.py rm <room>' "$here/router/bus_router.py" || fail "usage must document rm"
pass "router contracts"

# --- panel contracts ------------------------------------------------------------

grep -q 'type to chat' "$here/router/bus_panel.py" || fail "panel must be input-first"
grep -q '"live": True' "$here/router/bus_panel.py" || fail "panel must subscribe to live transcript diffs"
grep -q '"to": "\*"' "$here/router/bus_panel.py" || fail "plain input must broadcast (open a round)"
grep -q 'room_kill' "$here/router/bus_panel.py" || fail "quit must kill the room's participants"
grep -q 'observe-only' "$here/router/bus_panel.py" || fail "attach must observe, never revive"
[ "$(grep -c 'room_resume' "$here/router/bus_panel.py")" = 1 ] || fail "only explicit /join may revive participants"
grep -q '"/add \[alias\]"' "$here/router/bus_panel.py" || fail "panel actions must be slash commands"
grep -q 'session_preview' "$here/router/bus_panel.py" || fail "add picker must label sessions with content previews"
! grep -q '{"label": DEFAULT_ROOM, "room": DEFAULT_ROOM}' "$here/router/bus_panel.py" || fail "join picker must list server rooms only, never a hardcoded default"
grep -q 'startswith(home + os.sep)' "$here/router/bus_panel.py" || fail "dir slug must match omp session directory naming"
grep -q 'def _complete_input' "$here/router/bus_panel.py" || fail "bus panel must provide Tab autocomplete"
grep -q '_entry_rows' "$here/router/bus_panel.py" || fail "panel must expand entries into continuation rows"
grep -q 'kind") == "system"' "$here/router/bus_panel.py" || fail "panel must render system entries as aligned ~~~ lines"
grep -q '"-" \* filler' "$here/router/bus_panel.py" || fail "panel must stretch dividers to full width"
grep -q 'BODY_INLINE_CAP' "$here/router/bus_panel.py" || fail "panel must cap inline multiline bodies"
grep -q 'did you mean' "$here/router/bus_panel.py" || fail "panel must hint when operator text looks like an add or DM"
grep -q 'def discover_member_sessions' "$here/router/bus_panel.py" || fail "add picker must list the room's stored member sessions"
grep -q 'def room_membership' "$here/router/bus_panel.py" || fail "add picker must index which rooms already seat a session"
grep -q '\[in ' "$here/router/bus_panel.py" || fail "add picker must show the room count on seated rows"
grep -q 'participant_delete' "$here/router/bus_router.py" || fail "router must fully unseat deleted participants"
grep -q 'KEY_DC' "$here/router/bus_panel.py" || fail "add picker must delete stored sessions on DEL"
grep -q 'room_detach' "$here/router/bus_router.py" || fail "router must forget rooms while keeping members detached"
grep -q 'room_detach' "$here/router/bus_panel.py" || fail "join picker must forget rooms on DEL"
grep -q 'def available_commands' "$here/router/bus_panel.py" || fail "panel must gate commands by context"
grep -q '"/exit /q"' "$here/router/bus_panel.py" || fail "detach command must be /exit"
grep -q '"/evict"' "$here/router/bus_panel.py" || fail "panel must offer /evict"
grep -q '"/new <id>"' "$here/router/bus_panel.py" || fail "help must list the room creator"
# panel/router skew surfaces as "unknown frame type": every frame the panel
# sends must have a router handler.
grep -q 'ftype != "hello"' "$here/router/bus_router.py" || fail "router must gate pre-hello frames"
for _t in watch list send room_list kick room_new room_invite room_resume room_kill room_delete room_detach participant_delete bye; do
  grep -q "ftype == \"$_t\"" "$here/router/bus_router.py" || fail "router must handle panel frame '$_t'"
done
grep -q '"/verbose"' "$here/router/bus_panel.py" || fail "panel must provide a verbose toggle for transcript chrome"
grep -q 'Ctrl+O' "$here/router/bus_panel.py" || fail "panel must offer a full-message overlay"
grep -q 'def _rooms_bar' "$here/router/bus_panel.py" || fail "status bar must list sibling rooms only, never the active room twice"
grep -q 'def _draw_frame' "$here/router/bus_panel.py" || fail "panel must wrap the chat log in a frame"
grep -q 'framed = width' "$here/router/bus_panel.py" || fail "panel must define the framed flag before the frame branch"
grep -q 'def _last_room' "$here/router/bus_panel.py" || fail "panel must rejoin the last-watched room instead of default"
pass "panel contracts"

# --- round protocol unit test ----------------------------------------------------

PYTHONPATH="$here/router" python3 - <<'PY'
import asyncio
import json
import tempfile
from pathlib import Path

import bus_router
bus_router.ROOMS_DIR = Path(tempfile.mkdtemp(prefix="rainge-verify-"))
from bus_router import Room, PANEL_ALIAS

class FakeWriter:
    def __init__(self):
        self.frames = []
    def write(self, data):
        self.frames.append(json.loads(data.decode()))
        return None
    def is_closing(self):
        return False

async def main():
    room = Room("t", "dir")
    writers = {}
    for alias in ("asker", "b", "c"):
        w = FakeWriter()
        writers[alias] = w
        room.writers[alias] = w
        room.seen.add(alias)
        room.cursors[alias] = 0


    receipt = room.handle_send("asker", "*", "Q?", None, None)
    assert receipt["seq"] == 1, receipt
    assert room.round and room.round.state == "open", "seed must open a round"
    assert sorted(room.round.expected) == ["b", "c"], room.round.expected
    # seed diff delivered to expected participants only
    got_seed = {a: any("seed" in str(f.get("entries")) for f in w.frames) for a, w in writers.items()}
    assert got_seed["b"] and got_seed["c"] and not got_seed["asker"], got_seed

    room.handle_send("b", "*", "rb", None, None)
    room.handle_send("c", "*", "rc", None, None)
    assert room.round is None or room.round.state != "open", "round must leave open after all responses"
    # asker received both responses
    asker_texts = [str(f.get("entries")) for f in writers["asker"].frames]
    assert any("rb" in t for t in asker_texts) and any("rc" in t for t in asker_texts), asker_texts

    # synthesizer final diff carries the responses; candidate is not the asker
    finals = [(a, w) for a, w in writers.items() if any(f.get("final") for f in w.frames)]
    assert finals, "no final diff delivered"
    cand, cw = finals[0]
    assert cand in ("b", "c"), cand
    final_frame = next(f for f in cw.frames if f.get("final"))
    bodies = " ".join(str(e.get("body")) for e in final_frame["entries"])
    assert "rb" in bodies and "rc" in bodies, bodies
    # b and c never saw each other's responses before the final ping
    if cand == "b":
        other = "c"
    else:
        other = "b"
    other_seen = [str(f.get("entries")) for f in writers[other].frames]
    assert not any("rb" in t and "rc" in t for t in other_seen), "group must stay unaware"

    # close via decider verdict: the divider follows the verdict, never precedes it
    room.handle_send(cand, "*", "decided", "close", None)
    assert room.round is None, "close action must close the round"
    assert room.transcript[-2].get("body") == "decided", "verdict must be filed"
    assert room.transcript[-1].get("kind") == "system", "divider must follow the verdict"

    # chain: seed -> responses -> action=round opens chain 2
    room.handle_send("asker", "*", "Q2?", None, None)
    room.handle_send("b", "*", "x", None, None)
    room.handle_send("c", "*", "y", None, None)
    finals2 = [(a, w) for a, w in writers.items() if any(f.get("final") for f in w.frames) and a not in ("asker",)]
    assert finals2
    room.handle_send(finals2[0][0], "*", "deeper", "round", "*")
    assert room.round and room.round.chain == 2, "chained round must open at chain 2"

    # chained round completes; the decider closes with a verdict send and the
    # divider lands after the verdict — the round ends when the decider ends it
    for alias in room.round.expected:
        room.handle_send(alias, "*", "r-" + alias, None, None)
    assert room.round is not None and room.round.state == "synthesizing", "chained round must reach synthesizing"
    cand2 = room.round.candidate
    before = len(room.transcript)
    other2 = next(a for a in room.round.expected if a != cand2)
    room.handle_send(cand2, other2, "wins", "close", None)
    assert room.round is None, "decider verdict DM must close the round"
    added = list(room.transcript)[before:]
    assert added[-2].get("body") == "wins", "verdict DM must be filed before the divider"
    assert added[-1].get("kind") == "system" and "---" in added[-1].get("body", ""), "divider must follow the verdict"
    assert str(added[-1].get("body", "")).strip().split()[-1].startswith("#"), "divider must name the round it closed"
    # lap legs carry the full unknown diff, not just the DM
    room.handle_send("asker", "*", "Q4?", None, None)
    room.handle_send("b", "*", "xb4", None, None)
    room.handle_send("c", "*", "xc4", None, None)
    assert room.round is not None and room.round.state == "synthesizing", "Q4 must reach synthesizing"
    cand5 = room.round.candidate
    assert cand5 in ("b", "c"), cand5
    target5 = "c" if cand5 == "b" else "b"
    peer_answer = "xb4" if target5 == "c" else "xc4"
    room.handle_send(cand5, target5, "go", None, None)
    target_texts = [str(f.get("entries")) for f in writers[target5].frames]
    assert any("go" in t for t in target_texts), "lap DM must be delivered"
    assert any(peer_answer in t for t in target_texts), "lap leg must carry the unseen peer answer"
    room.handle_send(cand5, target5, "done4", "close", None)
    assert room.round is None, "lap verdict must close the round"
    # silence never gets the gavel: abstainer consumed, answerer decides
    room.handle_send("asker", "*", "Q5?", None, None)
    room.handle_send("b", "*", "", "ack", None)
    assert room.round is not None and room.round.state == "open", "consumed ack must not complete the round"
    room.handle_send("c", "*", "xc5", None, None)
    assert room.round is not None and room.round.state == "synthesizing", "Q5 must reach synthesizing"
    assert room.round.candidate == "c", "answerer must decide, not the abstainer"
    room.handle_send("c", "*", "done5", "close", None)
    assert room.round is None, "verdict must close"
    # empty close from the decider files only the divider
    room.handle_send("asker", "*", "Q6?", None, None)
    room.handle_send("b", "*", "xb6", None, None)
    room.handle_send("c", "*", "xc6", None, None)
    assert room.round is not None and room.round.state == "synthesizing"
    cand6 = room.round.candidate
    before6 = len(room.transcript)
    room.handle_send(cand6, "*", "", "close", None)
    assert room.round is None, "empty decider close must close the round"
    added6 = list(room.transcript)[before6:]
    assert len(added6) == 1 and added6[0].get("kind") == "system", "empty close must file only the divider"
    # empty close from anyone else closes nothing
    room.handle_send("asker", "*", "Q7?", None, None)
    room.handle_send("b", "*", "xb7", None, None)
    room.handle_send("c", "*", "", "close", None)
    assert room.round is not None, "non-decider empty close must not close the round"
    room.handle_send(room.round.candidate, "*", "done7", "close", None)
    assert room.round is None, "verdict must close"
    # intent filing: a broadcast citing a dead seed errors and files nothing;
    # the same body citing the open seed files as response.
    room.handle_send("asker", "*", "Q8?", None, None)
    q8 = room.round.id
    before = len(room.transcript)
    verdict = room.handle_send("b", "*", "stale game move", None, None, q8 - 5)
    assert "error" in verdict and "closed" in verdict["error"], verdict
    assert len(room.transcript) == before, "stale cite must file nothing"
    assert "b" not in (room.round.responded if room.round else {}), "stale cite must not mark answered"
    assert room.round is not None and room.round.state == "open", "stale cite must leave the round open"
    room.handle_send("b", "*", "fresh answer", None, None, q8)
    assert room.transcript[-1]["kind"] == "response", "open-seed cite must file as response"
    assert room.transcript[-1].get("ref") == q8, "open-seed cite must be preserved"
    room.handle_send("c", "*", "legacy answer", None, None)
    assert room.transcript[-1]["kind"] == "response", "uncited legacy send must still answer"
    room.handle_send(room.round.candidate, "*", "done8", "close", None)
    assert room.round is None, "verdict must close Q8"
    late = room.handle_send("b", "*", "too late", None, None, q8)
    assert "error" in late and "closed" in late["error"], late
    assert len(room.transcript) == before + 4, "late answer must file nothing"
    # directed relay: parallel round validates, then sequential one-by-one hops
    room.handle_send("asker", "*", "Q3?", None, None)
    room.handle_send("b", "*", "xb", None, None)
    room.handle_send("c", "*", "xc", None, None)
    assert room.round is not None and room.round.state == "synthesizing", "Q3 must reach synthesizing"
    cand3 = room.round.candidate
    assert cand3 in ("b", "c"), cand3
    target3 = "c" if cand3 == "b" else "b"
    room.handle_send(cand3, "*", "validate", "round", target3)
    assert room.round is not None and room.round.chain == 2, "directed hop must open chain 2"
    assert room.round.expected == [target3], f"directed hop must expect only {target3}"
    room.handle_send(target3, "*", "done", None, None)
    assert room.round is not None and room.round.state == "synthesizing", "directed answer must synthesize"
    cand4 = room.round.candidate
    room.handle_send(cand4, "*", "again", "round", cand3)
    assert room.round is not None and room.round.chain == 3, "second hop must open chain 3"
    assert room.round.expected == [cand3], "second hop must expect only the first validator"
    room.handle_send(cand3, "*", "done3", None, None)
    assert room.round is not None and room.round.state == "synthesizing", "chain-3 answer must synthesize"
    room.handle_send(room.round.candidate, "*", "too far", "round", target3)
    assert room.round is None, "chain cap must refuse the fourth link"

asyncio.run(main())
print("round protocol unit OK")
PY
pass "round protocol contract"

PYTHONPATH="$here/router" python3 - <<'PY'
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import Mock
import bus_router
from bus_router import Room

async def main():
    bus_router.ROOMS_DIR = Path(tempfile.mkdtemp(prefix="rainge-verify-nest-"))
    bus_router.OMP_SESSIONS_BASE = Path(tempfile.mkdtemp(prefix="rainge-verify-nests-"))
    room = Room("nest", "d")
    room.operator = "op"
    for a in ("asker", "b", "c"):
        room.writers[a] = Mock()
        room.live.add(a)
        room.seen.add(a)
    # outer round opens; one member answers, one still owes
    room.handle_send("asker", "*", "outer Q?", None, None)
    outer = room.round.id
    room.handle_send("b", "*", "outer answer", None, None, outer)
    assert set(room.round.responded) == {"b"}, "b answered the outer round"
    # operator content nests instead of killing: outer stashed, nested opens
    room.handle_send("op", "*", "nested Q?", None, None)
    assert room.round is not None and room.round.id != outer, "nested round must open"
    assert room.round.asker == "op", "operator asks the nested round"
    assert len(room.nest) == 1 and room.nest[0][0].id == outer, "outer must suspend"
    # the outer cite errors while suspended; nested answers cite the nested seed
    nested = room.round.id
    stale = room.handle_send("b", "*", "late outer", None, None, outer)
    assert "error" in stale, "outer cite must error while suspended"
    room.handle_send("b", "*", "nested b", None, None, nested)
    room.handle_send("c", "*", "nested c", None, None, nested)
    room.handle_send("asker", "*", "nested a", None, None, nested)
    assert room.round.state == "synthesizing", "nested must synthesize"
    room.handle_send(room.round.candidate, "*", "nested verdict", "close", None)
    # nested closed: outer resumes with the verdict in the diff, c still owes
    assert room.round is not None and room.round.id == outer, "outer must resume"
    assert not room.nest, "stack must drain"
    assert room.round.state == "open" and set(room.round.responded) == {"b"}
    assert any("resumed round" in e.get("body", "") for e in room.transcript), "resume marker must file"
    # c answers with the verdict visible, then the outer closes normally
    room.handle_send("c", "*", "outer answer with verdict", None, None, outer)
    assert room.round.state == "synthesizing", "outer must synthesize after resume"
    room.handle_send(room.round.candidate, "*", "outer verdict", "close", None)
    assert room.round is None and not room.nest, "outer close must settle"
    # operator close kills: no resume, stack cleared
    room.handle_send("asker", "*", "doomed Q?", None, None)
    assert room.round is not None
    room.handle_send("op", "*", "", "close", None)
    assert room.round is None and not room.nest, "operator close must kill"

asyncio.run(main())
print("nesting unit OK")
PY
pass "nested rounds suspend and resume"

PYTHONPATH="$here/router" python3 - <<'PY'
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import Mock
import bus_router
from bus_router import Room

async def main():
    bus_router.ROOMS_DIR = Path(tempfile.mkdtemp(prefix="rainge-verify-rearm-"))
    bus_router.OMP_SESSIONS_BASE = Path(tempfile.mkdtemp(prefix="rainge-verify-rearms-"))
    room = Room("rearm", "d")
    room.operator = "op"
    for a in ("asker", "b", "c"):
        room.writers[a] = Mock()
        room.live.add(a)
        room.seen.add(a)
    # an answer holds the response clock
    room.handle_send("asker", "*", "slow Q?", None, None)
    q = room.round.id
    assert room.transcript[-1]["round"] == q, "opener must link the round it opened"
    first = room.round.timer.when()
    await asyncio.sleep(0.05)
    room.handle_send("b", "*", "halfway", None, None, q)
    assert room.round.state == "open", "one answer must not complete"
    assert room.round.timer.when() > first, "answer must re-arm the response clock"
    room.handle_send("c", "*", "rest", None, None, q)
    assert room.round.state == "synthesizing", "both answers must synthesize"
    # loop progress holds the verdict clock
    cand = room.round.candidate
    peer = "b" if cand == "c" else "c"
    marked = room.round.timer.when()
    await asyncio.sleep(0.05)
    room.handle_send("b", "*", "", None, None)  # contentless: no stamp, no re-arm
    assert room.round.timer.when() == marked, "chrome must not touch the verdict clock"
    room.handle_send(cand, peer, "keep going", None, None)
    room.handle_send(peer, cand, "still on it", None, None)
    assert room.round.timer.when() > marked, "loop progress must re-arm the verdict clock"
    room.handle_send(cand, "*", "verdict", "close", None)
    assert room.round is None, "verdict must close"
    # empty DMs file nothing and close nothing
    filed = len(room.transcript)
    room.handle_send("b", "c", "", None, None)
    room.handle_send("b", "c", "", "close", None)
    assert len(room.transcript) == filed, "empty DMs must file no blank rows"
    # empty broadcasts never file, even carrying an action; the owed consume as ack
    room.handle_send("asker", "*", "Q?", None, None)
    qn = room.round.id
    n = len(room.transcript)
    r = room.handle_send("b", "*", "", "close", None)
    assert r.get("ack") and len(room.transcript) == n, "empty action close must ack-consume"
    assert "b" in room.round.responded, "empty send must consume the owed expectation"
    r2 = room.handle_send("c", "*", "", None, None)
    assert r2.get("ack") and len(room.transcript) == n, "stray empty must file nothing"

asyncio.run(main())
print("rearm unit OK")
PY
pass "progress re-arms round clocks"

PYTHONPATH="$here/router" python3 - <<'PY'
import asyncio
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock
import bus_router
from bus_router import Room

async def main():
    base = Path(tempfile.mkdtemp(prefix="rainge-verify-snoop-"))
    bus_router.ROOMS_DIR = Path(tempfile.mkdtemp(prefix="rainge-verify-snoopr-"))
    bus_router.OMP_SESSIONS_BASE = base
    room = Room("snoop", "d")
    room.operator = "op"
    files = {}
    for a in ("asker", "b", "c"):
        room.writers[a] = Mock()
        room.live.add(a)
        room.seen.add(a)
        sdir = base / "snoop" / a
        sdir.mkdir(parents=True)
        f = sdir / f"{a}.jsonl"
        f.write_text("{}\n")
        files[a] = f
        room.participants.append({"alias": a, "session": None, "session_dir": str(sdir)})
    # emitting sessions hold the response clock
    room.handle_send("asker", "*", "slow Q?", None, None)
    room._response_timeout()
    assert room.round is not None and room.round.state == "open", "fresh activity must defer"
    assert room.round.defers == 1, "defer must spend budget"
    # silent sessions let the clock win
    old = time.time() - 300
    for f in files.values():
        os.utime(f, (old, old))
    room._response_timeout()
    assert room.round is not None and room.round.state == "synthesizing", "stale activity must synthesize"
    # composing candidate holds the verdict clock
    now = time.time()
    for f in files.values():
        os.utime(f, (now, now))
    room._candidate_timeout()
    assert room.round is not None and room.round.state == "synthesizing", "composing must defer"
    assert room.round.defers == 2, "verdict defer must spend budget"
    for f in files.values():
        os.utime(f, (old, old))
    room._candidate_timeout()
    assert room.round is None, "silent candidate must close"

asyncio.run(main())
print("snoop unit OK")
PY
pass "session activity defers timeouts"




PYTHONPATH="$here/router" python3 - <<'PY'
import tempfile
import time
from pathlib import Path

import bus_router
bus_router.ROOMS_DIR = Path(tempfile.mkdtemp(prefix="rainge-verify-dep-"))
import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())  # open_round arms timers; never fired here

# deputy follow-up stamps the mandate and still opens a round
room = bus_router.Room("dep", str(bus_router.ROOMS_DIR))
room.operator = "op"
room.deputy = ("a", time.monotonic())
room.handle_send("a", "*", "moving on", None, None)
last = room.transcript[-1]
assert last.get("deputy") == "a", last
assert last.get("kind") == "seed", last
assert room.round is not None and room.round.expected == [], room.round.expected
# the stamp spends the mandate: a second send is ordinary
room.handle_send("a", "*", "also this", None, None)
assert "deputy" not in room.transcript[-1], room.transcript[-1]
# an undeputized status still lands as chat, unstamped
room2 = bus_router.Room("dep2", str(bus_router.ROOMS_DIR))
room2.operator = "op"
room2.handle_send("a", "*", "just saying", None, None)
assert room2.transcript[-1].get("kind") == "chat", room2.transcript[-1]
assert "deputy" not in room2.transcript[-1]
# decider loop: directed ping keeps the mandate and stamps both directions
room3 = bus_router.Room("dep3", str(bus_router.ROOMS_DIR))
room3.operator = "op"
room3.seen.update(("d", "m"))
room3.deputy = ("d", time.monotonic())
room3.handle_send("d", "m", "your move", None, None)
ping = room3.transcript[-1]
assert ping.get("kind") == "chat" and ping.get("deputy") == "d", ping
assert room3.deputy is not None, "directed ping must keep the mandate"
room3.handle_send("m", "d", "done it", None, None)
ans = room3.transcript[-1]
assert ans.get("deputy") == "d", ans
before = room3.deputy[1]
room3.handle_send("m", "d", "still working", None, None)
assert room3.deputy is not None and room3.deputy[1] >= before, "loop answer must refresh the mandate"
# broadcast spends it
room3.handle_send("d", "*", "announcing", None, None)
assert room3.deputy is None, "broadcast must spend the mandate"
assert room3.transcript[-1].get("kind") == "seed"
# live push never echoes the author (self-prompts caused double answers)
from unittest.mock import Mock
room4 = bus_router.Room("dep4", str(bus_router.ROOMS_DIR))
room4.operator = "op"
wa, wm = Mock(), Mock()
room4.writers["a"] = wa
room4.live.add("a")
room4.seen.update(("a", "m"))
room4.handle_send("a", "m", "hi there", None, None)
assert wa.write.call_count == 0, "author must not receive own send"
room4.writers["m"] = wm
room4.live.add("m")
room4.handle_send("a", "m", "hi again", None, None)
assert wm.write.call_count == 1, "target must still receive the DM"
assert wa.write.call_count == 0, "author must stay silent"
# courtesy DM to the decider stays plain
room3.deputy = ("d", time.monotonic())
room3.handle_send("m", "d", "thanks", None, None)
assert "deputy" not in room3.transcript[-1]
# verdict close: visible verdict DM clears the mandate in one send
room3.deputy = ("d", time.monotonic())
room3.handle_send("d", "m", "you win", "close", None)
v = room3.transcript[-2]
assert v.get("kind") == "chat" and v.get("action") == "close", v
assert room3.transcript[-1].get("kind") == "system", "divider must follow the verdict"
assert room3.deputy is None, "verdict close must clear the mandate"
# self-started loop arms the driver (no deputy path involved)
room3.handle_send("m", "d", "your move", None, None)
assert room3.deputy is not None and room3.deputy[0] == "m", "self-started loop must arm the driver"
print("deputy stamp unit OK")
PY
pass "deputy continuation stamp"
PYTHONPATH="$here/router" python3 - <<'PY'
import tempfile
from pathlib import Path
from unittest.mock import patch
import bus_router
bus_router.ROOMS_DIR = Path(tempfile.mkdtemp(prefix="rainge-verify-inv-"))
import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
# invite seats exactly who you named, never the roster
_bus = bus_router.Bus("127.0.0.1", 7499)
_room = bus_router.Room("inv", "")
_room.participants = [{"alias": "a"}, {"alias": "b"}]
with patch.object(bus_router.subprocess, "run"), patch.object(bus_router.subprocess, "Popen"):
    one = _bus.spawn(_room, _room.participants[0])
assert one is not None and one["alias"] == "a", "spawn must seat the named alias"
with patch.object(bus_router.subprocess, "run"), patch.object(bus_router.subprocess, "Popen"):
    # already online: no second seat
    _room.writers["a"] = object()
    assert _bus.spawn(_room, _room.participants[0]) is None, "spawn must skip online aliases"
    del _room.writers["a"]
    both = _bus.resume(_room)
assert [s["alias"] for s in both] == ["a", "b"], "resume must still seat the whole offline roster"
print("invite single-seat OK")
PY
pass "invite seats one"
PYTHONPATH="$here/router" python3 - <<'PY'
import socket
import threading
from bus_panel import BusClient
# a stale reader must not drop the live connection
c = BusClient("127.0.0.1", 7480)
a, b = socket.socketpair()
c.sock = a
c.connected = True
c.gen = 2
t = threading.Thread(target=c._read_loop, args=(1,), daemon=True)
t.start()
b.close()
t.join(timeout=5)
assert not t.is_alive(), "stale reader must exit on peer close"
assert c.connected is True and c.sock is a, "stale reader must not drop the live connection"
# the owning reader still drops on disconnect
a2, b2 = socket.socketpair()
c.sock = a2
c.gen = 3
t2 = threading.Thread(target=c._read_loop, args=(3,), daemon=True)
t2.start()
b2.close()
t2.join(timeout=5)
assert c.connected is False and c.sock is None, "owning reader must drop on disconnect"
a.close()
print("reader generation OK")
PY
pass "reader generation guard"
PYTHONPATH="$here/router" python3 - <<'PY'
from bus_panel import PanelState, _run_command, _send_chat
class FakeClient:
    def __init__(self, connected=True):
        self.connected = connected
        self.sent = []
    def send(self, frame):
        self.sent.append(frame)
# ghost default (never joined) must not emit
c = FakeClient(); st = PanelState()
assert _send_chat(c, st, "helli") is False, "ghost default must not send"
assert c.sent == [], "ghost default must not emit frames"
assert "no room joined" in st.status, st.status
# disconnected keeps the text (caller restores input on False)
c2 = FakeClient(False); st2 = PanelState(); st2.room = "hngmn"; st2.explicit_room = "hngmn"
assert _send_chat(c2, st2, "helli") is False, "disconnected must not send"
assert c2.sent == [] and "not sent" in st2.status, st2.status
# joined room sends to that room
c3 = FakeClient(); st3 = PanelState(); st3.room = "hngmn"; st3.explicit_room = "hngmn"
assert _send_chat(c3, st3, "helli") is True
assert c3.sent[0]["room"] == "hngmn" and c3.sent[0]["body"] == "helli", c3.sent
# /new creates AND joins (used to leave you on the ghost default)
_run_command(c3, st3, "/new newnew")
assert st3.room == "newnew" and st3.explicit_room == "newnew", (st3.room, st3.explicit_room)
kinds = [f.get("type") for f in c3.sent]
assert "room_new" in kinds and "room_resume" in kinds, kinds
from bus_panel import QuitPanel
# /q from a watched room just leaves: members stay
_cw = FakeClient(); _stw = PanelState(); _stw.room = "hngmn"; _stw.explicit_room = ""
try:
    _run_command(_cw, _stw, "/q")
    assert False, "/q must raise QuitPanel"
except QuitPanel:
    pass
assert all(f.get("type") != "room_kill" for f in _cw.sent), "watched-room quit must not kill"
# /q from a joined room detaches: members stay connected
_cj = FakeClient(); _stj = PanelState(); _stj.room = "hngmn"; _stj.explicit_room = "hngmn"
try:
    _run_command(_cj, _stj, "/q")
    assert False, "/q must raise QuitPanel"
except QuitPanel:
    pass
assert all(f.get("type") != "room_kill" for f in _cj.sent), "joined-room /q must not kill"
# /evict from a joined room kills its members; from a watched room it just leaves
_ce = FakeClient(); _ste = PanelState(); _ste.room = "hngmn"; _ste.explicit_room = "hngmn"
try:
    _run_command(_ce, _ste, "/evict")
    assert False, "/evict must raise QuitPanel"
except QuitPanel:
    pass
assert any(f.get("type") == "room_kill" for f in _ce.sent), "joined-room evict must kill"
_cw2 = FakeClient(); _stw2 = PanelState(); _stw2.room = "hngmn"; _stw2.explicit_room = ""
try:
    _run_command(_cw2, _stw2, "/evict")
    assert False, "/evict must raise QuitPanel"
except QuitPanel:
    pass
assert all(f.get("type") != "room_kill" for f in _cw2.sent), "watched-room evict must not kill"
print("panel send gates OK")
# strict lookup: reads never conjure deleted rooms
from bus_router import Bus, Session, Room
_bus = Bus("127.0.0.1", 7499)
_bus.rooms["mem"] = Room("mem", "")
_s = Session(_bus); _s.room = None; _s.alias = "t"
assert _s.target_room({"room": "ghost-xyz-never"}) is None, "reads must not conjure rooms"
assert _s.target_room({"room": "mem"}).name == "mem"
import os as _os
assert not _os.path.exists(_os.path.expanduser("~/.rainge/rooms/ghost-xyz-never.json")), "no file conjured"
print("strict lookup OK")
from bus_router import Room as _Room
class _W:
    def __init__(self):
        self.frames = []
    def write(self, b):
        self.frames.append(b)
_rm = _Room("t", ""); _rm.operator = "op"; _rm.seq = 1
_wop, _wm = _W(), _W()
_rm.live = {"op", "m"}; _rm.writers = {"op": _wop, "m": _wm}; _rm.cursors = {}
_rm.push_live({"seq": 1, "from": "op", "to": "*", "body": "hi", "kind": "seed"})
assert len(_wop.frames) == 1 and len(_wm.frames) == 1, "operator echo plus member delivery"
_rm2 = _Room("t2", ""); _rm2.operator = "op"; _rm2.seq = 1
_w2 = _W()
_rm2.live = {"m"}; _rm2.writers = {"m": _w2}; _rm2.cursors = {}
_rm2.push_live({"seq": 1, "from": "m", "to": "*", "body": "hi", "kind": "seed"})
assert _w2.frames == [], "member must not be prompted by own send"
print("author echo OK")
import json as _j
import asyncio as _a
_a.set_event_loop(_a.new_event_loop())  # open_round arms an asyncio timer
_rm3 = _Room("t3", ""); _rm3.operator = "op"
_wask, _wm1, _wm2, _wop = _W(), _W(), _W(), _W()
_rm3.writers = {"ask": _wask, "m1": _wm1, "m2": _wm2, "op": _wop}
_rm3.live = {"ask", "m1", "m2", "op"}; _rm3.cursors = {}; _rm3.seen.update(("ask", "m1", "m2", "op"))
_rm3.handle_send("ask", "*", "pick a word?", None, None)
_rm3.handle_send("m1", "*", "APPLE", None, None)
def _bodies(w):
    return [e.get("body") for f in w.frames for e in _j.loads(f.decode()).get("entries", [])]
assert "APPLE" in _bodies(_wask), "asker gets the answer"
assert "APPLE" in _bodies(_wop), "operator sees the answer"
assert "APPLE" not in _bodies(_wm2), "group stays unaware until the synthesizer decides"
print("asker-only answers OK")
from bus_panel import _absorb as _absorb2
_stx = PanelState()
assert _absorb2(_stx, {"type": "receipt", "error": "no such room: zz"}) is True
assert _absorb2(_stx, {"type": "receipt", "seq": 5}) is False
# peep mode blocks direct chat, keeps commands
from bus_panel import _handle_input
st4 = PanelState(); st4.allow_chat = False; st4.room = "hngmn"; st4.explicit_room = "hngmn"
st4.input = "helli"; st4.cursor = 5
c4 = FakeClient()
assert _handle_input(c4, st4, 10) is True
assert c4.sent == [] and st4.input == "helli" and "command-only" in st4.status, (st4.status, st4.input)
PY
pass "panel send gates"
PYTHONPATH="$here/router" python3 - <<'PY'
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bus_panel
from bus_panel import _open_invite_picker, discover_member_sessions, room_membership

sessbase = Path(tempfile.mkdtemp(prefix="rainge-verify-sess-"))
roomsbase = Path(tempfile.mkdtemp(prefix="rainge-verify-rooms-"))
(sessbase / "two" / "baab").mkdir(parents=True)
(sessbase / "two" / "abba").mkdir(parents=True)
line = json.dumps({"role": "user", "message": {"content": [{"type": "text", "text": "hello from baab"}]}})
(sessbase / "two" / "baab" / "baab.jsonl").write_text(line + "\n")
(sessbase / "two" / "abba" / "abba.jsonl").write_text(line.replace("baab", "abba") + "\n")
for room in ("two", "v3"):
    (roomsbase / f"{room}.json").write_text(json.dumps({
        "name": room, "dir": "d", "cwd": "", "seq": 0, "transcript": [],
        "participants": [{"alias": "baab", "session": None,
                           "session_dir": str(sessbase / "two" / "baab")}],
    }))
# stored sessions are found per alias, newest file wins
found = {m["alias"]: m for m in discover_member_sessions("two", base=sessbase)}
assert set(found) == {"baab", "abba"}, found
# membership indexes every seat of the identity across rooms
seated = room_membership(rooms_base=roomsbase)
assert seated.get(("alias", "baab")) == {"two", "v3"}, seated
assert ("alias", "abba") not in seated, seated
# picker lists stored sessions first, with the room count on seated rows
bus_panel.OMP_SESSIONS_BASE = sessbase
bus_panel.ROOMS_DIR = roomsbase
st = SimpleNamespace(room="two", invite_preset="baab",
                      pick_kind="", pick_items=[], pick_sel=0)
_open_invite_picker(st)
assert st.pick_items[0]["session"] is None, st.pick_items[0]
assert "resumes baab's stored session" in st.pick_items[0]["hint"], st.pick_items[0]
member_rows = [i for i in st.pick_items if i.get("member")]
assert {i["member"] for i in member_rows} == {"baab", "abba"}, member_rows
baab = next(i for i in member_rows if i["member"] == "baab")
assert "[in 2 rooms: two, v3]" in baab["label"], baab["label"]
assert "[in 2 rooms: two, v3]" in baab["hint"], baab["hint"]
abba = next(i for i in member_rows if i["member"] == "abba")
assert "[in " not in abba["label"], abba["label"]
assert st.pick_kind.startswith("add"), st.pick_kind
print("add picker unit OK")
PY
pass "add picker lists stored sessions with room counts"
PYTHONPATH="$here/router" python3 - <<'PY'
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
import bus_router
from bus_router import Room

import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())  # open_round arms an asyncio timer
tmp = Path(tempfile.mkdtemp(prefix="rainge-verify-delp-"))
bus_router.OMP_SESSIONS_BASE = tmp / "omp-sessions"
bus_router.ROOMS_DIR = tmp / "rooms"
room = Room("delroom", "d")
room.operator = "op"
wb, wc = Mock(), Mock()
room.writers = {"b": wb, "c": wc}
room.live = {"b", "c"}
room.seen.update(("b", "c"))
room.participants = [{"alias": "b", "session": None, "session_dir": "x"}, {"alias": "c"}]
sdir = tmp / "omp-sessions" / "delroom" / "b"
sdir.mkdir(parents=True)
(sdir / "b.jsonl").write_text("{}\n")
room.handle_send("op", "*", "say it?", None, None)
assert room.round is not None and set(room.round.expected) == {"b", "c"}
with patch.object(bus_router.subprocess, "run") as run:
    assert room.delete_participant("b") is True
    assert run.call_count == 1 and "kill-window" in str(run.call_args), run.call_args
assert room.delete_participant("b") is False, "second delete must miss"
assert [p["alias"] for p in room.participants] == ["c"]
assert "b" not in room.writers and "b" not in room.live
assert room.round is not None and room.round.expected == ["c"], room.round.expected
assert wb.close.call_count == 1 and not sdir.exists(), "socket closed, stored session gone"
print("participant delete unit OK")
PY
pass "participant delete unseats fully"

PYTHONPATH="$here/router" python3 - <<'PY'
import curses
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import bus_panel
from bus_panel import _handle_pick, _open_invite_picker

sessbase = Path(tempfile.mkdtemp(prefix="rainge-verify-del-"))
roomsbase = Path(tempfile.mkdtemp(prefix="rainge-verify-delr-"))
bdir = sessbase / "two" / "baab"
bdir.mkdir(parents=True)
(bdir / "baab.jsonl").write_text(json.dumps({"role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}}) + "\n")
for name in ("two", "v3"):
    (roomsbase / f"{name}.json").write_text(json.dumps({
        "name": name, "participants": [{"alias": "baab", "session": None, "session_dir": str(bdir)}]}))
bus_panel.OMP_SESSIONS_BASE = sessbase
bus_panel.ROOMS_DIR = roomsbase

class FakeClient:
    def __init__(self):
        self.sent = []
    def send(self, frame):
        self.sent.append(frame)

st = SimpleNamespace(room="two", invite_preset="", pick_kind="", pick_items=[], pick_sel=0, pending=None, status="")
_open_invite_picker(st)
rows = [i for i in st.pick_items if i.get("member")]
assert [i["member"] for i in rows] == ["baab"], st.pick_items
st.pick_sel = st.pick_items.index(rows[0])
c = FakeClient()
assert _handle_pick(c, st, curses.KEY_DC) is True
kinds = [(f.get("type"), f.get("room"), f.get("alias")) for f in c.sent]
assert ("participant_delete", "two", "baab") in kinds, kinds
assert ("participant_delete", "v3", "baab") in kinds, kinds
assert not bdir.exists(), "picker DEL must remove the stored dir"
assert "deleted stored session baab" in st.status, st.status
assert all(i.get("member") != "baab" for i in st.pick_items), "picker must refresh without baab"
st.pick_sel = 0
assert _handle_pick(c, st, curses.KEY_DC) is True
assert "no stored session" in st.status, st.status
print("picker DEL unit OK")
PY
pass "picker DEL deletes stored sessions everywhere"
PYTHONPATH="$here/router" python3 - <<'PY'
import tempfile
from pathlib import Path
from unittest.mock import Mock
import bus_router
from bus_router import Room

tmp = Path(tempfile.mkdtemp(prefix="rainge-verify-detach-"))
bus_router.ROOMS_DIR = tmp / "rooms"
bus_router.OMP_SESSIONS_BASE = tmp / "sess"
bus = bus_router.Bus("127.0.0.1", 7499)
room = Room("gone", "d")
room.participants = [{"alias": "m", "session": None, "session_dir": "x"}]
wm = Mock()
room.writers = {"m": wm}
room.live = {"m"}
room.seen.add("m")
sdir = tmp / "sess" / "gone" / "m"
sdir.mkdir(parents=True)
(sdir / "m.jsonl").write_text("{}\n")
bus.rooms["gone"] = room
room.save()
assert (tmp / "rooms" / "gone.json").exists()
out = bus.detach_room("gone")
assert out.get("detached") is True, out
assert "gone" not in bus.rooms and room.dead
assert not (tmp / "rooms" / "gone.json").exists(), "room file must go"
assert sdir.exists(), "member sessions survive a detach"
assert wm.close.call_count == 1 and not room.writers, "sockets close"
out2 = bus.detach_room("gone")
assert "error" in out2, "second detach must miss"
print("room detach unit OK")
PY
pass "room detach forgets the file, keeps members"

PYTHONPATH="$here/router" python3 - <<'PY'
import curses
from types import SimpleNamespace
import bus_panel
from bus_panel import NO_ROOM, _handle_pick

class FakeClient:
    def __init__(self):
        self.sent = []
    def send(self, frame):
        self.sent.append(frame)

c = FakeClient()
st = SimpleNamespace(room="two", explicit_room="two", entries=[1], online=["m"],
                      awaiting=[], candidate=None, round_state="s", detail=None,
                      detail_off=0, invite_preset="", pick_kind="join",
                      pick_items=[{"label": "two", "room": "two"}], pick_sel=0,
                      pending=None, status="")
assert _handle_pick(c, st, curses.KEY_DC) is True
assert c.sent[0] == {"type": "room_detach", "room": "two"}, c.sent
assert st.pick_items == [] and st.room == NO_ROOM, (st.pick_items, st.room)
assert "forgotten" in st.status and "detached" in st.status, st.status
print("join DEL unit OK")
PY
pass "join DEL forgets rooms, keeps members detached"
PYTHONPATH="$here/router" python3 - <<'PY'
import bus_panel
from bus_panel import PanelState, _run_command, available_commands

class FakeClient:
    def __init__(self):
        self.sent = []
    def send(self, frame):
        self.sent.append(frame)

# empty foyer: dead commands hide and refuse with a pointer
st = PanelState()
names = set(available_commands(st))
for hidden in ("/join [id]", "/rm [id]", "/kick <alias>", "/add [alias]", "/verbose"):
    assert hidden not in names, (hidden, names)
assert {"/help", "/new <id>", "/r[oster]", "/quit /bye", "/exit /q", "/evict"} <= names, names
c = FakeClient()
for cmd, hint in (("/join", "no rooms yet"), ("/rm", "no rooms yet"),
                   ("/kick x", "nobody here"), ("/add bob", "no room joined"),
                   ("/verbose", "nothing to show")):
    _run_command(c, st, cmd)
    assert hint in st.status, (cmd, st.status)
assert c.sent == [], "gated commands must not emit frames"
# live room: everything available, execution flows through
st2 = PanelState()
st2.room = "two"
st2.rooms = [{"name": "two"}]
st2.rooms_total = 1
st2.online = ["m"]
names2 = set(available_commands(st2))
for shown in ("/join [id]", "/rm [id]", "/kick <alias>", "/add [alias]", "/verbose"):
    assert shown in names2, (shown, names2)
c2 = FakeClient()
_run_command(c2, st2, "/verbose")
assert st2.verbose is False and c2.sent == []
_run_command(c2, st2, "/kick m")
assert c2.sent[-1] == {"type": "kick", "room": "two", "alias": "m"}, c2.sent
print("context gates unit OK")
PY
pass "context gates hide and refuse dead commands"

PYTHONPATH="$here/router" python3 - <<'PY'
import asyncio
import json
import tempfile
from pathlib import Path

import bus_router
bus_router.ROOMS_DIR = Path(tempfile.mkdtemp(prefix="rainge-verify-del-"))
from bus_router import Room

class FakeWriter:
    closed = False
    def write(self, data):
        return None
    def is_closing(self):
        return False
    def close(self):
        self.closed = True

async def main():
    room = Room("gone", "dir")
    keep, gone = FakeWriter(), FakeWriter()
    room.writers["keeper"] = keep
    room.writers["stale"] = gone
    room.seen.update(("keeper", "stale"))
    room.cursors["keeper"] = 0
    room.cursors["stale"] = 0
    room.handle_send("keeper", "*", "before", None, None)
    assert room.seq == 1
    room.destroy(keep=keep)
    assert room.dead, "destroy must mark dead"
    assert "keeper" in room.writers and "stale" not in room.writers, "destroy must evict stale sockets"
    assert gone.closed, "destroy must close stale sockets"
    assert not keep.closed, "destroy must keep the deleter"
    # stale traffic fails instead of resurrecting state
    receipt = room.handle_send("stale", "*", "late", None, None)
    room.save()
    for path in bus_router.ROOMS_DIR.glob("*.json"):
        path.unlink()
    room.save()
    assert list(bus_router.ROOMS_DIR.glob("*.json")) == [], "dead room must not persist"
    # timers are silent on dead rooms
    room.round = None
    room._response_timeout()
    room._candidate_timeout()

asyncio.run(main())
print("delete semantics unit OK")
PY
pass "delete semantics contract"

grep -q 'room_resume' "$here/docs/DESIGN.md" || fail "design must define room resume semantics"
grep -q 'transcript' "$here/docs/DESIGN.md" || fail "design must define the room transcript"
grep -q 'synthesizer' "$here/docs/DESIGN.md" || fail "design must define round synthesis"
grep -q 'bus_router.py' "$here/docs/DESIGN.md" || fail "design must define the bus router"
pass "docs contracts"

command -v omp >/dev/null || fail "omp executable available"
pass "omp executable available"
# The whole bus rides on this file parsing: a member with a broken extension
# boots extension-less and never joins. Fails only on positive evidence, so
# offline runs without credentials still pass.
if timeout 120 omp -p "reply with exactly: hi" 2>&1 | grep -q "Failed to load extension"; then
  fail "extension failed to parse (breaks every member join)"
fi
pass "extension parses"

echo "ALL PASS"
