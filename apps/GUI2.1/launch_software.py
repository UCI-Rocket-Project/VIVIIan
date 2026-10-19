"""Push this repo to the Windows support PC and start the NI-DAQ task."""

import asyncio
import subprocess
from pathlib import Path

import asyncssh
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REMOTE_REPO_NAME = "viviian"
TASK = r"python apps\GUI2.1\nidaq_gse.py"
SSH_CONFIG_PATH = Path(__file__).with_name("support_server.yaml")


def load_ssh_config() -> dict:
    if not SSH_CONFIG_PATH.is_file():
        raise SystemExit(
            f"missing {SSH_CONFIG_PATH.name}; copy support_server.example.yaml and fill it in"
        )
    loaded = yaml.safe_load(SSH_CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = loaded["support_server_ssh_config"]
    return {
        "host": cfg["host"],
        "username": cfg["username"],
        "password": cfg["password"],
        "known_hosts": None,
    }


def _remote_join(parent: str, name: str) -> str:
    return f"{parent.rstrip('/')}/{name}"


async def _remote_documents_viviian(conn: asyncssh.SSHClientConnection) -> str:
    """Windows OpenSSH: Documents\\viviian under the logged-in user."""
    result = await conn.run("cmd.exe /c echo %USERPROFILE%", check=True)
    profile = result.stdout.strip().replace("\\", "/")
    return f"{profile}/Documents/{REMOTE_REPO_NAME}"


def files_to_upload() -> list[Path]:
    """Tracked and untracked files that Git does not ignore."""
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return [
        REPO_ROOT / raw.decode()
        for raw in result.stdout.split(b"\0")
        if raw
    ]


async def upload_repo(sftp: asyncssh.SFTPClient, remote_root: str) -> int:
    """Copy the repo, skipping anything matched by .gitignore."""
    uploaded = 0
    made_dirs: set[str] = set()
    await sftp.makedirs(remote_root, exist_ok=True)
    for local_path in files_to_upload():
        relative = local_path.relative_to(REPO_ROOT).as_posix()
        remote_path = _remote_join(remote_root, relative)
        remote_dir = remote_path.rsplit("/", 1)[0]
        if remote_dir not in made_dirs:
            await sftp.makedirs(remote_dir, exist_ok=True)
            made_dirs.add(remote_dir)
        await sftp.put(str(local_path), remote_path)
        uploaded += 1
    return uploaded


def _windows_task_command(remote_root: str) -> str:
    # cmd.exe so this works whether OpenSSH's default shell is cmd or PowerShell.
    # `call` is the Windows stand-in for `source` on a venv activate.bat.
    root = remote_root.replace("/", "\\")
    return (
        "cmd.exe /c "
        f'"cd /d {root} && '
        "(if exist .venv\\Scripts\\activate.bat "
        "(call .venv\\Scripts\\activate.bat) "
        "else call venv\\Scripts\\activate.bat) && "
        f'{TASK}"'
    )


async def _print_stream(stream: asyncssh.SSHReader) -> None:
    async for line in stream:
        print(line, end="")


async def run_support_task(conn: asyncssh.SSHClientConnection, remote_root: str) -> None:
    """Start nidaq_gse.py in the remote venv and stream its output until it exits."""
    async with conn.create_process(
        _windows_task_command(remote_root),
        encoding="utf-8",
        errors="replace",
    ) as process:
        await asyncio.gather(_print_stream(process.stdout), _print_stream(process.stderr))
        if process.exit_status not in (0, None):
            raise SystemExit(process.exit_status)


async def main() -> None:

    
    async with asyncssh.connect(**load_ssh_config()) as conn:
        remote_root = await _remote_documents_viviian(conn)
        print(f"uploading {REPO_ROOT} -> {remote_root}")
        async with conn.start_sftp_client() as sftp:
            count = await upload_repo(sftp, remote_root)
        print(f"uploaded {count} files")
        print(f"starting {TASK}")
        await run_support_task(conn, remote_root)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (OSError, asyncssh.Error) as exc:
        raise SystemExit(f"support server ssh failed: {exc}") from exc
