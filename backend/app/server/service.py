"""`python -m app.server service`: a systemd unit for this installation, made from its real paths.

    python -m app.server service [--user NAME] [--env-file PATH] [--name suparagent]
    python -m app.server service --write [PATH]        # needs root; refuses to replace an existing file
    python -m app.server service --install [--scope system|user] [--dry-run]
    python -m app.server service --uninstall [--scope system|user] [--dry-run]

--install writes the unit (system: /etc/systemd/system, needs root; user: ~/.config/systemd/user), then
runs daemon-reload and `enable --now`. --uninstall stops, disables and removes it. --dry-run prints the
steps and changes nothing. Both refuse clearly on a system without Linux and systemd.

The unit runs the same Python that runs this command, from the backend folder, with the
settings in an environment file. Before each start it runs `migrate` (creates or updates the
database; a SQLite file is copied aside first), so an update is: pull, rebuild, restart. It
restarts the server if it dies, and stops it with SIGTERM, giving conversations time to end.

The text can be made on any system; installing it needs Linux with systemd.
"""

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

DEFAULT_ENV_FILE = "/etc/suparagent/suparagent.env"
DEFAULT_UNIT_PATH = "/etc/systemd/system/{name}.service"
UNIT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,60}$")
ACCOUNT = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
STOP_SECONDS = 30
# systemd expands %specifiers and $variables and splits on whitespace and quotes: a path with any of
# these would not mean what it says, so such a path is refused instead of guessed at.
UNSAFE_PATH = re.compile(r"[\s%$\"'\\;]")


class ServiceError(Exception):
    """The settings cannot make a safe unit; the message says what to change."""


def backend_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _check_path(label: str, path: str) -> str:
    if not path.startswith("/"):
        raise ServiceError(f"{label} must be an absolute path (got {path!r}).")
    if UNSAFE_PATH.search(path):
        raise ServiceError(f"{label} {path!r} has a space or a character systemd treats specially. Use a plain path.")
    return path


def render(*, user: str, env_file: str = DEFAULT_ENV_FILE, python: str | None = None, backend: Path | None = None,
           description: str = "Suparagent", scope: str = "system") -> str:
    """The unit text."""
    if scope == "user":
        user_line, wanted_by = "", "default.target"
    else:
        user_line, wanted_by = f"User={user}\n", "multi-user.target"
    if not ACCOUNT.match(user):
        raise ServiceError(f"{user!r} is not a usable account name. Give the Linux account that runs the server with --user.")
    python_path = _check_path("The Python path", python or sys.executable)
    work = _check_path("The backend folder", (backend or backend_dir()).as_posix())
    env = _check_path("The settings file", env_file)
    return f"""# Suparagent, made by `python -m app.server service`.
# The settings are in {env} (mode 0600, owned by {user}); see the self-hosting notes for what to put there.

[Unit]
Description={description}
After=network-online.target
Wants=network-online.target

[Service]
{user_line}WorkingDirectory={work}
EnvironmentFile={env}
# Prepare the database before serving: creates it, or updates it after an upgrade (a SQLite file is
# copied aside first). The server will not start on a database that is not ready, so a failed update
# stops here and shows in `journalctl`.
ExecStartPre={python_path} -m app.server migrate
ExecStart={python_path} -m app.server
Restart=on-failure
RestartSec=5
# A stop (SIGTERM) ends conversations cleanly; give it time before systemd gives up.
TimeoutStopSec={STOP_SECONDS}
# Agents run commands as this account. This does not stop that; it keeps a compromised process from
# gaining privileges it does not already have.
NoNewPrivileges=true

[Install]
WantedBy={wanted_by}
"""


def unit_target(name: str, scope: str, home: Path | None = None) -> Path:
    if scope == "user":
        return (home or Path.home()) / ".config" / "systemd" / "user" / f"{name}.service"
    return Path(DEFAULT_UNIT_PATH.format(name=name))


