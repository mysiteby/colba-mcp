import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from colba_mcp.server import create_pipeline, get_workflow_json_creation_doc, generate_pipeline_json

@pytest.mark.asyncio
async def test_create_pipeline_mcp_tool():
    """
    Test that create_pipeline MCP tool delegates to client.create_pipeline properly.
    """
    mock_created_template = {
        "id": "11111111-2222-3333-4444-555555555555",
        "name": "Test Vendor Pipeline",
        "description": "Test description",
        "is_active": True
    }

    mock_client = MagicMock()
    mock_client.create_pipeline = AsyncMock(return_value=mock_created_template)

    async def mock_handle_mcp_call(coro):
        return await coro(mock_client)

    pipeline_config = {
        "start_node_id": "start_1",
        "nodes": [
            {
                "id": "start_1",
                "type": "action",
                "config": {"action_type": "mutate_context"}
            }
        ]
    }

    with patch("colba_mcp.server.handle_mcp_call", side_effect=mock_handle_mcp_call):
        res = await create_pipeline(
            name="Test Vendor Pipeline",
            pipeline_config=pipeline_config,
            description="Test description"
        )

        assert res["id"] == "11111111-2222-3333-4444-555555555555"
        mock_client.create_pipeline.assert_called_once_with(
            name="Test Vendor Pipeline",
            pipeline_config=pipeline_config,
            description="Test description"
        )


def test_get_workflow_json_creation_doc_resource():
    """
    Test that the MCP resource returns non-empty documentation text with key rule sections.
    """
    doc = get_workflow_json_creation_doc()
    assert isinstance(doc, str)
    assert len(doc) > 100
    assert "Supported Node Types" in doc
    assert "output_enum" in doc
    assert "escalations" in doc


def test_generate_pipeline_json_prompt():
    """
    Test that the generate_pipeline_json MCP prompt combines requirements with rules.
    """
    prompt = generate_pipeline_json(user_requirements="Create invoice approval pipeline with 2 steps")
    assert "Create invoice approval pipeline with 2 steps" in prompt
    assert "STRICT WORKFLOW SPECIFICATION AND VALIDATION RULES:" in prompt
