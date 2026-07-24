import pytest
import respx
import httpx
from colba_mcp.client import ColbaClient

@pytest.mark.asyncio
@respx.mock
async def test_colba_client_integration_flow():
    # 1. Setup client settings
    api_url = "http://localhost:9000"
    token = "tk_live_test_integration_token"
    template_id = "00000000-0000-0000-0000-000000000001"
    process_id = "00000000-0000-0000-0000-000000000002"
    request_id = "00000000-0000-0000-0000-000000000003"
    org_id = "00000000-0000-0000-0000-000000000004"

    client = ColbaClient(api_url=api_url, token=token)

    # 2. Mock Dynamic Organization Resolution
    respx.get(f"{api_url}/api/v1/directory/me/organizations").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": org_id, "name": "Test Org", "role": "member"}]
        )
    )

    # 3. Mock list_pipelines
    respx.get(f"{api_url}/api/v1/templates").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": template_id,
                    "name": "Travel Expense",
                    "description": "Travel cost approval",
                    "header_schema": {"properties": {"amount": {"type": "number"}}}
                }
            ]
        )
    )

    # 4. Mock start_process (checks that X-Organization-ID header is correctly passed)
    respx.post(f"{api_url}/api/v1/workflow/start/{template_id}").mock(
        return_value=httpx.Response(
            201,
            json={"status": "started", "process_id": process_id}
        )
    )

    # 5. Mock list_processes
    respx.get(f"{api_url}/api/v1/workflow/processes").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": process_id,
                    "display_id": "PR-100",
                    "status": "active",
                    "pipeline_id": template_id,
                    "template_name": "Travel Expense"
                }
            ]
        )
    )

    # 6. Mock list_pending_requests (includes available_actions list)
    respx.get(f"{api_url}/api/v1/workflow/requests").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": request_id,
                    "process_id": process_id,
                    "status": "pending",
                    "node_id": "node_approve",
                    "pipeline_name": "Travel Expense",
                    "pipeline_id": template_id,
                    "available_actions": ["approved", "rejected", "escalate"]
                }
            ]
        )
    )

    # 7. Mock get_request_details (includes context, payload and available_actions)
    respx.get(f"{api_url}/api/v1/workflow/requests/{request_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": request_id,
                "process_id": process_id,
                "status": "pending",
                "payload": {"amount": 1500},
                "available_actions": ["approved", "rejected", "escalate"]
            }
        )
    )

    # 8. Mock submit_decision
    respx.post(f"{api_url}/api/v1/workflow/requests/{request_id}/decide").mock(
        return_value=httpx.Response(
            200,
            json={"status": "decision_recorded"}
        )
    )

    # 9. Mock resolve_mcp_approval
    respx.post(f"{api_url}/api/v1/mcp/approvals/action").mock(
        return_value=httpx.Response(
            200,
            json={"status": "approved", "id": "00000000-0000-0000-0000-000000000005"}
        )
    )

    # H. Mock list_custom_fields
    respx.get(f"{api_url}/api/v1/workflow/fields").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "00000000-0000-0000-0000-000000000006",
                    "name": "department",
                    "label": "Department",
                    "type": "select",
                    "options": {"source": "departments"}
                }
            ]
        )
    )

    # Execute client flow
    # A. List pipelines
    pipelines = await client.list_pipelines()
    assert len(pipelines) == 1
    assert pipelines[0]["name"] == "Travel Expense"

    # B. Start process (resolves organization context first)
    start_res = await client.start_process(template_id, {"amount": 1500})
    assert start_res["process_id"] == process_id
    assert client.org_id == org_id

    # C. List active processes
    processes = await client.list_processes(status="active")
    assert len(processes) == 1
    assert processes[0]["id"] == process_id

    # D. List pending requests
    pending = await client.list_pending_requests()
    assert len(pending) == 1
    assert pending[0]["id"] == request_id
    assert "escalate" in pending[0]["available_actions"]

    # E. Fetch request details
    req_details = await client.get_request_details(request_id)
    assert req_details["payload"]["amount"] == 1500
    assert "rejected" in req_details["available_actions"]

    # F. Submit decision
    decision_res = await client.submit_decision(request_id, "approved", "Approved by integration test")
    assert decision_res["status"] == "decision_recorded"

    # G. Resolve MCP approval
    resolve_res = await client.resolve_mcp_approval(
        action="approve",
        approval_id="00000000-0000-0000-0000-000000000005",
        session_key="tk_session_mock"
    )
    assert resolve_res["status"] == "approved"

    # H. List custom fields
    fields = await client.list_custom_fields()
    assert len(fields) == 1
    assert fields[0]["name"] == "department"

    await client.close()

