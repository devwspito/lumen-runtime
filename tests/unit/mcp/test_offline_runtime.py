"""Unit tests for offline MCP runtime resolution (npx --offline → node <bin>).

The connect-fix (2026-06-26): `npx --offline` fails ENOTCACHED, so a prefetched MCP
is run by rewriting its argv to the installed bin. These tests cover the spec/name
parsing, the passthrough contracts (non-prefetched / non-npx), and the rewrite when a
fake persistent install exists.
"""

from __future__ import annotations

import json

import pytest

from hermes.mcp.infrastructure import offline_runtime as ofr

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("spec,name", [
    ("@modelcontextprotocol/server-filesystem@2026.1.14", "@modelcontextprotocol/server-filesystem"),
    ("@scope/pkg", "@scope/pkg"),
    ("lodash@4.17.4", "lodash"),
    ("plain", "plain"),
])
def test_name_from_spec(spec, name):
    assert ofr._name_from_spec(spec) == name


def test_passthrough_non_npx():
    assert ofr.resolve_runtime_argv(["uvx", "x"]) == ["uvx", "x"]
    assert ofr.resolve_runtime_argv(["node", "/x.js"]) == ["node", "/x.js"]
    assert ofr.resolve_runtime_argv([]) == []


def test_passthrough_when_not_prefetched(tmp_path, monkeypatch):
    # No install dir on disk → original npx argv returned (launcher fallback).
    monkeypatch.setattr(ofr, "MCP_INSTALL_ROOT", tmp_path / "mcp-installs")
    argv = ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    assert ofr.resolve_runtime_argv(argv) == argv


def test_rewrites_to_node_bin_when_prefetched(tmp_path, monkeypatch):
    monkeypatch.setattr(ofr, "MCP_INSTALL_ROOT", tmp_path / "mcp-installs")
    name = "@modelcontextprotocol/server-filesystem"
    pkgdir = ofr.npm_install_dir(name) / "node_modules" / name
    pkgdir.mkdir(parents=True)
    (pkgdir / "package.json").write_text(json.dumps({
        "name": name, "version": "1.0.0", "bin": {"server-filesystem": "dist/index.js"},
    }))
    (pkgdir / "dist").mkdir()
    (pkgdir / "dist" / "index.js").write_text("// server")

    out = ofr.resolve_runtime_argv(["npx", "-y", name, "/tmp"])
    assert out[0] == "node"
    assert out[1].endswith("/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js")
    assert out[2] == "/tmp"  # server args preserved


def test_bin_string_form_and_main_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(ofr, "MCP_INSTALL_ROOT", tmp_path / "mcp-installs")
    name = "tool"
    pkgdir = ofr.npm_install_dir(name) / "node_modules" / name
    pkgdir.mkdir(parents=True)
    (pkgdir / "package.json").write_text(json.dumps({"name": name, "main": "main.js"}))
    (pkgdir / "main.js").write_text("// main")
    out = ofr.resolve_runtime_argv(["npx", name, "arg1"])
    assert out[0] == "node" and out[1].endswith("/main.js") and out[2] == "arg1"


def test_rejects_bin_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(ofr, "MCP_INSTALL_ROOT", tmp_path / "mcp-installs")
    name = "evil"
    pkgdir = ofr.npm_install_dir(name) / "node_modules" / name
    pkgdir.mkdir(parents=True)
    (pkgdir / "package.json").write_text(json.dumps({"name": name, "bin": "../../../../etc/passwd"}))
    # traversal outside the package dir → no rewrite (passthrough)
    argv = ["npx", name]
    assert ofr.resolve_runtime_argv(argv) == argv
