"""Fase C gating: the REST layer rejects operator edits/deletes of cloud-managed
providers ("el empleado no lo toca"). The config-sync applier mutates these rows
via D-Bus directly (never REST), so it stays the sole owner.

This tests the _reject_if_cloud_managed guard in isolation against a fake proxy
(the live path additionally sits behind the operator-token middleware +
feature_guard, which block uncredentialed / unentitled callers first).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from hermes.shell_server.cowork.providers_api import _reject_if_cloud_managed

pytestmark = pytest.mark.unit


class _FakeProxy:
    def __init__(self, providers: list[dict]) -> None:
        self._providers = providers

    async def call_list(self, member: str, *args):
        assert member == "list_providers"
        return list(self._providers)


_CLOUD = {"provider_id": "p-cloud", "alias": "openai-gpt4", "managed_by": "cloud"}
_LOCAL = {"provider_id": "p-local", "alias": "my-ollama", "managed_by": None}


@pytest.mark.asyncio
async def test_rejects_delete_of_cloud_provider_by_id() -> None:
    proxy = _FakeProxy([_CLOUD, _LOCAL])
    with pytest.raises(HTTPException) as ei:
        await _reject_if_cloud_managed(proxy, provider_id="p-cloud")
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_rejects_overwrite_of_cloud_alias() -> None:
    proxy = _FakeProxy([_CLOUD])
    with pytest.raises(HTTPException) as ei:
        await _reject_if_cloud_managed(proxy, alias="openai-gpt4")
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_allows_local_provider_delete() -> None:
    proxy = _FakeProxy([_CLOUD, _LOCAL])
    # Must NOT raise for a locally-owned provider.
    await _reject_if_cloud_managed(proxy, provider_id="p-local")


@pytest.mark.asyncio
async def test_allows_unknown_id() -> None:
    proxy = _FakeProxy([_CLOUD])
    await _reject_if_cloud_managed(proxy, provider_id="does-not-exist")
