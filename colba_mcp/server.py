import os
import sys
import logging
from typing import Optional, Any

from mcp.server.fastmcp import FastMCP
from .client import ColbaClient, validate_uuid
import httpx

# Configure logging to stderr so it does not interfere with stdio transport
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("colba-mcp")

# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

# Built-in statuses always accepted by the backend's DecisionService
_BUILTIN_DECISION_STATUSES = {"approved", "rejected"}

# limit/offset guards: prevent absurdly large requests from the LLM
_MAX_LIMIT = 200

# ---------------------------------------------------------------------------
# Server init
# ---------------------------------------------------------------------------

mcp = FastMCP("colba-mcp")


def get_client() -> ColbaClient:
    api_url = os.getenv("COLBA_API_URL", "http://localhost:9000")
    token = os.getenv("COLBA_TOKEN", "")
    if not token:
        raise ValueError(
            "COLBA_TOKEN environment variable is not set. "
            "Generate a token in Colba Settings → MCP Agent Integration."
        )
    return ColbaClient(api_url=api_url, token=token)


async def handle_mcp_call(coro):
    client = None
    try:
        client = get_client()
        result = await coro(client)
        return result
    except ValueError as e:
        # Input validation errors (UUID format, missing config, etc.)
        return {"error": "invalid_input", "message": str(e)}
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text[:500]  # cap length — never log full response

        error_map = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            422: "validation_error",
        }
        error_type = error_map.get(status_code, "api_error")
        return {
            "error": error_type,
            "status_code": status_code,
            "message": f"Colba API error ({status_code}): {detail}",
        }
    except httpx.HTTPError:
        # Do NOT include exception message — it may contain the URL with creds
        return {
            "error": "network_error",
            "message": "Could not reach the Colba API. Check COLBA_API_URL and network connectivity.",
        }
    except Exception as e:
        # Sanitise: never echo raw exception strings from internal code paths
        logger.error("mcp_internal_error", exc_info=True)
        return {
            "error": "internal_error",
            "message": "An unexpected error occurred. Check the MCP server logs.",
        }
    finally:
        if client:
            await client.close()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_pipelines() -> Any:
    """
    Get a list of available workflow templates/pipelines that can be started.
    Returns: List of pipeline templates with their required header schemas.
    """
    async def _call(client: ColbaClient):
        return await client.list_pipelines()
    return await handle_mcp_call(_call)


@mcp.tool()
async def start_process(template_id: str, payload: dict) -> Any:
    """
    Start a new workflow process under a template.
    template_id: UUID of the workflow template (use list_pipelines to find it).
    payload: Input data matching the template's header_schema.
    Returns: The started process ID and initial status.
    """
    async def _call(client: ColbaClient):
        return await client.start_process(template_id, payload)
    return await handle_mcp_call(_call)


@mcp.tool()
async def list_processes(
    status: Optional[str] = None,
    pipeline_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """
    List all processes visible to the member (filtered by backend per role).
    status: Filter by status: 'active', 'completed', 'rejected', 'failed'.
    pipeline_id: Filter by pipeline template UUID.
    limit: Max results to return (default: 50, max: 200).
    offset: Pagination offset (default: 0).
    NOTE: Pagination is server-side; the list may be incomplete if total > limit.
    """
    if limit > _MAX_LIMIT:
        return {
            "error": "invalid_input",
            "message": f"limit cannot exceed {_MAX_LIMIT}.",
        }
    if offset < 0:
        return {"error": "invalid_input", "message": "offset cannot be negative."}

    async def _call(client: ColbaClient):
        return await client.list_processes(
            status=status, pipeline_id=pipeline_id, limit=limit, offset=offset
        )
    return await handle_mcp_call(_call)


@mcp.tool()
async def list_pending_requests(limit: int = 50, offset: int = 0) -> Any:
    """
    List pending approval requests waiting for this member's decision.
    Includes the available_actions list for each request so you know valid statuses.
    limit: Max results to return (default: 50, max: 200).
    offset: Pagination offset (default: 0).
    NOTE: Pagination is server-side; the list may be incomplete if total > limit.
    """
    if limit > _MAX_LIMIT:
        return {
            "error": "invalid_input",
            "message": f"limit cannot exceed {_MAX_LIMIT}.",
        }
    if offset < 0:
        return {"error": "invalid_input", "message": "offset cannot be negative."}

    async def _call(client: ColbaClient):
        return await client.list_pending_requests(limit=limit, offset=offset)
    return await handle_mcp_call(_call)


@mcp.tool()
async def get_process_details(process_id: str) -> Any:
    """
    Fetch detailed status, current node states, and context variables of a specific process.
    process_id: UUID of the process.
    """
    async def _call(client: ColbaClient):
        return await client.get_process_details(process_id)
    return await handle_mcp_call(_call)


@mcp.tool()
async def get_request_details(request_id: str) -> Any:
    """
    Fetch detailed request information, including audit history, context payload,
    and available_actions. Always call this before submit_decision to know
    which action values are valid for this request.
    request_id: UUID of the pending approval request.
    """
    async def _call(client: ColbaClient):
        return await client.get_request_details(request_id)
    return await handle_mcp_call(_call)


@mcp.tool()
async def submit_decision(
    request_id: str, status: str, comment: Optional[str] = None
) -> Any:
    """
    Submit a decision on a pending approval request.
    request_id: UUID of the pending approval request.
    status: MUST be one of the values from available_actions returned by
            get_request_details, or one of the built-in values: 'approved', 'rejected'.
            Do NOT guess — always call get_request_details first.
    comment: Optional explanation for the decision (recommended for audit trail).
    """
    # Validate UUID early — surfaces a clean error instead of a backend 422
    try:
        validate_uuid(request_id, "request_id")
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    # Strip and length-cap the status to prevent injection / abuse
    status = status.strip()[:100]
    if not status:
        return {"error": "invalid_input", "message": "status cannot be empty."}

    # Cap comment length
    if comment:
        comment = comment.strip()[:2000] or None

    async def _call(client: ColbaClient):
        return await client.submit_decision(request_id, status, comment)
    return await handle_mcp_call(_call)
