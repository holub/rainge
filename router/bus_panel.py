"""Rainge bus control panel v2: pure chat — transcript, rounds, diffs.

Input-first chat over the room transcript. Plain text + Enter broadcasts to
the room, opening a round: every online participant answers; answers stream
back to you; when all have answered the router picks a synthesizer (marked
in the roster) who closes the round or chains a follow-up. `@alias text`
direct-messages one participant. The router — not any participant — routes.

Input:   <text> + Enter          broadcast to the room (opens a round)
         @alias <text> + Enter   direct message to one participant
         Esc / Ctrl+C             clear the input
Commands: /help /new <id> /join [id] /add [alias] /kick <alias>
          /r[oster] /q[uit] /bye  (quit leaves; members die only with a room you joined —
                                  a watched room is left alone; they return on /join)
Tab:      complete @aliases and /commands
Scroll:   Ctrl+N / Ctrl+P / PgDn / PgUp / Ctrl+G bottom
Detail:   Ctrl+O shows the full message, Esc closes
"""

from __future__ import annotations

import curses
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from bus_router import OMP_SESSIONS_BASE, ROOMS_DIR, read_router_lock, safe_name

# The operator seats under their real login name so participants address a
# person, not a component. PANEL_ALIAS stays as the headless fallback.
OPERATOR_ALIAS = os.environ.get("USER") or "panel"
PANEL_ALIAS = OPERATOR_ALIAS
NO_ROOM = "foyer"
BODY_INLINE_CAP = 6  # body lines shown per entry before the "Ctrl+O for full" hint

COMMANDS = {
    "/help": "list commands",
    "/new <id>": "create a room for this directory and join it",
    "/join [id]": "pick a room or join one by id (revives its participants); DEL forgets the room file, members stay detached",
    "/add [alias]": "seat a participant: pick a stored session or a fresh instance; DEL on a stored row deletes it and unseats everywhere",
    "/kick <alias>": "disconnect an alias",
    "/r[oster]": "refresh roster and round state",
    "/rm [id]": "delete a room (kills its participants, clears transcript and roster)",
    "/exit /quit /q /bye": "close the panel; room and participants stay connected",
    "/evict": "kill the joined room's members and close the panel (a watched room is left alone)",
    "/verbose": "toggle transcript chrome (dividers, #seq, join/leave lines)",
}


def available_commands(st: PanelState) -> dict[str, str]:
    # Context gates: a command that cannot act hides everywhere (help,
    # completion) and refuses at execution with a pointer to what acts.
    # No rooms: no /join, no /rm. Empty room: no /kick. Foyer: no /add, no /verbose.
    cmds = dict(COMMANDS)
    if not st.rooms and not st.rooms_total:
        cmds.pop("/join [id]", None)
        cmds.pop("/rm [id]", None)
    if not st.online:
        cmds.pop("/kick <alias>", None)
    if st.room == NO_ROOM:
        cmds.pop("/add [alias]", None)
        cmds.pop("/verbose", None)
    return cmds


class QuitPanel(Exception):
    pass


def dir_slug(cwd: str) -> str:
    home = str(Path.home())
    if cwd == home:
        return "-"
    if cwd.startswith(home + os.sep):
        return "-" + os.path.relpath(cwd, home).replace(os.sep, "-")
    return cwd.replace(os.sep, "-")


ROOMS_DIR = Path.home() / ".rainge" / "rooms"  # mirrors bus_router.ROOMS_DIR
LAST_ROOM_FILE = Path.home() / ".rainge" / "last_room.json"


def _last_room(slug: str) -> str:
    # The room this directory last watched; default when unknown or invalid.
    try:
        name = str(json.loads(LAST_ROOM_FILE.read_text()).get(slug, NO_ROOM))
    except (OSError, ValueError, AttributeError):
        return NO_ROOM
    if name and all(ch.isalnum() or ch in "-_" for ch in name):
        return name[:48]
    return NO_ROOM


def _remember_room(slug: str, room: str) -> None:
    try:
        try:
            data = json.loads(LAST_ROOM_FILE.read_text())
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data[slug] = room
        LAST_ROOM_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_ROOM_FILE.write_text(json.dumps(data))
    except OSError:
        pass


