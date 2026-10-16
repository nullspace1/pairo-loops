from collections.abc import Sequence
from os import PathLike
from pathlib import Path
import subprocess

from pairo.core.agent import Agent
from pairo.core.mcp.servers import StdioMCPServer

from ._agent_configuration import add_server_to_agents


def create_git_worktree(
    source: str | PathLike[str],
    dest: str | PathLike[str],
) -> Path:
    """Create a detached worktree of ``source`` at ``dest``.

    The new worktree points at the source repository's current ``HEAD``. Git
    worktrees contain committed files only, so uncommitted source changes are
    intentionally not copied.

    Args:
        source: Path to an existing Git working tree.
        dest: Path for the new worktree. It must not already exist.

    Returns:
        The absolute path to the created worktree.

    Raises:
        FileNotFoundError: If ``source`` does not exist.
        NotADirectoryError: If ``source`` is not a directory.
        FileExistsError: If ``dest`` already exists.
        RuntimeError: If Git cannot create the worktree.
    """
    source_path = Path(source).expanduser().resolve()
    dest_path = Path(dest).expanduser().resolve()

    if not source_path.exists():
        raise FileNotFoundError(f"Source folder does not exist: {source_path}")
    if not source_path.is_dir():
        raise NotADirectoryError(f"Source is not a directory: {source_path}")
    if dest_path.exists():
        raise FileExistsError(f"Destination already exists: {dest_path}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(source_path),
                "worktree",
                "add",
                "--detach",
                str(dest_path),
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Git is not installed or is not available on PATH.") from error
    except subprocess.CalledProcessError as error:
        detail = (
            (error.stderr or "").strip()
            or (error.stdout or "").strip()
            or "unknown Git error"
        )
        raise RuntimeError(
            f"Could not create Git worktree from '{source_path}' at "
            f"'{dest_path}': {detail}"
        ) from error

    return dest_path


def add_github_mcp_server(
    agents: Sequence[Agent],
    port: int = 8085,
) -> StdioMCPServer:
    port_text = str(port)
    server = StdioMCPServer(
        name="github",
        command="docker",
        args=(
            "run",
            "-i",
            "--rm",
            "-p",
            f"127.0.0.1:{port_text}:{port_text}",
            "-e",
            "GITHUB_OAUTH_CALLBACK_PORT",
            "ghcr.io/github/github-mcp-server",
        ),
        env={"GITHUB_OAUTH_CALLBACK_PORT": port_text},
    )
    return add_server_to_agents(agents, server)

