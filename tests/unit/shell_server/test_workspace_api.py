"""Unit tests for the Workspace Files API.

Coverage:
  - GET /api/v1/workspace/files (root listing)
  - GET /api/v1/workspace/files?path=<subdir> (subfolder listing)
  - Response shape: name, kind, path, is_dir, size, modified present
  - Traversal attempts rejected with HTTP 400
  - GET /api/v1/workspace/download?path= (subfolder-aware download)
  - GET /api/v1/workspace/file/{name} (legacy flat download, still works)
  - Absent workspace returns empty list (fail-soft)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes.shell_server.cowork.workspace_api import create_workspace_router

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def client(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HERMES_WORKSPACE_DIR", str(workspace))
    app = FastAPI()
    app.include_router(create_workspace_router())
    return TestClient(app)


# ---------------------------------------------------------------------------
# List root
# ---------------------------------------------------------------------------


class TestListRoot:
    def test_returns_empty_list_for_empty_workspace(self, client: TestClient) -> None:
        r = client.get("/api/v1/workspace/files")
        assert r.status_code == 200
        assert r.json() == []

    def test_lists_files_in_root(self, client: TestClient, workspace: Path) -> None:
        (workspace / "report.pdf").write_bytes(b"content")
        r = client.get("/api/v1/workspace/files")
        assert r.status_code == 200
        names = [e["name"] for e in r.json()]
        assert "report.pdf" in names

    def test_root_listing_includes_subdirectories(
        self, client: TestClient, workspace: Path
    ) -> None:
        (workspace / "subdir").mkdir()
        r = client.get("/api/v1/workspace/files")
        assert r.status_code == 200
        entries = r.json()
        dirs = [e for e in entries if e["is_dir"]]
        assert any(d["name"] == "subdir" for d in dirs)

    def test_directories_sorted_before_files(
        self, client: TestClient, workspace: Path
    ) -> None:
        (workspace / "aaa.txt").write_bytes(b"x")
        (workspace / "bbb").mkdir()
        r = client.get("/api/v1/workspace/files")
        entries = r.json()
        names = [e["name"] for e in entries]
        assert names.index("bbb") < names.index("aaa.txt")

    def test_fail_soft_when_workspace_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HERMES_WORKSPACE_DIR", str(tmp_path / "no_such_dir"))
        app = FastAPI()
        app.include_router(create_workspace_router())
        c = TestClient(app)
        r = c.get("/api/v1/workspace/files")
        assert r.status_code == 200
        assert r.json() == []


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


class TestResponseShape:
    def test_file_entry_has_required_fields(
        self, client: TestClient, workspace: Path
    ) -> None:
        (workspace / "data.xlsx").write_bytes(b"binary")
        entry = client.get("/api/v1/workspace/files").json()[0]
        assert entry["name"] == "data.xlsx"
        assert entry["kind"] == "xls"
        assert entry["path"] == "data.xlsx"
        assert entry["is_dir"] is False
        assert entry["size"] == 6
        assert "modified" in entry

    def test_size_matches_file_bytes(self, client: TestClient, workspace: Path) -> None:
        data = b"hello world"
        (workspace / "msg.txt").write_bytes(data)
        entry = client.get("/api/v1/workspace/files").json()[0]
        assert entry["size"] == len(data)

    def test_dir_entry_has_zero_size(self, client: TestClient, workspace: Path) -> None:
        (workspace / "outbox").mkdir()
        entries = client.get("/api/v1/workspace/files").json()
        dir_entry = next(e for e in entries if e["name"] == "outbox")
        assert dir_entry["is_dir"] is True
        assert dir_entry["size"] == 0
        assert dir_entry["kind"] == "folder"

    def test_path_is_relative_to_workspace_root(
        self, client: TestClient, workspace: Path
    ) -> None:
        sub = workspace / "reports"
        sub.mkdir()
        (sub / "q1.pdf").write_bytes(b"q1")
        r = client.get("/api/v1/workspace/files?path=reports")
        entry = r.json()[0]
        assert entry["path"] == "reports/q1.pdf"
        assert "/" in entry["path"]

    def test_modified_is_iso8601(self, client: TestClient, workspace: Path) -> None:
        (workspace / "note.txt").write_bytes(b"x")
        entry = client.get("/api/v1/workspace/files").json()[0]
        # ISO-8601 with timezone offset or Z suffix.
        modified = entry["modified"]
        assert "T" in modified
        assert modified.endswith("+00:00") or modified.endswith("Z")


# ---------------------------------------------------------------------------
# Subfolder listing
# ---------------------------------------------------------------------------


class TestSubfolderListing:
    def test_lists_contents_of_subdirectory(
        self, client: TestClient, workspace: Path
    ) -> None:
        sub = workspace / "docs"
        sub.mkdir()
        (sub / "readme.txt").write_bytes(b"hi")
        r = client.get("/api/v1/workspace/files?path=docs")
        assert r.status_code == 200
        names = [e["name"] for e in r.json()]
        assert "readme.txt" in names

    def test_subdir_listing_does_not_expose_siblings(
        self, client: TestClient, workspace: Path
    ) -> None:
        sub = workspace / "private"
        sub.mkdir()
        (sub / "secret.txt").write_bytes(b"s3cr3t")
        (workspace / "public.txt").write_bytes(b"open")
        r = client.get("/api/v1/workspace/files?path=private")
        names = [e["name"] for e in r.json()]
        assert "secret.txt" in names
        assert "public.txt" not in names

    def test_nonexistent_subdir_returns_400(self, client: TestClient) -> None:
        r = client.get("/api/v1/workspace/files?path=does_not_exist")
        assert r.status_code == 400

    def test_file_path_instead_of_dir_returns_400(
        self, client: TestClient, workspace: Path
    ) -> None:
        (workspace / "file.txt").write_bytes(b"content")
        r = client.get("/api/v1/workspace/files?path=file.txt")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Traversal guard — list endpoint
# ---------------------------------------------------------------------------


class TestTraversalGuardList:
    def test_dotdot_traversal_rejected(self, client: TestClient) -> None:
        r = client.get("/api/v1/workspace/files?path=../")
        assert r.status_code == 400

    def test_dotdot_in_subpath_rejected(self, client: TestClient) -> None:
        r = client.get("/api/v1/workspace/files?path=subdir/../../etc")
        assert r.status_code == 400

    def test_absolute_path_rejected(self, client: TestClient) -> None:
        r = client.get("/api/v1/workspace/files?path=/etc/passwd")
        # Leading slash is stripped; /etc/passwd → "etc/passwd" inside workspace
        # which doesn't exist → 400 (not found).
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Subfolder-aware download
# ---------------------------------------------------------------------------


class TestSubfolderDownload:
    def test_download_top_level_file(
        self, client: TestClient, workspace: Path
    ) -> None:
        (workspace / "output.pdf").write_bytes(b"pdf data")
        r = client.get("/api/v1/workspace/download?path=output.pdf")
        assert r.status_code == 200
        assert r.content == b"pdf data"

    def test_download_file_in_subfolder(
        self, client: TestClient, workspace: Path
    ) -> None:
        sub = workspace / "reports"
        sub.mkdir()
        (sub / "q2.xlsx").write_bytes(b"sheet data")
        r = client.get("/api/v1/workspace/download?path=reports/q2.xlsx")
        assert r.status_code == 200
        assert r.content == b"sheet data"

    def test_download_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.get("/api/v1/workspace/download?path=ghost.pdf")
        assert r.status_code == 404

    def test_download_traversal_rejected(
        self, client: TestClient, workspace: Path, tmp_path: Path
    ) -> None:
        # Write a file outside the workspace.
        secret = tmp_path / "secret.txt"
        secret.write_bytes(b"outside")
        # Attempt to traverse out via ..
        r = client.get("/api/v1/workspace/download?path=../secret.txt")
        assert r.status_code == 404

    def test_download_directory_returns_404(
        self, client: TestClient, workspace: Path
    ) -> None:
        (workspace / "mydir").mkdir()
        r = client.get("/api/v1/workspace/download?path=mydir")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Legacy flat download endpoint
# ---------------------------------------------------------------------------


class TestLegacyDownload:
    def test_downloads_top_level_file(
        self, client: TestClient, workspace: Path
    ) -> None:
        (workspace / "report.docx").write_bytes(b"word data")
        r = client.get("/api/v1/workspace/file/report.docx")
        assert r.status_code == 200
        assert r.content == b"word data"

    def test_missing_file_returns_404(self, client: TestClient) -> None:
        r = client.get("/api/v1/workspace/file/ghost.pdf")
        assert r.status_code == 404

    def test_basename_stripping_blocks_traversal(
        self, client: TestClient, workspace: Path, tmp_path: Path
    ) -> None:
        secret = tmp_path / "outside.txt"
        secret.write_bytes(b"secret")
        r = client.get("/api/v1/workspace/file/../outside.txt")
        assert r.status_code == 404