def session_preview(path: str, size: int) -> tuple[str, bool]:
    rainge = False
    preview = ""
    try:
        with open(path, "rb") as handle:
            head = handle.read(65536)
            tail = b""
            if size > 65536:
                handle.seek(max(0, size - 262144))
                tail = handle.read(262144)
        rainge = b"dev.rainge.state.v1" in head + tail
        for line in head.decode(errors="replace").splitlines():
            if '"role":"user"' not in line.replace(" ", ""):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (obj.get("message") or {}).get("content")
            texts: list[str] = []
            if isinstance(content, str):
                texts = [content]
            elif isinstance(content, list):
                texts = [
                    str(b.get("text", ""))
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
            snippet = " ".join(" ".join(texts).split())[:60]
            if snippet:
                preview = snippet
                break
    except OSError:
        pass
    return preview, rainge


def default_alias_for(stem: str) -> str:
    if "T" in stem:
        clock = stem.split("T", 1)[1][:8].replace("-", "")
        if len(clock) == 6:
            return "s" + clock
    return stem[-6:]


def discover_member_sessions(room: str, base: Path | None = None) -> list[dict]:
    # Rainge's own separate store: one dir per member alias, holding that
    # alias's session. Native operator sessions live elsewhere and seat
    # foreign history; these rows re-seat ours.
    root = (base or OMP_SESSIONS_BASE) / safe_name(room)
    if not root.is_dir():
        return []
    found = []
    for alias_dir in sorted(root.iterdir()):
        if not alias_dir.is_dir():
            continue
        sessions = sorted(alias_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not sessions:
            continue
        path = sessions[-1]
        stat = path.stat()
        found.append(
            {
                "alias": alias_dir.name,
                "file": str(path),
                "name": path.stem,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
            }
        )
    return sorted(found, key=lambda item: -item["mtime"])


def room_membership(rooms_base: Path | None = None) -> dict[tuple[str, str], set[str]]:
    # Which rooms already seat this identity — by alias, exact session file,
    # or member session dir — so the picker shows double-seating at a glance.
    root = rooms_base or ROOMS_DIR
    index: dict[tuple[str, str], set[str]] = {}
    if not root.is_dir():
        return index
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        room = str(data.get("name") or path.stem)
        parts = data.get("participants")
        if not isinstance(parts, list):
            continue
        for participant in parts:
            if not isinstance(participant, dict):
                continue
            for kind in ("alias", "session", "session_dir"):
                value = participant.get(kind)
                if value:
                    index.setdefault((kind, str(value)), set()).add(room)
    return index


class BusClient:
    """Threaded NDJSON client: a reader thread appends parsed frames."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.frames: list[dict] = []
        self.lock = threading.Lock()
        self.connected = False
        self.sock: socket.socket | None = None
        self.gen = 0  # connection generation: a stale reader drops only its own

    def connect(self, room: str = NO_ROOM) -> None:
        self._drop()
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        self.sock.settimeout(None)
        self.gen += 1
        gen = self.gen
        self.send({"type": "hello", "alias": PANEL_ALIAS, "room": room, "live": True, "operator": True})
        self.connected = True
        threading.Thread(target=self._read_loop, args=(gen,), daemon=True).start()

    def send(self, frame: dict) -> None:
        if self.sock is None:
            return
        try:
            self.sock.sendall((json.dumps(frame) + "\n").encode())
        except OSError:
            self.connected = False

    def _drop(self) -> None:
        self.connected = False
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _read_loop(self, gen: int) -> None:
        sock = self.sock
        assert sock is not None
        pending = ""
        while True:
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            pending += chunk.decode(errors="replace")
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                with self.lock:
                    self.frames.append(parsed)
        if gen == self.gen:
            self._drop()


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def reap_child(child: subprocess.Popen | None) -> None:
    if child is None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def run_panel(host: str, port: int, allow_spawn: bool = True, allow_chat: bool = True) -> None:
    child: subprocess.Popen | None = None
    if not port_open(host, port):
        lock = read_router_lock() if host in ("127.0.0.1", "localhost") else None
        if lock is not None and (lock["host"], lock["port"]) != (host, port) and port_open(lock["host"], lock["port"]):
            print(f"router runs at {lock['host']}:{lock['port']} (pid {lock['pid']}) — attaching there (singleton)")
            host, port = lock["host"], lock["port"]
        elif not allow_spawn:
            print(f"no router at {host}:{port} (attach only)")
            return
        else:
            print(f"no router at {host}:{port} — starting one (it dies with this panel)")
            router = __file__.replace("bus_panel.py", "bus_router.py")
            child = subprocess.Popen(
                [sys.executable, router, "serve", host, str(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(40):
                if port_open(host, port):
                    break
                time.sleep(0.25)
    client = BusClient(host, port)
    try:
        client.connect()
    except OSError as exc:
        print(f"cannot attach to {host}:{port}: {exc}")
        reap_child(child)
        return
    try:
        curses.wrapper(_draw, client, host, port, allow_chat)
    finally:
        # A router this panel spawned is a throwaway: reap it. An attached
        # daemon (serve / singleton redirect) is never ours to kill, so /q
        # leaves it running.
        reap_child(child)


class PanelState:
    def __init__(self) -> None:
        self.room = NO_ROOM
        self.explicit_room: str | None = None  # set only by /join — implicit watches don't count
        self.allow_chat = True  # show mode observes/controls: commands only, no direct chat
        self.rooms: list[dict] = []
        self.rooms_total = 0
        self.online: list[str] = []
        self.awaiting: list[str] = []
        self.candidate: str | None = None
        self.round_state = ""
        self.status = "type to chat · /help for commands"
        self.verbose = True  # /verbose hides transcript chrome (dividers, #seq, join/leave)
        self.input = ""
        self.cursor = 0
        self.pending: dict | None = None  # invite alias state
        self.invite_preset = ""
        self.offset = 0
        self.at_bottom = True
        self.pick_items: list[dict] = []
        self.pick_sel = 0
        self.pick_kind = ""
        self.history: list[str] = []
        self.history_index: int | None = None
        self.history_draft = ""
        self.detail: dict | None = None  # entry open in the full-body overlay
        self.detail_off = 0


def _watch(client: BusClient, state: PanelState, room: str) -> None:
    state.room = room
    state.entries = []
    state.online = []
    state.awaiting = []
    state.candidate = None
    state.round_state = ""
    state.detail = None
    state.detail_off = 0
    client.send({"type": "watch", "room": room})
    client.send({"type": "list", "room": room})
    client.send({"type": "room_list", "dir": dir_slug(os.getcwd())})
    _remember_room(dir_slug(os.getcwd()), room)


def _join_room(client: BusClient, state: PanelState, room: str) -> None:
    _watch(client, state, room)
    state.explicit_room = room
    client.send({"type": "room_resume", "room": room})
    state.status = f"joined room {room}; participants reviving"


def _absorb(st: PanelState, frame: dict) -> bool:
    ftype = frame.get("type")
    if ftype == "diff":
        for entry in frame.get("entries", []):
            seq = entry.get("seq")
            if seq is not None and st.entries and seq <= st.entries[-1].get("seq", 0):
                continue
            st.entries.append(entry)
    elif ftype == "presence":
        if frame.get("room", st.room) == st.room:
            st.online = list(frame.get("online", []))
    elif ftype == "roster":
        if frame.get("room", st.room) == st.room:
            st.online = list(frame.get("participants", []))
            round = frame.get("round") or None
            if round:
                st.awaiting = list(round.get("awaiting", []))
                st.candidate = round.get("candidate")
                st.round_state = str(round.get("state", ""))
            else:
                st.awaiting, st.candidate, st.round_state = [], None, ""
    elif ftype == "round":
        if frame.get("room", st.room) == st.room:
            st.awaiting = list(frame.get("awaiting", []))
            st.candidate = frame.get("candidate")
            st.round_state = str(frame.get("state", ""))
            if frame.get("state") == "closed":
                st.status = f"round closed ({frame.get('reason', '?')})"
    elif ftype == "rooms":
        st.rooms = list(frame.get("rooms", []))
        st.rooms_total = int(frame.get("total") or len(st.rooms))
    elif ftype == "receipt":
        if frame.get("error"):
            st.status = f"error: {frame['error']}"
            if "no such room" in str(frame["error"]):
                return True
        elif frame.get("seq") is not None:
            st.status = f"sent (seq {frame['seq']})"
        spawned = frame.get("spawned")
        if isinstance(spawned, list) and spawned:
            st.status = f"spawned {len(spawned)}: {', '.join(s.get('alias', '') for s in spawned)}"
    return False

def _send_chat(client: BusClient, st: PanelState, text: str) -> bool:
    if not client.connected:
        st.status = "not sent — disconnected, reconnecting… (text kept, Enter retries)"
        return False
    if (
        st.room == NO_ROOM
        and st.explicit_room != NO_ROOM
        and not any(r.get("name") == NO_ROOM for r in st.rooms)
    ):
        st.status = "not sent — no room joined (/join a room or /new one first)"
        return False
    if text.startswith("@") and " " in text:
        alias, body = text[1:].split(" ", 1)
        if alias != "all":
            client.send({"type": "send", "room": st.room, "to": alias, "body": body})
            st.status = f"sent to {alias}"
            return True
    lowered = text.strip().lower()
    if lowered.startswith(("invite ", "add ")) or lowered in ("invite", "add"):
        client.send({"type": "send", "room": st.room, "to": "*", "body": text})
        st.status = "sent as broadcast — did you mean /add (slash command)?"
        return True
    if " " not in text.strip() and any(text.strip().lower() == a.lower() for a in st.online):
        client.send({"type": "send", "room": st.room, "to": "*", "body": text})
        st.status = f"broadcast as round — did you mean @{text.strip()} <msg> or /add {text.strip()}?"
        return True
    client.send({"type": "send", "room": st.room, "to": "*", "body": text})
    st.status = "round opened — awaiting answers"
    return True


def _entry_prefix(entry: dict, verbose: bool = True) -> str:
    seq = str(entry.get("seq", ""))
    ts = str(entry.get("ts", ""))
    src = str(entry.get("from", ""))
    dst = str(entry.get("to", ""))
    kind = str(entry.get("kind", ""))
    mark = {"seed": "◆", "response": "·", "synthesis": "✓"}.get(kind, " ")
    arrow = f"@{dst}" if dst not in ("*", "all", "") else ""
    seq_slot = f"#{seq:<4}" if verbose else " " * 5
    return f" {ts} {seq_slot}{mark}{src:<10.10}{arrow:<8.8} "


def _fold(head: str, text: str, width: int, indent: str | None = None) -> list[str]:
    # Fold one physical line to the available columns: the head (prefix)
    # rides the first chunk, later chunks keep the indent so wrapped text
    # stays readable. width <= 0 keeps the old no-wrap flow.
    if width <= 0 or len(head) + len(text) <= width:
        return [head + text]
    if len(head) >= width:
        # Degenerate (tiny terminal): head rides solo, text folds full-width.
        return [head[:width]] + [text[i:i + width] for i in range(0, len(text), width)]
    ind = head if indent is None else indent
    room = width - len(head)
    parts = [text[i:i + room] for i in range(0, len(text), room)] or [""]
    return [head + parts[0]] + [ind + p for p in parts[1:]]


def _entry_rows(entry: dict, verbose: bool = True, width: int = 0) -> list[str]:
    """One entry -> screen rows: first line prefixed, up to
    BODY_INLINE_CAP continuation lines indented, then a hint row.
    System join/leave lines render aligned as ~~~ body ~~~; dividers
    stretch to the full width."""
    if entry.get("kind") == "system":
        body = str(entry.get("body", ""))
        prefix = _entry_prefix(entry, verbose)
        core, sep, tail = body.partition("#")
        suffix = ""
        if sep and tail.strip().isdigit() and core.strip() != "" and core.strip("-─ ") == "":
            suffix = " #" + tail.strip()  # divider naming its closed round
            body = core
        if body.strip("-─ ") == "":
            filler = max(len(body), width - 1 - len(prefix) - len(suffix)) if width > 0 else len(body)
            return [prefix + "-" * filler + suffix]
        return [prefix + f"~~~ {body} ~~~"]
    prefix = _entry_prefix(entry, verbose)
    pad = " " * len(prefix)
    lines = str(entry.get("body", "")).split("\n")
    rows = _fold(prefix, lines[0], width, pad)
    for cont in lines[1:BODY_INLINE_CAP]:
        rows.extend(_fold(pad, cont, width))
    if len(lines) > BODY_INLINE_CAP:
        rows.append(pad + f"… +{len(lines) - BODY_INLINE_CAP} lines (Ctrl+O for full)")
    ref = entry.get("ref")
    if verbose and isinstance(ref, int) and not isinstance(ref, bool):
        tag = f"↳#{ref}"
        if width > 0 and width - len(rows[-1]) - len(tag) >= 1:
            rows[-1] += " " * (width - len(rows[-1]) - len(tag)) + tag
        else:
            rows[-1] += f" {tag}"
    return rows


def _open_detail(st: PanelState) -> None:
    target = None
    for entry in reversed(st.entries):
        if len(str(entry.get("body", "")).split("\n")) > BODY_INLINE_CAP:
            target = entry
            break
    if target is None and st.entries:
        target = st.entries[-1]
    st.detail = target
    st.detail_off = 0
    if target is not None:
        st.status = "full message — Esc closes, ↑↓/PgUp/PgDn scroll"


def _draw_detail(screen: "curses._CursesWindow", st: PanelState, width: int) -> None:
    entry = st.detail
    height = screen.getmaxyx()[0]
    if entry is None:
        return
    title = f" #{entry.get('seq', '')} {entry.get('from', '')} → {entry.get('to', '')} "
    lines = str(entry.get("body", "")).split("\n") or ["(empty)"]
    footer_top = max(1, height - 4)
    bottom = max(2, footer_top - 1)
    max_rows = max(1, bottom - 3)
    left = 1
    right = max(left + 4, width - 2)
    inner_width = right - left - 1
    wrapped: list[str] = []
    for ln in lines:
        wrapped.extend(ln[i:i + inner_width] for i in range(0, max(1, len(ln)), inner_width))
    st.detail_off = max(0, min(st.detail_off, max(0, len(wrapped) - max_rows)))
    rows = min(len(wrapped), max_rows)
    top = max(1, bottom - rows - 2)
    label = title[:inner_width]
    top_fill = max(0, inner_width - len(label))
    top_line = f"┌{'─' * (top_fill // 2)}{label}{'─' * (top_fill - top_fill // 2)}┐"
    screen.addstr(top, left, top_line[: right - left + 1])
    for row in range(rows):
        line = wrapped[st.detail_off + row].ljust(inner_width)
        screen.addstr(top + 1 + row, left, "│")
        screen.addstr(top + 1 + row, left + 1, line)
        screen.addstr(top + 1 + row, right, "│")
    dim = getattr(curses, "A_DIM", curses.A_NORMAL)
    hint_line = "Esc closes · ↑↓/PgUp/PgDn scroll"[:inner_width].ljust(inner_width)
    screen.addstr(top + 1 + rows, left, "│")
    screen.addstr(top + 1 + rows, left + 1, hint_line, dim)
    screen.addstr(top + 1 + rows, right, "│")
    screen.addstr(bottom, left, f"└{'─' * inner_width}┘"[: right - left + 1])


def _run_command(client: BusClient, st: PanelState, text: str) -> None:
    parts = text.split()
    cmd = (parts[0] or "").lower()
    arg = " ".join(parts[1:])
    slug = dir_slug(os.getcwd())
    gate = {
        "/join": ("no rooms yet — /new <id> creates one", bool(st.rooms or st.rooms_total)),
        "/rm": ("no rooms yet — /new <id> creates one", bool(st.rooms or st.rooms_total)),
        "/kick": ("nobody here — the room is empty", bool(st.online)),
        "/add": ("no room joined — /join one first (or /new <id>)", st.room != NO_ROOM),
        "/verbose": ("nothing to show — join a room first", st.room != NO_ROOM),
    }
    if cmd in gate:
        hint, ok = gate[cmd]
        if not ok:
            st.status = hint
            return
    if cmd in ("/exit", "/q", "/quit", "/bye"):
        # Detach: the UI closes, the room and its members stay on the bus.
        raise QuitPanel
    if cmd == "/evict":
        if st.room != NO_ROOM and st.explicit_room == st.room:
            client.send({"type": "room_kill", "room": st.room})
        raise QuitPanel
    if cmd == "/help":
        st.pick_kind = "help — Enter runs or loads command"
        st.pick_items = [
            {"label": f"{name:<20} {what}", "command": name}
            for name, what in available_commands(st).items()
        ]
        st.pick_sel = 0
    elif cmd == "/new" and arg:
        name = safe_name(arg)
        client.send({"type": "room_new", "name": name, "dir": slug, "cwd": os.getcwd()})
        _join_room(client, st, name)
        st.status = f"room {name} created and joined"
    elif cmd == "/join":
        if arg:
            _join_room(client, st, arg)
        else:
            client.send({"type": "room_list", "dir": slug})
            items = [
                {"label": r["name"], "room": r["name"]} for r in st.rooms
            ]
            if not items:
                st.status = f"no rooms for dir '{slug}' — router knows {st.rooms_total} elsewhere — /new <name> creates here"
            else:
                st.pick_kind, st.pick_items, st.pick_sel = "join", items, 0
    elif cmd == "/add":
        st.invite_preset = arg
        _open_invite_picker(st)
    elif cmd == "/kick" and arg:
        client.send({"type": "kick", "room": st.room, "alias": arg})
        st.status = f"kick sent: {arg}"
    elif cmd in ("/roster", "/r", "/r[oster]"):
        client.send({"type": "list", "room": st.room})
        client.send({"type": "room_list", "dir": slug})
    elif cmd == "/verbose":
        st.verbose = not st.verbose
        st.status = f"verbose {'on' if st.verbose else 'off'} — chrome {'shown' if st.verbose else 'hidden'}"
    elif cmd == "/rm":
        if arg:
            client.send({"type": "room_delete", "room": arg})
            if arg == st.room:
                _watch(client, st, NO_ROOM)
            st.status = f"room {arg} deleted"
        else:
            client.send({"type": "room_list", "dir": slug})
            items = [{"label": r["name"], "room": r["name"]} for r in st.rooms] or [
                {"label": f"(no rooms for dir '{slug}' — {st.rooms_total} elsewhere)", "room": None}
            ]
            st.pick_kind, st.pick_items, st.pick_sel = "rm", items, 0
    else:
        st.status = f"unknown or incomplete: {text} — /help lists commands"


def _open_invite_picker(st: PanelState) -> None:
    members = {m["alias"]: m for m in discover_member_sessions(st.room)}
    seated = room_membership()

    def rooms_bit(rooms: set[str]) -> str:
        if not rooms:
            return ""
        word = "room" if len(rooms) == 1 else "rooms"
        return f" [in {len(rooms)} {word}: {', '.join(sorted(rooms))[:40]}]"

    preset_home = st.invite_preset and st.invite_preset in members
    items = [
        {
            "label": "+ start a fresh omp instance (new session)",
            "session": None,
            "hint": (
                f"resumes {st.invite_preset}'s stored session"
                if preset_home
                else "spawns a brand-new omp, empty history"
            ),
        }
    ]
    for member in members.values():
        preview, _rainge = session_preview(member["file"], member["size"])
        stamp = time.strftime("%m-%d %H:%M", time.localtime(member["mtime"]))
        size = member["size"] // 1024
        rooms = set(seated.get(("alias", member["alias"]), ())) | set(
            seated.get(("session_dir", str(Path(member["file"]).parent)), ())
        )
        items.append(
            {
                "label": f"{stamp} {size:>5}K ◈ {member['alias']:<10.10} {preview or '(stored session)'}{rooms_bit(rooms)}",
                "session": member["file"],
                "member": member["alias"],
                "hint": f"resumes {member['alias']}'s stored session{rooms_bit(rooms)}",
            }
        )
    st.pick_kind = "add: pick the omp session to seat as a participant"
    st.pick_items = items
    st.pick_sel = 0


def _draw_frame(screen, title: str, width: int, bottom: int) -> None:
    # Border around the chat log in the overlay box style; the status title
    # rides the top edge while the footer (status/roster/input) lives below
    # `bottom` full-bleed. Log rows draw inside at x=2, last inner column width-2.
    label = f" {title} "[: max(0, width - 2)]
    fill = max(0, width - 2 - len(label))
    screen.addstr(0, 0, "┌")
    screen.addstr(0, 1, label, curses.A_REVERSE)
    screen.addstr(0, 1 + len(label), "─" * fill + "┐")
    for row in range(1, bottom):
        screen.addstr(row, 0, "│")
        screen.addstr(row, width - 1, "│")
    try:
        screen.addstr(bottom, 0, "└" + "─" * max(0, width - 2) + "┘")
    except curses.error:
        # Bottom-right cell cannot be written (cursor would scroll);
        # drop the corner, keep the rule.
        screen.addstr(bottom, 0, ("└" + "─" * max(0, width - 2))[: max(0, width - 1)])

def _rooms_bar(rooms: list[dict], current: str, total: int) -> str:
    # The header already names the watched room ([room]); the bar lists
    # siblings only, so the active room never appears twice.
    if not rooms:
        return f"(no rooms here — {total} on router — /new)"
    others = " ".join(r["name"] for r in rooms if r["name"] != current)
    return others or "(only this room — /new for more)"

def _draw_input(screen: "curses._CursesWindow", st: PanelState, row: int, width: int, left: int = 0) -> None:
    prompt = (st.pending or {}).get("label", "")
    label = f" {prompt}> "
    start = left + min(len(label), max(0, width - left - 2))
    screen.addstr(row, left, label[: start - left], curses.A_BOLD)
    available = max(0, width - start - 2)
    st.cursor = max(0, min(st.cursor, len(st.input)))
    first = max(0, st.cursor - available)
    shown = st.input[first : first + available]
    cursor_offset = st.cursor - first
    if shown:
        screen.addstr(row, start, shown[:cursor_offset], curses.A_BOLD)
    cursor_col = start + cursor_offset
    if cursor_col < width - 1:
        cursor_char = st.input[st.cursor] if st.cursor < len(st.input) else " "
        screen.addstr(row, cursor_col, cursor_char, curses.A_BOLD | curses.A_REVERSE)
        if st.cursor < len(st.input):
            tail = st.input[st.cursor + 1 : first + available]
            screen.addstr(row, cursor_col + 1, tail[: width - cursor_col - 2], curses.A_BOLD)
    screen.move(row, min(cursor_col, max(0, width - 2)))


WAITING_SPINNER = ("|", "/", "-", "\\")

def _draw(
    screen: "curses._CursesWindow",
    client: BusClient,
    host: str,
    port: int,
    allow_chat: bool = True,
) -> None:
    screen.timeout(150)
    st = PanelState()
    st.allow_chat = allow_chat
    slug = dir_slug(os.getcwd())
    start = _last_room(slug)
    if start != NO_ROOM and not (ROOMS_DIR / f"{start}.json").exists():
        start = NO_ROOM  # remembered room was deleted; do not resurrect it
    _watch(client, st, start)
    if start != NO_ROOM:
        st.status = f"rejoined {start}"
    auto_resume_done = False
    room_list_seen = False
    consumed = 0
    tick = 0
    while True:
        with client.lock:
            fresh = client.frames[consumed:]
            consumed = len(client.frames)
        for frame in fresh:
            if _absorb(st, frame):
                _watch(client, st, NO_ROOM)
                st.status = f"room is gone — back to no room (/join or /new)"
            room_list_seen = room_list_seen or frame.get("type") == "rooms"
        tick += 1
        if not client.connected:
            st.status = "disconnected — reconnecting… (Ctrl+C clears input)"
            if tick % 13 == 0:
                try:
                    client.connect(st.room)
                    _watch(client, st, st.room)
                    st.status = f"reconnected — watching {st.room}"
                except OSError:
                    pass
        if room_list_seen and not auto_resume_done:
            auto_resume_done = True
            candidates = [r for r in st.rooms if r.get("participants")]
            if st.room == NO_ROOM and len(candidates) == 1:
                _watch(client, st, str(candidates[0].get("name", NO_ROOM)))
            # Attach is observe-only: revival happens only on explicit /join.
        if st.at_bottom:
            st.offset = 0

        height, width = screen.getmaxyx()
        header = 1
        footer = 4
        body_height = max(1, height - header - footer)
        try:
            key = screen.getch()
        except curses.error:
            key = -1

        if st.pick_items:
            if not _handle_pick(client, st, key):
                return
        else:
            if not _handle_input(client, st, key):
                return

        if height < 5 or width < 12:
            screen.erase()
            try:
                screen.addstr(0, 0, "terminal too small (need 12x5) - resize or /q")
                screen.refresh()
            except curses.error:
                pass
            continue

        try:
            screen.erase()
            rooms = list(st.rooms)
            if all(r["name"] != st.room for r in rooms):
                rooms.append({"name": st.room})  # server dir-filter can omit the watched room (e.g. pre-stamp rooms with dir "")
            rooms_bar = _rooms_bar(rooms, st.room, st.rooms_total)
            title = f"RaiNGE bus [{st.room}] {host}:{port} · {os.getcwd()} · rooms: {rooms_bar}"
            framed = width >= 12 and height >= 5
            foot = height - footer
            lx, vis, row_width = (2, width - 5, width - 4) if framed else (0, width - 1, width)
            if framed:
                _draw_frame(screen, title, width, foot)
            else:
                screen.addstr(0, 0, f" {title}"[: width - 1], curses.A_REVERSE)
            visible = st.entries[: len(st.entries) - st.offset] if st.offset else st.entries
            rows: list[str] = []
            for entry in visible:
                if not st.verbose and entry.get("kind") == "system":
                    continue
                rows.extend(_entry_rows(entry, st.verbose, vis))
            chat_start = header + 1 if framed else header
            chat_rows = max(1, body_height - 2) if framed else body_height
            for row, line in enumerate(rows[-chat_rows:], start=chat_start):
                screen.addstr(row, lx, line[:vis])
            screen.addstr(foot + 1, 0, f" {st.status}"[: width - 1])
            glyph = WAITING_SPINNER[tick % len(WAITING_SPINNER)] if (st.awaiting or st.candidate) else ""
            roster = ", ".join(
                (f"{glyph}{alias}" if alias in st.awaiting else f"{alias}⎇" if alias == st.candidate else alias)
                for alias in st.online
            ) or "(none)"
            round_note = f"round {st.round_state}" if st.round_state else "no round"
            screen.addstr(foot + 2, 0, f" online: {roster} · {round_note}"[: width - 1])
            if st.pick_items:
                _draw_pick(screen, st, width)
            if st.detail is not None:
                _draw_detail(screen, st, width)
            _draw_input(screen, st, foot + 3, width, 0)
            screen.refresh()
        except curses.error:
            # Resize race or undersized terminal mid-frame: drop this frame.
            # The next tick re-reads the size and redraws; input handling
            # above already ran, so /q still works while small.
            continue


def _remember_input(st: PanelState, text: str) -> None:
    if text and (not st.history or st.history[-1] != text):
        st.history.append(text)
        del st.history[:-100]
    st.history_index = None
    st.history_draft = ""


def _history_move(st: PanelState, direction: int) -> None:
    if not st.history or (direction > 0 and st.history_index is None):
        return
    if st.history_index is None:
        st.history_draft = st.input
        st.history_index = len(st.history)
    next_index = st.history_index + direction
    if next_index < 0:
        next_index = 0
    if next_index >= len(st.history):
        st.history_index = None
        st.input = st.history_draft
        st.cursor = len(st.input)
        st.history_draft = ""
        return
    st.history_index = next_index
    st.input = st.history[next_index]
    st.cursor = len(st.input)


def _complete_input(st: PanelState) -> None:
    text = st.input
    if text.startswith("@") and " " not in text:
        matches = [a for a in sorted(st.online) if a != PANEL_ALIAS and a.startswith(text[1:])]
        if matches:
            st.input = "@" + matches[0] + " "
            st.cursor = len(st.input)
            st.status = f"matches: {', '.join(matches[:8])}"
        else:
            st.status = "no alias matches"
        return
    if text.startswith("/") and " " not in text:
        names = sorted({name.split()[0] for name in available_commands(st)} | {"/q", "/quit", "/bye", "/r"})
        matches = [n for n in names if n.startswith(text)]
        if len(matches) == 1:
            st.input = matches[0] + " "
            st.cursor = len(st.input)
        else:
            st.status = f"matches: {', '.join(matches[:8])}"
        return
    st.status = "no completion for current input"


def _handle_input(client: BusClient, st: PanelState, key: int) -> bool:
    if st.detail is not None:
        if key in (27, 10, 13, 15):
            st.detail = None
            st.detail_off = 0
            return True
        if key == curses.KEY_UP:
            st.detail_off = max(0, st.detail_off - 1)
        elif key == curses.KEY_DOWN:
            st.detail_off += 1
        elif key == curses.KEY_PPAGE:
            st.detail_off = max(0, st.detail_off - 10)
        elif key == curses.KEY_NPAGE:
            st.detail_off += 10
        return True
    if key == 3:  # Ctrl+C clears input
        st.input, st.cursor = "", 0
        st.history_index, st.history_draft = None, ""
        return True
    if key in (14, curses.KEY_NPAGE):
        max_off = max(0, len(st.entries) - 1)
        st.offset = min(st.offset + 1, max_off)
        st.at_bottom = st.offset >= max_off
        return True
    if key in (16, curses.KEY_PPAGE):
        st.offset = max(0, st.offset - 1)
        st.at_bottom = False
        return True
    if key == 7:
        st.at_bottom = True
        return True
    if key == 15:  # Ctrl+O opens the full-body overlay
        _open_detail(st)
        return True
    if key == curses.KEY_UP:
        _history_move(st, -1)
        return True
    if key == curses.KEY_DOWN:
        _history_move(st, 1)
        return True
    if key in (curses.KEY_HOME, 1):
        st.cursor = 0
        return True
    if key in (curses.KEY_END, 5):
        st.cursor = len(st.input)
        return True
    if key == curses.KEY_LEFT:
        st.cursor = max(0, st.cursor - 1)
        return True
    if key == curses.KEY_RIGHT:
        st.cursor = min(len(st.input), st.cursor + 1)
        return True
    if key == 21:
        st.input, st.cursor = "", 0
        st.history_index, st.history_draft = None, ""
        return True
    if key == 27:
        st.input, st.cursor, st.pending, st.invite_preset, st.status = "", 0, None, "", "cancelled"
        st.history_index, st.history_draft = None, ""
        return True
    if key == 9:
        _complete_input(st)
        return True
    if key in (curses.KEY_BACKSPACE, 127, 8):
        if st.cursor > 0:
            st.input = st.input[: st.cursor - 1] + st.input[st.cursor :]
            st.cursor -= 1
        st.history_index, st.history_draft = None, ""
        return True
    if key in (10, 13, curses.KEY_ENTER):
        text = st.input.strip()
        _remember_input(st, text)
        st.input, st.cursor = "", 0
        if st.pending is not None:
            session = st.pending.get("session")
            alias = text or str(st.pending.get("default") or "")
            st.pending = None
            if not alias:
                st.status = "alias required — /add again or type one"
                return True
            client.send(
                {
                    "type": "room_invite",
                    "room": st.room,
                    "alias": alias,
                    "session": session or None,
                    "spawn": True,
                }
            )
            st.status = f"invited {alias} (spawning)"
            return True
        if not text:
            return True
        if text.startswith("/"):
            try:
                _run_command(client, st, text)
            except QuitPanel:
                return False
            return True
        if not st.allow_chat:
            st.status = "show is command-only — chat from a joined member session (/help)"
            st.input, st.cursor = text, len(text)
        elif not _send_chat(client, st, text):
            st.input, st.cursor = text, len(text)
        return True
    if key >= 32 and key < 127:
        st.history_index, st.history_draft = None, ""
        st.input = st.input[: st.cursor] + chr(key) + st.input[st.cursor :]
        st.cursor += 1
    return True


def _handle_pick(client: BusClient, st: PanelState, key: int) -> bool:
    if key in (27, 3):
        st.pick_items, st.pending, st.invite_preset, st.status = [], None, "", "cancelled"
        return key != 3 or True
    if key in (14, curses.KEY_DOWN):
        st.pick_sel = min(st.pick_sel + 1, len(st.pick_items) - 1)
    elif key in (curses.KEY_DC, 127):
        item = st.pick_items[st.pick_sel]
        if st.pick_kind == "join":
            # DEL forgets the room file; member processes keep running detached.
            room = str(item.get("room") or "")
            if not room:
                st.status = "nothing to delete here"
                return True
            client.send({"type": "room_detach", "room": room})
            st.pick_items = []
            if room == st.room:
                _watch(client, st, NO_ROOM)
            st.status = f"room {room} forgotten — members keep running detached"
            return True
        if not st.pick_kind.startswith("add"):
            return True
        member = str(item.get("member") or "")
        if not member or not item.get("session"):
            st.status = "the fresh row holds no stored session — nothing to delete"
            return True
        seated = room_membership()
        rooms = set(seated.get(("alias", member), ()))
        rooms |= set(seated.get(("session", str(item["session"])), ()))
        rooms |= set(seated.get(("session_dir", str(Path(item["session"]).parent)), ()))
        for room in sorted(rooms):
            client.send({"type": "participant_delete", "room": room, "alias": member})
        shutil.rmtree(Path(item["session"]).parent, ignore_errors=True)
        _open_invite_picker(st)
        unseated = f" — unseated from {', '.join(sorted(rooms))}" if rooms else " — seated nowhere"
        st.status = f"deleted stored session {member}{unseated}"
        return True
    elif key in (16, curses.KEY_UP):
        st.pick_sel = max(0, st.pick_sel - 1)
    elif key in (10, 13, curses.KEY_ENTER):
        item = st.pick_items[st.pick_sel]
        st.pick_items = []
        if st.pick_kind == "join":
            _join_room(client, st, str(item.get("room", NO_ROOM)))
        elif st.pick_kind == "rm":
            room = item.get("room")
            if room:
                client.send({"type": "room_delete", "room": str(room)})
                if str(room) == st.room:
                    _watch(client, st, NO_ROOM)
                st.status = f"room {room} deleted"
        elif st.pick_kind.startswith("help"):
            command = str(item.get("command", ""))
            if "<" in command:
                st.input = command
                st.cursor = len(st.input)
                st.status = "edit command, then press Enter"
            else:
                try:
                    _run_command(client, st, command.split(" ", 1)[0])
                except QuitPanel:
                    return False
        else:
            session = str(item.get("session") or "")
            default = st.invite_preset or str(item.get("member") or "") or (default_alias_for(Path(session).stem) if session else "")
            st.invite_preset = ""
            st.pending = {
                "label": f"alias{' — Enter accepts ' + default if default else ' for the fresh instance'}",
                "session": session,
                "default": default,
            }
    return True


def _draw_pick(screen: "curses._CursesWindow", st: PanelState, width: int) -> None:
    height = screen.getmaxyx()[0]
    title = st.pick_kind if st.pick_kind.startswith(("add", "help", "join")) else f"open: {st.pick_kind}"
    selected = st.pick_items[st.pick_sel]
    hint = selected.get("hint", "") if isinstance(selected, dict) else ""
    hint_rows = 1 if hint else 0
    footer_top = max(1, height - 4)
    bottom = max(2, footer_top - 1)
    max_rows = max(1, bottom - 2 - hint_rows)
    rows = min(12, len(st.pick_items), max_rows)
    first = max(0, min(st.pick_sel - rows + 1, len(st.pick_items) - rows))
    top = max(1, bottom - rows - hint_rows - 1)
    left = 1
    right = max(left + 4, width - 2)
    inner_width = right - left - 1
    label = f" {title} "[:inner_width]
    top_fill = max(0, inner_width - len(label))
    top_line = f"┌{"─" * (top_fill // 2)}{label}{"─" * (top_fill - top_fill // 2)}┐"
    screen.addstr(top, left, top_line[: right - left + 1])
    for row in range(rows):
        index = first + row
        item = st.pick_items[index]
        marker = ">" if index == st.pick_sel else " "
        line = f" {marker} {item['label']}"[:inner_width].ljust(inner_width)
        attr = curses.A_REVERSE if index == st.pick_sel else curses.A_NORMAL
        screen.addstr(top + 1 + row, left, "│")
        screen.addstr(top + 1 + row, left + 1, line, attr)
        screen.addstr(top + 1 + row, right, "│")
    if hint:
        dim = getattr(curses, "A_DIM", curses.A_NORMAL)
        hint_line = f"→ {hint}"[:inner_width].ljust(inner_width)
        screen.addstr(top + 1 + rows, left, "│")
        screen.addstr(top + 1 + rows, left + 1, hint_line, dim)
        screen.addstr(top + 1 + rows, right, "│")
    screen.addstr(bottom, left, f"└{"─" * inner_width}┘"[: right - left + 1])
