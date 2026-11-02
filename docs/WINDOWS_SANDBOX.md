# Native Windows sandbox for shell.exec (no WSL2 required)

## Why

Windows without WSL2 previously had no sandboxing story for `Bash` at
all — every command ran directly on the real system. This closes that gap
using OpenAI's own open-sourced Windows sandbox (Codex, Apache 2.0, shipped
March 2026), rather than inventing a new Windows sandboxing approach from
scratch — verified by cloning the actual source
(`vendor/codex-windows-sandbox/`, pinned at commit `be33f80b...`) and
reading it directly, not from marketing material.

## The design point that mattered

The original request was: if sandbox setup fails, fall back to running
directly on the system by default, with a warning notification. Checking
OpenAI's own source and docs first:

- Their real fallback chain is **Elevated → RestrictedToken ("unelevated")
  → Disabled**, and the transition to Elevated → Unelevated is automatic.
- The transition to **Disabled is never automatic**. Its own config key is
  literally named `"danger-full-access"` (`codex-rs/protocol/src/config_types.rs`),
  and their docs explicitly warn it "might perform unintentional destructive
  actions that can lead to data loss."

Universe mirrors this exactly:
- `security.windows_sandbox.run_sandboxed()` tries **elevated**, then
  automatically **unelevated**. It never returns "run it unsandboxed" on
  its own.
- Fully unsandboxed execution is `BashUnsandboxed` in
  `connectors/builtin/shell.py` — a **separate tool**, `consent="explicit"`,
  gated through Universe's own `CapabilityGate` (the same mechanism already
  used for `DockerBuild`/`Ssh`) rather than a bespoke notify-and-proceed
  mechanism.

## Protocol, traced from the actual source

`codex-command-runner.exe` is not a simple CLI wrapper — it's a persistent
process speaking a length-prefixed JSON protocol over two named pipes
(confirmed live during development; an earlier design assumption that it
was a one-shot CLI call was wrong and caught before any code was written
against it):

```
[4-byte little-endian length][JSON payload]
```

Message envelope: `{"version": 4, "type": "<snake_case>", "payload": {...}}`

| Direction | Types |
|---|---|
| Parent → Runner | `spawn_request`, `stdin`, `close_stdin`, `resize`, `terminate` |
| Runner → Parent | `spawn_ready`, `output`, `exit`, `error` |

`spawn_request.payload` fields: `command` (list[str]), `cwd`, `env` (dict),
`permission_profile` (`{"type": "managed", "file_system": ..., "network":
"restricted"|"enabled"}`), `workspace_roots`, `codex_home`,
`real_codex_home`, `cap_sids`, `timeout_ms`, `tty`, `stdin_open`,
`use_private_desktop`. `output.payload` is base64 `data_b64` + `stream`
(`stdout`/`stderr`). `exit.payload` is `exit_code` + `timed_out`.

Exact shapes were checked against `ipc_framed.rs`'s own unit tests (which
assert precise JSON output) rather than inferred from the struct
definitions alone — `tests/test_windows_sandbox.py`'s
`test_spawn_request_shape_matches_upstream_exactly` mirrors that upstream
test directly.

### Launch sequence (parent side, `runner_client.rs`)
1. Create two named pipes, ACL'd to the sandbox user only.
2. Launch `codex-command-runner.exe` via `CreateProcessWithLogonW` as that
   low-privilege user, with `--pipe-in=`/`--pipe-out=` pointing at them.
3. `ConnectNamedPipe` on both, then speak the protocol above.

### Credentials (`identity.rs`)
The setup binary writes `sandbox_users.json` (two records: `offline` and
`online`, matching the `network: restricted|enabled` distinction) with each
password **DPAPI-encrypted, base64-encoded** — the standard Windows secret-
protection API (`CryptProtectData`/`CryptUnprotectData`), the same category
of mechanism `security/secrets_fallback.py` already uses on other platforms
(AES + OS keyring), not custom cryptography. Universe reads this file
directly and calls the same DPAPI unprotect step via `pywin32`.

## A real bug this caught

`_drive_protocol()`'s first draft checked a wall-clock deadline only
*between* blocking `read()` calls on the pipe. Directly reproduced during
development: a plain blocking read on an open-but-silent pipe **never
returns** — confirmed with a 1.5-second `os.pipe()` test that stayed
blocked. That means a genuinely hung sandboxed process (or runner) would
have hung Universe forever, despite `timeout_s` appearing to promise a
bounded wait. Fixed by moving reads to a background thread and enforcing
the timeout via `queue.get(timeout=...)` instead — verified with a mock
runner that holds its pipe open and silent (an earlier version of that
same test accidentally closed the pipe immediately, which exercises a
different, already-handled code path and would have hidden this bug).

## Verification status — read this before trusting this in production

**Genuinely tested, executing, in `tests/test_windows_sandbox.py`:**
frame encode/decode, spawn_request shape, the full `_drive_protocol()`
state machine (success, non-zero exit, runner error, closed-before-ready,
genuine hang/timeout, forward-compatible unknown message types) via a mock
runner speaking the real protocol over `os.pipe()`. Platform/WSL2
detection logic. `run_sandboxed()`'s missing-binaries guard clause.

**Written from precise source reading, NOT executed anywhere — the vendored
Rust binaries have not been built on any machine yet:**
`_win_load_credentials` (DPAPI unprotect + JSON parsing), `_win_run_via_runner`
(named pipe creation with ACLs, `CreateProcessWithLogonW`, `ConnectNamedPipe`),
and the `_PipeReadAdapter`/`_PipeWriteAdapter` glue. Until
`vendor/codex-windows-sandbox/bin/*.exe` is built (see that directory's
README), `run_sandboxed()` fails closed with a binaries-not-found error and
`Bash` on Windows points the user at `BashUnsandboxed`
(explicit consent). These need real-machine testing on Windows before this
is trusted to the same standard as the rest of this project.

## Known gaps (deferred, not silently dropped)
- Only `ManagedFileSystemPermissions::Unrestricted` is implemented;
  per-path `Restricted{entries}` (fine-grained write allowlists) is a
  documented follow-up.
- `stdin`/`resize`/`terminate` (interactive PTY control) aren't wired —
  `Bash` doesn't need live interactivity today.
- No automatic vendoring/build pipeline — see `vendor/codex-windows-sandbox/README.md`
  for the manual build steps required on a real Windows machine before
  first use.
