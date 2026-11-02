# vendor/codex-windows-sandbox/

Source: https://github.com/openai/codex, commit `be33f80bc65159c094ecd06bf155afa3061ce23d`
(cloned 2026-07-05). Apache 2.0 (LICENSE + NOTICE in this folder are copied
verbatim from that commit, as required for redistribution).

`windows-sandbox-rs-src/` is the `codex-windows-sandbox` crate's source,
copied here **for reference and audit** — every design decision documented
in `docs/WINDOWS_SANDBOX.md` was verified against this exact code, not
guessed from blog posts. It is **not standalone-buildable**: it's a member
of the larger `codex-rs` Cargo workspace and depends on sibling crates
(`codex-protocol`, `codex-utils-absolute-path`, etc.) that aren't vendored
here, to avoid the real risk of a hand-extracted, partially-vendored
dependency tree that looks complete but fails to compile (or subtly
diverges from upstream) in a way nobody can catch without a Windows machine
to test on.

## Building the two binaries this project needs (run on Windows)

```powershell
# 1. Clone the exact pinned commit — do not use a later commit without
#    re-verifying against docs/WINDOWS_SANDBOX.md first, since the IPC
#    protocol version or CLI surface could change upstream.
git clone https://github.com/openai/codex.git codex-upstream
cd codex-upstream
git checkout be33f80bc65159c094ecd06bf155afa3061ce23d

# 2. Build just the two binaries Galaxy needs (Rust + MSVC toolchain required)
cd codex-rs
cargo build --release -p codex-windows-sandbox `
  --bin codex-windows-sandbox-setup --bin codex-command-runner

# 3. Copy the built artifacts into Galaxy's vendor/bin/ folder
mkdir ..\..\bin -Force
copy target\release\codex-windows-sandbox-setup.exe ..\..\bin\
copy target\release\codex-command-runner.exe ..\..\bin\
```

`security/windows_sandbox.py` looks for both `.exe` files under
`vendor/codex-windows-sandbox/bin/` by default (overridable via
`run_sandboxed(..., vendor_dir=...)`). Until they're placed there,
`run_sandboxed()` returns a clear "vendored sandbox binaries not found"
error rather than failing confusingly later.

## What has and hasn't been verified

See `docs/WINDOWS_SANDBOX.md` for the full breakdown. Short version: the
Python-side IPC protocol logic (frame encode/decode, message shapes,
timeout handling) is covered by real, executing tests
(`tests/test_windows_sandbox.py`) using a mock runner over `os.pipe()` —
that part is genuinely verified, the same standard as the rest of this
project. The Windows-OS-specific glue (named pipes, `CreateProcessWithLogonW`,
DPAPI credential decryption) was written by reading this vendored source
precisely, but has **not** been executed anywhere — there is no Windows
machine available in this development environment. That needs real-machine
testing before it's trusted the way everything else here has been.
