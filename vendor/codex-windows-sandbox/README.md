# vendor/codex-windows-sandbox/

Source: https://github.com/openai/codex, commit `be33f80bc65159c094ecd06bf155afa3061ce23d`
(cloned 2026-07-05). Apache 2.0 (LICENSE + NOTICE in this folder are copied
verbatim from that commit, as required for redistribution).

`windows-sandbox-rs-src/` is the `codex-windows-sandbox` crate's source,
copied here **for reference and audit only**. There is no
`security/windows_sandbox.py` in this repository and no
`docs/WINDOWS_SANDBOX.md` — both were deleted in commit `76a50e9`, so
nothing in this repo was verified against them and nothing here depends on
them. The Rust source is vendored so the design can still be audited; the
Python IPC layer was **never implemented**. It is **not
standalone-buildable**: it's a member of the larger `codex-rs` Cargo
workspace and depends on sibling crates
(`codex-protocol`, `codex-utils-absolute-path`, etc.) that aren't vendored
here, to avoid the real risk of a hand-extracted, partially-vendored
dependency tree that looks complete but fails to compile (or subtly
diverges from upstream) in a way nobody can catch without a Windows machine
to test on.

## Building the two binaries this project needs (run on Windows)

```powershell
# 1. Clone the exact pinned commit — do not use a later commit without
#    re-verifying against the vendored Rust source first, since the IPC
#    protocol version or CLI surface could change upstream.
git clone https://github.com/openai/codex.git codex-upstream
cd codex-upstream
git checkout be33f80bc65159c094ecd06bf155afa3061ce23d

# 2. Build just the two binaries this repo needs (Rust + MSVC toolchain required)
cd codex-rs
cargo build --release -p codex-windows-sandbox `
  --bin codex-windows-sandbox-setup --bin codex-command-runner

# 3. Copy the built artifacts into this repo's vendor/bin/ folder
mkdir ..\..\bin -Force
copy target\release\codex-windows-sandbox-setup.exe ..\..\bin\
copy target\release\codex-command-runner.exe ..\..\bin\
```

No code in this repository looks for those two `.exe` files: there is no
`security/windows_sandbox.py` here (it was deleted in commit `76a50e9`).
They are built for reference only — `Bash` runs natively behind the
Capability Gate today (see `docs/SECURITY.md` §6).

## What has and hasn't been verified

Short version: the Python-side IPC protocol logic (frame encode/decode,
message shapes, timeout handling) was **never implemented in this
repository**, so there is no executing test for it — the module and its
test (`security/windows_sandbox.py`, `tests/test_windows_sandbox.py`) were
deleted in commit `76a50e9`. The Windows-OS-specific glue (named pipes,
`CreateProcessWithLogonW`, DPAPI credential decryption) has **not** been
executed anywhere — there is no Windows machine available in this
development environment. That needs real-machine testing before it's
trusted the way everything else here has been.
