const STATE_TYPE = "dev.rainge.state.v1";
const DECISION_TYPE = "dev.rainge.decision.v1";
const MAX_ALIAS_LENGTH = 32;

type Participant = {
  alias: string;
  agent: string;
  status: "invited" | "paused" | "kicked";
  joinedAt: string;
  liveId?: string;
};

// Router message: one hub A→B delivery logged for the panel. Status is
// participant-declared (done, waiting, working); moderator dispatches log as
// "dispatch". Bodies stay short: participants keep hub messages to 1-2 lines.
type RoutedMsg = {
  from: string;
  to: string;
  status: string;
  body: string;
  ts: string;
};

const MAX_LOG = 30;
type State = {
  version: 1;
  active: boolean;
  paused: boolean;
  autoPaused: boolean;
  participants: Participant[];
  log: RoutedMsg[];
};

type ExtensionContext = any;
type ExtensionApi = any;

const emptyState = (): State => ({
  version: 1,
  active: false,
  paused: false,
  autoPaused: false,
  participants: [],
  log: [],
});

let state = emptyState();

function clean(value: unknown): string {
  return String(value ?? "").trim();
}

function safeAlias(value: string): string {
  return clean(value)
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, MAX_ALIAS_LENGTH);
}

function stateFromEntry(data: unknown): State | undefined {
  if (!data || typeof data !== "object") return undefined;
  const candidate = data as Partial<State>;
  if (candidate.version !== 1 || !Array.isArray(candidate.participants)) return undefined;
  return {
    version: 1,
    active: candidate.active === true,
    paused: candidate.paused === true,
    autoPaused: candidate.autoPaused === true,
    participants: candidate.participants
      .filter((item): item is Participant => {
        if (!item || typeof item !== "object") return false;
        const p = item as Partial<Participant>;
        return typeof p.alias === "string" && typeof p.agent === "string";
      })
      .map((item) => ({
        alias: item.alias,
        agent: item.agent,
        joinedAt: typeof item.joinedAt === "string" ? item.joinedAt : "",
        ...(typeof item.liveId === "string" && item.liveId !== "" ? { liveId: item.liveId } : {}),
      })),
    log: validLog(candidate.log),
  };
}

function latestState(ctx: ExtensionContext): State {
  let restored = emptyState();
  for (const entry of ctx.sessionManager?.getBranch?.() ?? []) {
    if (entry.type !== "custom" || entry.customType !== STATE_TYPE) continue;
    const candidate = stateFromEntry(entry.data);
    if (candidate) restored = candidate;
  }
  return restored;
}
// Advisory taps log hub traffic even in sessions that never ran a rainge
// invite. Before any tap persists, reconcile an unauthored in-memory state
// with the branch so appending never writes empty-over-authored entries.
function syncFromBranch(ctx: ExtensionContext): void {
  if (state.active || state.participants.length > 0 || state.log.length > 0) return;
  const branch = latestState(ctx);
  if (branch.active || branch.participants.length > 0 || branch.log.length > 0) state = branch;
}


async function persist(pi: ExtensionApi): Promise<void> {
  await pi.appendEntry(STATE_TYPE, { ...state, participants: state.participants.map((p) => ({ ...p })), log: state.log.map((m) => ({ ...m })) });
}

function activeParticipants(): Participant[] {
  return state.participants.filter((participant) => participant.status !== "kicked");
}

function validLog(value: unknown): RoutedMsg[] {
  if (!Array.isArray(value)) return [];
  const entries: RoutedMsg[] = [];
  for (const item of value) {
    const from = eventText(item, "from");
    const body = eventText(item, "body");
    if (!from || !body) continue;
    entries.push({
      from,
      to: eventText(item, "to") || "main",
      status: eventText(item, "status") || "unknown",
      body,
      ts: eventText(item, "ts"),
    });
  }
  return entries.slice(-MAX_LOG);
}

// Envelope: participants start every hub message with `to:` and `status:`
// header lines, then the body. Lenient: missing headers default to Main /
// unknown and the whole text stays the body — never drop a message.
function parseEnvelope(body: unknown): { to: string; status: string; text: string } {
  const raw = typeof body === "string" ? body : "";
  const lines = raw.split("\n");
  let to = "main";
  let status = "unknown";
  let start = 0;
  const toMatch = /^to:\s*@?([A-Za-z0-9-]+)\s*$/i.exec(lines[0] ?? "");
  if (toMatch) {
    to = toMatch[1] ?? "main";
    start = 1;
  }
  const statusMatch = /^status:\s*([A-Za-z-]+)\s*$/i.exec(lines[1] ?? "");
  if (statusMatch) {
    status = statusMatch[1] ?? "unknown";
    start = Math.max(start, 2);
  }
  const text = lines.slice(start).join("\n").trim();
  return { to, status, text: text === "" ? raw.trim() : text };
}

function logRouted(from: string, to: string, status: string, body: string): boolean {
  if (from === "" || body === "") return false;
  state.log.push({ from, to: to === "" ? "main" : to, status: status === "" ? "unknown" : status, body, ts: new Date().toISOString() });
  if (state.log.length > MAX_LOG) state.log.splice(0, state.log.length - MAX_LOG);
  return true;
}

// Moderator→participant dispatches, seen on the way out.
function recordHubSend(event: unknown): boolean {
  if (eventText(event, "toolName") !== "hub") return false;
  const input = eventField(event, "input");
  const args = input !== undefined && typeof input === "object" ? input : eventField(event, "arguments");
  if (eventText(args, "op") !== "send") return false;
  const to = eventText(args, "to").toLowerCase();
  if (to === "") return false;
  return logRouted("main", to, "dispatch", eventText(args, "message"));
}

// Participant arrivals, seen when a wait surfaces them. Realtime irc cards
// bypass tools entirely and stay moderator-relayed, never logged here.
function recordHubDelivery(event: unknown): boolean {
  if (eventText(event, "toolName") !== "hub") return false;
  const waited = eventField(eventField(event, "details"), "waited");
  if (waited === undefined || typeof waited !== "object") return false;
  const envelope = parseEnvelope(eventText(waited, "body"));
  return logRouted(eventText(waited, "from").toLowerCase(), envelope.to, envelope.status, envelope.text);
}

// Exact spawned agent ids (e.g. "omp1-3") recorded per roster alias from task
// tool traffic. The hub bus is process-global: same-named rows from other
// sessions are visible but stale, so routing must name these ids, never
// broadcast. The tool_call/tool_result event key names are host-owned and may
// vary; read both casings and degrade to no recording rather than guessing.
const pendingTaskInputs = new Map<string, unknown>();

// Host-owned event payloads arrive as unknown: read one field at a time
// through typeof/in narrowing, never by asserting an object shape.
function eventField(value: unknown, key: string): unknown {
  if (typeof value !== "object" || value === null) return undefined;
  if (!(key in value)) return undefined;
  const record: unknown = (value as { [k: string]: unknown })[key];
  return record;
}

function eventText(value: unknown, key: string): string {
  const field = eventField(value, key);
  return typeof field === "string" ? field : "";
}

// Pure id-matching core: assignment names win, task-item index is the fallback.

function resolveSpawnIds(names: string[], results: unknown): { alias: string; id: string }[] {
  if (!Array.isArray(results)) return [];
  const hits: { alias: string; id: string }[] = [];
  for (const result of results) {
    const id = eventText(result, "id");
    if (!id) continue;
    const index = eventField(result, "index");
    const fromIndex = typeof index === "number" ? names[index] ?? "" : "";
    const alias = (eventText(eventField(result, "assignment"), "name") || fromIndex).toLowerCase();
    if (alias) hits.push({ alias, id });
  }
  return hits;
}

function taskNames(input: unknown): string[] {
  const tasks = eventField(input, "tasks");
  if (!Array.isArray(tasks)) return [];
  return tasks.map((item) => eventText(item, "name"));
}

