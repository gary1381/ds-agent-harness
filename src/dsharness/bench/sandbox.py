"""Process sandbox for live agents (bubblewrap), so a real agent cannot see the answer key.

The agent CLI runs in a fresh mount namespace that shows only:

- ``~/work``: its attempt directory (read-write). It is the agent's working directory and holds
  whatever the arm hands it (a warehouse copy and its docs, or nothing).
- ``$HOME``: an empty per-attempt home with the CLI's own login file bind-mounted from the real
  home, so a token refresh lands in the real file, plus a sanitised CLI config. The agent's own
  transcript is written here, which keeps it for error analysis.
- the host system (``/usr``, ``/etc``, ...) read-only, plus the CLI binary, the uv Python and the
  agent tool environment, read-only.

Hidden: all of ``/home`` (the repository, custody truth, past transcripts, the user's instructions,
hooks, plugins and memories), ``/mnt``, ``/media``, ``/tmp`` and ``/run/user``. The network is shared
because the CLI needs its API. The harness MCP server runs outside the sandbox and is reached
through a unix socket bridged by socat, so the agent cannot read the server's workspace either.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

HOME = Path.home()
SOCKET_IN_SANDBOX = "/run/user/dsh/mcp.sock"
WORK_IN_SANDBOX = str(HOME / "work")   # the agent's working directory inside the sandbox
# ~/.claude.json keys the CLI needs to know who is logged in; everything else (project history,
# MCP servers, caches) stays out of the sandbox.
CLAUDE_JSON_KEEP = ("oauthAccount", "userID", "machineID", "firstStartTime", "firstStartVersion",
                    "installMethod", "migrationVersion", "opusProMigrationComplete",
                    "sonnet1m45MigrationComplete", "claudeCodeFirstTokenDate")


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class CLI:
    name: str                  # claude | codex
    binary: Path               # resolved executable on the host
    login_file: Path | None    # real credentials file (bind-mounted, never copied); None on an API key
    login_in_home: str         # its path relative to the sandbox home
    api_key_file: Path | None = None   # API key mode: a 0600 file holding only the key, bound read-only

    @property
    def binary_dir(self) -> Path:
        return self.binary.parent


KEY_IN_SANDBOX = "/tmp/dsh-claude-key"     # where the API key file appears inside; ``apiKeyHelper`` cats it


def write_key_file(directory: Path, key: str) -> Path:
    """A 0600 file with only the key, outside every run directory. The caller deletes it."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    f = directory / "claude.key"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as h:
        h.write(key)
    return f


def find_cli(name: str, binary: str | None = None, *, api_key_file: Path | None = None) -> CLI:
    """``api_key_file`` (claude only): run on that API key instead of the logged-in subscription."""
    if name == "claude":
        exe = Path(binary or shutil.which("claude") or HOME / ".local/bin/claude").resolve()
        if api_key_file is not None:
            if not exe.is_file():
                raise SandboxError(f"claude binary not found at {exe}")
            return CLI("claude", exe, None, ".claude/.credentials.json", api_key_file)
        cli = CLI("claude", exe, HOME / ".claude/.credentials.json", ".claude/.credentials.json")
    elif name == "codex":
        exe = Path(binary or shutil.which("codex") or _newest_codex()).resolve()
        cli = CLI("codex", exe, HOME / ".codex/auth.json", ".codex/auth.json")
    else:
        raise SandboxError(f"unknown agent CLI {name!r}")
    if not exe.is_file():
        raise SandboxError(f"{name} binary not found at {exe}")
    if not cli.login_file.is_file():
        raise SandboxError(f"{name} is not logged in ({cli.login_file} missing)")
    return cli


def _newest_codex() -> Path:
    cands = sorted(HOME.glob(".vscode/extensions/openai.chatgpt-*/bin/linux-x86_64/codex"),
                   key=lambda p: p.stat().st_mtime)
    if not cands:
        raise SandboxError("codex binary not found on PATH or in the VS Code extension")
    return cands[-1]


