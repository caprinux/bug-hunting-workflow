"""Per-agent Docker isolation for pipeline agents.

Each agent invocation launches its Codex ``app-server`` / Claude CLI *inside* a
``docker run`` container; the host Python SDK talks to it over stdio JSON-RPC.
The container is the isolation boundary:

  - ``--read-only`` rootfs: the agent cannot write to ``/etc``, ``/root``, ``/usr``…
  - the only writable places are the mounts we control — ``/work`` (its private
    working dir), ``/agent-home`` (persistent home for auth + resume state), and
    a ``--tmpfs /tmp``.
  - ``HOME`` and the working dir both point at ``/work`` so wherever the agent
    writes "by default" lands captured + isolated, not lost or escaped.
  - the source under audit is mounted read-only at ``/src``; nothing else from
    the host is visible, so concurrent agents cannot see each other's work.

To guarantee the app-server protocol matches the installed Python SDK, the
SDK's *bundled* codex/claude binary is mounted into the container rather than
installing a separate copy in the image.
"""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

# Fixed container-side paths.
WORK = "/work"
SRC = "/src"
AGENT_HOME = "/agent-home"
CODEX_BIN = "/opt/codex"
CLAUDE_BIN = "/opt/claude"


def bundled_codex_bin() -> str:
    """Path to the codex binary bundled with the installed openai_codex SDK."""
    from codex_cli_bin import bundled_codex_path
    return str(bundled_codex_path())


def bundled_claude_bin() -> str:
    """Path to the claude CLI bundled with the installed claude_agent_sdk."""
    import claude_agent_sdk
    return str(Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude")


@dataclass
class ContainerSpec:
    """A per-agent container: private /work, read-only /src, persistent home."""
    image: str
    kind: Literal["codex", "claude"]
    work_host: str          # host dir  -> /work        (rw, private per agent)
    source_host: str        # host dir  -> /src         (ro, target under audit)
    agent_home_host: str    # host dir  -> /agent-home  (rw, persistent for resume)
    network: bool = True
    env: dict = field(default_factory=dict)
    codex_bin: Optional[str] = None   # override the codex binary mounted in (else bundled)

    @property
    def home_env_name(self) -> str:
        return "CODEX_HOME" if self.kind == "codex" else "CLAUDE_CONFIG_DIR"

    @property
    def bin_host(self) -> str:
        if self.kind == "codex":
            return self.codex_bin or bundled_codex_bin()
        return bundled_claude_bin()

    @property
    def bin_container(self) -> str:
        return CODEX_BIN if self.kind == "codex" else CLAUDE_BIN


def _mount_args(spec: ContainerSpec) -> list[str]:
    args: list[str] = []
    for host, cont, mode in (
        (spec.work_host, WORK, "rw"),
        (spec.source_host, SRC, "ro"),
        (spec.agent_home_host, AGENT_HOME, "rw"),
        (spec.bin_host, spec.bin_container, "ro"),
    ):
        if host:
            args += ["-v", f"{os.path.abspath(host)}:{cont}:{mode}"]
    return args


def build_docker_argv(spec: ContainerSpec, entry: list[str]) -> list[str]:
    """Full ``docker run`` argv: read-only rootfs, tmpfs, fixed mounts, and the
    agent binary + ``entry`` args."""
    argv = [
        "docker", "run", "-i", "--rm",
        "--read-only",
        "--tmpfs", "/tmp:exec",   # tools may write+exec scratch scripts
        "--tmpfs", "/run",
        "--pull", "never",
        "-w", WORK,
        "-e", f"HOME={WORK}",
        "-e", f"XDG_CACHE_HOME={WORK}/.cache",
        "-e", f"{spec.home_env_name}={AGENT_HOME}",
    ]
    for k, v in spec.env.items():
        argv += ["-e", f"{k}={v}"]
    argv += ["--network", "bridge" if spec.network else "none"]
    argv += _mount_args(spec)
    argv += [spec.image, spec.bin_container, *entry]
    return argv


def codex_launch_args(spec: ContainerSpec) -> tuple[str, ...]:
    """argv for CodexConfig.launch_args_override — runs codex app-server in the
    container over stdio."""
    return tuple(build_docker_argv(spec, ["app-server", "--listen", "stdio://"]))


def write_claude_wrapper(spec: ContainerSpec, out_dir: str) -> str:
    """Write a wrapper script to pass as ClaudeAgentOptions.cli_path.

    The claude SDK spawns ``cli_path <args…>``; the wrapper forwards those args
    (``"$@"``) to the containerized claude with stdio passed through.
    """
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "claude_docker_wrapper.sh")
    argv = build_docker_argv(spec, [])  # real args appended via "$@"
    quoted = " ".join(shlex.quote(a) for a in argv)
    with open(path, "w") as f:
        f.write(f"#!/bin/sh\nexec {quoted} \"$@\"\n")
    os.chmod(path, 0o755)
    return path


def seed_agent_home(spec: ContainerSpec) -> None:
    """Seed the persistent per-agent home with host auth so the container can
    authenticate. Idempotent — only copies what's missing."""
    home = Path(spec.agent_home_host)
    home.mkdir(parents=True, exist_ok=True)
    if spec.kind == "codex":
        _copy_secret(Path.home() / ".codex" / "auth.json", home / "auth.json")
    else:
        # With CLAUDE_CONFIG_DIR set, claude reads .credentials.json and
        # .claude.json from that dir.
        _copy_secret(Path.home() / ".claude" / ".credentials.json", home / ".credentials.json")
        _copy_secret(Path.home() / ".claude.json", home / ".claude.json")


def _copy_secret(src: Path, dst: Path) -> None:
    if src.exists() and not dst.exists():
        shutil.copy2(src, dst)
        try:
            dst.chmod(0o600)
        except OSError:
            pass
