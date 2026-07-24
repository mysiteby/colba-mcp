import os
import pytest
from mcp.server.fastmcp import FastMCP
from colba_mcp.client import ColbaClient
from colba_mcp.server import get_client, get_update_log

def test_get_client_missing_token(monkeypatch):
    monkeypatch.delenv("COLBA_TOKEN", raising=False)
    with pytest.raises(ValueError, match="COLBA_TOKEN environment variable is not set"):
        get_client()

def test_get_client_success(monkeypatch):
    monkeypatch.setenv("COLBA_TOKEN", "tk_live_test_token_123")
    monkeypatch.setenv("COLBA_API_URL", "http://localhost:9000/")
    client = get_client()
    assert isinstance(client, ColbaClient)
    assert client.token == "tk_live_test_token_123"
    assert client.api_url == "http://localhost:9000"  # Should strip trailing slash


@pytest.mark.asyncio
async def test_get_update_log():
    log_content = await get_update_log()
    assert "Colba MCP Server Update Log" in log_content
    assert "Restart Required" in log_content

