"""The command line of `python -m app.server backup` and `restore` (app/server/backup.py)."""

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
from pathlib import Path

EXIT_KEY_MISMATCH = 3  # same numbers as migrate and the desktop launch
EXIT_DATA = 4

NOT_INCLUDED = (
    "Not in the archive, on purpose: the encryption key (TOKEN_ENCRYPTION_KEYS), the engines' own sign-ins "
    "(for example ~/.claude and ~/.codex), and any folder outside the workspace root that an agent was pointed at. "
    "Keep a copy of the key somewhere else. The archive holds your conversations and notes in clear: keep it private."
)


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


PASSWORD_ENV = "OPEN_DUET_BACKUP_PASSWORD"


def _password(args, *, confirm: bool) -> str | None:
    """From --password-file, the environment, or a prompt; never from the command line itself."""
    if getattr(args, "password_file", None):
        try:
            text = args.password_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"Open Duet server: cannot read the password file ({exc.strerror}).") from None
        return text.rstrip("\r\n") or None
    if os.environ.get(PASSWORD_ENV):
        return os.environ[PASSWORD_ENV]
    if getattr(args, "password", False):
        first = getpass.getpass("Backup password: ")
        if confirm and getpass.getpass("Repeat it: ") != first:
            raise SystemExit("Open Duet server: the passwords do not match.")
        return first or None
    return None


def main(argv: Sequence[str]) -> int:
    from app.server import backup

    parser = argparse.ArgumentParser(prog="python -m app.server")
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("backup", help="write one archive of the database, attachments, decision log and workspace")
    make.add_argument("--out", type=Path, help="the folder to write it in (default: backups/ beside the database)")
    make.add_argument("--skip-workspaces", action="store_true", help="leave out the workspace folder (it can be large)")
    make.add_argument("--list", action="store_true", help="only list what a backup copies (and where it lives); write nothing")
    make.add_argument("--password", action="store_true", help="protect the archive with a password asked for here")
    make.add_argument("--password-file", type=Path, help="protect the archive with the password in this file")
    back = commands.add_parser("restore", help="fill an empty data folder from an archive")
    back.add_argument("archive", type=Path)
    back.add_argument("--password", action="store_true", help="ask for the archive's password")
    back.add_argument("--password-file", type=Path, help="read the archive's password from this file")
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return 2 if exc.code else 0

    try:
        if args.command == "backup" and args.list:
            for item in backup.plan_backup(skip_workspaces=args.skip_workspaces):
                print(f"{item['part']:<13} {item['kind']:<6} {item['path']}" + ("" if item["exists"] else "  (not there yet)"))
            print("Also keep a copy of TOKEN_ENCRYPTION_KEYS; it is never in an archive. " + NOT_INCLUDED.split(": ", 1)[1])
            return 0
        if args.command == "backup":
            password = _password(args, confirm=True)
            report = backup.create_backup(args.out, skip_workspaces=args.skip_workspaces, passphrase=password)
            print(f"Backup written to {report.archive}" + (" (password-protected; there is no way to recover it without the password)" if password else ""))
            for name, part in report.parts.items():
                if part.files or name == "database":
                    print(f"  {name}: {part.files} file(s), {_size(part.bytes)}")
            if args.skip_workspaces:
                print("  workspace: left out (--skip-workspaces); the agents' notes live there")
            for item in report.skipped[:10]:
                print(f"  skipped: {item}")
            if len(report.skipped) > 10:
                print(f"  ... and {len(report.skipped) - 10} more skipped")
            if report.server_running:
                print("The server was running: the database copy is consistent, but other files may have changed while they were copied. "
                      "Stop the server for a fully quiet backup.")
            print(NOT_INCLUDED)
            return 0
        report = backup.restore_backup(args.archive, passphrase=_password(args, confirm=False))
        total = sum(part.files for part in report.parts.values())
        print(f"Restored {total} file(s) from {args.archive}.")
        if report.older_than_code:
            print("The backup is from an older version: run `python -m app.server migrate` before starting the server.")
        if report.moved_from:
            print(f"The workspace moved from {report.moved_from}: {report.rewritten} stored path(s) now point into the new workspace folder.")
        for path in report.outside[:10]:
            print(f"  kept as it was, outside the old workspace folder and not in the backup: {path}")
        if len(report.outside) > 10:
            print(f"  ... and {len(report.outside) - 10} more")
        print("Sign in to the engines again if they are not signed in on this machine; they are not part of a backup.")
        print("Conversations and files are back, but an engine's own memory of a conversation (for example under ~/.claude) is not. "
              "Where it is missing, sending a message says so and offers to start a new session; the history stays.")
        return 0
    except backup.KeyProblem as exc:
        print(f"Open Duet server: {exc}", file=sys.stderr)
        return EXIT_KEY_MISMATCH
    except backup.BackupError as exc:
        print(f"Open Duet server: {exc}", file=sys.stderr)
        return EXIT_DATA
