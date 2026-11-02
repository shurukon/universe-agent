"""connectors/builtin/docker.py — docker tool (requires explicit connector.run consent).

Command construction is argv-list + shell=False everywhere: agent-supplied
values (tag, image, command, ...) can never inject host-shell metacharacters
(audit 2026-09: the old f-string let `image="alpine & calc.exe"` execute on
the host). The container-side command runs via `sh -c` INSIDE the container —
that is the quarantine boundary, not the host.
"""
from __future__ import annotations

from core.agent.base_agent import Tool
from connectors.builtin import ToolRegistry
from connectors.builtin.shell import _run_argv


def docker_build(dockerfile: str = ".", tag: str = "universe-build") -> dict:
    return _run_argv(["docker", "build", "-t", tag, dockerfile])


def docker_run(image: str, command: str = "", network: str = "none",
               readonly: bool = True, scratch: str = "") -> dict:
    """Run a container. Default: --network=none + read-only FS (quarantine mode)."""
    argv = ["docker", "run", f"--network={network}", "--rm"]
    if readonly:
        argv.append("--read-only")
    if scratch:
        argv += ["--tmpfs", f"{scratch}:rw,size=64m"]
    argv.append(image)
    if command:
        # explicit shell INSIDE the container only; the host never parses it
        argv += ["sh", "-c", command]
    return _run_argv(argv)


# consent escalation (audit 2026-10): agent-chosen flags can downgrade the
# quarantine the tool is ADVERTISED as providing ("default: no network,
# read-only") — the explicit consent covers the tool name, not the flag
# set. A run with network or a writable FS re-asks per goal.
def _docker_run_unsafe_flags(args: dict) -> bool:
    network = str(args.get("network") or "none").lower()
    safe = {"none", "bridge"}  # NAT'd/isolated modes; "host" shares the host stack
    return network not in safe or not bool(args.get("readonly", True))


def docker_ps() -> dict:
    return _run_argv(["docker", "ps", "-a"])


def register(reg: ToolRegistry) -> None:
    from security.capability import register_consent_escalator
    # a run that disables the advertised quarantine re-asks per goal even
    # though the tool itself is explicit-consented (the escalator catches
    # auto/per_turn ladders; explicit stays explicit)
    register_consent_escalator("DockerRun", _docker_run_unsafe_flags)
    reg.register(Tool(name="DockerBuild", capability="connector.run",
                      description="Build a docker image", handler=docker_build,
                      consent="explicit", resources=["connector:docker"]))
    reg.register(Tool(name="DockerRun", capability="connector.run",
                      description="Run a docker container (default: no network, read-only; "
                                  "network/readonly flags that weaken the quarantine re-ask per goal)",
                      handler=docker_run, consent="explicit",
                      resources=["connector:docker"]))
    reg.register(Tool(name="DockerPs", capability="connector.run",
                      description="List docker containers", handler=docker_ps,
                      consent="per_turn", resources=["connector:docker"]))
