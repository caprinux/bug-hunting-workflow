"""Tier 0 — unit tests for the Docker isolation argv builder.

No Docker required: these assert the `docker run` command line confines the
agent (read-only rootfs, only the intended mounts writable) and points the
agent home at the persistent volume.
"""

from __future__ import annotations

import pytest

from bug_hunter.core.sandbox import (
    AGENT_HOME,
    SRC,
    WORK,
    ContainerSpec,
    build_docker_argv,
    codex_launch_args,
    write_claude_wrapper,
)


def _spec(kind, **kw):
    return ContainerSpec(
        image="bhw-agent:latest", kind=kind,
        work_host="/host/ws", source_host="/host/src", agent_home_host="/host/home",
        **kw,
    )


def _pairs(argv, flag):
    """All values immediately following occurrences of `flag`."""
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag and i + 1 < len(argv)]


def test_readonly_rootfs_and_tmpfs():
    argv = build_docker_argv(_spec("codex"), ["app-server"])
    assert "--read-only" in argv
    tmpfs = _pairs(argv, "--tmpfs")
    assert any(t.startswith("/tmp") for t in tmpfs)


def test_only_intended_mounts_and_modes():
    argv = build_docker_argv(_spec("codex"), [])
    mounts = _pairs(argv, "-v")
    # work rw, source ro, home rw, codex binary ro
    assert "/host/ws:/work:rw" in mounts
    assert "/host/src:/src:ro" in mounts
    assert "/host/home:/agent-home:rw" in mounts
    assert any(m.endswith("/opt/codex:ro") for m in mounts)
    # nothing else is mounted (exactly these four)
    assert len(mounts) == 4


def test_home_and_workdir_point_at_work():
    argv = build_docker_argv(_spec("codex"), [])
    envs = _pairs(argv, "-e")
    assert f"HOME={WORK}" in envs
    assert _pairs(argv, "-w") == [WORK]
    assert WORK == "/work" and SRC == "/src" and AGENT_HOME == "/agent-home"


def test_codex_home_env_and_entry():
    argv = codex_launch_args(_spec("codex"))
    envs = _pairs(list(argv), "-e")
    assert f"CODEX_HOME={AGENT_HOME}" in envs
    # runs the app-server over stdio inside the container
    assert argv[-3:] == ("app-server", "--listen", "stdio://")
    assert "/opt/codex" in argv


def test_claude_uses_config_dir_env():
    argv = build_docker_argv(_spec("claude"), [])
    envs = _pairs(argv, "-e")
    assert f"CLAUDE_CONFIG_DIR={AGENT_HOME}" in envs
    mounts = _pairs(argv, "-v")
    assert any(m.endswith("/opt/claude:ro") for m in mounts)


def test_network_toggle():
    on = _pairs(build_docker_argv(_spec("codex", network=True), []), "--network")
    off = _pairs(build_docker_argv(_spec("codex", network=False), []), "--network")
    assert on == ["bridge"] and off == ["none"]


def test_no_host_paths_leak_beyond_declared_mounts():
    argv = build_docker_argv(_spec("codex"), [])
    joined = " ".join(argv)
    # engagement/other-agent host paths must never appear
    assert "/host/other" not in joined
    # every host path in argv is one we declared
    for m in _pairs(argv, "-v"):
        host = m.split(":")[0]
        assert host in ("/host/ws", "/host/src", "/host/home") or host.endswith(("/codex", "/claude"))


def test_claude_wrapper_script(tmp_path):
    path = write_claude_wrapper(_spec("claude"), str(tmp_path))
    body = open(path).read()
    assert body.startswith("#!/bin/sh")
    assert "docker run" in body
    assert body.rstrip().endswith('"$@"')   # forwards the SDK's args
    import os
    assert os.access(path, os.X_OK)
