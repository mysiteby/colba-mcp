import os
from types import SimpleNamespace
import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from colba_mcp.client import ColbaClient
from colba_mcp.server import (
    create_job_title,
    delete_job_title,
    get_client,
    get_update_log,
    handle_mcp_call,
    _get_cached_client,
    close_cached_clients,
    set_pipeline_access,
    update_job_title,
    mcp,
)


def test_server_uses_mcp_v2():
    assert isinstance(mcp, MCPServer)

def test_get_client_missing_token(monkeypatch):
    monkeypatch.delenv("COLBA_TOKEN", raising=False)
    with pytest.raises(ValueError, match="COLBA_TOKEN not provided"):
        get_client()

def test_get_client_success(monkeypatch):
    monkeypatch.setenv("COLBA_TOKEN", "tk_live_test_token_123")
    monkeypatch.setenv("COLBA_API_URL", "http://localhost:9000/")
    client = get_client()
    assert isinstance(client, ColbaClient)
    assert client.token == "tk_live_test_token_123"
    assert client.api_url == "http://localhost:9000"  # Should strip trailing slash


def test_get_client_uses_streamable_http_authorization_header(monkeypatch):
    monkeypatch.delenv("COLBA_TOKEN", raising=False)
    ctx = SimpleNamespace(
        headers={"Authorization": "Bearer tk_mcp_remote_test_token"},
    )

    client = get_client(ctx)

    assert client.token == "tk_mcp_remote_test_token"


def test_get_client_does_not_use_stdio_token_for_http(monkeypatch):
    monkeypatch.setenv("COLBA_TOKEN", "tk_stdio_only_token")
    ctx = SimpleNamespace(headers={})

    with pytest.raises(ValueError, match="HTTP request has no authentication token"):
        get_client(ctx)


@pytest.mark.asyncio
async def test_mcp_client_is_reused_and_can_be_closed(monkeypatch):
    monkeypatch.setenv("COLBA_TOKEN", "tk_cached_test_token")
    await close_cached_clients()

    first = await _get_cached_client()
    second = await _get_cached_client()

    assert first is second
    await close_cached_clients()


@pytest.mark.asyncio
async def test_mcp_call_exposes_rate_limit_retry_after(monkeypatch):
    import colba_mcp.server as server_module

    async def _fake_client(ctx=None):
        return object()

    monkeypatch.setattr(server_module, "_get_cached_client", _fake_client)

    request = httpx.Request("GET", "http://colba.test/api/v1/templates")
    response = httpx.Response(
        429,
        request=request,
        headers={"Retry-After": "23"},
        json={"detail": "Too Many Requests"},
    )

    async def failing_call(_client):
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    result = await handle_mcp_call(failing_call, ctx=None)

    assert result == {
        "error": "rate_limited",
        "status_code": 429,
        "message": "Colba API error (429): {'detail': 'Too Many Requests'}",
        "retry_after": "23",
    }

@pytest.mark.asyncio
async def test_get_update_log():
    log_content = await get_update_log()
    assert "Colba MCP Server Update Log" in log_content
    assert "Restart Required" in log_content


@pytest.mark.asyncio
async def test_job_title_tools_validate_names_before_api_call():
    assert await create_job_title("   ") == {
        "error": "invalid_input",
        "message": "name cannot be empty",
    }
    assert await delete_job_title("   ") == {
        "error": "invalid_input",
        "message": "name cannot be empty",
    }
    assert await update_job_title("Existing", "   ") == {
        "error": "invalid_input",
        "message": "new_name cannot be empty",
    }

    too_long_name = "x" * 101
    result = await create_job_title(too_long_name)
    assert result["error"] == "invalid_input"
    assert "100 characters or fewer" in result["message"]


@pytest.mark.asyncio
async def test_pipeline_access_tool_validates_rules_before_api_call():
    template_id = "00000000-0000-0000-0000-000000000001"

    assert await set_pipeline_access(template_id) == {
        "error": "invalid_input",
        "message": "Provide view_type, launch_type, or both",
    }
    assert await set_pipeline_access(template_id, launch_type="department") == {
        "error": "invalid_input",
        "message": "launch_value or launch_values is required for department",
    }
    result = await set_pipeline_access(template_id, launch_type="unsupported", launch_value="x")
    assert result["error"] == "invalid_input"

    assert await set_pipeline_access(
        template_id,
        view_type="all_members",
        view_value="unexpected",
    ) == {
        "error": "invalid_input",
        "message": "view_value(s) must be omitted for all_members",
    }
