#!/usr/bin/env python3
"""Rainge federated bus v2: pure chat protocol — transcript, cursors, rounds.

No coordinator: the router is a dumb sequencer. Every room owns an
append-only transcript of numbered entries; every participant owns a cursor
(last seq they have been pinged with). Participants are pinged with a DIFF
of everything past their cursor only at protocol events — a broadcast seed,
a DM to them, or being chosen as round synthesizer — so the group pays
attention only when the protocol demands it.

Round lifecycle:
  1. A broadcast (to "*") from S seeds a round: every online participant
     except S is expected to answer (their answer is a "*" send).
  2. Answers are appended to the transcript and delivered ONLY to S (the
     asker) and the live panel — the group stays unaware.
  3. When every expected alias answered (or RESPONSE_TIMEOUT passes), the
     router picks a synthesizer — least messages in the round, never the
     last responder, roster order as tie-break — and pushes them the diff
     they missed with final=true.
  4. The synthesizer replies with action=ack (settled, nothing appended),
     action=close (settled, body kept as the conclusion), or
     action=round,target=<alias|"*"> (continue: a new round, capped at
     MAX_CHAIN rounds per topic chain).
  Operator content suspends: an open round is stashed, the message opens
  a nested round, and the outer resumes with the verdict. Only an
  explicit operator close kills.
  A send citing a round that is not taking answers errors (round closed)
  and files nothing: late replies die with the round.

client -> server:
    {"type":"hello","alias","room","lastSeq":0,"live":false}
    {"type":"send","to":"b"|"*","body":"...","action":"ack"|"close"|"round"|null,
     "target":"c"|"*", "seen":12, "ref":<seq you answer>}
    {"type":"list"}   {"type":"kick","alias":...}   {"type":"participant_delete","room":...,"alias":...}   {"type":"bye"}
    room management: room_new/room_list/room_invite/room_resume/room_kill/
                     room_delete/room_detach
  server -> client:
    {"type":"welcome","alias","room","seq":<transcript end>}
    {"type":"diff","entries":[{seq,ts,from,to,body,round,kind}], "final":bool}
    {"type":"presence","room","online":[...]}
    {"type":"round","id","state":"open|synthesizing|closed","awaiting":[],
     "candidate":...,"chain":n}
    {"type":"receipt","seq":N}   {"type":"rooms","rooms":[...]}

Usage: bus_router.py serve [host] [port] [--takeover] [--foreground]   detached singleton router (returns input at once; --foreground stays attached)
       bus_router.py | peep [host] [port]  control panel (bare may spawn a throwaway router, peep never does; peep is command-only)
       bus_router.py list [host port] | delete <room> [host port] | watch <room>:<alias> | kill
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, Set

TRANSCRIPT_CAP = 500
ROOMS_DIR = Path.home() / ".rainge" / "rooms"
OMP_SESSIONS_BASE = Path.home() / ".rainge" / "omp-sessions"
TMUX_SESSION = "rainge"
NO_ROOM = "foyer"
RESPONSE_TIMEOUT = 120.0
CANDIDATE_TIMEOUT = 180.0
MAX_CHAIN = 3
ACTIVITY_WINDOW = 60.0  # session touched this recently: still emitting, not silent
MAX_DEFERS = 3  # total extra windows granted on fresh activity before the clock wins
PANEL_ALIAS = "panel"

LOCK_FILE = Path.home() / ".rainge" / "router.lock"
ROUTER_LOG = Path.home() / ".rainge" / "router.log"


def read_router_lock() -> dict | None:
    try:
        data = json.loads(LOCK_FILE.read_text())
        if isinstance(data, dict) and data.get("pid") and data.get("port"):
            return {"host": str(data.get("host") or "127.0.0.1"), "port": int(data["port"]), "pid": int(data["pid"])}
    except (OSError, ValueError, TypeError):
        pass
    return None


def lock_owner_live(lock: dict | None) -> bool:
    # Pid-alive plus a cmdline match: pid reuse alone must not veto a serve.
    if lock is None:
        return False
    try:
        os.kill(lock["pid"], 0)
    except OSError:
        return False
    me = str(os.getpid())
    return any(pid == str(lock["pid"]) and pid != me for pid, _, _ in _local_routers())


def take_router_lock(host: str, port: int) -> None:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(json.dumps({"host": host, "port": port, "pid": os.getpid()}))

def daemonize(log: Path) -> int:
    # Detach so serve survives terminal close: the parent reports the child
    # pid and returns the terminal; the child leads its own session (no HUP)
    # with output appended to log. Returns 0 in the child.
    log.parent.mkdir(parents=True, exist_ok=True)
    pid = os.fork()
    if pid > 0:
        return pid
    try:
        os.setsid()
        sys.stdout.flush()
        sys.stderr.flush()
        fd = os.open(str(log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(fd, sys.stdout.fileno())
        os.dup2(fd, sys.stderr.fileno())
        if fd > 2:
            os.close(fd)
        with open(os.devnull, "rb") as dn:
            os.dup2(dn.fileno(), sys.stdin.fileno())
    except OSError:
        pass
    return 0


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip())[:48] or "room"

QUESTION_WORDS = ("who", "what", "when", "where", "why", "how", "which", "whose", "whom",
    "can", "could", "would", "should", "do", "does", "did", "is", "are", "was", "were",
    "will", "have", "has", "may", "shall")


def is_question(body: str) -> bool:
    text = body.strip() if isinstance(body, str) else ""
    if "?" in text:
        return True
    first = text.split(None, 1)[0].lower().rstrip(".,!?") if text else ""
    return first in QUESTION_WORDS


COURTESY_FIRST = ("hi", "hello", "hey", "yo", "thanks", "thank", "ok", "okay", "ack", "bye")


def is_courtesy(body: str) -> bool:
    # Greetings, acks, and thanks carry no task. Member-side courtesy never
    # opens a round (it lands as chat), and the deputy never spends its
    # follow-up on it.
    text = body.strip() if isinstance(body, str) else ""
    if not text or "?" in text:
        return False
    first = text.split(None, 1)[0].lower().rstrip(".,!?")
    return first in COURTESY_FIRST


def opens_round(sender: str, body: str, operator: str | None) -> bool:
    # Courtesy-control is mechanism, not deliberation: only the operator or a
    # real question opens a round. Acks, greetings, and thanks land as chat
    # (visible in the transcript, no pings), so a courtesy reply can never
    # re-seed a round and ping-pong itself into a new one.
    return sender == operator or is_question(body)

class Round:
    def __init__(self, rid: int, asker: str, expected: list[str], chain: int) -> None:
        self.id = rid
        self.asker = asker
        self.expected = list(expected)
        self.responded: Dict[str, int] = {}
        self.chain = chain
        self.state = "open"
        self.candidate: str | None = None
        self.counts: Dict[str, int] = {}
        self.timer: asyncio.TimerHandle | None = None
        self.defers = 0  # extra windows already granted on session activity

    def state_frame(self, room: str) -> dict:
        return {
            "type": "round",
            "room": room,
            "id": self.id,
            "state": self.state,
            "awaiting": [a for a in self.expected if a not in self.responded],
            "candidate": self.candidate,
            "chain": self.chain,
        }


class Room:
    def __init__(self, name: str, directory: str, cwd: str = "") -> None:
        self.name = name
        self.dir = directory
        self.cwd = cwd  # real project dir; members spawn here, not in the router's cwd
        self.participants: list[dict] = []  # [{alias, session(id|null), session_dir(path|null)}]
        self.writers: Dict[str, asyncio.StreamWriter] = {}
        self.live: Set[str] = set()  # aliases receiving every entry as it lands
        self.seen: Set[str] = set()
        self.seq = 0
        self.transcript: Deque[dict] = deque(maxlen=TRANSCRIPT_CAP)
        self.cursors: Dict[str, int] = {}  # alias -> last seq delivered
        self.operator: str | None = None  # live panel connection, not persisted
        self.round: Round | None = None
        self.dead = False  # set by room_delete: dead rooms never save, answer, or fire timers
        self.deputy: tuple[str, float] | None = None  # final-pinged candidate owed a tie-break; survives close
        self.nest: list = []  # suspended outer rounds: (Round, deputy) pairs, innermost last

    # -- persistence ------------------------------------------------------

    def to_disk(self) -> dict:
        return {
            "name": self.name,
            "dir": self.dir,
            "cwd": self.cwd,
            "participants": self.participants,
            "seq": self.seq,
            "transcript": list(self.transcript),
        }

    def save(self) -> None:
        if self.dead:
            return
        ROOMS_DIR.mkdir(parents=True, exist_ok=True)
        path = ROOMS_DIR / f"{safe_name(self.name)}.json"
        path.write_text(json.dumps(self.to_disk()))

    @classmethod
    def load(cls, path: Path) -> "Room":
        data = json.loads(path.read_text())
        room = cls(str(data.get("name", path.stem)), str(data.get("dir", "")), str(data.get("cwd", "")))
        room.participants = list(data.get("participants", []))
        room.seen = {str(p.get("alias", "")) for p in room.participants if p.get("alias")}
        room.seq = int(data.get("seq", 0))
        room.transcript = deque(data.get("transcript", []), maxlen=TRANSCRIPT_CAP)
        return room

    # -- membership -------------------------------------------------------

    def member_recent(self, alias: str) -> bool:
        # Sneak into the member's own omp session: a session file touched
        # inside ACTIVITY_WINDOW means the model is still emitting —
        # progress, not silence. Missing files read as silent.
        dirs = [str(p.get("session_dir") or "") for p in self.participants if p.get("alias") == alias]
        dirs = [d for d in dirs if d] or [str(OMP_SESSIONS_BASE / safe_name(self.name) / safe_name(alias))]
        try:
            latest = max((q.stat().st_mtime for d in dirs for q in Path(d).glob("*.jsonl")), default=0.0)
        except OSError:
            return False
        return time.time() - latest < ACTIVITY_WINDOW

    def online(self) -> list[str]:
        return sorted(self.writers)

    def presence(self) -> dict:
        return {"type": "presence", "room": self.name, "online": self.online(), "operator": self.operator}

    def broadcast_presence(self) -> None:
        self.push_all(self.presence())

    def roster(self) -> dict:
        return {
            "type": "roster",
            "room": self.name,
            "participants": self.online(),
            "operator": self.operator,
            "round": self.round.state_frame(self.name) if self.round else None,
        }

    # -- transcript -------------------------------------------------------

    def append(self, sender: str, to: str, body: str, kind: str, action: str | None = None, target: str | None = None) -> dict:
        self.seq += 1
        entry = {
            "seq": self.seq,
            "ts": datetime.now().strftime("%H:%M:%S"),
            "from": sender,
            "to": to,
            "body": body,
            "kind": kind,
            "round": self.round.id if self.round else None,
        }
        if action:
            entry["action"] = action
        if target:
            entry["target"] = target
        if sender == self.operator:
            entry["operator"] = True
        self.transcript.append(entry)
        return entry

    def note_text(self, body: str) -> None:
        # System lines are transcript chrome, not rounds: visible everywhere,
        # pinging nobody, never opening rounds or consuming expectations.
        if self.dead or not body:
            return
        entry = self.append("", "*", body, "system")
        self.push_live(entry)
        self.save()

    def note_presence(self, alias: str, joined: bool) -> None:
        if not alias:
            return
        self.note_text(f"{alias} {'joined' if joined else 'left'}")

    def suspend_round(self) -> None:
        # An operator question nests; it never kills: stash the open round
        # and the loop mandate so the nested round runs first and the outer
        # resumes with the verdict in its diff.
        if self.round is not None:
            if self.round.timer:
                self.round.timer.cancel()
                self.round.timer = None
            self.nest.append((self.round, self.deputy))
            self.round = None
            self.deputy = None
    def resume_round(self) -> None:
        # A nested round closed: pop the outer back, re-arm its clock, and
        # re-ping whoever still owes so they answer with the nested verdict
        # already in their diff.
        if not self.nest:
            return
        outer, stashed = self.nest.pop()
        self.round = outer
        self.deputy = stashed
        self.note_text(f"resumed round #{outer.id} — nested verdict above")
        if outer.state == "synthesizing" and outer.candidate:
            self.arm_candidate_timeout()
            self.ping(outer.candidate, final=True)
        else:
            outer.state = "open"
            self.arm_response_timeout()
            for alias in outer.expected:
                if alias not in outer.responded:
                    self.ping(alias)
        self.push_round_state()

    def open_round(self, asker: str, seed: dict, chain: int = 1, only: list[str] | None = None) -> None:
        if only is not None:
            # Directed relay hop: exactly the named validator, online only.
            # No courtesy shortcut — a directed hop is always intentional.
            expected = [a for a in only if a in self.writers and a != asker]
        else:
            # Uniform first turn: every operator seed pings everyone, greetings
            # included. An all-greetings round carries no goal, so the
            # decider ends it with a close send carrying its hello.
            expected = [a for a in self.writers if a not in (asker, self.operator)]
        self.deputy = None  # a new round supersedes any pending tie-break
        self.round = Round(seed["seq"], asker, expected, chain=chain)
        for alias in expected:
            self.round.counts[alias] = self.round.counts.get(alias, 0)
        self.arm_response_timeout()
        self.push_round_state()


    def entries_after(self, cursor: int) -> list[dict]:
        return [e for e in self.transcript if e["seq"] > cursor]


    # -- delivery ---------------------------------------------------------

    def _write(self, writer: asyncio.StreamWriter, frame: dict) -> bool:
        try:
            writer.write((json.dumps(frame) + "\n").encode())
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.drop_writer(writer)
            return False

    def push_all(self, frame: dict) -> None:
        for writer in list(self.writers.values()):
            self._write(writer, frame)

    def push_round_state(self) -> None:
        if self.round:
            self.push_all(self.round.state_frame(self.name))

    def ping(self, alias: str, final: bool = False) -> None:
        """Deliver the diff past alias's cursor; advances it."""
        writer = self.writers.get(alias)
        if writer is None:
            return
        cursor = self.cursors.get(alias, 0)
        entries = self.entries_after(cursor)
        if not entries and not final:
            return
        frame: dict = {"type": "diff", "entries": entries, "final": final}
        if self.round and final:
            frame["round"] = self.round.id
        if self._write(writer, frame):
            self.cursors[alias] = entries[-1]["seq"] if entries else self.seq

    def push_live(self, entry: dict, only: set[str] | None = None) -> None:
        seq = entry["seq"]
        for alias in list(self.live):
            if only is not None and alias not in only:
                continue  # asker-only answers: the group stays unaware until the synthesizer decides
            if alias == entry.get("from") and alias != self.operator:
                continue  # never prompt a member with their own send; the operator gets a display echo
            writer = self.writers.get(alias)
            if writer is None:
                self.live.discard(alias)
                continue
            if self.cursors.get(alias, 0) >= seq:
                continue  # already delivered by a direct ping
            if self._write(writer, {"type": "diff", "entries": [entry], "final": False}):
                self.cursors[alias] = max(self.cursors.get(alias, 0), seq)

    def drop_writer(self, writer: asyncio.StreamWriter) -> None:
        left: list[str] = []
        for alias, registered in list(self.writers.items()):
            if registered is writer:
                del self.writers[alias]
                self.live.discard(alias)
                if self.operator == alias:
                    self.operator = None
                elif alias:
                    left.append(alias)
        for alias in left:
            self.note_presence(alias, False)
        self.broadcast_presence()

    def destroy(self, keep: asyncio.StreamWriter | None = None) -> None:
        """Delete semantics: nothing in this room may outlive it. Marks dead
        (future saves and sends fail instead of resurrecting the file),
        cancels round timers, and evicts every connected socket except the
        deleter's own — stale sessions must re-hello, not keep writing."""
        self.dead = True
        if self.round is not None and self.round.timer is not None:
            self.round.timer.cancel()
            self.round.timer = None
        for alias, registered in list(self.writers.items()):
            if registered is keep:
                continue
            try:
                registered.close()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            del self.writers[alias]
            self.live.discard(alias)
            if self.operator == alias:
                self.operator = None
        self.broadcast_presence()

    def delete_participant(self, alias: str) -> bool:
        # Full unseat: roster, socket, window, stored session, and any owed
        # answer. Only the room-scoped session dir goes; a grafted native
        # `session` file is the operator's history and stays. Deleting the
        # last owed seat lets the open round close itself.
        if not alias or not any(p.get("alias") == alias for p in self.participants):
            return False
        self.participants = [p for p in self.participants if p.get("alias") != alias]
        registered = self.writers.pop(alias, None)
        if registered is not None:
            try:
                registered.close()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        self.live.discard(alias)
        if self.operator == alias:
            self.operator = None
        if self.round is not None:
            self.round.expected = [a for a in self.round.expected if a != alias]
            self.round.responded.pop(alias, None)
            if self.round.state == "open":
                self._check_complete(self.round)
        subprocess.run(["tmux", "kill-window", "-t", f"{TMUX_SESSION}:={alias}"], capture_output=True, check=False)
        shutil.rmtree(OMP_SESSIONS_BASE / safe_name(self.name) / safe_name(alias), ignore_errors=True)
        self.broadcast_presence()
        self.save()
        return True

    # -- rounds -----------------------------------------------------------


    def arm_response_timeout(self) -> None:
        loop = asyncio.get_event_loop()
        if self.round.timer:
            self.round.timer.cancel()
        self.round.timer = loop.call_later(RESPONSE_TIMEOUT, self._response_timeout)

    def _response_timeout(self) -> None:
        if self.dead:
            return
        room_round = self.round
        if room_round is None or room_round.state != "open":
            return
        owed = [x for x in room_round.expected if x not in room_round.responded]
        if owed and room_round.defers < MAX_DEFERS and all(self.member_recent(x) for x in owed):
            room_round.defers += 1
            self.arm_response_timeout()  # still emitting: hold the clock, don't orphan the round
            self.push_round_state()
            return
        room_round.state = "synthesizing"
        candidate = self.pick_candidate()
        if candidate is None:
            self.close_round("timeout-no-candidate")
            return
        room_round.candidate = candidate
        self.push_round_state()
        self.arm_candidate_timeout()
        self.ping(candidate, final=True)
        self.deputy = (candidate, time.monotonic())  # its next room send is the tie-break, across close

    def arm_candidate_timeout(self) -> None:
        loop = asyncio.get_event_loop()
        if self.round.timer:
            self.round.timer.cancel()
        self.round.timer = loop.call_later(CANDIDATE_TIMEOUT, self._candidate_timeout)

    def _candidate_timeout(self) -> None:
        if self.dead:
            return
        if self.round is None or self.round.state != "synthesizing":
            return
        if (self.round.candidate and self.round.defers < MAX_DEFERS
                and self.member_recent(self.round.candidate)):
            self.round.defers += 1
            self.arm_candidate_timeout()  # verdict still composing: hold the clock
            return
        self.close_round("candidate-timeout")

    def pick_candidate(self) -> str | None:
        room_round = self.round
        if room_round is None:
            return None
        pool = [a for a in room_round.expected if a in self.writers]
        if not pool:
            pool = [a for a in self.writers if a not in (room_round.asker, self.operator)]
        if not pool:
            return None
        answered = [a for a in pool if room_round.counts.get(a, 0) > 0]
        if answered:
            pool = answered  # the judge answered: silence never gets the gavel
        last_responder = max(room_round.responded, key=lambda a: room_round.responded[a], default=None)
        ranked = sorted(pool, key=lambda a: (room_round.counts.get(a, 0), a == last_responder, a))
        return ranked[0]

    def close_round(self, reason: str) -> None:
        if self.round is None:
            return
        if self.round.timer:
            self.round.timer.cancel()
            self.round.timer = None
        self.round = None
        self.push_all({"type": "round", "room": self.name, "state": "closed", "reason": reason})
        self.note_text("--------------")
        self.resume_round()
        self.save()

    # -- message intake ---------------------------------------------------

    def handle_send(self, sender: str, to: str, body: str, action: str | None, target: str | None, ref: int | None = None) -> dict:
        if self.dead:
            return {"type": "receipt", "error": "room deleted"}
        if to == "all":
            to = "*"
        if to != "*" and to not in self.seen and to not in self.writers:
            return {"type": "receipt", "error": f"unknown alias: {to}"}
        if to == sender:
            return {"type": "receipt", "error": "self-addressed"}
        # Operator content nests: the open round suspends and resumes with
        # the nested verdict in its diff. Only an explicit operator close
        # kills — timeouts bound everything else.
        if sender == self.operator and action == "close" and to == "*":
            self.nest.clear()
            self.deputy = None
            if self.round is not None:
                self.close_round("operator-closed")
            self.save()
            return {"type": "receipt", "seq": self.seq}
        if sender == self.operator and self.round is not None and body.strip():
            self.suspend_round()

        room_round = self.round
        # A cited round that is not taking answers is dead: the answer dies
        # with it. Error, never file — filing past the divider lets a late
        # reply land in a round that already closed. The synthesizing
        # candidate's own verdict still passes through.
        if ref is not None and not (
            room_round is not None
            and room_round.state == "open"
            and sender in room_round.expected
            and sender not in room_round.responded
            and ref == room_round.id
        ):
            if not (
                room_round is not None
                and room_round.state == "synthesizing"
                and sender == room_round.candidate
            ):
                return {"type": "receipt", "error": f"round #{ref} is closed — send nothing"}

        # A contentless broadcast is protocol chrome, never content: members
        # with nothing to say abstain, and stale prompts send the same signal
        # without the action. Consume it against the open round if owed, else
        # swallow it — filing strays as chat polluted the transcript with
        # empty rows from unpinged members and junk rounds from deputy noise.
        if to == "*" and not body.strip() and not (
            action == "close"
            and room_round is not None
            and room_round.state == "synthesizing"
            and (sender == room_round.candidate or (self.deputy is not None and sender == self.deputy[0]))
        ):
            if (
                room_round is not None
                and room_round.state == "open"
                and sender in room_round.expected
                and sender not in room_round.responded
            ):
                room_round.responded[sender] = self.seq
                self._write_safe(room_round.asker)
                self._check_complete(room_round)
            if room_round is not None and room_round.state == "open":
                self.arm_response_timeout()  # answers in flight hold the response clock
            self.save()
            return {"type": "receipt", "seq": self.seq, "ack": True}
        # A contentless close from the decider files nothing: the verdict is
        # silence, the divider follows. Anyone else cannot close this way.
        if (
            action == "close"
            and not body.strip()
            and room_round is not None
            and room_round.state == "synthesizing"
            and (sender == room_round.candidate or (self.deputy is not None and sender == self.deputy[0]))
        ):
            self.deputy = None
            self.close_round("decided")
            self.save()
            return {"type": "receipt", "seq": self.seq, "ack": True}
        # Answers file by cited intent, not arrival order: a send answers the
        # open round only when it cites the open seed (or cites nothing, the
        # raw-client legacy). A stale cite lands as chat; the answer stays owed.
        if to == "*" and room_round and room_round.state == "open" and sender in room_round.expected and sender not in room_round.responded and (ref is None or ref == room_round.id):
            kind = "response"
        elif to == "*":
            kind = "seed" if not (room_round and room_round.state == "synthesizing" and sender == room_round.candidate) else "synthesis"
        else:
            kind = "chat"
        deputy, deputized_at = self.deputy if self.deputy else (None, 0.0)
        fresh = time.monotonic() - deputized_at < CANDIDATE_TIMEOUT
        deputized = sender == deputy and to == "*" and fresh  # broadcast continuation (spends)
        looping = sender == deputy and to != "*" and fresh  # decider's directed ping (keeps)
        if deputy is None and to != "*" and sender != self.operator and not is_courtesy(body):
            self.deputy = (sender, time.monotonic())  # self-started loop: the driver owns the close
            deputy, fresh, looping = sender, True, True
        if sender == deputy and (to == "*" or not fresh):
            self.deputy = None  # a broadcast spends the mandate; expiry clears it
        if looping:
            self.deputy = (deputy, time.monotonic())  # directed pings refresh the loop
        verdict_close = sender == deputy and action == "close"
        if to != "*" and not body.strip() and action in (None, "ack", "close"):
            # An empty DM is a stand-down, not content: file no blank row.
            # A closing one still releases the loop mandate.
            if action == "close" and sender == deputy:
                self.deputy = None
            self.save()
            return {"type": "receipt", "seq": self.seq, "ack": True}
        substantive = opens_round(sender, body, self.operator) or action in ("close", "round") or (deputized and not is_courtesy(body))
        if kind == "seed" and not substantive:
            kind = "chat"

        entry = self.append(sender, to, body, kind, action, target)
        if ref is not None:
            entry["ref"] = ref
        if deputized and kind == "seed":
            # Extra delivery: the final-pinged synthesizer continues with the
            # full picture. Stamp the mandate on the seed so recipients answer
            # instead of empty-acking a continuation.
            entry["deputy"] = sender
        elif looping and kind == "chat" and not is_courtesy(body):
            # Decider's directed ping carries the mandate; the recipient answers back.
            entry["deputy"] = sender
        elif to != "*" and to == deputy and fresh and sender != deputy and kind == "chat" and not is_courtesy(body):
            # Loop answer: addressed to the decider, rendered with the decider footer.
            entry["deputy"] = deputy
            self.deputy = (deputy, time.monotonic())  # live loop keeps the mandate; expiry is for dead loops
        if entry.get("deputy") and room_round is not None and room_round.state == "synthesizing":
            self.arm_candidate_timeout()  # loop progress holds the verdict clock
        if to != "*" and kind == "chat":
            self.ping(to)  # lap leg carries the full unknown diff; the push below skips the delivered
        if kind == "response" and room_round is not None:
            self.push_live(entry, only={room_round.asker, self.operator})
        else:
            self.push_live(entry)

        if kind == "response" and room_round is not None:
            room_round.responded[sender] = entry["seq"]
            room_round.counts[sender] = room_round.counts.get(sender, 0) + 1
            self._write_safe(room_round.asker)
            self._check_complete(room_round)
            if room_round.state == "open":
                self.arm_response_timeout()  # answers in flight hold the response clock
        elif to == "*" and kind == "seed":
            self.open_round(sender, entry)
            for alias in self.round.expected:
                self.ping(alias)
        elif kind == "synthesis" and room_round is not None and sender == room_round.candidate:
            self.save()
            if action == "round" and target and room_round.chain < MAX_CHAIN:
                chain = room_round.chain + 1
                self.close_round("chained")
                self.suspend_round()  # the followup nests; the resumed outer stays stashed
                followup = self.append(sender, target, body, "seed", None, None)
                self.push_live(followup)
                if target in ("*", "all"):
                    self.open_round(sender, followup, chain=chain)
                    for alias in self.round.expected:
                        self.ping(alias)
                else:
                    if target in self.writers and target != sender:
                        self.open_round(sender, followup, chain=chain, only=[target])
                        self.ping(target)
                    # else: offline or self target — followup stays filed, no round.
            else:
                self.close_round("closed")
        self.save()
        # A verdict closes only the round it was filed on: the synthesis
        # branch above already closed (and resumed past) its round, while a
        # loop-driver close targets the round its own send just opened.
        if verdict_close and self.round is not None and (room_round is None or self.round is room_round):
            # verdict close, the decider's signature: verdict filed above, divider follows now.
            self.deputy = None
            self.close_round("decided")
        return {"type": "receipt", "seq": entry["seq"]}

    def _check_complete(self, room_round: Round) -> None:
        # Shared closure drive after any answer, visible or silent: when all
        # expected members responded, move to synthesizing with a final ping;
        # otherwise publish the waiting state.
        if all(a in room_round.responded for a in room_round.expected):
            room_round.state = "synthesizing"
            candidate = self.pick_candidate()
            if candidate is None:
                self.close_round("no-candidate")
                return
            room_round.candidate = candidate
            self.push_round_state()
            self.arm_candidate_timeout()
            self.ping(candidate, final=True)
            self.deputy = (candidate, time.monotonic())  # its next room send is the tie-break, across close
        else:
            self.push_round_state()

    def _write_safe(self, alias: str) -> None:
        writer = self.writers.get(alias)
        if writer is not None:
            self.ping(alias)


