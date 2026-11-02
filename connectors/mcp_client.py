"""connectors/mcp_client.py — MCP server connection manager.

§6, §25 Phase 5 ㉑. REWRITE (2026-07): the previous version of this file
declared three security guarantees in its own docstring that were never
actually enforced in code — confirmed by direct inspection and by the fact
that the wizard's own confirmation message ("read-only, egress limited to
...") was printed unconditionally regardless of what the code actually did.
This rewrite makes all three real:

  1. PROTOCOL CORRECTNESS: real servers require an `initialize` handshake
     before anything else, and can interleave asynchronous notifications
     with responses. The old code skipped the handshake entirely and read
     exactly one line per call, assuming it was the response — verified
     live against the official `@modelcontextprotocol/server-everything`
     reference server that this breaks immediately (the very first
     tools/list call returns a `notifications/tools/list_changed` push
     before the real response). Fixed by matching responses on JSON-RPC id.

  2. READ-ONLY ENFORCEMENT: `read_only=True` (the default) now actually
     blocks tools determined to be non-read-only, using the tool's own MCP
     annotations (`readOnlyHint`) when the server provides them, falling
     back to a name-based heuristic otherwise (verified against the
     reference server: it does not populate annotations, so the heuristic
     path is the common case in practice, not a rare fallback).

  3. EGRESS FILTERING: MCP servers run as subprocesses launched with
     HTTP_PROXY/HTTPS_PROXY pointed at a local per-server proxy (_EgressProxy
     below) that only tunnels CONNECT requests to declared_hosts (+
     localhost). HONEST LIMITATION, stated once here rather than repeated
     in every docstring: this is defense-in-depth against a well-behaved
     subprocess that honors standard proxy env vars (the large majority of
     Node/Python HTTP clients do by default) — it is NOT a kernel-level
     sandbox, and a deliberately malicious server using raw sockets could
     still bypass it. A real network-namespace/iptables-based sandbox is a
     larger undertaking (needs root/capabilities Universe doesn't assume it
     has) and is noted as a follow-up, not silently implied to be solved.

Crash-loop supervision (3 crashes in 5 min -> pause) delegates to the
existing `concurrency/supervisor.py::Supervisor` — a first pass of this
rewrite duplicated that exact logic inline here instead of using it, caught
during a later spec cross-check and fixed. `Supervisor` is also what
enforces the unrelated 4-concurrent-Moons-per-Planet limit (§14), so
sharing one instance keeps both concerns in the single module the build
order actually assigns them to, rather than tracking MCP crashes in two
places that don't agree with each other.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.agent.base_agent import new_id
from storage.local import get_storage

MCP_PROTOCOL_VERSION = "2024-11-05"  # current stable spec as of 2026-07;
# a 2026-07-28 spec is expected but not yet released — see MCP TypeScript
# SDK v2 beta notes checked while building this.

# One JSON-RPC line bigger than this is never a legitimate MCP message —
# it is a runaway or hostile server. Raised as an error (callers already
# convert exceptions into `{"ok": False, ...}` dicts), never buffered
# without bound: a single unbounded readline() would let one bad server
# OOM the whole loop.
_MAX_MCP_LINE_BYTES = 16 * 1024 * 1024

# Tool names containing any of these are treated as non-read-only when the
# server doesn't supply a readOnlyHint annotation. Best-effort, not a
# guarantee — documented plainly rather than implied to be exhaustive.
_WRITE_HINTS = ("create", "delete", "remove", "write", "update", "send",
                "post", "put", "patch", "execute", "run", "modify", "upload",
                "publish", "deploy", "drop", "truncate", "kill", "terminate",
                "insert", "set", "toggle", "enable", "disable", "register",
                "unregister", "reset", "clear", "cancel", "trigger",
                # m4 additions (state-mutating verbs seen in the wild that the
                # narrower list let through as read-only):
                "edit", "move", "save", "commit", "append", "rename", "init",
                "grant", "install", "launch")
# ^ Extended after live-testing against the real reference server, whose
# tool list includes "toggle-simulated-logging" and
# "trigger-long-running-operation" — clearly state-mutating names that the
# original, narrower list would have missed and treated as read-only. This
# heuristic can never be exhaustive (a tool author can name a write
# operation anything); annotations remain the authoritative signal when a
# server provides them.

# Package-manager bootstrap hosts, auto-allowed on the egress proxy
# SEPARATELY from a server's user-declared_hosts. Discovered necessary by
# live-testing against the real reference server: `npx -y <pkg>` checks
# registry.npmjs.org for version metadata EVEN when the package is already
# cached locally, and treats a 403 (our proxy correctly denying an
# undeclared host) as fatal rather than falling back to the cache — the
# server subprocess never even starts. This is a genuinely different trust
# boundary from the server's own runtime calls: resolving/fetching the
# package is package-manager bootstrap traffic (the same trust level Universe
# already extends to `pip install`/`npm install` elsewhere, unrestricted),
# whereas declared_hosts governs what the SERVER itself may reach once it's
# live and handling tool-call arguments — those remain fully restricted.
_PACKAGE_MANAGER_HOSTS = {
    "npx": ["registry.npmjs.org", "npmjs.org", "npmjs.com"],
    "npm": ["registry.npmjs.org", "npmjs.org", "npmjs.com"],
    "uvx": ["pypi.org", "files.pythonhosted.org", "pythonhosted.org"],
    "uv": ["pypi.org", "files.pythonhosted.org", "pythonhosted.org"],
    "pip": ["pypi.org", "files.pythonhosted.org", "pythonhosted.org"],
}


def _bootstrap_hosts_for(command: str) -> list[str]:
    """Which package-manager hosts to auto-allow for this command's launcher,
    based on the first token (e.g. 'npx', 'uvx'). Empty for anything else
    (e.g. a bare 'node path/to/server.js' with no package manager involved
    gets no bootstrap exception — nothing to resolve)."""
    first = command.strip().split(None, 1)[0] if command.strip() else ""
    first = first.rsplit("/", 1)[-1]  # handle an absolute path to the binary
    return _PACKAGE_MANAGER_HOSTS.get(first, [])


def _quote_template_value(value: str, *, allow_connection_string: bool = False) -> str:
    """Quote a user-supplied value substituted into a curated {path} /
    {connection_string} placeholder BEFORE the command string is split.

    Without this, a path containing spaces splits into several argv
    elements (wrong directory, confusing protocol errors) and a path
    containing a quote character breaks tokenization entirely — on
    Windows a stray `"` regroups the rest of the command line. Quoting
    here, at the template boundary, keeps the value exactly one argv
    element however _split_command tokenizes it (POSIX shlex and the
    Windows quoter both honor double quotes).

    Not recursive quoting and not a shell escape: the whole command is
    never executed by a shell (create_subprocess_exec), so this only
    protects the splitter, and only the single user-supplied element."""
    if any(c in value for c in ('"', "\n", "\r", "\x00")):
        raise ValueError(
            f"placeholder value contains a character that cannot appear "
            f"in a command argument: {value[:80]!r}")
    if os.name == "nt":
        return f'"{value}"'
    return shlex.quote(value)


def _split_command(command: str) -> list[str]:
    """Split a stdio launch command into argv.

    shlex assumes POSIX shell rules: a backslash escapes the next
    character, so every Windows path a user pastes gets corrupted
    (r'D:\\a\\b.py' -> 'D:ab', r'\\tests' -> token break). Windows has
    no such rule — backslashes are plain path separators and only double
    quotes group. Unix keeps standard POSIX quoting.
    """
    if os.name != "nt":
        return shlex.split(command)
    tokens: list[str] = []
    cur: list[str] = []
    in_q = False
    for ch in command.strip():
        if ch == '"':
            in_q = not in_q
        elif ch in (" ", "\t") and not in_q:
            if cur:
                tokens.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        tokens.append("".join(cur))
    return tokens

# A small curated directory of well-known official servers, so mcp_add_server
# can take a short name instead of requiring the user to already know the
# exact launch command. Verified launchable (via `npx -y <pkg> --help`/plain
# run) while building this, 2026-07. NOT exhaustive — see
# registry.modelcontextprotocol.io for the full community registry; this is
# a starting "top shelf", the Universe analogue of Antigravity's MCP Store,
# not a replacement for it.
KNOWN_SERVERS: dict[str, dict[str, Any]] = {
    "everything": {
        "command": "npx -y @modelcontextprotocol/server-everything stdio",
        "description": "Official MCP reference/test server — exercises the full protocol.",
        "declared_hosts": [],
    },
    "filesystem": {
        "command": "npx -y @modelcontextprotocol/server-filesystem {path}",
        "description": "Read/write files under a directory you allow. {path} is required.",
        "declared_hosts": [],
        "requires": ["path"],
    },
    "memory": {
        "command": "npx -y @modelcontextprotocol/server-memory",
        "description": "A simple persistent knowledge-graph memory store.",
        "declared_hosts": [],
    },
    "postgres": {
        "command": "npx -y @modelcontextprotocol/server-postgres {connection_string}",
        "description": "Read-only SQL access to a Postgres database. {connection_string} is required.",
        "declared_hosts": [],
        "requires": ["connection_string"],
    },
    "git": {
        "command": "uvx mcp-server-git --repository {path}",
        "description": "Git operations (log, diff, status) on a local repo. {path} is required.",
        "declared_hosts": [],
        "requires": ["path"],
    },
    # NOTE: @modelcontextprotocol/server-github is NOT listed here — the npm
    # package is deprecated (confirmed on npm, 2026-07: "development moved to
    # github.com/github/github-mcp-server", a separate Go binary with a
    # different install method). Universe already has a native github_repo_info
    # / github_create_issue pair in connectors/builtin/thirdparty.py, so
    # nothing is lost by leaving this out rather than pointing at a
    # deprecated package.
}


def _tool_is_read_only(tool_def: dict) -> bool:
    """Best-effort per-tool read-only determination. MCP tool annotations
    win if the server provides them; otherwise a name-based heuristic.
    Defaults permissive (True) for genuinely ambiguous names — the
    documented tradeoff is false negatives on a maliciously-named tool that
    avoids every write-verb, not false positives blocking legitimate reads."""
    ann = tool_def.get("annotations") or {}
    if "readOnlyHint" in ann:
        return bool(ann["readOnlyHint"])
    name = (tool_def.get("name") or "").lower()
    return not any(h in name for h in _WRITE_HINTS)


class _EgressProxy:
    """Minimal local HTTP forward proxy. Only tunnels CONNECT (HTTPS)
    requests to hosts in `allowed_hosts` (+ localhost); everything else gets
    403. Plain (non-TLS) HTTP forwarding is deliberately NOT implemented —
    the overwhelming majority of real third-party API traffic is HTTPS, and
    a half-implemented plaintext relay would be more dangerous than useful
    to claim as "supported". See module docstring for the honest limitation
    on subprocesses that bypass proxy env vars entirely."""

    def __init__(self, allowed_hosts: list[str]):
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self._server: asyncio.base_events.Server | None = None
        self.port: int = 0
        self.denied_log: list[str] = []  # last N denied hosts, for diagnostics

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _host_allowed(self, host: str) -> bool:
        host = host.lower().strip("[]")  # strip IPv6 brackets if present
        if host in ("localhost", "127.0.0.1", "::1"):
            return True
        return host in self.allowed_hosts

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass

    @staticmethod
    def _parse_connect_target(target: str) -> tuple[str | None, int]:
        """Split a CONNECT authority into (host, port), honoring IPv6
        bracket notation. The naive target.partition(":") turns
        "[::1]:443" into host="[" — a parse failure that either 403s a
        legitimate loopback connection or, worse, tunnels somewhere
        unintended. Returns (None, 0) on anything unparseable so the
        caller fails closed with 400."""
        t = target.strip()
        host: str | None = None
        port = 443
        try:
            if t.startswith("["):
                end = t.find("]")
                if end < 0:
                    return None, 0
                host = t[1:end]
                rest = t[end + 1:]
                if rest.startswith(":"):
                    port = int(rest[1:] or 443)
                elif rest:
                    return None, 0
            elif t.count(":") > 1:
                # bare IPv6 literal without brackets and without a port
                host = t
            else:
                host, _, port_s = t.partition(":")
                port = int(port_s) if port_s else 443
        except (ValueError, TypeError):
            return None, 0
        if not host or not (0 < port < 65536):
            return None, 0
        return host, port

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            first_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not first_line:
                writer.close()
                return
            parts = first_line.decode(errors="replace").split()
            if len(parts) < 2:
                writer.close()
                return
            method, target = parts[0], parts[1]
            if method != "CONNECT":
                # Plain HTTP — not relayed (see class docstring). Fail
                # closed rather than silently forwarding unchecked.
                writer.write(b"HTTP/1.1 501 Not Implemented (HTTPS/CONNECT only)\r\n\r\n")
                await writer.drain()
                writer.close()
                return
            host, port = self._parse_connect_target(target)
            if host is None:
                writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await writer.drain()
                writer.close()
                return
            # drain remaining CONNECT request headers
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b""):
                    break
            if not self._host_allowed(host):
                self.denied_log.append(host)
                self.denied_log = self.denied_log[-50:]
                writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                await writer.drain()
                writer.close()
                return
            try:
                remote_reader, remote_writer = await asyncio.open_connection(host, port)
            except Exception:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await writer.drain()
                writer.close()
                return
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(
                self._pipe(reader, remote_writer),
                self._pipe(remote_reader, writer),
                return_exceptions=True,
            )
        except Exception:
            try:
                writer.close()
            except Exception:
                pass

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass


@dataclass
class MCPServer:
    id: str
    name: str
    command: str
    declared_hosts: list[str] = field(default_factory=list)
    declared_capabilities: list[str] = field(default_factory=list)  # optional extra allowlist
    read_only: bool = True
    connected_at: float = 0.0
    tools: dict[str, dict] = field(default_factory=dict)  # populated by tools/list
    proc: Any = None                 # asyncio.subprocess.Process (stdio)
    proxy: "_EgressProxy | None" = None
    handshake_ok: bool = False       # True only after a successful tools/list
    _stderr_tail: list[str] = field(default_factory=list)  # last stderr lines (capped), for diagnostics
    _stderr_task: Any = None         # background drain task, cancelled in _stop
    # remote transports (MCP Streamable-HTTP / SSE); command stays "" for them
    transport: str = "stdio"         # stdio | http | sse
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    _http: Any = None                 # httpx.AsyncClient, lazily created
    _session_id: str = ""             # Mcp-Session-Id returned by the server
    _next_id: int = 1
    _lock: Any = None  # asyncio.Lock, created lazily


class MCPClient:
    """Manages MCP server subprocesses: launch, handshake, tool discovery,
    egress-gated calls, and crash supervision (3 crashes in 5 min -> pause)."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServer] = {}
        self._load()

    def _load(self) -> None:
        st = get_storage()
        try:
            rows = st.query_all("SELECT * FROM mcp_servers;")
        except Exception:
            rows = []
        for r in rows:
            caps_raw = r.get("capabilities") or "[]"
            srv = MCPServer(
                id=r["id"], name=r["name"], command=r.get("command") or "",
                declared_hosts=json.loads(r.get("declared_hosts") or "[]"),
                declared_capabilities=json.loads(caps_raw),
                read_only=bool(r.get("read_only", 1)),
                connected_at=r.get("connected_at", time.time()),
                transport=r.get("transport") or "stdio",
                url=r.get("url") or "",
                headers=json.loads(r.get("headers") or "{}"),
                enabled=bool(r.get("enabled", 1)),
            )
            self._servers[r["name"]] = srv

    def add(self, *, name: str, command: str = "", declared_hosts: list[str] | None = None,
            capabilities: list[str] | None = None, read_only: bool = True,
            transport: str = "stdio", url: str = "",
            headers: dict[str, str] | None = None) -> MCPServer:
        if transport not in ("stdio", "http", "sse"):
            raise ValueError(f"unsupported MCP transport {transport!r}")
        if transport != "stdio" and not url:
            raise ValueError(f"{transport} transport requires a url")
        if transport != "stdio":
            _validate_mcp_url(url)
        # re-adding a live server kills the old process FIRST: two
        # processes sharing one stdio pipe race stdout lines and can
        # cross-deliver JSON-RPC responses. proc.kill() is synchronous —
        # no loop needed — and the reaped handle dies with the reference.
        old = self._servers.get(name)
        if old is not None and old.proc is not None:
            try:
                if old.proc.returncode is None:
                    old.proc.kill()
            except (ProcessLookupError, OSError):
                pass
        srv = MCPServer(
            id=new_id("mcp-"), name=name, command=command,
            declared_hosts=declared_hosts or [],
            declared_capabilities=capabilities or [],
            read_only=read_only, connected_at=time.time(),
            transport=transport, url=url, headers=dict(headers or {}),
        )
        # a fresh registration is a fresh start: stale crash-loop history
        # (e.g. an earlier transient bind failure) must not pause the new
        # server before it ever gets a chance to run
        from concurrency.supervisor import get_supervisor
        get_supervisor().resume(name)
        self._servers[name] = srv
        st = get_storage()
        try:
            with st.transaction() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO mcp_servers(id,name,command,declared_hosts,"
                    "capabilities,read_only,connected_at,transport,url,headers,enabled) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?);",
                    (srv.id, name, command, json.dumps(srv.declared_hosts),
                     json.dumps(srv.declared_capabilities), 1 if read_only else 0,
                     srv.connected_at, transport, url, json.dumps(srv.headers),
                     1 if srv.enabled else 0),
                )
        except Exception:
            pass  # table may not exist yet; config still held in-memory
        from security.audit import log as _audit
        _audit(actor="user", action="mcp:add",
               args={"server": name, "transport": transport,
                     "read_only": read_only,
                     "url": url or "", "command": command[:120]})
        return srv

    def set_enabled(self, name: str, enabled: bool) -> bool:
        srv = self._servers.get(name)
        if srv is None:
            return False
        srv.enabled = enabled
        st = get_storage()
        try:
            with st.transaction() as conn:
                conn.execute("UPDATE mcp_servers SET enabled=? WHERE name=?;",
                             (1 if enabled else 0, name))
        except Exception:
            pass
        return True

    async def reconfigure(self, name: str, **fields: Any) -> "MCPServer":
        """Edit a registered server in place: url/command/headers/
        declared_hosts/read_only/transport. Same validation as add(); the
        live connection is stopped and the stale handshake cleared so the
        next tools/list re-verifies against the new target. Unknown keys
        are ignored; renaming a server stays delete+re-add."""
        srv = self._servers.get(name)
        if srv is None:
            raise ValueError(f"unknown MCP server {name!r}")
        transport = fields.get("transport", srv.transport)
        if transport not in ("stdio", "http", "sse"):
            raise ValueError(f"unsupported MCP transport {transport!r}")
        url = fields.get("url", srv.url)
        if transport != "stdio" and not url:
            raise ValueError(f"{transport} transport requires a url")
        if transport != "stdio":
            _validate_mcp_url(url)
        command = fields.get("command", srv.command)
        if transport == "stdio" and not command:
            raise ValueError("stdio transport requires a command")
        headers = fields.get("headers", srv.headers)
        if not isinstance(headers, dict):
            raise ValueError("headers must be an object")
        declared = fields.get("declared_hosts", srv.declared_hosts)
        if not isinstance(declared, list):
            raise ValueError("declared_hosts must be a list")
        # drop the live connection first: a stdio child or http session
        # bound to the old target must never serve the new config
        await self._stop(srv)
        srv.transport = transport
        srv.url = url
        srv.command = command
        srv.headers = dict(headers)
        srv.declared_hosts = list(declared)
        if "read_only" in fields:
            srv.read_only = bool(fields["read_only"])
        srv.tools = {}
        srv.handshake_ok = False
        srv._stderr_tail = []
        st = get_storage()
        try:
            with st.transaction() as conn:
                conn.execute(
                    "UPDATE mcp_servers SET command=?,declared_hosts=?,"
                    "read_only=?,transport=?,url=?,headers=? WHERE name=?;",
                    (srv.command, json.dumps(srv.declared_hosts),
                     1 if srv.read_only else 0, srv.transport, srv.url,
                     json.dumps(srv.headers), name),
                )
        except Exception:
            pass  # table may not exist yet; config still held in-memory
        return srv

    async def remove(self, name: str) -> bool:
        srv = self._servers.pop(name, None)
        if srv is not None:
            await self._stop(srv)
        st = get_storage()
        try:
            with st.transaction() as conn:
                conn.execute("DELETE FROM mcp_servers WHERE name=?;", (name,))
        except Exception:
            pass
        from security.audit import log as _audit
        _audit(actor="user", action="mcp:remove", args={"server": name},
               result="ok" if srv is not None else "unknown_server")
        return srv is not None

    async def _stop(self, srv: MCPServer) -> None:
        if srv._stderr_task is not None:
            try:
                srv._stderr_task.cancel()
            except Exception:
                pass
            srv._stderr_task = None
        if srv.proc is not None:
            try:
                srv.proc.terminate()
                await asyncio.wait_for(srv.proc.wait(), timeout=5)
            except Exception:
                try:
                    srv.proc.kill()
                except Exception:
                    pass
        if srv.proxy is not None:
            await srv.proxy.stop()
        if srv._http is not None:
            try:
                await srv._http.aclose()
            except Exception:
                pass
        srv.proc = None
        srv.proxy = None
        srv._http = None
        srv._session_id = ""

    def list_servers(self) -> list[MCPServer]:
        return list(self._servers.values())

    def get(self, name: str) -> MCPServer | None:
        return self._servers.get(name)

    # -- protocol plumbing ---------------------------------------------

    def _gen_id(self, srv: MCPServer) -> int:
        i = srv._next_id
        srv._next_id += 1
        return i

    async def _send(self, srv: MCPServer, msg: dict) -> None:
        line = (json.dumps(msg) + "\n").encode()
        srv.proc.stdin.write(line)
        await srv.proc.stdin.drain()

    async def _drain_stderr(self, srv: MCPServer) -> None:
        """Background reader for the child's stderr: discards everything
        (stderr is logs, never protocol), keeping only a capped tail for
        start/handshake failure diagnostics. Without a reader the child
        blocks on a full pipe buffer and the whole server deadlocks."""
        try:
            while True:
                chunk = await srv.proc.stderr.readline()
                if not chunk:
                    return
                try:
                    srv._stderr_tail.append(chunk.decode(errors="replace").rstrip())
                except Exception:
                    pass
                del srv._stderr_tail[:-50]
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _recv_response_for(self, srv: MCPServer, expected_id: int,
                                 timeout: float = 20.0) -> dict | None:
        """Read lines until one matches expected_id, silently skipping any
        server-pushed notifications in between (verified live: the
        reference server sends notifications/tools/list_changed BEFORE the
        actual tools/list response on first call). Lines over
        _MAX_MCP_LINE_BYTES abort the read as an error, never buffering a
        runaway server without bound."""
        async def _read():
            while True:
                line = await srv.proc.stdout.readline()
                if not line:
                    return None
                if len(line) > _MAX_MCP_LINE_BYTES:
                    raise RuntimeError(
                        f"MCP server {srv.name!r} sent a line over "
                        f"{_MAX_MCP_LINE_BYTES} bytes — refusing to buffer it")
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == expected_id:
                    return msg
        return await asyncio.wait_for(_read(), timeout=timeout)

    # -- remote transport (MCP Streamable-HTTP / SSE) ----------------------

    async def _http_rpc(self, srv: MCPServer, msg: dict, timeout: float = 20.0) -> dict | None:
        """One JSON-RPC round-trip over Streamable-HTTP. The server may answer
        with plain JSON or an SSE stream; both carry the same message
        payload, so parse whichever arrives. Session id (if the server issues
        one) is echoed on every subsequent request per the MCP spec.

        SSE events are matched on JSON-RPC `id` against THIS request: a
        server may interleave progress notifications, and returning the
        first data: line unexamined (the old code) attributes another
        call's payload to this one — the same cross-delivery bug class
        the stdio path fixed by matching ids. A caller that needs a
        response when none arrives with our id gets None (timeout path),
        never someone else's message."""
        if srv._http is None:
            import httpx
            srv._http = httpx.AsyncClient(timeout=timeout)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(srv.headers or {}),
        }
        if srv._session_id:
            headers["Mcp-Session-Id"] = srv._session_id
        res = await srv._http.post(srv.url, json=msg, headers=headers)
        if res.status_code == 404 and srv._session_id:
            # session expired server-side; drop it so the next call re-initializes
            srv._session_id = ""
            srv.handshake_ok = False
            raise RuntimeError("MCP session expired; retry will re-initialize")
        res.raise_for_status()
        sid = res.headers.get("mcp-session-id")
        if sid:
            srv._session_id = sid
        ctype = res.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            want_id = msg.get("id")
            for line in res.text.splitlines():
                if not line.startswith("data:"):
                    continue
                try:
                    candidate = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                # notifications carry no id: skip, never attribute to us
                if want_id is not None and candidate.get("id") != want_id:
                    continue
                return candidate
            return None
        try:
            return res.json()
        except ValueError:
            return None

    def _is_remote(self, srv: MCPServer) -> bool:
        return srv.transport in ("http", "sse")

    async def _remote_start(self, srv: MCPServer) -> None:
        """initialize + notifications/initialized + tools/list over HTTP."""
        mid = self._gen_id(srv)
        resp = await self._http_rpc(srv, {
            "jsonrpc": "2.0", "id": mid, "method": "initialize",
            "params": {"protocolVersion": MCP_PROTOCOL_VERSION,
                       "capabilities": {},
                       "clientInfo": {"name": "universe-agent", "version": "1.0"}},
        })
        if resp is None or "error" in resp:
            raise RuntimeError(f"MCP initialize failed for {srv.name!r}: {resp}")
        # notifications get no response; the spec allows fire-and-forget here
        try:
            await self._http_rpc(srv, {"jsonrpc": "2.0",
                                       "method": "notifications/initialized",
                                       "params": {}})
        except Exception:
            pass  # some servers reject a body on notifications; that's fine
        mid2 = self._gen_id(srv)
        resp2 = await self._http_rpc(srv, {"jsonrpc": "2.0", "id": mid2,
                                           "method": "tools/list", "params": {}})
        if resp2 is None:
            raise RuntimeError(f"MCP tools/list got no response for {srv.name!r}")
        tools = ((resp2 or {}).get("result") or {}).get("tools", [])
        srv.tools = {t["name"]: t for t in tools}
        srv.handshake_ok = True

    async def _ensure_started(self, srv: MCPServer) -> None:
        if srv._lock is None:
            srv._lock = asyncio.Lock()
        async with srv._lock:
            if not srv.enabled:
                raise RuntimeError(f"MCP server {srv.name!r} is disabled")
            if self._is_remote(srv):
                if srv.handshake_ok:
                    return
                srv.handshake_ok = False
                await self._remote_start(srv)
                return
            # NOTE: checking only `proc.returncode is None` is not reliable
            # for a process that exits almost instantly (e.g. a crash-loop
            # test using `false`) — asyncio's SIGCHLD-driven watcher may not
            # have updated returncode yet by the time the very next call
            # checks it, milliseconds later, causing a dead process to look
            # "still starting" and skipping re-launch entirely. handshake_ok
            # is the reliable signal: only a REAL completed tools/list sets
            # it, and any failure explicitly clears it below.
            if srv.proc is not None and srv.proc.returncode is None and srv.handshake_ok:
                return
            srv.handshake_ok = False
            if srv.proxy is not None:
                await srv.proxy.stop()  # clean up a previous failed attempt's proxy
            srv.proxy = _EgressProxy(
                allowed_hosts=list(srv.declared_hosts) + _bootstrap_hosts_for(srv.command))
            await srv.proxy.start()
            # sanitized: third-party MCP server processes are untrusted code
            # — they must never see UNIVERSE_BRIDGE_TOKEN / UNIVERSE_PASSPHRASE /
            # provider key env vars (audit 2026-09)
            from security.child_env import sanitized_child_env
            env = sanitized_child_env()
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                env[k] = srv.proxy.url
            args = _split_command(srv.command)
            if os.name == "nt" and args and not Path(args[0]).suffix:
                # `npx` and friends are .cmd shims on Windows; CreateProcess
                # only finds .exe files on PATH, so resolve the real shim
                # location ourselves (shutil.which honors PATHEXT).
                resolved = shutil.which(args[0])
                if resolved:
                    args[0] = resolved
            srv.proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env,
            )
            # Drain stderr in the background (capped tail kept for
            # diagnostics): a child that logs verbosely — npx prints
            # version checks and warnings there — would otherwise block
            # forever once the OS pipe buffer fills, deadlocking the
            # server. Cancelled in _stop().
            srv._stderr_tail = []
            srv._stderr_task = asyncio.ensure_future(self._drain_stderr(srv))
            mid = self._gen_id(srv)
            await self._send(srv, {"jsonrpc": "2.0", "id": mid, "method": "initialize",
                                   "params": {"protocolVersion": MCP_PROTOCOL_VERSION,
                                             "capabilities": {},
                                             "clientInfo": {"name": "universe-agent",
                                                            "version": "1.0"}}})
            # bootstrap gets a generous budget, tool calls stay at 20s: a
            # COLD npx launch (registry download through the egress proxy,
            # AV scanning on Windows) regularly blew the old fixed 20s
            # initialize budget — the server was healthy, just slow to boot
            # (observed live: 3/6 cold-start handshakes timed out)
            resp = await self._recv_response_for(srv, mid, timeout=90.0)
            if resp is None or "error" in resp:
                raise RuntimeError(f"MCP initialize failed for {srv.name!r}: {resp}")
            await self._send(srv, {"jsonrpc": "2.0", "method": "notifications/initialized",
                                   "params": {}})
            mid2 = self._gen_id(srv)
            await self._send(srv, {"jsonrpc": "2.0", "id": mid2, "method": "tools/list",
                                   "params": {}})
            resp2 = await self._recv_response_for(srv, mid2, timeout=30.0)
            if resp2 is None:
                raise RuntimeError(f"MCP tools/list got no response for {srv.name!r}")
            tools = ((resp2 or {}).get("result") or {}).get("tools", [])
            srv.tools = {t["name"]: t for t in tools}
            srv.handshake_ok = True

    def _record_crash(self, srv: MCPServer) -> None:
        from concurrency.supervisor import get_supervisor
        get_supervisor().record_crash(srv.name)

    @staticmethod
    def _is_timeout_error(exc: BaseException) -> bool:
        """Timeouts are liveness failures (server too slow / wedged), not
        integrity failures (wrong bytes on the pipe). Burning crash-loop
        budget on a slow server pauses a healthy-but-slow server after 3
        slow calls — the pause must be reserved for real crashes."""
        import asyncio as _aio
        name = type(exc).__name__
        return isinstance(exc, (_aio.TimeoutError, TimeoutError)) or name in (
            "TimeoutExpired", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
            "PoolTimeout",
        )

    # -- public API -------------------------------------------------------

    async def list_tools(self, name: str) -> dict:
        """Connect (if needed) and return the server's REAL discovered tool
        list — the authoritative source, not whatever a user typed at
        add-time."""
        srv = self._servers.get(name)
        if srv is None:
            return {"ok": False, "error": f"unknown MCP server {name!r}"}
        from concurrency.supervisor import get_supervisor
        if get_supervisor().is_paused(name):
            return {"ok": False, "error": "server paused (crash loop)"}
        try:
            await self._ensure_started(srv)
        except Exception as e:
            if not self._is_timeout_error(e):
                self._record_crash(srv)
            return {"ok": False, "error": f"failed to start/handshake: {e}"}
        return {"ok": True, "tools": [
            {"name": t["name"], "description": t.get("description", ""),
             "read_only": _tool_is_read_only(t)}
            for t in srv.tools.values()
        ]}

    async def call(self, name: str, tool: str, params: dict | None = None) -> dict:
        """Invoke a tool on an MCP server via JSON-RPC. Enforces (in order):
        server paused? -> tool actually exists (per real tools/list)? ->
        within the optional declared_capabilities allowlist, if any is set?
        -> read_only gating? Only then does the call go out."""
        srv = self._servers.get(name)
        if srv is None:
            return {"ok": False, "error": f"unknown MCP server {name!r}"}
        from concurrency.supervisor import get_supervisor
        if get_supervisor().is_paused(name):
            return {"ok": False, "error": "server paused (crash loop)"}
        try:
            await self._ensure_started(srv)
        except Exception as e:
            if not self._is_timeout_error(e):
                self._record_crash(srv)
            return {"ok": False, "error": f"failed to start/handshake: {e}"}

        if tool not in srv.tools:
            return {"ok": False, "error": f"tool {tool!r} not found on server {name!r} "
                                          f"(discovered: {sorted(srv.tools)})"}
        if srv.declared_capabilities and tool not in srv.declared_capabilities:
            return {"ok": False, "error": f"tool {tool!r} not in the declared "
                                          f"capability allowlist for {name!r}"}
        if srv.read_only and not _tool_is_read_only(srv.tools[tool]):
            return {"ok": False,
                    "error": f"{tool!r} on {name!r} looks like a write operation; "
                             f"the server is configured read_only=True",
                    "needs_consent": True}
        try:
            if srv._lock is None:
                srv._lock = asyncio.Lock()
            # One request-response pair at a time per server: _send writes
            # and _recv_response_for reads share a single stdout pipe, so
            # two concurrent calls interleave lines and can cross-deliver
            # responses (an integrity bug, not just a race — a response
            # belonging to call A would be attributed to call B).
            async with srv._lock:
                if self._is_remote(srv):
                    mid = self._gen_id(srv)
                    try:
                        resp = await self._http_rpc(srv, {
                            "jsonrpc": "2.0", "id": mid, "method": "tools/call",
                            "params": {"name": tool, "arguments": params or {}},
                        })
                    except RuntimeError as e:
                        # the server dropped our session (HTTP 404 clears
                        # _session_id + handshake_ok in _http_rpc): exactly
                        # ONE re-initialization, then the same call retried
                        # once with a fresh id. Anything beyond one retry is
                        # a crash loop wearing a session-expiry costume.
                        if "session expired" not in str(e):
                            raise
                        await self._remote_start(srv)
                        mid = self._gen_id(srv)
                        resp = await self._http_rpc(srv, {
                            "jsonrpc": "2.0", "id": mid, "method": "tools/call",
                            "params": {"name": tool, "arguments": params or {}},
                        })
                else:
                    mid = self._gen_id(srv)
                    await self._send(srv, {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                                            "params": {"name": tool, "arguments": params or {}}})
                    resp = await self._recv_response_for(srv, mid)
            if resp is None:
                raise RuntimeError("no response from MCP server (timeout)")
            if "error" in resp:
                return {"ok": False, "error": resp["error"]}
            # a response with NEITHER result nor error is a protocol
            # violation, not a success — the old code stringified the
            # missing result as "null" and returned ok=True, letting an
            # agent treat a void reply as a completed side effect
            if "result" not in resp:
                return {"ok": False,
                        "error": "MCP server returned neither result nor error"}
            result = resp.get("result")
            # MCP tool output is external, untrusted content (§10) exactly
            # like any other tool that reads outside data.
            rendered = json.dumps(result)
            truncated = len(rendered) > 8000
            return {"ok": True,
                    "result": f"[UNTRUSTED:mcp:{name}] {rendered[:8000]}"
                              + ("...[truncated]" if truncated else ""),
                    "truncated": truncated}
        except Exception as e:
            if not self._is_timeout_error(e):
                self._record_crash(srv)
            return {"ok": False, "error": str(e)}


def _validate_mcp_url(url: str) -> None:
    """Remote MCP endpoints are directly reachable network services — unlike
    stdio servers there is no local egress proxy between Universe and them, so
    the URL itself is the security boundary we can enforce: https anywhere,
    or http only on loopback (a developer's local Streamable-HTTP server)."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"MCP url must be http(s), got {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("plain-http MCP urls are only allowed on localhost; "
                         "use https for remote servers")
    if not parsed.netloc:
        raise ValueError("MCP url has no host")


_mcp: MCPClient | None = None


def get_mcp_client() -> MCPClient:
    global _mcp
    if _mcp is None:
        _mcp = MCPClient()
    return _mcp


def reset_mcp_client_for_tests() -> MCPClient:
    global _mcp
    _mcp = MCPClient()
    return _mcp

