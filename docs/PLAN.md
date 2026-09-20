# Rainge implementation plan

## Goal

Provide one native OMP roundtable surface where the user can invite configured task agents, observe them, record decisions, and control dispatch priority.

## Fixed decisions

1. OMP owns every model turn. No external harness subprocesses, tmux coordinator, static member registry, or side-panel plugin system. One deliberate exception: the stdlib-only bus router (`router/bus_router.py`) as non-LLM transport between whole omp processes — numbered transcript, no judgment, no model calls.
2. OMP's `task` tool creates participants and owns concurrency, child sessions, models, output, and cancellation.
3. Agent Hub is the live observation/control surface. Rainge's custom TUI is the membership and dispatch control panel.
4. `/todo` owns shared tasks. Namespaced OMP session entries own Rainge state and decisions.
5. User prompts and `/rainge` controls steer the main session with priority.
6. tmux is optional hosting for the OMP terminal only.

## Entry points

- `extension/rainge.ts`: extension factory, commands, tool, mode badge, overlay panel, state replay.
- `skills/rainge/SKILL.md`: moderator and participant contract.
- `package.json` + `install.sh`: plugin manifest and linker (`omp plugin link`).
- `router/bus_router.py` + `router/bus_panel.py`: federated transport and operator panel (serve/kill/stop/clean CLI, context-gated commands, stored member sessions).

## Commands

```text
/rainge
/rainge panel
/rainge exit
/rainge invite <agent|role> [alias]
/rainge kick <alias>
/rainge pause
/rainge resume
/rainge status
/rainge decide <text>
```

## State contract

```text
customType: dev.rainge.state.v1
customType: dev.rainge.decision.v1
```

State is restored from `sessionManager.getBranch()`. The extension does not write files outside OMP's session persistence.

## Verification contract

Offline checks must prove:

- installer syntax and native destination paths;
- skill frontmatter and native-only instructions;
- extension source contains the required command/tool/state contracts;
- router and panel keep every protocol and command gate pinned (`tests/verify.sh`);
- the extension parses under a real omp run (member joins ride on it).

Interactive proof must exercise `/rainge`, invite, panel rendering, and a native task dispatch in an OMP session where credentials are available. If no suitable non-network runtime is available, report that limitation rather than claiming a live dispatch was tested.