class Bus:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.rooms: Dict[str, Room] = {}
        self.load_rooms()

    def load_rooms(self) -> None:
        if not ROOMS_DIR.is_dir():
            return
        for path in sorted(ROOMS_DIR.glob("*.json")):
            try:
                room = Room.load(path)
                self.rooms.setdefault(room.name, room)
            except (json.JSONDecodeError, OSError):
                continue

    def room(self, name: str) -> Room:
        if name not in self.rooms:
            # A room may have been persisted by another router generation.
            path = ROOMS_DIR / f"{safe_name(name)}.json"
            if path.exists():
                try:
                    self.rooms[name] = Room.load(path)
                    return self.rooms[name]
                except (json.JSONDecodeError, OSError):
                    pass
            self.rooms[name] = Room(name, "")
        return self.rooms[name]

    def rooms_for(self, directory: str | None) -> list[dict]:
        self.load_rooms()  # pick up rooms written by other router runs
        return [
            {
                "name": r.name,
                "dir": r.dir,
                "cwd": r.cwd,
                "seq": r.seq,
                "participants": [p.get("alias") for p in r.participants],
                "online": r.online(),
                "operator": r.operator,
                "round": r.round.state_frame(r.name) if r.round else None,
            }
            for r in self.rooms.values()
            if (directory is None or r.dir == directory)
            # hide only bare hello-created ghosts: no dir, no roster, no traffic.
            # A freshly /new-ed room (dir set, still empty) stays visible.
            and (r.participants or r.seq > 0 or r.dir != "")
        ]

    def alias_session_dir(self, room: str, alias: str) -> Path:
        # One isolated session store per room member. The extension host
        # exposes no session id, so the router cannot learn one to `-r`;
        # instead each alias keeps exactly one session in its own dir and
        # rejoins it with `-c`, which is unambiguous there.
        return OMP_SESSIONS_BASE / safe_name(room) / safe_name(alias)

    def spawn_cmd(self, alias: str, session: str | None, session_dir: Path | None, room: str) -> str:
        endpoint = f"{self.host}:{self.port}"
        omp = "omp"
        if session:
            omp = f"omp -r {shlex.quote(session)}"
        elif session_dir is not None:
            if next(session_dir.glob("*.jsonl"), None) is not None:
                omp = f"omp --session-dir {shlex.quote(str(session_dir))} -c"
            else:
                omp = f"omp --session-dir {shlex.quote(str(session_dir))}"
        env_bus = shlex.quote(f"RAINGE_BUS={alias}@{endpoint}")
        env_room = shlex.quote(f"RAINGE_BUS_ROOM={room}")
        return f"env {env_bus} {env_room} {omp}"

    def spawn(self, room: Room, participant: dict) -> dict | None:
        # Seat exactly one alias: invite seats who you named, never the roster.
        alias = str(participant.get("alias", ""))
        if not alias or alias in room.writers:
            return None
        session_dir = self.alias_session_dir(room.name, alias)
        cmd = self.spawn_cmd(alias, participant.get("session"), session_dir, room.name)
        participant["session_dir"] = str(session_dir)
        room.save()
        subprocess.run(["tmux", "has-session", "-t", TMUX_SESSION], capture_output=True, check=False)
        subprocess.run(["tmux", "new-session", "-d", "-s", TMUX_SESSION], capture_output=True, check=False)
        subprocess.run(
            ["tmux", "kill-window", "-t", f"{TMUX_SESSION}:={alias}"],
            capture_output=True,
            check=False,
        )  # clear orphaned windows from a dead router run
        workdir = Path(room.cwd) if room.cwd else None
        if workdir is not None and (not workdir.is_absolute() or not workdir.is_dir()):
            workdir = None  # stale or forged dir: fall back to the router's cwd
        new_window = ["tmux", "new-window", "-t", TMUX_SESSION, "-n", alias]
        if workdir is not None:
            new_window += ["-c", str(workdir)]
        subprocess.Popen(
            new_window + [cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"room": room.name, "alias": alias, "cmd": cmd}

    def resume(self, room: Room) -> list[dict]:
        return [s for s in (self.spawn(room, p) for p in room.participants) if s is not None]

    def kill(self, room: Room) -> list[str]:
        killed = []
        for participant in room.participants:
            alias = str(participant.get("alias", ""))
            if not alias:
                continue
            subprocess.run(
                ["tmux", "kill-window", "-t", f"{TMUX_SESSION}:={alias}"],
                capture_output=True,
                check=False,
            )
            killed.append(alias)
        room.close_round("killed")
        return killed

    def delete_room(self, name: str, keep: asyncio.StreamWriter | None = None) -> dict:
        # Shared by panel /delete, CLI delete, and pre-hello maintenance:
        # kill members, evict sockets, drop the file, and remove orphaned
        # member sessions. Idempotent: deleting nothing still reports deleted.
        room = self.room(name)
        killed = self.kill(room)
        room.destroy(keep=keep)
        self.rooms.pop(room.name, None)
        path = ROOMS_DIR / f"{safe_name(room.name)}.json"
        if path.exists():
            path.unlink()
        member_home = OMP_SESSIONS_BASE / safe_name(room.name)
        if member_home.exists():
            shutil.rmtree(member_home, ignore_errors=True)
        return {"type": "receipt", "room": room.name, "deleted": True, "killed": killed}


    def detach_room(self, name: str) -> dict:
        # Forget semantics: the room file goes, member processes and their
        # stored sessions keep running detached for later re-seat. Unlike
        # delete_room, no windows die and no session dirs are removed.
        key = safe_name(name)
        room = self.rooms.get(key) or self.rooms.get(name)
        if room is None:
            path = ROOMS_DIR / f"{key}.json"
            if path.exists():
                path.unlink()
                return {"type": "receipt", "room": name, "detached": True}
            return {"type": "receipt", "error": f"no such room: {name}"}
        room.destroy()
        self.rooms.pop(room.name, None)
        path = ROOMS_DIR / f"{safe_name(room.name)}.json"
        if path.exists():
            path.unlink()
        return {"type": "receipt", "room": room.name, "detached": True}


class Session:
    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self.alias: str | None = None
        self.room: Room | None = None
        self.operator = False  # set by hello with operator:true (the panel)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    self.reply(writer, {"type": "receipt", "error": "malformed json"})
                    continue
                if not self.handle_frame(frame, writer):
                    break
        finally:
            self.disconnect(writer)

    def reply(self, writer: asyncio.StreamWriter, frame: dict) -> None:
        try:
            writer.write((json.dumps(frame) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def target_room(self, frame: dict) -> Room | None:
        # Strict lookup: only room_new creates. Read paths must never conjure
        # a file for a deleted room (a stale watcher resurrected zombies via
        # watch -> note_presence -> save).
        name = str(frame.get("room", "") or (self.room.name if self.room else NO_ROOM))
        room = self.bus.rooms.get(name)
        if room is None:
            path = ROOMS_DIR / f"{safe_name(name)}.json"
            if path.exists():
                try:
                    room = Room.load(path)
                except (json.JSONDecodeError, OSError):
                    room = None
                else:
                    self.bus.rooms[name] = room
        return room

    def handle_frame(self, frame: dict, writer: asyncio.StreamWriter) -> bool:
        ftype = frame.get("type")
        if self.alias is None:
            if ftype == "room_list":
                # Read-only listing is safe before hello (used by `bus_router.py list`).
                rooms = self.bus.rooms_for(frame.get("dir"))
                self.reply(writer, {"type": "rooms", "router": {"host": self.bus.host, "port": self.bus.port, "pid": os.getpid()}, "rooms": rooms, "total": len(self.bus.rooms)})
                return True
            if ftype == "room_delete":
                # Local maintenance needs no alias; the router trusts the socket.
                name = safe_name(str(frame.get("room") or ""))
                if not str(frame.get("room") or ""):
                    self.reply(writer, {"type": "receipt", "error": "no room named"})
                    return True
                self.reply(writer, self.bus.delete_room(name))
                return True
            if ftype != "hello":
                self.reply(writer, {"type": "receipt", "error": "expected hello"})
                return True
            alias = str(frame.get("alias", "")).strip()
            if not alias or alias in ("all", "*", "Main"):
                self.reply(writer, {"type": "receipt", "error": "invalid alias"})
                return True
            room = self.bus.room(str(frame.get("room", "") or NO_ROOM))
            if alias in room.writers:
                self.reply(writer, {"type": "receipt", "error": "alias in use"})
                return True
            self.alias = alias
            self.room = room
            room.writers[alias] = writer
            room.seen.add(alias)
            if frame.get("live"):
                room.live.add(alias)
            if frame.get("operator"):
                self.operator = True
                room.operator = alias
            last_seq = int(frame.get("lastSeq", 0) or 0)
            self.reply(writer, {"type": "welcome", "alias": alias, "room": room.name, "seq": room.seq})
            room.cursors[alias] = min(last_seq, room.seq)
            room.ping(alias)
            room.broadcast_presence()
            if not frame.get("operator"):
                room.note_presence(alias, True)
            return True
        if ftype == "watch":
            room = self.target_room(frame)
            if room is None:
                self.reply(writer, {"type": "receipt", "error": f"no such room: {frame.get('room', '')}"})
                return True
            if self.room is not None and self.room is not room:
                self.room.writers.pop(self.alias, None)
                self.room.live.discard(self.alias)
                if self.operator and self.room.operator == self.alias:
                    self.room.operator = None
                self.room.broadcast_presence()
            self.room = room
            fresh_watch = self.alias not in room.writers
            room.writers[self.alias] = writer
            room.seen.add(self.alias)
            room.live.add(self.alias)
            if self.operator:
                room.operator = self.alias
            last_seq = int(frame.get("lastSeq", 0) or 0)
            self.reply(writer, {"type": "welcome", "alias": self.alias, "room": room.name, "seq": room.seq})
            room.cursors[self.alias] = min(last_seq, room.seq)
            room.ping(self.alias)
            room.broadcast_presence()
            if fresh_watch and not self.operator and self.alias:
                room.note_presence(str(self.alias), True)
            return True
        if ftype == "bye":
            return False
        if "seen" in frame:
            try:
                self.room.cursors[self.alias] = max(self.room.cursors.get(self.alias, 0), int(frame["seen"]))
            except (TypeError, ValueError):
                pass
        if ftype == "list":
            room = self.target_room(frame)
            if room is None:
                self.reply(writer, {"type": "receipt", "error": f"no such room: {frame.get('room', '')}"})
                return True
            self.reply(writer, room.roster())
            return True
        if ftype == "send":
            room = self.target_room(frame)
            if room is None:
                self.reply(writer, {"type": "receipt", "error": f"no such room: {frame.get('room', '')}"})
                return True
            raw_ref = frame.get("ref")
            ref = None
            if isinstance(raw_ref, int) and not isinstance(raw_ref, bool):
                ref = raw_ref
            elif isinstance(raw_ref, str) and raw_ref.strip().isdigit():
                ref = int(raw_ref.strip())
            receipt = room.handle_send(
                self.alias,
                str(frame.get("to", "")),
                str(frame.get("body", "")),
                frame.get("action") if isinstance(frame.get("action"), str) else None,
                frame.get("target") if isinstance(frame.get("target"), str) else None,
                ref,
            )
            self.reply(writer, receipt)
            return True
        if ftype == "room_list":
            rooms = self.bus.rooms_for(frame.get("dir"))
            self.reply(writer, {"type": "rooms", "router": {"host": self.bus.host, "port": self.bus.port, "pid": os.getpid()}, "rooms": rooms, "total": len(self.bus.rooms)})
            return True
        if ftype == "kick":
            room = self.target_room(frame)
            if room is None:
                self.reply(writer, {"type": "receipt", "error": f"no such room: {frame.get('room', '')}"})
                return True
            target = room.writers.get(str(frame.get("alias", "")))
            if target is None:
                self.reply(writer, {"type": "receipt", "error": "not online"})
            else:
                target.close()
                self.reply(writer, {"type": "receipt", "kicked": True})
            return True
        if ftype == "room_detach":
            self.reply(writer, self.bus.detach_room(str(frame.get("room", ""))))
            return True
        if ftype == "participant_delete":
            room = self.target_room(frame)
            if room is None:
                self.reply(writer, {"type": "receipt", "error": f"no such room: {frame.get('room', '')}"})
                return True
            alias = str(frame.get("alias", ""))
            if room.delete_participant(alias):
                self.reply(writer, {"type": "receipt", "room": room.name, "deleted": alias})
            else:
                self.reply(writer, {"type": "receipt", "error": f"no such participant: {alias}"})
            return True
        if ftype == "room_new":
            name = safe_name(str(frame.get("name", "")))
            existing = self.bus.rooms.get(name)
            if existing is not None:
                # Connections are bound to the live Room object; keep it and
                # only update the directory, so room_new after hello does not
                # orphan registered writers.
                existing.dir = str(frame.get("dir") or existing.dir)
                existing.cwd = str(frame.get("cwd") or existing.cwd)
                existing.save()
                self.reply(writer, {"type": "receipt", "room": name, "created": False})
                return True
            self.bus.rooms[name] = Room(name, str(frame.get("dir") or ""), str(frame.get("cwd") or ""))
            self.bus.rooms[name].save()
            self.reply(writer, {"type": "receipt", "room": name, "created": True})
            return True
        if ftype == "room_list":
            rooms = self.bus.rooms_for(frame.get("dir"))
            self.reply(writer, {"type": "rooms", "router": {"host": self.bus.host, "port": self.bus.port, "pid": os.getpid()}, "rooms": rooms, "total": len(self.bus.rooms)})
            return True
        if ftype == "room_invite":
            room = self.target_room(frame)
            if room is None:
                self.reply(writer, {"type": "receipt", "error": f"no such room: {frame.get('room', '')}"})
                return True
            alias = str(frame.get("alias", "")).strip()
            if not alias:
                self.reply(writer, {"type": "receipt", "error": "invalid alias"})
                return True
            session = frame.get("session")
            session = str(session) if session else None
            room.participants = [p for p in room.participants if p.get("alias") != alias]
            participant = {"alias": alias, "session": session}
            room.participants.append(participant)
            room.seen.add(alias)
            room.save()
            spawned = [self.bus.spawn(room, participant)] if frame.get("spawn") else []
            spawned = [s for s in spawned if s is not None]
            self.reply(writer, {"type": "receipt", "room": room.name, "invited": alias, "spawned": spawned})
            return True
        if ftype == "room_resume":
            room = self.target_room(frame)
            if room is None:
                self.reply(writer, {"type": "receipt", "error": f"no such room: {frame.get('room', '')}"})
                return True
            spawned = self.bus.resume(room)
            self.reply(writer, {"type": "receipt", "room": room.name, "spawned": spawned})
            return True
        if ftype == "room_kill":
            room = self.target_room(frame)
            if room is None:
                self.reply(writer, {"type": "receipt", "error": f"no such room: {frame.get('room', '')}"})
                return True
            killed = self.bus.kill(room)
            self.reply(writer, {"type": "receipt", "room": room.name, "killed": killed})
            return True
        if ftype == "room_delete":
            name = safe_name(str(frame.get("room") or ""))
            if not name:
                self.reply(writer, {"type": "receipt", "error": "no room named"})
                return True
            self.reply(writer, self.bus.delete_room(name, keep=writer))
            return True
        self.reply(writer, {"type": "receipt", "error": "unknown frame type"})
        return True

    def disconnect(self, writer: asyncio.StreamWriter) -> None:
        if self.room is not None:
            self.room.drop_writer(writer)
        writer.close()


async def main(host: str, port: int) -> None:
    bus = Bus(host, port)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await Session(bus).handle(reader, writer)

    server = await asyncio.start_server(handle, host, port)
    print(f"rainge bus v2 listening on {host}:{port} (rooms: {len(bus.rooms)})", flush=True)
    async with server:
        await server.serve_forever()


USAGE = """rainge bus router (chat protocol v2)
  bus_router.py serve [host port] [--takeover] [--foreground]  router daemon, detached (returns input at once; stays up after you leave)
  bus_router.py <host> <port>   headless router (compat: same as serve)
  bus_router.py                 control panel (spawns a throwaway router if none runs; dies with the panel)
  bus_router.py peep [host port]  command-only control panel on a running router (observes/controls; direct chat blocked)
  bus_router.py list [host port]  rooms on the router, grouped by dir (omit host/port: the locked singleton)
  bus_router.py delete <room> [host port]  delete a room (kills members, drops transcript and sessions)
  bus_router.py watch <room>:<alias>  open a frozen copy of a member session in omp
  bus_router.py kill            kill the singleton router daemon
  (omit host/port: list, show, delete and panel use the locked singleton router)"""


async def list_rooms(host: str, port: int) -> None:
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except (ConnectionRefusedError, OSError):
        print(f"no router at {host}:{port}")
        return
    writer.write((json.dumps({"type": "room_list"}) + "\n").encode())
    await writer.drain()
    while True:
        line = await reader.readline()
        if not line:
            break
        frame = json.loads(line.decode())
        if frame.get("type") != "rooms":
            continue
        router = frame.get("router") or {}
        where = f"{router.get('host', host)}:{router.get('port', port)}"
        pid = f" pid {router.get('pid')}" if router.get("pid") else ""
        print(f"router {where}{pid}")
        rooms = frame.get("rooms") or []
        if not rooms:
            print("no rooms")
        last_dir = None
        for r in sorted(rooms, key=lambda r: (str(r.get("dir") or ""), str(r.get("name") or ""))):
            d = str(r.get("dir") or "-")
            if d != last_dir:
                print(f"dir {d}")
                last_dir = d
            rnd = r.get("round") or {}
            rstate = str(rnd.get("state", "")) if rnd else "-"
            await_ = f" awaiting={','.join(rnd.get('awaiting') or [])}" if rnd and rnd.get("awaiting") else ""
            op = f" op={r.get('operator')}" if r.get("operator") else ""
            names = ",".join(r.get("participants") or [])
            online = ",".join(r.get("online") or [])
            print(f"  {r.get('name'):<14} seq={r.get('seq', '?')} round={rstate}{await_}{op} online=[{online}] roster=[{names}]")
        break
    writer.close()

def _local_routers() -> list[tuple[str, int, int]]:
    """Running `bus_router.py serve` processes as (pid, host, port), via ps."""
    found: list[tuple[str, int, int]] = []
    try:
        proc = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, check=False)
    except OSError:
        return found
    for line in (proc.stdout or "").splitlines():
        if "bus_router.py" not in line or " list" in line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, cmd = parts[0], parts[2]
        if "serve" not in cmd and not re.search(r"\b\d{2,5}\s*$", cmd):
            continue
        host, port = "127.0.0.1", 7480
        numbers = re.findall(r"(\S+)", cmd.split("bus_router.py", 1)[1])
        numbers = [t for t in numbers if t != "serve"]
        if len(numbers) >= 2 and numbers[-1].isdigit():
            port = int(numbers[-1])
            host = numbers[-2] if not numbers[-2].isdigit() else host
        elif len(numbers) == 1 and numbers[0].isdigit():
            port = int(numbers[0])
        try:
            found.append((pid.strip(), host, port))
        except ValueError:
            continue
    return sorted(set(found))


def watch_snapshot(room: str, alias: str) -> Path | None:
    # Frozen observe-only copy of a member session. Watching the live dir
    # would fork the session under a second writer; the copy opens without
    # RAINGE_BUS env so it can never rejoin the room as a participant.
    src = OMP_SESSIONS_BASE / safe_name(room) / safe_name(alias)
    sessions = sorted(src.glob("*.jsonl"))
    if not sessions:
        print(f"no session for {room}:{alias} in {src}")
        return None
    snap = Path(tempfile.mkdtemp(prefix=f"rainge-watch-{safe_name(room)}-{safe_name(alias)}-"))
    for path in sessions:
        shutil.copy2(path, snap / path.name)
    return snap

async def delete_room_cli(name: str, host: str, port: int) -> None:
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except (ConnectionRefusedError, OSError):
        print(f"no router at {host}:{port}")
        return
    writer.write((json.dumps({"type": "room_delete", "room": name}) + "\n").encode())
    await writer.drain()
    while True:
        line = await reader.readline()
        if not line:
            break
        frame = json.loads(line.decode())
        if frame.get("type") == "receipt":
            if frame.get("error"):
                print(f"error: {frame['error']}")
            else:
                print(f"room {frame.get('room')} deleted")
            break
    writer.close()


def kill_router_daemon() -> int:
    # Singleton kill: SIGTERM the locked owner (or a lone ps-found serve),
    # escalate to SIGKILL, and drop a lock pointing at the dead pid.
    lock = read_router_lock()
    target: tuple[int, str] | None = None
    if lock_owner_live(lock):
        assert lock is not None
        target = (lock["pid"], f"{lock['host']}:{lock['port']}")
    else:
        # No live lock: the daemon is either gone or pre-lock. Stop only kills
        # what the lockfile owns; guessing among ps-found servers once killed
        # a live router the user wanted. List, don't shoot.
        others = [(p, h, pt) for p, h, pt in _local_routers() if p != str(os.getpid())]
        if others:
            print("no locked router; refusing to guess which server to kill:")
            for p, h, pt in others:
                print(f"  pid {p} {h}:{pt}")
            print("kill one by pid (kill <pid>), or restart it under the lock era")
            return 1
    if target is None:
        print("no router running")
        return 1
    pid, where = target
    print(f"killing router at {where} (pid {pid})")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.1)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    lock = read_router_lock()
    if lock is not None and lock["pid"] == pid:
        LOCK_FILE.unlink(missing_ok=True)
    print("member tmux windows left running; /join revives them")
    return 0


def _looks_like_endpoint(args: list[str]) -> bool:
    if not args:
        return False
    head = args[0]
    return head == "serve" or head.replace(".", "").isdigit() or head == "localhost"


def default_endpoint() -> tuple[str, int]:
    # Singleton default: commands without an explicit host/port talk to the
    # locked router. Explicit args always win; stale lock falls back to 7480.
    lock = read_router_lock()
    if lock_owner_live(lock):
        assert lock is not None
        return lock["host"], lock["port"]
    return "127.0.0.1", 7480


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        if args and args[0] in ("-h", "--help", "help"):
            print(USAGE)
        elif args and args[0] == "watch":
            target = args[1] if len(args) > 1 else ""
            room, sep, alias = target.partition(":")
            if not sep or not room or not alias:
                print("usage: bus_router.py watch <room>:<alias>")
            else:
                snap = watch_snapshot(room, alias)
                if snap is not None:
                    print(f"watching frozen copy of {room}:{alias} in {snap}")
                    os.execvp("omp", ["omp", "--session-dir", str(snap), "-c"])
        elif args and args[0] == "list":
            positional = list(args[1:])
            if positional and positional[0].startswith("-"):
                print("usage: bus_router.py list [host port]")
            else:
                host, port = default_endpoint()
                if positional:
                    host = positional[0]
                    port = int(positional[1]) if len(positional) > 1 else 7480
                asyncio.run(list_rooms(host, port))
        elif args and args[0] == "delete":
            name = args[1] if len(args) > 1 else ""
            rest = args[2:]
            if not name:
                print("usage: bus_router.py delete <room> [host port]")
            else:
                host, port = default_endpoint()
                if rest:
                    host = rest[0]
                    port = int(rest[1]) if len(rest) > 1 else 7480
                asyncio.run(delete_room_cli(name, host, port))
        elif args and args[0] == "kill":
            if len(args) > 1:
                print("usage: bus_router.py kill")
            else:
                raise SystemExit(kill_router_daemon())
        elif args and args[0] == "peep":
            positional = [arg for arg in args if arg != "peep"]
            host, port = default_endpoint()
            if positional:
                host = positional[0]
                port = int(positional[1]) if len(positional) > 1 else 7480
            from bus_panel import run_panel
            run_panel(host, port, allow_spawn=False, allow_chat=False)
        elif _looks_like_endpoint(args):
            positional = [arg for arg in args if arg not in ("serve", "--takeover", "--foreground")]
            host = positional[0] if positional else "127.0.0.1"
            port = int(positional[1]) if len(positional) > 1 else 7480
            takeover = "--takeover" in args
            lock = read_router_lock()
            if lock_owner_live(lock) and not takeover:
                where = f"{lock['host']}:{lock['port']} (pid {lock['pid']})"
                if (lock["host"], lock["port"]) == (host, port):
                    print(f"router already serves {where} — refusing a second instance (singleton)")
                else:
                    print(f"router already runs at {where} — refusing a second instance (singleton); pass --takeover to replace it")
                raise SystemExit(1)
            if takeover and lock_owner_live(lock):
                print(f"taking over from router at {lock['host']}:{lock['port']} (pid {lock['pid']}) — re-invite members, they stay bound to the old endpoint")
                try:
                    os.kill(lock["pid"], signal.SIGTERM)
                except OSError:
                    pass
                for _ in range(50):
                    try:
                        os.kill(lock["pid"], 0)
                    except OSError:
                        break
                    time.sleep(0.1)
            for pid, rhost, rport in _local_routers():
                if rport != port:
                    print(f"another router runs (pid {pid} {rhost}:{rport}) — rooms share files only; live state is per-router")
            if "--foreground" not in args:
                pid = daemonize(ROUTER_LOG)
                if pid > 0:
                    print(f"router daemon at {host}:{port} (pid {pid}), log {ROUTER_LOG}")
                    raise SystemExit(0)
            take_router_lock(host, port)
            asyncio.run(main(host, port))
        else:
            from bus_panel import run_panel

            positional = list(args)
            host, port = default_endpoint()
            if positional:
                host = positional[0]
                port = int(positional[1]) if len(positional) > 1 else 7480
            run_panel(host, port)
    except KeyboardInterrupt:
        pass
