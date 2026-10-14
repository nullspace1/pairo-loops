"""Tests for Git worktree creation."""

from pathlib import Path
import subprocess

import pytest

from pairo.mcp.github import create_git_worktree


def run_git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def create_repository(path: Path) -> None:
    path.mkdir()
    run_git("init", cwd=path)
    run_git("config", "user.email", "tests@example.com", cwd=path)
    run_git("config", "user.name", "Test User", cwd=path)
    (path / "tracked.txt").write_text("committed\n", encoding="utf-8")
    run_git("add", "tracked.txt", cwd=path)
    run_git("commit", "-m", "Initial commit", cwd=path)


def test_create_git_worktree_copies_committed_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "nested" / "destination"
    create_repository(source)

    result = create_git_worktree(source, destination)

    assert result == destination.resolve()
    assert (destination / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    assert run_git("rev-parse", "HEAD", cwd=destination) == run_git(
        "rev-parse", "HEAD", cwd=source
    )
    assert run_git("rev-parse", "--is-inside-work-tree", cwd=destination) == "true"


def test_create_git_worktree_rejects_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    create_repository(source)
    destination.mkdir()

    with pytest.raises(FileExistsError, match="Destination already exists"):
        create_git_worktree(source, destination)


def test_create_git_worktree_reports_non_repository(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(RuntimeError, match="Could not create Git worktree"):
        create_git_worktree(source, tmp_path / "destination")