def _systemctl(scope: str) -> list[str]:
    return ["systemctl", "--user"] if scope == "user" else ["systemctl"]


def _default_run(cmd: list[str]) -> int:
    return subprocess.run(cmd, check=False).returncode


def _preflight(scope: str, *, platform: str, which: Callable[[str], str | None], euid: int | None) -> str | None:
    """Why this system cannot manage a service, or None."""
    if not platform.startswith("linux"):
        return "systemd is Linux only. The unit text can be printed here (leave out --install) and copied to the server."
    if which("systemctl") is None:
        return "systemctl was not found, so this system does not use systemd. Print the unit (leave out --install) and set it up by hand."
    if scope == "system" and euid not in (None, 0):
        return "A system service needs root. Run it with sudo, or use --scope user for a service that runs as you."
    return None


def install(*, name: str, user: str, env_file: str, scope: str = "system", dry_run: bool = False,
            run: Callable[[list[str]], int] = _default_run, platform: str = sys.platform,
            which: Callable[[str], str | None] = shutil.which, euid: int | None = None,
            target: Path | None = None, out=print) -> int:
    """Write the unit and enable it. Never replaces an existing unit."""
    problem = _preflight(scope, platform=platform, which=which, euid=euid)
    if problem:
        out(f"Suparagent server: {problem}", file=sys.stderr)
        return 2
    try:
        text = render(user=user, env_file=env_file, scope=scope)
    except ServiceError as exc:
        out(f"Suparagent server: {exc}", file=sys.stderr)
        return 2
    path = target or unit_target(name, scope)
    systemctl = _systemctl(scope)
    steps = [f"write {path}", " ".join(systemctl + ["daemon-reload"]), " ".join(systemctl + ["enable", "--now", name])]
    if dry_run:
        out("Dry run, nothing changed. Would:\n" + "\n".join(f"  - {step}" for step in steps) + "\n\n" + text)
        return 0
    if path.exists():
        out(f"Suparagent server: {path} already exists and was not replaced. Run --uninstall first, or remove it.", file=sys.stderr)
        return 4
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o644)
    except OSError as exc:
        out(f"Suparagent server: could not write {path} ({exc.strerror or exc}).", file=sys.stderr)
        return 4
    out(f"Wrote {path}.")
    for command in (systemctl + ["daemon-reload"], systemctl + ["enable", "--now", name]):
        if run(command) != 0:
            out(f"Suparagent server: `{' '.join(command)}` failed. The unit is at {path}; check `journalctl -u {name}`.", file=sys.stderr)
            return 5
    out(f"Installed and started {name}. Watch it: journalctl {'--user ' if scope == 'user' else ''}-u {name} -f")
    return 0


def uninstall(*, name: str, scope: str = "system", dry_run: bool = False,
              run: Callable[[list[str]], int] = _default_run, platform: str = sys.platform,
              which: Callable[[str], str | None] = shutil.which, euid: int | None = None,
              target: Path | None = None, out=print) -> int:
    """Stop, disable and remove the unit. Data and settings files are left alone."""
    problem = _preflight(scope, platform=platform, which=which, euid=euid)
    if problem:
        out(f"Suparagent server: {problem}", file=sys.stderr)
        return 2
    path = target or unit_target(name, scope)
    systemctl = _systemctl(scope)
    steps = [" ".join(systemctl + ["disable", "--now", name]), f"remove {path}", " ".join(systemctl + ["daemon-reload"])]
    if dry_run:
        out("Dry run, nothing changed. Would:\n" + "\n".join(f"  - {step}" for step in steps))
        return 0
    if not path.exists():
        out(f"Suparagent server: {path} does not exist; nothing to remove.", file=sys.stderr)
        return 4
    if run(systemctl + ["disable", "--now", name]) != 0:
        out(f"Suparagent server: could not stop and disable {name}; the unit was left in place.", file=sys.stderr)
        return 5
    try:
        path.unlink()
    except OSError as exc:
        out(f"Suparagent server: could not remove {path} ({exc.strerror or exc}).", file=sys.stderr)
        return 4
    run(systemctl + ["daemon-reload"])
    out(f"Stopped, disabled and removed {name}. Settings, database and workspace were not touched.")
    return 0


