# MCP client rewrite + agent integration (2026-07)

## Why this happened
While confirming the "bring-your-own-credential" comparison to Antigravity,
inspection of `connectors/mcp_client.py` found that its own docstring
claimed three security guarantees none of which were actually enforced in
code — confirmed by the wizard printing "read-only, egress limited to..."
unconditionally regardless of what the code did. Per the project's own
"enforcement before expansion" principle, these were fixed before building
the MCP directory/store layer on top.

## Bugs found and fixed (all confirmed via live testing, not just reading)

1. **No protocol handshake.** The old code sent `tools/call` directly with
   no `initialize` first, and read exactly one stdout line per call assuming
   it was the response. Verified live against the official
   `@modelcontextprotocol/server-everything` reference server that the very
   first `tools/list` call returns a `notifications/tools/list_changed`
   push *before* the real response — the old code would have parsed that
   notification as if it were the answer. Fixed: real `initialize` →
   `notifications/initialized` → `tools/list` handshake, matching responses
   to requests by JSON-RPC id and skipping notifications in between.

2. **`read_only=True` was declared but never checked anywhere.** Any tool
   could be called on a "read-only" server. Fixed: `_tool_is_read_only()`
   checks the tool's own MCP `readOnlyHint` annotation when a server
   provides one, falling back to a name heuristic otherwise (verified live:
   the reference server does *not* populate annotations, so the fallback is
   the common path in practice, not a rare edge case). The heuristic was
   itself extended mid-build after live tool names like
   `toggle-simulated-logging` and `trigger-long-running-operation` revealed
   gaps in the initial write-verb list.

3. **`declared_hosts` (egress filtering) was stored but never enforced.**
   Any MCP server subprocess had full, unrestricted network access
   regardless of what a user declared. Fixed: each server now launches with
   `HTTP_PROXY`/`HTTPS_PROXY` pointed at a per-server local proxy
   (`_EgressProxy`) that only tunnels CONNECT requests to declared hosts (+
   localhost) — verified with real allow/deny cases. **Honest limitation,
   stated once:** this only restricts subprocesses that honor standard
   proxy env vars (most Node/Python HTTP clients do by default); it is not
   a kernel-level sandbox, and a deliberately malicious server using raw
   sockets could bypass it. A real namespace/iptables sandbox needs
   privileges Universe doesn't assume it has — flagged as a follow-up, not
   silently implied to be solved.

4. **Real bug found only because of live testing, not mocks:** enforcing
   egress with an empty `declared_hosts` broke every `npx`-launched server
   outright — `npx` checks `registry.npmjs.org` for version metadata even
   on an already-cached package, and `npm` treats a 403 there (our proxy
   correctly denying an undeclared host) as fatal rather than falling back
   to cache. Fixed by auto-allowing each package manager's own bootstrap
   host(s) (`registry.npmjs.org` for npx/npm, `pypi.org` for uvx/pip/uv)
   *separately* from the server's own declared_hosts — package resolution
   is a different trust boundary than the server's own runtime calls.
   `tests/test_mcp_client.py::test_npx_server_actually_starts_with_egress_enforced`
   locks this in as a regression test.

5. **Blocking sync subprocess I/O with no timeout**, and **shell=True with
   an unsplit command string** (shell-injection-shaped, even if the
   practical risk was low since only the local user configures it). Fixed:
   `asyncio.create_subprocess_exec` with `shlex.split()`, and a 20s timeout
   on response reads.

6. **Zero integration with the CapabilityGate.** No agent could actually
   call an MCP tool during real session execution before this — `mcp_client.py`
   was only reachable from the CLI wizard's add/list flow. Fixed: new
   `connectors/builtin/mcp_tools.py` registers `McpAddServer` (explicit
   consent — runs third-party code), `McpListServers` (auto — pure local
   read), `McpRemoveServer` (explicit), and `McpCallTool` (per_turn —
   the server's own read_only/allowlist gating happens inside
   `MCPClient.call()` regardless). All four are on the `api` agent's
   whitelist and go through the exact same `enforce()` path — including the
   per-tool consent fix from earlier — as every other tool in Universe.

## What "opening Universe to the MCP store" means here (v1, modest)

A small curated directory (`KNOWN_SERVERS` in `mcp_client.py`) of
verified-launchable official servers: `everything` (reference/test),
`filesystem`, `memory`, `postgres`, `git`. `mcp_add_server(server_key=...)`
fills in the launch command automatically. **`server-github` is
deliberately not included** — confirmed on npm (2026-07) that the package
is deprecated in favor of a separate Go binary with a different install
method; Universe already has native `github_repo_info`/`github_create_issue`
in `thirdparty.py` anyway. This is a starting shelf, not a replacement for
the full `registry.modelcontextprotocol.io` community registry.

## Known, honest gaps (not silently dropped)

- **Per-tool consent granularity** (matching thirdparty.py's per-operation
  explicit/per_turn split) isn't implemented for MCP tools — `McpCallTool`
  is one generic dispatcher gated at `per_turn` overall; the server's own
  read_only flag is what actually blocks write-like calls. Finer-grained
  per-discovered-tool consent would need a runtime-whitelist-mutation
  mechanism that doesn't exist yet.
- **Plain (non-TLS) HTTP is not proxied at all** — only CONNECT/HTTPS
  tunneling is implemented. Fails closed (501) rather than silently
  forwarding plaintext unchecked.
- **No kernel-level sandboxing** — see point 3 above.
- Native connector batch 2 (the ~170 remaining of "top 200") is still
  pending — this session went into MCP enforcement instead, per the
  "enforcement before expansion" principle already agreed.

## Verification summary
- 23 new tests in `tests/test_mcp_client.py`, the majority running against
  the *real* `@modelcontextprotocol/server-everything` subprocess (not
  mocked) — this is what caught bugs #1 and #4 above; a mocked subprocess
  would have hidden both.
- Full existing suite: 137 passed (up from 115), same 8 pre-existing
  Chromium-only failures (environment, unrelated), 3 skipped (Docker, DDG,
  and this run's GitHub rate-limit — all environment/external, not code).
- Eval suite: 15/15, unchanged.

## Update (post-spec-review, same day): crash-loop supervision de-duplicated
The initial rewrite above tracked crash-loop state (`crash_times`/`paused`)
directly on `MCPServer`, not realizing `concurrency/supervisor.py::Supervisor`
already existed and does exactly this (§6, §14 — it also enforces the
unrelated 4-concurrent-Moons-per-Planet limit). Fixed: `mcp_client.py` now
calls `get_supervisor().record_crash()`/`.is_paused()` instead of tracking
state independently. Found by cross-checking this file against the master
spec after it was made available, not by a bug report — no behavior change,
same 23 tests still pass, just one source of truth instead of two that
could have drifted apart.
