# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read `AGENTS.md` first — it is the canonical, detailed development guide for this repo** (contribution rubric, footprint ladder, plugin/skill/toolset authoring, slash-command registry, profiles, curator, cron, kanban, known pitfalls). This file is a condensed index; when in doubt, AGENTS.md wins.

## What Hermes Is

A personal AI agent running one agent core (`run_agent.py::AIAgent`) across a CLI (`cli.py`), a messaging gateway (`gateway/` — Telegram, Discord, Slack, WhatsApp, ~20 platforms), an Ink TUI (`ui-tui/` + `tui_gateway/`), a web dashboard (`web/`), and an Electron desktop app (`apps/desktop/`). Extended through **plugins and skills**, not by growing the core.

Two invariants shape every design decision:

1. **Per-conversation prompt caching is sacred.** Never mutate past context, swap toolsets, or rebuild the system prompt mid-conversation (only exception: context compression). Never break strict message-role alternation.
2. **The core is a narrow waist.** Every core model tool ships on every API call. New capability goes to the highest rung of the Footprint Ladder that works: extend existing code → CLI command + skill → service-gated tool (`check_fn`) → plugin → MCP catalog server → new core tool (last resort). See AGENTS.md "The Footprint Ladder".

## Commands

```bash
source .venv/bin/activate          # or venv/ — run_tests.sh probes both

# Tests — ALWAYS via the wrapper, never bare pytest (CI-parity env:
# blanked credentials, TZ=UTC, per-file subprocess isolation)
scripts/run_tests.sh                                  # full suite
scripts/run_tests.sh tests/gateway/                   # one directory
scripts/run_tests.sh tests/agent/test_foo.py::test_x  # single test
scripts/run_tests.sh tests/foo.py -v --tb=long        # pytest flags pass through

# Lint / typecheck (Python)
ruff check .                       # only PLW1514 enforced (explicit encoding=)
ty check                           # type checker (targets py3.13)
```

TUI (from `ui-tui/`): `npm run dev` (watch), `npm run build`, `npm run typecheck`, `npm run lint`, `npm test` (vitest). Root `package.json` is an npm workspace (`apps/*`, `ui-tui`, `web`); desktop tests run via the repo-root vitest.

Integration tests are excluded by default (`addopts = "-m 'not integration'"`).

## Architecture

Dependency chain: `tools/registry.py` ← `tools/*.py` (self-register at import) ← `model_tools.py` (discovery + dispatch) ← `run_agent.py` / `cli.py` / `batch_runner.py`.

- `run_agent.py` — `AIAgent`, the synchronous conversation loop (~12k LOC). OpenAI-format messages.
- `model_tools.py` — tool orchestration, `discover_builtin_tools()`, `handle_function_call()`, plugin hooks.
- `toolsets.py` — `TOOLSETS` dict + `_HERMES_CORE_TOOLS`. A tool registered in `tools/` is only exposed if its name appears in a toolset — wiring is a deliberate manual step.
- `cli.py` — `HermesCLI` (~11k LOC). Slash commands are defined once in `COMMAND_REGISTRY` (`hermes_cli/commands.py`); CLI, gateway, Telegram menu, Slack routing, help, and autocomplete all derive from it.
- `gateway/` — `run.py` + `session.py` + `platforms/` (one adapter per platform; see `ADDING_A_PLATFORM.md`).
- `agent/` — provider adapters, memory manager, compression, auxiliary LLM client, curator, skill commands.
- Plugin surfaces under `plugins/`: general (`hermes_cli/plugins.py`, `register(ctx)`), memory providers (ABC in `agent/memory_provider.py` — **closed set**, new ones ship as standalone repos), model providers (`plugins/model-providers/`, separate lazy discovery via `providers/__init__.py`), context engines, image gen. Plugins MUST NOT modify core files.
- Skills: `skills/` (bundled, active) vs `optional-skills/` (shipped, not active by default). Authoring standards are HARDLINE — see AGENTS.md "Skill authoring standards" (description ≤ 60 chars, section order, `platforms:` gating, tests in `tests/skills/`).

### Config

- `~/.hermes/config.yaml` = all behavioral settings; `~/.hermes/.env` = **secrets only** (API keys, tokens). Never route non-secret config through new `HERMES_*` env vars.
- Three config loaders — know which path you're on: `load_cli_config()` (cli.py), `load_config()` (`hermes_cli/config.py`, most subcommands), raw YAML (gateway). New keys go in `DEFAULT_CONFIG`; bump `_config_version` only for migrations, not for added keys.
- **Profiles:** always `get_hermes_home()` / `display_hermes_home()` from `hermes_constants` — never hardcode `~/.hermes`.

## Critical Rules

- **Dependency pinning:** core deps in `pyproject.toml` are exact-pinned (`==X.Y.Z`); everything else needs an upper bound. Run `uv lock` after changes. Opt-in backends go in extras + `tools/lazy_deps.py`, not core deps.
- **No change-detector tests.** Assert invariants/relationships, not snapshots of model lists, config version literals, or enumeration counts.
- **Tests must not touch `~/.hermes/`** — the autouse `_isolate_hermes_home` fixture redirects `HERMES_HOME`; profile tests must also mock `Path.home()` (pattern in `tests/hermes_cli/test_profiles.py`).
- **Verify the premise before fixing.** Reproduce on current `main` and point to the exact line where the bug manifests; apparent gaps are often intentional design (`git log -p -S "<symbol>"`).
- **Explicit `encoding=` on all text-mode file I/O** (ruff PLW1514) — locale defaults corrupt non-ASCII on Windows.
- Cache-mutating slash commands default to deferred invalidation with an opt-in `--now` flag.
- New interactive menus use `hermes_cli/curses_ui.py`, not `simple_term_menu`.
- Do not re-implement the primary chat experience in React — the dashboard embeds the real `hermes --tui`; extend Ink instead. Desktop app is a separate surface with its own composer.
- Skill slash commands inject as **user messages**, not system prompt (preserves caching).
- TypeScript style (desktop/TUI/web): nanostores per feature, `useStore` in rendering components / `$atom.get()` in actions, interfaces over type aliases for props, thin route roots. Full list in AGENTS.md "TypeScript Style".