def instructions(name: str, unit_path: str, env_file: str, user: str) -> str:
    return f"""Next, as root:

  1. Make the settings file at {env_file} if it is not there yet, then edit it (an existing file is left alone):
       install -d -m 0750 -o {user} "$(dirname {env_file})"
       test -e {env_file} || install -m 0600 -o {user} /dev/null {env_file}
  2. Install and start the service:
       systemctl daemon-reload
       systemctl enable --now {name}
  3. Watch it: journalctl -u {name} -f      and check the setup: python -m app.server doctor

The unit is at {unit_path}."""


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.server service", description="Make a systemd unit for this installation.")
    parser.add_argument("--user", default=None, help="the Linux account that runs the server (default: the current one)")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help=f"the settings file (default {DEFAULT_ENV_FILE})")
    parser.add_argument("--name", default="suparagent", help="the service name (default suparagent)")
    parser.add_argument("--write", nargs="?", const="", metavar="PATH", help="write the unit instead of printing it (default path /etc/systemd/system/NAME.service)")
    parser.add_argument("--install", action="store_true", help="write the unit, then enable and start it")
    parser.add_argument("--uninstall", action="store_true", help="stop, disable and remove the unit")
    parser.add_argument("--scope", choices=("system", "user"), default="system", help="system (root) or user (runs as you); default system")
    parser.add_argument("--dry-run", action="store_true", help="with --install/--uninstall: show the steps, change nothing")
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not UNIT_NAME.match(args.name):
        print(f"Suparagent server: {args.name!r} is not a usable service name.", file=sys.stderr)
        return 2
    user = args.user or getpass.getuser()
    if args.install and args.uninstall:
        print("Suparagent server: choose --install or --uninstall, not both.", file=sys.stderr)
        return 2
    geteuid = getattr(os, "geteuid", None)
    euid = geteuid() if geteuid else None
    env_file = args.env_file
    if args.scope == "user" and env_file == DEFAULT_ENV_FILE:
        env_file = str(Path.home() / ".config" / "suparagent" / "suparagent.env")
    if args.install:
        return install(name=args.name, user=user, env_file=env_file, scope=args.scope, dry_run=args.dry_run, euid=euid,
                       out=lambda m, file=sys.stdout: print(m, file=file))
    if args.uninstall:
        return uninstall(name=args.name, scope=args.scope, dry_run=args.dry_run, euid=euid,
                         out=lambda m, file=sys.stdout: print(m, file=file))
    try:
        text = render(user=user, env_file=args.env_file)
    except ServiceError as exc:
        print(f"Suparagent server: {exc}", file=sys.stderr)
        return 2
    if args.write is None:
        print(text)
        return 0
    if not sys.platform.startswith("linux"):
        print("Suparagent server: systemd is Linux only. The unit text can be printed here (leave out --write) and copied to the server.", file=sys.stderr)
        return 2
    target = Path(args.write or DEFAULT_UNIT_PATH.format(name=args.name))
    if target.exists():
        print(f"Suparagent server: {target} already exists and was not replaced. Remove it or choose another --write PATH.", file=sys.stderr)
        return 4
    try:
        target.write_text(text, encoding="utf-8")
        target.chmod(0o644)
    except OSError as exc:
        print(f"Suparagent server: could not write {target} ({exc.strerror or exc}). Run it as root, or print the unit and copy it.", file=sys.stderr)
        return 4
    print(f"Wrote {target}.\n")
    print(instructions(args.name, str(target), args.env_file, user))
    return 0