def prepare_home(home: Path, cli: CLI, *, codex_config: str | None = None) -> None:
    """Build the per-attempt home: sanitised CLI config only (the login file is bind-mounted)."""
    home.mkdir(parents=True, exist_ok=True)
    (home / cli.login_in_home).parent.mkdir(parents=True, exist_ok=True)
    if cli.login_file is not None:
        (home / cli.login_in_home).touch()                   # mount point for the real login file
    if cli.name == "claude":
        src = HOME / ".claude.json"
        real = json.loads(src.read_text()) if src.exists() else {}
        keep = [k for k in CLAUDE_JSON_KEEP if cli.api_key_file is None or k != "oauthAccount"]
        cfg = {k: real[k] for k in keep if k in real}
        cfg.update(autoUpdates=False, hasCompletedOnboarding=True)
        (home / ".claude.json").write_text(json.dumps(cfg))
        settings = {"autoUpdates": False}
        if cli.api_key_file is not None:
            settings["apiKeyHelper"] = f"cat {KEY_IN_SANDBOX}"
        (home / ".claude/settings.json").write_text(json.dumps(settings))
    else:
        (home / ".codex/config.toml").write_text(codex_config or "")


def bwrap_command(cmd: list[str], *, work: Path, home: Path, cli: CLI, ro_paths: list[Path] = (),
                  socket_dir: Path | None = None, env: dict[str, str] | None = None,
                  ro_binds_at: list[tuple[Path, str]] = ()) -> list[str]:
    """The bwrap argv that runs ``cmd`` (paths as seen inside) in the sandbox."""
    if shutil.which("bwrap") is None:
        raise SandboxError("bubblewrap (bwrap) is not installed")
    a = ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
         "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
         "--tmpfs", "/home", "--tmpfs", "/tmp", "--tmpfs", "/run/user"]
    # /opt and /snap hold the host's installed apps: the agents (outside models) need none of them
    for hidden in ("/mnt", "/media", "/root", "/srv", "/var/tmp", "/opt", "/snap"):
        if Path(hidden).is_dir():
            a += ["--tmpfs", hidden]
    a += ["--bind", str(home), str(HOME)]
    if cli.login_file is not None:
        a += ["--bind", str(cli.login_file), str(HOME / cli.login_in_home)]
    if cli.api_key_file is not None:
        a += ["--ro-bind", str(cli.api_key_file), KEY_IN_SANDBOX]
    a += ["--ro-bind", str(cli.binary_dir), str(cli.binary_dir)]
    for p in ro_paths:
        p = Path(p).resolve()
        a += ["--ro-bind", str(p), str(p)]
    for src, dest in ro_binds_at:          # e.g. a shim dir over /usr/local/bin (login shells reset PATH)
        a += ["--ro-bind", str(Path(src).resolve()), dest]
    if socket_dir is not None:
        a += ["--bind", str(socket_dir), str(Path(SOCKET_IN_SANDBOX).parent)]
    a += ["--bind", str(work), WORK_IN_SANDBOX, "--chdir", WORK_IN_SANDBOX, "--clearenv"]
    base_env = {"HOME": str(HOME), "USER": os.environ.get("USER", "agent"), "LANG": "C.UTF-8",
                "TERM": "dumb", "PATH": f"{cli.binary_dir}:/usr/local/bin:/usr/bin:/bin",
                "DISABLE_AUTOUPDATER": "1"}
    for k, v in {**base_env, **(env or {})}.items():
        a += ["--setenv", k, v]
    return a + ["--"] + list(cmd)


@contextmanager
def mcp_bridge(server_argv: list[str], socket_dir: Path, *, cwd: Path | None = None, timeout_s: float = 10.0):
    """Serve ``server_argv`` (a stdio MCP server, run on the host) on ``socket_dir/mcp.sock``.

    Each connection spawns one server process. Inside the sandbox the MCP client runs
    ``socat - UNIX-CONNECT:/run/user/dsh/mcp.sock``."""
    if shutil.which("socat") is None:
        raise SandboxError("socat is not installed")
    socket_dir.mkdir(parents=True, exist_ok=True)
    sock = socket_dir / "mcp.sock"
    script = socket_dir / "serve.sh"
    script.write_text("#!/bin/sh\nexec " + shlex.join(server_argv) + "\n")
    script.chmod(0o700)
    proc = subprocess.Popen(["socat", f"UNIX-LISTEN:{sock},fork,mode=600", f"EXEC:{script}"],
                            cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        t0 = time.monotonic()
        while not sock.exists():
            if proc.poll() is not None or time.monotonic() - t0 > timeout_s:
                raise SandboxError(f"MCP bridge did not start (socat exit {proc.poll()})")
            time.sleep(0.05)
        yield sock
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        subprocess.run(["pkill", "-f", str(script)], check=False)
        sock.unlink(missing_ok=True)