function noteSpawnIds(tasksInput: unknown, details: unknown): string[] {
  const changed: string[] = [];
  for (const hit of resolveSpawnIds(taskNames(tasksInput), eventField(details, "results"))) {
    const participant = activeParticipants().find((item) => item.alias === hit.alias);
    if (!participant || participant.liveId === hit.id) continue;
    participant.liveId = hit.id;
    changed.push(participant.alias);
  }
  return changed;
}

function stashTaskInput(event: unknown): void {
  if (eventText(event, "toolName") !== "task") return;
  const id = toolCallId(event);
  if (!id) return;
  if (pendingTaskInputs.size > 20) pendingTaskInputs.clear();
  pendingTaskInputs.set(id, eventField(event, "input"));
}

async function recordTaskResult(pi: ExtensionApi, event: unknown): Promise<boolean> {
  if (eventText(event, "toolName") !== "task") return false;
  const id = eventText(event, "toolCallId") || eventText(event, "tool_call_id");
  const input = id ? pendingTaskInputs.get(id) : undefined;
  if (id) pendingTaskInputs.delete(id);
  const changed = noteSpawnIds(input, eventField(event, "details")).length > 0;
  if (changed) await persist(pi);
  return changed;
}

function statusText(): string {
  const members = activeParticipants();
  const mode = state.paused ? "paused" : "running";
  if (members.length === 0) return `Rainge: ${mode}; no participants invited.`;
  return `Rainge: ${mode}; ${members.map((p) => `${p.alias} (${p.agent})${p.liveId ? ` = ${p.liveId}` : ""}`).join(", ")}.${ircLive ? " irc-live" : ""}`;
}
function badge(): string {
  if (!state.active) return "";
  const members = activeParticipants();
  const mode = state.paused ? "paused" : "running";
  return `◈ rainge ${mode} · ${members.length} participant${members.length === 1 ? "" : "s"}`;
}

function applyMode(ctx: ExtensionContext): void {
  try {
    ctx?.ui?.setStatus?.("rainge", badge());
  } catch {
    // headless or older UI: the badge is best-effort
  }
}

function clearMode(ctx: ExtensionContext): void {
  try {
    ctx?.ui?.setStatus?.("rainge", "");
  } catch {
    // headless or older UI: the badge is best-effort
  }
}

function refreshChrome(ctx: ExtensionContext): void {
  if (state.active) applyMode(ctx);
  else clearMode(ctx);
}
type SuggestItem = {
  value: string;
  label: string;
  description: string;
  keywords: string[];
};

type SuggestResult = {
  items: SuggestItem[];
  prefix: string;
};

type Candidate = {
  value: string;
  description: string;
};

type RoleInfo = {
  name: string;
  model: string;
};

type NodeLibs = {
  readdir: (dir: string) => string[];
  readFile: (path: string) => string;
  join: (...parts: string[]) => string;
  homedir: () => string;
  mkdir: (dir: string) => void;
  writeFile: (path: string, text: string) => void;
};

type HostFn = (...called: unknown[]) => unknown;

const SUBCOMMANDS: Candidate[] = [
  { value: "invite", description: "add a task agent or model role as participant" },
  { value: "kick", description: "stop routing work to a participant" },
  { value: "pause", description: "gate new participant turns" },
  { value: "resume", description: "resume participant turns" },
  { value: "decide", description: "record a durable decision" },
  { value: "status", description: "show participants and pause state" },
  { value: "panel", description: "open the overlay roster" },
  { value: "exit", description: "leave rainge mode" },
];

const BUNDLED_AGENTS: Candidate[] = [
  { value: "scout", description: "bundled agent: read-only codebase research" },
  { value: "reviewer", description: "bundled agent: code review" },
  { value: "security-reviewer", description: "bundled agent: security review" },
  { value: "task", description: "bundled agent: general-purpose delegate" },
  { value: "sonic", description: "bundled agent: mechanical updates" },
];

const INVITE_CACHE_MS = 15000;

let completionCwd: string | undefined;
let inviteCache: { at: number; cwd: string; list: Candidate[] } | undefined;

// The completion protocol is defined by the OMP host, not our types:
// narrow to callable with one named assertion.
function asHostFn(value: unknown): HostFn | undefined {
  if (typeof value !== "function") return undefined;
  return value as HostFn;
}

async function nodeLibs(): Promise<NodeLibs | undefined> {
  // Dynamic imports: extension hosts may lack node builtins. Any failure
  // degrades completion to bundled agents instead of breaking extension load.
  try {
    const fs = (await import("node:fs")) as unknown as {
      readdirSync: (dir: string) => string[];
      readFileSync: (path: string, encoding: string) => string;
      writeFileSync: (path: string, data: string, encoding: string) => void;
      mkdirSync: (dir: string, options?: unknown) => unknown;
    };
    const os = (await import("node:os")) as unknown as { homedir: () => string };
    const path = (await import("node:path")) as unknown as { join: (...parts: string[]) => string };
    return {
      readdir: (dir) => fs.readdirSync(dir),
      readFile: (file) => fs.readFileSync(file, "utf8"),
      writeFile: (file, text) => fs.writeFileSync(file, text, "utf8"),
      mkdir: (dir) => {
        fs.mkdirSync(dir, { recursive: true });
      },
      join: (...parts) => path.join(...parts),
      homedir: () => os.homedir(),
    };
  } catch {
    return undefined;
  }
}

function agentEnv(): Record<string, string | undefined> {
  const proc: unknown = globalThis.process;
  if (typeof proc === "object" && proc !== null && "env" in proc) {
    const env: unknown = proc.env;
    // Node guarantees string-valued env entries; assert the record shape once.
    if (typeof env === "object" && env !== null) return env as Record<string, string | undefined>;
  }
  return {};
}

function agentRoots(libs: NodeLibs, cwd: string): { userAgents: string; projectAgents: string; config: string } {
  const override = agentEnv()["PI_CODING_AGENT_DIR"] ?? "";
  const base = override.trim() !== "" ? override.trim() : libs.join(libs.homedir(), ".omp", "agent");
  return {
    userAgents: libs.join(base, "agents"),
    projectAgents: libs.join(cwd, ".omp", "agents"),
    config: libs.join(base, "config.yml"),
  };
}

function parseRoles(text: string): RoleInfo[] {
  const roles: RoleInfo[] = [];
  let inRoles = false;
  for (const line of text.split("\n")) {
    if (/^modelRoles:\s*(#.*)?$/.test(line)) { inRoles = true; continue; }
    if (!inRoles) continue;
    if (/^\S/.test(line)) break;
    const match = /^  ([A-Za-z0-9_-]+):\s*(\S[^#]*?)\s*(?:#.*)?$/.exec(line);
    if (match) roles.push({ name: match[1], model: match[2] });
  }
  return roles;
}

function parseAgentName(file: string, text: string): string {
  let inFrontmatter = false;
  for (const line of text.split("\n", 25)) {
    if (/^---\s*$/.test(line)) {
      inFrontmatter = !inFrontmatter;
      if (!inFrontmatter) break;
      continue;
    }
    if (!inFrontmatter) break;
    const match = /^name:\s*["']?([^"'#\s}]+)["']?\s*(?:#.*)?$/.exec(line);
    if (match) return match[1];
  }
  return file.replace(/\.md$/, "");
}

function readAgentNames(libs: NodeLibs, dir: string): string[] {
  let entries: string[];
  try {
    entries = libs.readdir(dir).filter((name) => name.endsWith(".md"));
  } catch {
    return [];
  }
  const names: string[] = [];
  for (const entry of entries.slice(0, 64)) {
    try {
      names.push(parseAgentName(entry, libs.readFile(libs.join(dir, entry))));
    } catch {
      // skip unreadable agent files
    }
  }
  return names;
}

async function inviteCandidates(cwd: string): Promise<Candidate[]> {
  const now = Date.now();
  if (inviteCache && inviteCache.cwd === cwd && now - inviteCache.at < INVITE_CACHE_MS) return inviteCache.list;
  const list: Candidate[] = [...BUNDLED_AGENTS];
  const seen = new Set(list.map((candidate) => candidate.value));
  const libs = await nodeLibs();
  if (libs) {
    const roots = agentRoots(libs, cwd);
    for (const name of [...readAgentNames(libs, roots.projectAgents), ...readAgentNames(libs, roots.userAgents)]) {
      if (!seen.has(name)) {
        seen.add(name);
        list.push({ value: name, description: "custom task agent" });
      }
    }
    try {
      for (const role of parseRoles(libs.readFile(roots.config))) {
        if (!seen.has(role.name)) {
          seen.add(role.name);
          list.push({ value: role.name, description: `model role → ${role.model}` });
        }
      }
    } catch {
      // no readable config: bundled and custom agents only
    }
  }
  inviteCache = { at: now, cwd, list };
  return list;
}

function filterCandidates(list: Candidate[], partial: string): Candidate[] {
  const needle = partial.toLowerCase();
  return list.filter((candidate) => candidate.value.toLowerCase().startsWith(needle));
}

function toResult(list: Candidate[], lineHead: string): SuggestResult | null {
  if (list.length === 0) return null;
  lastOwnValues = new Set(list.map((candidate) => candidate.value));
  return {
    items: list.map((candidate) => ({
      value: candidate.value,
      label: candidate.value,
      description: candidate.description,
      keywords: [candidate.value],
    })),
    prefix: lineHead,
  };
}

async function suggestFor(args: unknown[], cwd: string): Promise<SuggestResult | null> {
  const head = headOf(args);
  if (head === undefined) return null;
  const match = /^\/rainge(?:\s+(\S*))?(?:\s+(.*))?$/.exec(head);
  if (!match) return null;
  const first = match[1] ?? "";
  const rest = match[2];
  if (rest === undefined) return toResult(filterCandidates(SUBCOMMANDS, first), head);
  const partial = rest.trim();
  if (first === "invite") return toResult(filterCandidates(await inviteCandidates(cwd), partial), head);
  if (first === "kick") {
    const aliases = activeParticipants().map((participant) => ({
      value: participant.alias,
      description: `participant (${participant.agent})`,
    }));
    return toResult(filterCandidates(aliases, partial), head);
  }
  return null;
}

type AppliedCompletion = {
  lines: string[];
  cursorLine: number;
  cursorCol: number;
};

function itemValue(item: unknown): string | undefined {
  if (typeof item === "object" && item !== null && "value" in item) {
    const value: unknown = item.value;
    if (typeof value === "string" && value !== "") return value;
  }
  return undefined;
}

function noEdit(rawLines: unknown, rawLine: unknown, rawCol: unknown): AppliedCompletion {
  if (!Array.isArray(rawLines) || typeof rawLine !== "number" || typeof rawCol !== "number") {
    return { lines: [], cursorLine: 0, cursorCol: 0 };
  }
  const lines = rawLines.filter((entry): entry is string => typeof entry === "string");
  return { lines, cursorLine: rawLine, cursorCol: rawCol };
}

function applyOwnValue(args: unknown[], value: string): AppliedCompletion {
  const fallback = noEdit(args[0], args[1], args[2]);
  const rawLines = args[0];
  const rawLine = args[1];
  const rawCol = args[2];
  if (!Array.isArray(rawLines) || typeof rawLine !== "number" || typeof rawCol !== "number") return fallback;
  const current = rawLines[rawLine];
  if (typeof current !== "string") return fallback;
  const col = Math.max(0, Math.min(rawCol, current.length));
  const head = current.slice(0, col);
  const tail = current.slice(col);
  if (!/^\/rainge(?:\s+\S+)?(?:\s+\S*)?$/.test(head)) return fallback;
  const tokenStart = head.search(/\S+$/);
  const cut = tokenStart === -1 ? col : tokenStart;
  const rest = rawLines.map((entry) => (typeof entry === "string" ? entry : ""));
  rest[rawLine] = head.slice(0, cut) + value + tail;
  return { lines: rest, cursorLine: rawLine, cursorCol: cut + value.length };
}

type InnerProvider = {
  getSuggestions: (...args: unknown[]) => Promise<unknown>;
  applyCompletion: (...args: unknown[]) => unknown;
};

function asInnerProvider(inner: unknown): InnerProvider | undefined {
  if (typeof inner !== "object" || inner === null) return undefined;
  if (!("getSuggestions" in inner) || !("applyCompletion" in inner)) return undefined;
  const suggest = asHostFn(inner.getSuggestions);
  const apply = asHostFn(inner.applyCompletion);
  if (!suggest || !apply) return undefined;
  return {
    // Bound to the host object: the built-in provider reads private state via `this`.
    getSuggestions: (...args: unknown[]) => suggest.call(inner, ...args),
    applyCompletion: (...args: unknown[]) => apply.call(inner, ...args),
  };
}

function headOf(args: unknown[]): string | undefined {
  const rawLines = args[0];
  const rawLine = args[1];
  const rawCol = args[2];
  if (!Array.isArray(rawLines) || typeof rawLine !== "number" || typeof rawCol !== "number") return undefined;
  const current = rawLines[rawLine];
  if (typeof current !== "string") return undefined;
  return current.slice(0, Math.max(0, rawCol));
}

function isRaingeHead(head: string): boolean {
  return /^\/rainge(?:\s|$)/.test(head);
}

// Values from our most recent suggestion list. Consulted to route an accepted
// item to our applier; cleared off-command so stale values never hijack natives.
let lastOwnValues = new Set<string>();

function buildProvider(inner: unknown, cwd: string): unknown {
  const host = asInnerProvider(inner);
  return {
    getSuggestions: async (...args: unknown[]): Promise<SuggestResult | null> => {
      const callHost = async (): Promise<SuggestResult | null> => {
        if (!host) return null;
        try {
          const existing = await host.getSuggestions(...args);
          if (existing === null || existing === undefined) return null;
          // Host-owned item shape; passed through untouched, never inspected.
          const passthrough = existing as SuggestResult;
          return passthrough;
        } catch {
          return null;
        }
      };
      const callOwn = async (): Promise<SuggestResult | null> => {
        try {
          return await suggestFor(args, cwd);
        } catch {
          return null;
        }
      };
      try {
        const head = headOf(args);
        if (head === undefined) return (await callHost()) ?? (await callOwn());
        if (!isRaingeHead(head)) {
          lastOwnValues.clear();
          return (await callHost()) ?? (await callOwn());
        }
        return (await callOwn()) ?? (await callHost());
      } catch {
        return null;
      }
    },
    applyCompletion: (...args: unknown[]): AppliedCompletion => {
      try {
        const value = itemValue(args[3]);
        const head = headOf(args);
        const own = value !== undefined && head !== undefined && isRaingeHead(head) && lastOwnValues.has(value);
        if (!own && host) {
          const applied = host.applyCompletion(...args);
          if (typeof applied === "object" && applied !== null) {
            // Host-owned result; trusted shape from the built-in provider.
            const result = applied as AppliedCompletion;
            if (Array.isArray(result.lines)) return result;
          }
        }
        if (value !== undefined) return applyOwnValue(args, value);
      } catch {
        // fall through to a structured no-op
      }
      // The host dereferences `.lines` unconditionally: never return null.
      return noEdit(args[0], args[1], args[2]);
    },
  };
}

function registerCompletion(pi: ExtensionApi, ctx: ExtensionContext): void {
  try {
    const cwd = typeof ctx.cwd === "string" ? ctx.cwd : "";
    if (completionCwd === cwd) return;
    const register = asHostFn(ctx.ui?.addAutocompleteProvider);
    if (!register) return;
    register((inner: unknown) => buildProvider(inner, cwd));
    completionCwd = cwd;
  } catch {
    // completion is advisory; the commands work without it
  }
}

// Pure helpers exported for offline verification; the host only uses the default export.
export { parseRoles, parseAgentName, suggestFor, applyOwnValue, itemValue, buildProvider, invite, kick, resolveInvite, resolveSpawnIds, noteSpawnIds, parseEnvelope, logRouted, hubExecute, arrivalTexts, syncFromBranch, connectBus, busSend, busList, closeBus, busStatus };
export { assistantText, relayBusReplies };

// Federated bus: a TCP bridge to the standalone router (router/bus_router.py).
// The bus federates SEPARATE omp processes — each instance connects under an
// alias and steers incoming peer messages into its own session. It is not the
// in-process hub: hub addresses task agents in one process, the bus addresses
// whole omp instances across processes.
type BusFrame = Record<string, unknown>;
type BusConn = {
  alias: string;
  addr: string;
  room: string;
  cursor: number;
  write: (frame: BusFrame) => void;
  close: () => void;
  frames: BusFrame[];
};



let bus: BusConn | undefined;
let conventionSeeded = false;
type BusReplyTarget = { from: string };
const pendingBusReplies: BusReplyTarget[] = [];

function assistantText(event: unknown): string {
  const messages = eventField(event, "messages");
  if (!Array.isArray(messages)) return "";
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (eventText(message, "role") !== "assistant") continue;
    const content = eventField(message, "content");
    if (typeof content === "string" && content.trim() !== "") return content.trim();
    if (!Array.isArray(content)) continue;
    const text = content
      .filter((block) => eventText(block, "type") === "text")
      .map((block) => eventText(block, "text"))
      .join("")
      .trim();
    if (text !== "") return text;
  }
  return "";
}

function clearBusReplies(): void {
  pendingBusReplies.splice(0);
}

function relayBusReplies(event: unknown): void {
  if (eventField(event, "isTerminal") === false) return;
  const body = assistantText(event);
  const conn = bus;
  // Keep the target when the host emits an intermediate/empty agent_end or
  // while the socket is reconnecting. Dropping it here loses the user-visible
  // answer even though the participant completed its turn.
  if (!conn || body === "") return;
  const targets = pendingBusReplies.splice(0);
  const sent = new Set<string>();
  for (const target of targets) {
    const from = target.from.trim();
    if (from === "" || sent.has(from)) continue;
    sent.add(from);
    try {
      conn.write({ type: "send", to: from, body });
    } catch {
      // A disconnect between agent_end and the write is non-fatal.
    }
  }
}


async function connectBus(pi: ExtensionApi, alias: string, host: string, port: number, room?: string): Promise<string> {
  if (bus && bus.alias === alias && bus.addr === `${host}:${port}`) return `Bus already connected as '${alias}' at ${bus.addr}.`;
  if (bus) {
    clearBusReplies();
    bus.close();
  }
  const net = (await import("node:net")) as unknown as {
    connect: (port: number, host: string) => {
      write: (data: string) => void;
      destroy: () => void;
      on: (event: string, listener: (arg?: unknown) => void) => void;
    };
  };
  const socket = net.connect(port, host);
  const conn: BusConn = {
    alias,
    addr: `${host}:${port}`,
    room: room ?? "foyer",
    cursor: 0,
    write: (frame) => socket.write(JSON.stringify(frame) + "\n"),
    close: () => socket.destroy(),
    frames: [],
  };
  let pending = "";
  socket.on("data", (chunk: unknown) => {
    pending += typeof chunk === "string" ? chunk : String((chunk as { toString(): string }));
    let idx;
    while ((idx = pending.indexOf("\n")) >= 0) {
      const line = pending.slice(0, idx).trim();
      pending = pending.slice(idx + 1);
      if (line === "") continue;
      try {
        conn.frames.push(JSON.parse(line) as BusFrame);
      } catch {
        // a torn line is skipped; NDJSON resumes on the next newline
      }
      const frame = conn.frames[conn.frames.length - 1];
      if (frame && frame.type === "welcome" && typeof frame.seq === "number") {
        conn.cursor = Math.min(conn.cursor, frame.seq);
      }
      if (frame && frame.type === "diff" && bus === conn) {
        const entries = (Array.isArray(frame.entries) ? frame.entries : []) as Record<string, unknown>[];
        if (entries.length > 0) {
          const last = Number(entries[entries.length - 1]["seq"] ?? conn.cursor);
          if (Number.isFinite(last)) conn.cursor = Math.max(conn.cursor, last);
        }
        // No socket-level ack: the round stays open until the decider closes
        // it with an explicit bus send. Silence parks the room on the
        // candidate timeout; a real follow-up is always an explicit send.
        const rendered = entries.map((entry) => {
          const kind = String(entry["kind"] ?? "chat");
          if (kind === "system") {
            const body = String(entry["body"] ?? "");
            if (body.replace(/[-─ ]/g, "") === "") return body;
            return `~~~ ${body} ~~~`;
          }
          const tag = kind === "seed" ? "round" : kind === "response" ? "answer" : kind === "synthesis" ? "synthesis" : "msg";
          return `[${tag} #${String(entry["seq"] ?? "?")}] ${String(entry["from"] ?? "")}> ${String(entry["body"] ?? "")}`;
        });
        // System-only diffs (joins/leaves) are context, never a turn: a model
        // turn here would burn a full reasoning cycle on chrome. Mixed diffs
        // still prompt; the system lines ride along as context.
        const talk = entries.filter((entry) => String(entry["kind"] ?? "") !== "system");
        if (talk.length > 0 || frame.final === true) {
          // Operator-authored entries decide the reply channel (chat vs bus);
          // every bus prompt itself is our machinery and arrives as the
          // trusted input channel — agent attribution invited "should I
          // comply?" deliberation and inflated hidden reasoning.
          const fromOperator = talk.length > 0 && talk.every((entry) => entry["operator"] === true);
          const header = frame.final === true
            ? `Round answers complete — the round stays open until you close it. Full exchange for your context:`
            : `Bus messages for '${conn.alias}' (room '${conn.room}'):`;
          let footer: string;
          if (frame.final === true) {
            footer = `\n\nYou are the decider — the round ends when you end it. Goal achieved? Close it with ONE bus send: op=send, action='close', to='*' (verdict all should see) or to='<last sender>' (verdict for them), message='<one line verdict>'. That send files the verdict and the divider follows it. Nothing worth filing (greetings done, agreed with no news)? Close empty: op=send, action='close', message='' — files nothing, the divider follows. Answers conflict? You own the tie-break — close with it as the verdict. If anyone still has to move — including you — this is NOT closed: a plan written into a verdict parks the room, because closing ends your mandate and nobody gets pinged. DM the first mover now (to='<alias>', message='<their first step>', no action) and you stay decider across answers; broadcast only if all must hear. Never name the operator, he only reads. Nobody has seen the answers but you — waiting on a move without pinging the mover stalls the room. If no next move exists at all (agreed to play, nothing started), define it yourself and DM it to them — never file the plan as a closing verdict. No silent branch exists: silence parks the room on the candidate timeout. Never courtesy or thanks.`;
          } else if (entries.some((entry) => String(entry["kind"] ?? "") === "seed")) {
            const cont = entries.find((entry) => typeof entry["deputy"] === "string" && String(entry["deputy"]) !== conn.alias);
            footer = typeof cont !== "undefined"
              ? `\n\n${String(cont["from"] ?? "A member")} is continuing the last round with the full picture — reply once via bus (op=send, to='*', message=<one line, your own words>), then end your turn. No empty acks on a continuation.`
              : `\n\nIf this message asks a question or assigns a task: reply once via bus (op=send, to='*', ref=<the round's #>, message=<one line, your own words — never echo the question>), then end your turn. The ref cites what you answer: anything older files as chat and the answer stays owed. Your answer reaches the asker only — the room will not see it. Otherwise (greeting, ack, thanks, status): the moment you decide to stay silent, send an empty ack at once (op=send, to='*', message='', action='ack') so the round closes — silence with no signal parks the room on timeout.`;
            if (fromOperator) {
              footer += ` The operator addressed the room: reply once via bus (op=send, to='*', ref=<the seed's #>, message=<one line, your own words>), then end your turn — never an empty ack on an operator seed. If it reads as a greeting, your hello IS the answer; if it reads as noise, answer what it most likely means. The round needs every voice before the judge step.`;
            }
          } else {
            const dm = entries.filter((entry) => String(entry["kind"] ?? "") === "chat");
            const stamped = entries.slice().reverse().find((entry) => typeof entry["deputy"] === "string");
            const mandate = typeof stamped !== "undefined" ? String(stamped["deputy"]) : "";
            const lastSender = dm.length > 0 ? String(dm[dm.length - 1]["from"] ?? "").trim() : "";
            if (mandate === conn.alias) {
              footer = `\n\nLoop answer from ${String(stamped?.["from"] ?? "a member")} — goal achieved? Close with ONE bus call: DM the verdict with the close flag (op=send, to='${String(stamped?.["from"] ?? "")}', action='close', message='<one line, e.g. who won>'). That send files the verdict and the divider follows it. Nothing worth filing? Close empty (op=send, action='close', message='') — files nothing, the divider follows. Still playing? DM the next member the next step (no action). You stay decider until you close or broadcast. Never broadcast mid-loop; never park the move on the operator.`;
            } else if (mandate !== "" && String(stamped?.["to"] ?? "") === conn.alias) {
              footer = `\n\n${String(stamped?.["from"] ?? "The decider")} is driving the loop — reply to them directly once via bus (op=send, to='${String(stamped?.["from"] ?? "")}', message=<one line, your own words>), then end your turn. No decision, no broadcast.`;
            } else if (fromOperator) {
              footer = `\n\n${lastSender || "The operator"} addressed you directly: answer in chat as your final message — delivery back is automatic. No bus call.`;
              if (lastSender !== "") pendingBusReplies.push({ from: lastSender });
            } else {
              footer = `\n\nReply once via bus (op=send, to='${lastSender}', message=<your reply>), then wait for their answer.`;
            }
            void dm;
          }
          // No forced deliverAs: idle sessions receive this as a normal prompt
          // (the same channel as typed input); a busy session queues it as a
          // steer. Forcing "steer" routed idle delivery through the
          // injected-message screen, which refused bus texts nondeterministically.
          void pi.sendUserMessage(`${header}\n${rendered.join("\n")}${footer}`, { attribution: "user" });
        }
      }
    }
  });
  socket.on("close", () => {
    if (bus === conn) {
      clearBusReplies();
      bus = undefined;
    }
  });
  socket.on("error", () => {
    if (bus === conn) {
      clearBusReplies();
      bus = undefined;
    }
  });
  bus = conn;
  conn.write({ type: "hello", alias, lastSeq: conn.cursor, ...(conn.room !== "" ? { room: conn.room } : {}) });
  return `Bus connected as '${alias}' at ${conn.addr}${conn.room ? ` in room '${conn.room}'` : ""} at seq ${conn.cursor}. New messages arrive as prompts.`;
}

function closeBus(): string {
  if (!bus) return "Bus not connected.";
  const addr = bus.addr;
  clearBusReplies();
  bus.close();
  bus = undefined;
  return `Bus disconnected from ${addr}.`;
}

function busStatus(): string {
  return bus ? `Bus connected as '${bus.alias}' at ${bus.addr} in room '${bus.room}'; ${bus.frames.length} frames seen.` : "Bus not connected.";
}
async function busWait(start: number, predicate: (frame: BusFrame) => boolean, ms = 3000): Promise<BusFrame | undefined> {
  const deadline = Date.now() + ms;
  const conn = bus;
  if (!conn) return undefined;
  while (Date.now() < deadline && bus === conn) {
    const hit = conn.frames.slice(start).find(predicate);
    if (hit) return hit;
    const { promise, resolve } = Promise.withResolvers<void>();
    setTimeout(resolve, 25);
    await promise;
  }
  return undefined;
}

async function busSend(to: string, message: string, note?: string, action?: string, target?: string, ref?: number): Promise<string> {
  const conn = bus;
  if (!conn) return "Bus not connected — connect first with /rainge bus <alias> <host:port>.";
  const start = conn.frames.length;
  const frame: BusFrame = { type: "send", to, body: message, seen: conn.cursor };
  if ((note ?? "").trim() !== "") frame.note = (note ?? "").trim();
  if ((action ?? "").trim() !== "") frame.action = action;
  if ((target ?? "").trim() !== "") frame.target = target;
  if (typeof ref === "number" && Number.isFinite(ref)) frame.ref = ref;
  conn.write(frame);
  const receipt = await busWait(start, (f) => f.type === "receipt" && (f.seq !== undefined || f.error !== undefined));
  if (!receipt) return `No receipt from router for '${to}'.`;
  if (receipt.error) return `Bus send to '${to}' failed: ${String(receipt.error)}.`;
  return `Sent (seq ${String(receipt.seq ?? "?")}).`;
}

async function busList(): Promise<string> {
  const conn = bus;
  if (!conn) return "Bus not connected.";
  const start = conn.frames.length;
  conn.write({ type: "list" });
  const roster = await busWait(start, (frame) => frame.type === "roster");
  if (!roster) return "No roster from router.";
  const participants = Array.isArray(roster.participants) ? roster.participants.join(", ") : "";
  const round = (roster.round ?? null) as Record<string, unknown> | null;
  const roundText = round
    ? ` Round ${String(round["id"])} ${String(round["state"])}${Array.isArray(round["awaiting"]) && round["awaiting"].length > 0 ? ` (awaiting ${round["awaiting"].join(", ")})` : ""}${round["candidate"] ? ` synthesizer ${String(round["candidate"])}` : ""}.`
    : " No open round.";
  return `Bus roster (online): ${participants || "(empty)"}.${roundText}`;
}

// "<host:port>" | "<host>" "<port>" both reduce to a connectable pair.
function busHost(token: string): string {
  const value = token.trim();
  return value.includes(":") ? value.split(":", 2)[0] ?? "127.0.0.1" : value || "127.0.0.1";
}
function busPort(token: string): number {
  const value = Number.parseInt(token.trim(), 10);
  return Number.isFinite(value) && value > 0 ? value : 7480;
}

async function enterMode(pi: ExtensionApi, ctx: ExtensionContext): Promise<string> {
  state.active = true;
  await persist(pi);
  refreshChrome(ctx);
  return `${statusText()} Input stays yours; the rainge badge marks the mode.`;
}

async function exitMode(pi: ExtensionApi, ctx: ExtensionContext): Promise<string> {
  state.active = false;
  await persist(pi);
  clearMode(ctx);
  return "Rainge mode off; badge cleared.";
}

function dispatchInstruction(kind: string, details: string): string {
  return [
    "[RAINGE CONTROL — user priority]",
    `Action: ${kind}`,
    details,
    state.paused
      ? "Roundtable paused — hold starts/chains until resume."
      : "Roundtable running — latest user message first; contract: skill://rainge.",
  ].join("\n");
}

async function steer(pi: ExtensionApi, text: string): Promise<void> {
  await pi.sendUserMessage(text, { deliverAs: "steer" });
}

async function queueTurn(pi: ExtensionApi, text: string): Promise<void> {
  // Stored for the next user prompt: no model turn, no thinking spam.
  // Several queued invites collapse into one native task batch.
  await pi.sendUserMessage(text, { deliverAs: "nextTurn" });
}

type ResolvedInvite = { agent: string; note: string } | { error: string };

function cwdOf(ctx: ExtensionContext): string {
  const cwd: unknown = ctx?.cwd;
  return typeof cwd === "string" ? cwd : "";
}

function roleAgentFile(role: string, model: string): string {
  return [
    "---",
    `name: ${role}`,
    `description: Rainge participant running the ${model} model (${role} role).`,
    `model: "@${role}"`,
    "---",
    "",
    `You are a Rainge roundtable participant running as the ${role} role.`,
    "Do not inspect the repository or start work until the moderator assigns you a task.",
    "When assigned: report concrete findings, decisions, and unresolved risks concisely. Keep hub messages to one or two lines.",
    "",
  ].join("\n");
}

function provisionError(value: string): string {
  return `Role '${value}' has no same-named agent. Create .omp/agents/${value}.md with frontmatter model: "@${value}" once, then invite again.`;
}

async function resolveInvite(value: string, cwd: string): Promise<ResolvedInvite> {
  const known = new Set(BUNDLED_AGENTS.map((candidate) => candidate.value));
  const libs = await nodeLibs();
  let roles: RoleInfo[] = [];
  let projectAgents = "";
  if (libs) {
    const roots = agentRoots(libs, cwd);
    projectAgents = roots.projectAgents;
    for (const name of [...readAgentNames(libs, roots.projectAgents), ...readAgentNames(libs, roots.userAgents)]) {
      known.add(name);
    }
    try {
      roles = parseRoles(libs.readFile(roots.config));
    } catch {
      roles = [];
    }
  }
  if (known.has(value)) return { agent: value, note: "" };
  const role = roles.find((entry) => entry.name === value);
  if (!role) {
    const agents = [...known].join(", ");
    const roleNames = roles.map((entry) => entry.name).join(", ");
    return { error: `Unknown agent or role '${value}'. Agents: ${agents}${roleNames ? `; roles: ${roleNames}` : ""}.` };
  }
  if (!libs || cwd.trim() === "") return { error: provisionError(value) };
  const file = libs.join(projectAgents, `${value}.md`);
  let exists = true;
  try {
    libs.readFile(file);
  } catch {
    exists = false;
  }
  if (!exists) {
    try {
      libs.mkdir(projectAgents);
      libs.writeFile(file, roleAgentFile(value, role.model));
    } catch {
      return { error: `${provisionError(value)} (provisioning failed)` };
    }
    if (inviteCache && inviteCache.cwd === cwd) inviteCache = undefined;
  }
  return { agent: value, note: `Role '${value}' pinned to ${role.model} via ${exists ? "existing" : "new"} role-backed agent ${file}.` };
}

async function invite(pi: ExtensionApi, agent: string, requestedAlias?: string, cwd?: string): Promise<string> {
  const value = clean(agent);
  if (!value) return "Usage: /rainge invite <agent|role> [alias]";
  const alias = safeAlias(requestedAlias || value);
  if (!alias) return "Alias must contain at least one letter or digit.";
  if (["all", "user", "moderator"].includes(alias)) return `Alias '${alias}' is reserved.`;
  if (activeParticipants().some((participant) => participant.alias === alias)) {
    return `Participant '${alias}' is already invited.`;
  }
  const resolved = await resolveInvite(value, typeof cwd === "string" ? cwd : "");
  if ("error" in resolved) return resolved.error;

  state.participants.push({
    alias,
    agent: resolved.agent,
    status: state.paused ? "paused" : "invited",
    joinedAt: new Date().toISOString(),
  });
  await persist(pi);
 const action = state.paused
 ? `Hold '${alias}' (agent '${resolved.agent}'): paused — fold it into the first assignment batch on resume, never spawn for the roster line itself.`
 : `Roster '${alias}' (agent '${resolved.agent}'): registered only — do NOT spawn, no join task, no ack, no DM, no hub check, no tools for this line. Use { name: '${alias}', agent: '${resolved.agent}' } verbatim in the next real assignment batch.${resolved.note ? ` ${resolved.note}` : ""}`;
 await queueTurn(pi, dispatchInstruction("invite", action));
 const mapping = resolved.note ? ` ${resolved.note}` : "";
 return `Invited ${alias} using agent '${resolved.agent}'.${mapping} No spawn — joins on first assignment.`;
}

async function kick(pi: ExtensionApi, alias: string): Promise<string> {
  const name = safeAlias(alias);
  const participant = activeParticipants().find((item) => item.alias === name);
  if (!participant) {
    const invited = activeParticipants().map((item) => item.alias).join(", ");
    return `Participant '${name || alias}' is not invited.${invited ? ` Invited: ${invited}.` : " No participants invited."}`;
  }
  participant.status = "kicked";
  await persist(pi);
  await steer(
    pi,
    dispatchInstruction(
      "kick",
      `Participant '${participant.alias}' is kicked: stop routing ALL new work to it immediately. If a live child session or background job '${participant.liveId ?? participant.alias}' exists, cancel/kill it NOW via the hub tool or Agent Hub; do not let it keep working. Confirm in one line once no live child remains. Do not replace it.`,
    ),
  );
  return `Kicked ${participant.alias}.`;
}

async function setPaused(pi: ExtensionApi, paused: boolean): Promise<string> {
  state.paused = paused;
  for (const participant of activeParticipants()) participant.status = paused ? "paused" : "invited";
  await persist(pi);
  await steer(
    pi,
    dispatchInstruction(paused ? "pause" : "resume", paused ? "Pause all new participant turns." : "Resume participant turns."),
  );
  return paused ? "Rainge paused." : "Rainge resumed.";
}

async function decide(pi: ExtensionApi, text: string): Promise<string> {
  const decision = clean(text);
  if (!decision) return "Usage: /rainge decide <decision>";
  await pi.appendEntry(DECISION_TYPE, {
    version: 1,
    decision,
    recordedAt: new Date().toISOString(),
  });
  await steer(pi, dispatchInstruction("decision", `Record and honor this user decision: ${decision}`));
  await persist(pi);
  return `Decision recorded: ${decision}`;
}

function helpText(): string {
  return [
    "/rainge — enter rainge mode: status badge on, input stays yours",
    "/rainge panel — open the overlay panel (q closes, editor text intact)",
    "/rainge exit — leave rainge mode and clear the badge",
    "/rainge invite <agent|role> [alias] — add a native OMP task participant (agent name or model role)",
    "/rainge kick <alias> — stop routing work to a participant",
    "/rainge pause | resume — control participant dispatch",
    "/rainge decide <text> — append a durable decision and steer the roundtable",
    "/rainge status — show participants and pause state",
    "Alt+A — open Agent Hub for live child status, transcripts, steering, revive, and kill",
    "/rainge bus <alias> <host:port> — federate this omp with peer instances via the bus router",
    "/todo — manage the shared task list",
  ].join("\n");
}

// The durable inbox: irc arrivals to Main persist as session entries even
// though their host cards flash and vanish. Pure extraction over the branch
// snapshot keeps the panel render side-effect free.
function arrivalTexts(branch: unknown): { from: string; text: string }[] {
  if (!Array.isArray(branch)) return [];
  const arrivals: { from: string; text: string }[] = [];
  for (const entry of branch) {
    if (eventText(entry, "customType") !== "irc:incoming") continue;
    const details = eventField(entry, "details");
    const from = eventText(details, "from") || "peer";
    const raw = eventText(details, "message") || eventText(entry, "content").replace(/^<irc>\n.*?\n\n/s, "");
    const text = parseEnvelope(raw).text;
    if (text !== "") arrivals.push({ from, text });
  }
  return arrivals.slice(-8);
}

function frameLines(lines: string[], width: number, title: string): string[] {
  if (width < 3) return lines.map((line) => line.slice(0, Math.max(0, width)));
  const innerWidth = width - 2;
  const label = ` ${title} `;
  const top = label.length <= innerWidth
    ? `┌${"─".repeat(Math.floor((innerWidth - label.length) / 2))}${label}${"─".repeat(Math.ceil((innerWidth - label.length) / 2))}┐`
    : `┌${"─".repeat(innerWidth)}┐`;
  const body = lines.map((line) => `│${line.slice(0, innerWidth).padEnd(innerWidth)}│`);
  return [top, ...body, `└${"─".repeat(innerWidth)}┘`];
}

function panelLines(width: number, arrivals: { from: string; text: string }[] = []): string[] {
  const lines = [
    "RAINGE — native OMP roundtable",
    `state: ${state.paused ? "PAUSED" : "RUNNING"}`,
    "",
    "Participants:",
  ];
  const members = activeParticipants();
  if (members.length === 0) lines.push("  (none — press i or use /rainge invite)");
  for (const participant of members) {
    lines.push(`  ${participant.alias.padEnd(12)} ${participant.agent.padEnd(12)} ${participant.status}${participant.liveId ? `  id:${participant.liveId}` : ""}`);
  }
  lines.push("", "Inbox — messages to Main (durable):");
  if (arrivals.length === 0) lines.push("  (none — participant replies appear here and stay)");
  for (const arrival of arrivals) {
    const first = arrival.text.split("\n")[0] ?? "";
    const more = arrival.text.includes("\n") || first.length > 64;
    const clipped = first.length > 64 ? first.slice(0, 64) : first;
    lines.push(`  ${arrival.from.padEnd(12)} ${clipped}${more ? "…" : ""}`);
  }
  lines.push("", "Messages (router log):");
  if (state.log.length === 0) lines.push("  (none yet)");
  for (const entry of state.log.slice(-8)) {
    const first = entry.body.split("\n")[0] ?? "";
    const more = entry.body.includes("\n") || first.length > 64;
    const clipped = first.length > 64 ? first.slice(0, 64) : first;
    lines.push(`  ${entry.from}>${entry.to} [${entry.status}] ${clipped}${more ? "…" : ""}`);
  }
  lines.push(
    "",
    "i invite   k kick   p pause   r resume   d decision   t tasks   ? help   q close",
    "Agent Hub: Alt+A",
  );
  return frameLines(lines, width, "Rainge");
}

async function openHelpPanel(ctx: ExtensionContext): Promise<void> {
  if (!ctx.hasUI) {
    ctx.ui.notify(helpText(), "info");
    return;
  }
  await ctx.ui.custom((_tui: unknown, _theme: unknown, _keys: unknown, done: (value: undefined) => void) => ({
    render(width: number): readonly string[] {
      return frameLines(helpText().split("\n"), width, "Rainge help");
    },
    handleInput(data: string): void {
      if (data.toLowerCase() === "q" || data === "\u001b") done(undefined);
    },
    invalidate(): void {},
  }), { overlay: true });
}

async function openPanel(pi: ExtensionApi, ctx: ExtensionContext): Promise<void> {
  if (!ctx.hasUI) {
    ctx.ui.notify(statusText(), "info");
    return;
  }

  await ctx.ui.custom((_tui: unknown, _theme: unknown, _keys: unknown, done: (value: undefined) => void) => {
    const component = {
      render(width: number): readonly string[] {
        return panelLines(width, arrivalTexts(ctx.sessionManager?.getBranch?.() ?? []));
      },
      handleInput(data: string): void {
        const key = data.toLowerCase();
        if (key === "q" || data === "\u001b") {
          done(undefined);
          return;
        }
        if (key === "i") {
          void ctx.ui.input("Agent role to invite", "").then(async (role: string | undefined) => {
            if (role) {
              const result = await invite(pi, role, undefined, cwdOf(ctx));
              ctx.ui.notify(result, result.startsWith("Invited") ? "info" : "warning");
            }
          });
          return;
        }
        if (key === "k") {
          void ctx.ui.input("Participant alias to kick", "").then(async (alias: string | undefined) => {
            if (alias) ctx.ui.notify(await kick(pi, alias), "info");
          });
          return;
        }
        if (key === "p") {
          void setPaused(pi, true).then((result) => ctx.ui.notify(result, "info"));
          return;
        }
        if (key === "r") {
          void setPaused(pi, false).then((result) => ctx.ui.notify(result, "info"));
          return;
        }
        if (key === "d") {
          void ctx.ui.input("Decision to record", "").then(async (text: string | undefined) => {
            if (text) ctx.ui.notify(await decide(pi, text), "info");
          });
          return;
        }
        if (key === "t") {
          ctx.ui.notify("Use /todo or the built-in todo tool for the shared task list.", "info");
          return;
        }
        if (key === "?") void openHelpPanel(ctx);
      },
      invalidate(): void {},
    };
    return component;
  }, { overlay: true });
}

async function runCommand(pi: ExtensionApi, args: string, ctx: ExtensionContext): Promise<void> {
  const tokens = clean(args).split(/\s+/).filter(Boolean);
  const command = tokens.shift() ?? "";
  let result: string | undefined;

  if (command === "") result = await enterMode(pi, ctx);
  else if (command === "panel" || command === "open") await openPanel(pi, ctx);
  else if (command === "help" || command === "?") await openHelpPanel(ctx);
  else if (command === "exit" || command === "leave" || command === "close") result = await exitMode(pi, ctx);
  else if (command === "bus" && tokens[0] === "off") result = closeBus();
  else if (command === "bus" && tokens[0] && tokens[1]) {
    const [host, port] = tokens.slice(1, 3).join(" ").split(":");
    result = await connectBus(pi, tokens[0], busHost(host ?? ""), busPort(port ?? ""), tokens[2]);
  }
  else if (command === "bus") result = busStatus();
  else if (command === "decide") { state.active = true; result = await decide(pi, tokens.join(" ")); }
  else if (command === "status") result = statusText();
  else await openHelpPanel(ctx);

  refreshChrome(ctx);
  if (result) ctx.ui.notify(result, result.startsWith("Usage") || result.startsWith("Participant") ? "warning" : "info");
}

export default function rainge(pi: ExtensionApi): void {
  pi.setLabel("Rainge");
  const z = pi.zod;

  const restoreSession = async (_event: unknown, ctx: ExtensionContext): Promise<void> => {
    // Router-spawned instances arrive with RAINGE_BUS=alias@host:port
    // (plus RAINGE_BUS_ROOM): join the room before the first turn, so queued
    // history is already flowing.
    const configured = agentEnv().RAINGE_BUS ?? "";
    const match = /^([A-Za-z0-9-]+)@([A-Za-z0-9.-]+):(\d+)$/.exec(configured.trim());
    if (match && !bus) {
      const alias = match[1]!;
      await connectBus(pi, alias, match[2]!, Number.parseInt(match[3]!, 10), agentEnv().RAINGE_BUS_ROOM);
      if (ctx?.hasUI) ctx.ui.notify(busStatus(), "info");
      // Seed the bus convention once per process (in-memory flag: a live
      // session never re-gets it) so the first bus turn never spends a
      // doc-exploration roundtrip discovering the protocol.
      if (!conventionSeeded) {
        conventionSeeded = true;
        void pi.sendUserMessage(
          `You are '${alias}' on the rainge bus${agentEnv().RAINGE_BUS_ROOM ? `, room '${agentEnv().RAINGE_BUS_ROOM}'` : ""}. A round asks you to reply once if it asks a question or assigns a task: bus op=send, to='*', ref=<the round's #>, message=<one-line answer in your own words> — otherwise send nothing (a send without a question opens no round). A direct message: reply bus op=send, to=<sender> (operator DMs: just answer in chat). Rounds close themselves. Never poll, never read docs to answer bus chat. No reply needed to this note.`,
          { attribution: "user" },
        );
      }
    }
    state = latestState(ctx);
    if (state.active && state.paused && state.autoPaused) {
      // Back in a session the switch auto-paused: lift only the system pause,
      // never a manual one.
      state.paused = false;
      state.autoPaused = false;
      await persist(pi);
      refreshChrome(ctx);
      if (ctx?.hasUI) ctx.ui.notify("Roundtable auto-resumed after switch.", "info");
      registerCompletion(pi, ctx);
      return;
    }
    refreshChrome(ctx);
    registerCompletion(pi, ctx);
  };
  const restoreBranch = async (_event: unknown, ctx: ExtensionContext): Promise<void> => {
    // Branch/tree snapshots can lag the state entry: never let an entry-less
    // snapshot wipe an active roundtable (and its badge) mid-session.
    const rebuilt = latestState(ctx);
    const empty = !rebuilt.active && rebuilt.participants.length === 0;
    if (!(empty && state.active)) state = rebuilt;
    refreshChrome(ctx);
    registerCompletion(pi, ctx);
  };
  pi.on("session_before_switch", async (_event: unknown, ctx: ExtensionContext): Promise<void> => {
    clearBusReplies();
    // Leaving the session: freeze dispatch so background turns stop chaining
    // while you are away.
    if (state.active && !state.paused) {
      state.paused = true;
      state.autoPaused = true;
      await persist(pi);
      refreshChrome(ctx);
    }
  });
  pi.on("session_start", restoreSession);
  pi.on("session_switch", restoreSession);
  pi.on("session_branch", restoreBranch);
  pi.on("session_tree", restoreBranch);
  pi.on("tool_call", async (event: unknown, ctx: ExtensionContext): Promise<void> => {
    try {
      stashTaskInput(event);
      syncFromBranch(ctx);
      if (recordHubSend(event)) await persist(pi);
    } catch {
      // spawn-id and router-log tracking are advisory; the roundtable works without them
    }
  });
  pi.on("agent_end", (event: unknown): void => {
    try {
      relayBusReplies(event);
    } catch {
      // Automatic bus replies are advisory; never break the agent turn.
    }
  });
  pi.on("tool_result", async (event: unknown, ctx: ExtensionContext): Promise<void> => {
    try {
      let changed = await recordTaskResult(pi, event);
      syncFromBranch(ctx);
      if (recordHubDelivery(event)) {
        await persist(pi);
        changed = true;
      }
      void changed;
    } catch {
      // spawn-id and router-log tracking are advisory; the roundtable works without them
    }
  });
  pi.on("turn_start", async (_event: unknown, ctx: ExtensionContext): Promise<void> => {
    refreshChrome(ctx);
  });
  pi.on("turn_end", async (_event: unknown, ctx: ExtensionContext): Promise<void> => {
    refreshChrome(ctx);
  });

  pi.registerCommand("rainge", {
    description: "Open and control the native OMP roundtable",
    handler: async (args: string, ctx: ExtensionContext) => {
      await runCommand(pi, args, ctx);
    },
  });

  pi.registerTool({
    name: "rainge",
    label: "Rainge",
    description: "Control the native OMP roundtable: invite or remove task participants, pause/resume dispatch, record decisions, and inspect status.",
    parameters: z.object({
      op: z.string(),
      agent: z.string().optional(),
      alias: z.string().optional(),
      text: z.string().optional(),
    }),
    async execute(_toolCallId: string, params: { op: string; agent?: string; alias?: string; text?: string }, _signal: AbortSignal | undefined, _onUpdate: unknown, ctx: ExtensionContext) {
      const result = params.op === "invite"
        ? await invite(pi, params.agent ?? "", params.alias, cwdOf(ctx))
        : params.op === "kick"
        ? await kick(pi, params.alias ?? "")
        : params.op === "pause"
        ? await setPaused(pi, true)
        : params.op === "resume"
        ? await setPaused(pi, false)
        : params.op === "decide"
        ? await decide(pi, params.text ?? "")
        : params.op === "status"
        ? statusText()
        : `Unknown operation '${params.op}'.`;
      refreshChrome(ctx);
      if (ctx.hasUI) ctx.ui.notify(result, "info");
      return { content: [{ type: "text", text: result }], details: { state } };
    },
  });
  pi.registerTool({
    name: "bus",
    label: "Bus",
    description: "Federated rainge bus (chat v2). op=send: to='*' answers the open round (cite ref=<open seed #>) or starts one; to=<alias> direct-messages one participant. Rounds close themselves. op=list: roster and round state; op=status: connection.",
    parameters: z.object({
      op: z.string(),
      to: z.string().optional(),
      message: z.string().optional(),
      note: z.string().optional(),
      action: z.string().optional(),
      target: z.string().optional(),
      ref: z.number().optional(),
    }),
    async execute(_toolCallId: string, params: { op: string; to?: string; message?: string; note?: string; action?: string; target?: string; ref?: number }) {
      const result = params.op === "send"
        ? await busSend(params.to ?? "", params.message ?? "", params.note, params.action, params.target, params.ref)
        : params.op === "list"
        ? await busList()
        : params.op === "status"
        ? busStatus()
        : params.op === "close"
        ? closeBus()
        : `Unknown operation '${params.op}'.`;
      return { content: [{ type: "text", text: result }], details: { bus: bus ? { alias: bus.alias, addr: bus.addr, room: bus.room, seq: bus.cursor } : null } };
    },
  });
  pi.registerTool({
    name: "hub",
    label: "Hub",
    description: "Message peers, wait for replies, and inspect async jobs and processes. During a rainge roundtable, a send addressed to a participant alias routes by that participant's exact live id.",
    parameters: z.object({
      i: z.string().optional(),
      op: z.string().optional(),
      to: z.string().optional(),
      message: z.string().optional(),
      replyTo: z.string().optional(),
      from: z.string().optional(),
      ids: z.array(z.string()).optional(),
      peek: z.boolean().optional(),
      status: z.string().optional(),
      limit: z.number().optional(),
      name: z.string().optional(),
      application: z.string().optional(),
      args: z.array(z.string()).optional(),
      env: z.object({}).optional(),
      cwd: z.string().optional(),
      pty: z.boolean().optional(),
      ready: z.object({}).optional(),
      restart: z.string().optional(),
      persist: z.boolean().optional(),
      detached: z.boolean().optional(),
      lines: z.number().optional(),
      head: z.boolean().optional(),
      grep: z.string().optional(),
      follow: z.boolean().optional(),
      cursor: z.number().optional(),
      for: z.string().optional(),
      pattern: z.string().optional(),
      text: z.string().optional(),
      enter: z.boolean().optional(),
      keys: z.array(z.string()).optional(),
      signal: z.string().optional(),
      timeout: z.number().optional(),
    }),
    async execute(_toolCallId: string, params: { op?: string; to?: string; message?: string; timeout?: number }, signal: AbortSignal | undefined, onUpdate: unknown, ctx: ExtensionContext) {
      return hubExecute(params, params, signal, onUpdate, ctx.invokeTool);
    },
  });
  pi.on("irc_message", async (event: unknown, ctx: ExtensionContext): Promise<void> => {
    // Probe: the host emits this session event for relay observations and
    // autoreplies. If it reaches extensions, arrivals log in realtime with
    // no wait required; if not, the wait path still collects everything.
    try {
      const message = eventField(event, "message");
      const target = message !== undefined ? message : event;
      const details = eventField(target, "details");
      const body = eventText(details, "body") || eventText(details, "message");
      const from = eventText(details, "from").toLowerCase();
      if (from === "" || body === "") return;
      const envelope = parseEnvelope(body);
      syncFromBranch(ctx);
      if (logRouted(from, envelope.to, envelope.status, envelope.text)) {
        ircLive = true;
        await persist(pi);
        refreshChrome(ctx);
      }
    } catch {
      // probe is advisory; the roundtable works without it
    }
  });
}

// Hub shadow: alias rewriting only. Shadowing replaces the model-facing
// contract, so the schema mirrors the native arg surface and every call
// delegates through ctx.invokeTool (same-tool delegation runs the native
// built-in with our params). A send addressed to a rostered alias is
// rewritten to that participant's exact live id; waits, lists, jobs, and
// process ops pass through untouched. Collection stays native — parallel
// DMs with multi-id waits, or the reporter pattern.
type InvokeFn = (params: Record<string, unknown>, opts?: { signal?: unknown; onUpdate?: unknown }) => Promise<unknown>;

let ircLive = false;

function hubArgsOf(params: unknown): Record<string, unknown> {
  if (typeof params !== "object" || params === null) return {};
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(params)) out[key] = value;
  return out;
}

async function hubExecute(
  params: { op?: string; to?: string },
  raw: unknown,
  signal: AbortSignal | undefined,
  onUpdate: unknown,
  invoke: InvokeFn | undefined,
): Promise<{ content: { type: string; text: string }[]; details: unknown; isError?: boolean }> {
  if (!invoke) return { content: [{ type: "text", text: "hub unavailable" }], details: {}, isError: true };
  const args = hubArgsOf(raw);
  const to = (params.to ?? "").trim().toLowerCase();
  if ((params.op ?? "") === "send" && to !== "" && to !== "all") {
    const participant = activeParticipants().find((item) => item.alias === to && item.liveId);
    if (participant) args.to = participant.liveId;
  }
  return invoke(args, { signal, onUpdate }) as Promise<{ content: { type: string; text: string }[]; details: unknown }>;
}
