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
    Note: Always use the 'id' field (and not the legacy 'pipeline_id' field) to start a process or archive a pipeline.
    """
    async def _call(client: ColbaClient):
        pipelines = await client.list_pipelines()
        for p in pipelines:
            p.pop("pipeline_id", None)
            if isinstance(p.get("pipeline_config"), dict):
                p["pipeline_config"].pop("pipeline_id", None)
        return pipelines
    return await handle_mcp_call(_call)


@mcp.tool()
async def start_process(template_id: str, payload: dict) -> Any:
    """
    Start a new workflow process under a template.
    template_id: UUID of the workflow template (MUST use the 'id' field from list_pipelines, NOT 'pipeline_id').
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
    pipeline_id: Filter by pipeline template UUID (use the 'id' field from list_pipelines).
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
async def get_process_details(process_id: str, verbose: bool = False) -> Any:
    """
    Fetch detailed status, current node states, and context variables of a specific process.
    process_id: UUID of the process.
    verbose: If True, returns full process structure including pipeline_config and display_all_data.
             If False (default), returns a compact representation with context variables but config omitted.
    """
    import copy
    async def _call(client: ColbaClient):
        raw = await client.get_process_details(process_id)
        if not raw or "error" in raw:
            return raw
        cleaned_data = copy.deepcopy(raw)
        if not verbose:
            cleaned_data.pop("pipeline_config", None)
            cleaned_data.pop("display_all_data", None)
        return cleaned_data
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
            get_request_details. Always call get_request_details first to obtain valid action IDs.
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


@mcp.tool()
async def sync_directory(data: list) -> Any:
    """
    Sync members of the organization.
    data: List of members mapping to the onboarding structure.
    """
    async def _call(client: ColbaClient):
        return await client.sync_directory(data)
    return await handle_mcp_call(_call)


@mcp.tool()
async def create_workgroup(name: str, type: str, parent_id: Optional[str] = None, key: Optional[str] = None) -> Any:
    """
    Create a new workgroup (DEPARTMENT, LOCATION, etc.) in the organization.
    name: Name of the workgroup.
    type: 'DEPARTMENT', 'LOCATION', or 'SQUAD'.
    parent_id: Optional parent workgroup UUID.
    key: Optional unique workgroup key.
    """
    async def _call(client: ColbaClient):
        return await client.create_workgroup(name, type, parent_id, key)
    return await handle_mcp_call(_call)


@mcp.tool()
async def delete_workgroup(workgroup_id: str) -> Any:
    """
    Delete a workgroup from the organization structure.
    workgroup_id: UUID of the workgroup to delete.
    """
    async def _call(client: ColbaClient):
        return await client.delete_workgroup(workgroup_id)
    return await handle_mcp_call(_call)


@mcp.tool()
async def list_custom_fields() -> Any:
    """
    List all registered global custom fields in the organization.
    Returns: A list of custom fields with their ID, name, label, type, validation, and options.
    """
    async def _call(client: ColbaClient):
        return await client.list_custom_fields()
    return await handle_mcp_call(_call)


@mcp.tool()
async def create_custom_field(
    name: str,
    label: str,
    type: str,
    description: Optional[str] = None,
    validation: Optional[dict] = None,
    options: Optional[list] = None,
    is_active: bool = True
) -> Any:
    """
    Create a custom metadata field for workflows.
    name: Identifier name (alphanumeric/snake_case).
    label: Human readable display label.
    type: Field type (e.g. 'text', 'number', 'select', 'date').
    description: Optional details.
    validation: Optional regex/constraint config dictionary.
    options: Optional choice list for select fields.
    is_active: True if field is enabled.
    """
    payload = {
        "name": name,
        "label": label,
        "type": type,
        "description": description,
        "validation": validation or {},
        "options": options or [],
        "is_active": is_active
    }
    async def _call(client: ColbaClient):
        return await client.create_custom_field(payload)
    return await handle_mcp_call(_call)


@mcp.tool()
async def delete_custom_field(field_id: str) -> Any:
    """
    Delete a custom field from the system.
    field_id: UUID of the custom field to delete.
    """
    async def _call(client: ColbaClient):
        return await client.delete_custom_field(field_id)
    return await handle_mcp_call(_call)


@mcp.tool()
async def create_vendor(
    name: str,
    email: Optional[str] = None,
    account_number: Optional[str] = None,
    bank_country_code: Optional[str] = None,
    contact_details: Optional[dict] = None,
    address_details: Optional[dict] = None,
    settings: Optional[dict] = None,
    visible: bool = True,
    is_active: bool = True,
    financial_details: Optional[list] = None
) -> Any:
    """
    Create a new supplier/vendor profile.
    name: Name of the vendor.
    email: Primary email.
    account_number: Bank account number.
    bank_country_code: country code.
    financial_details: List of financial settings/details for invoicing.
    """
    payload = {
        "name": name,
        "email": email,
        "account_number": account_number,
        "bank_country_code": bank_country_code,
        "contact_details": contact_details or {},
        "address_details": address_details or {},
        "settings": settings or {},
        "visible": visible,
        "is_active": is_active,
        "financial_details": financial_details or []
    }
    async def _call(client: ColbaClient):
        return await client.create_vendor(payload)
    return await handle_mcp_call(_call)


@mcp.tool()
async def delete_vendor(vendor_id: str) -> Any:
    """
    Delete a vendor from accounting records.
    vendor_id: UUID of the vendor.
    """
    async def _call(client: ColbaClient):
        return await client.delete_vendor(vendor_id)
    return await handle_mcp_call(_call)


@mcp.tool()
async def archive_pipeline(template_id: str) -> Any:
    """
    Deactivate/archive a workflow pipeline template.
    template_id: UUID of the template (MUST use the 'id' field from list_pipelines, NOT 'pipeline_id').
    """
    async def _call(client: ColbaClient):
        return await client.archive_pipeline(template_id)
    return await handle_mcp_call(_call)


@mcp.tool()
async def resolve_mcp_approval(
    action: str,
    approval_id: Optional[str] = None,
    token: Optional[str] = None,
    session_key: Optional[str] = None
) -> Any:
    """
    Resolve (approve or reject) a pending MCP human-in-the-loop (HITL) transaction.
    action: MUST be 'approve' or 'reject'.
    approval_id: UUID of the pending approval (optional, exactly one of approval_id or token is required).
    token: Raw token string from the pending approval response (optional).
    session_key: Operator's active session key. If not provided, defaults to COLBA_TOKEN.
    """
    if action not in ("approve", "reject"):
        return {"error": "invalid_input", "message": "action must be 'approve' or 'reject'."}

    async def _call(client: ColbaClient):
        return await client.resolve_mcp_approval(
            action=action,
            approval_id=approval_id,
            token=token,
            session_key=session_key
        )
    return await handle_mcp_call(_call)


@mcp.tool()
async def get_pipeline_generation_rules() -> Any:
    """
    Get the official specification, validation rules, node type hierarchies, and schema guidelines
    for generating new Colba workflow pipeline JSON configurations.
    Returns: Complete Markdown specification text to guide pipeline JSON creation.
    """
    return get_workflow_json_creation_doc()


@mcp.tool()
async def create_pipeline(
    name: str,
    pipeline_config: dict,
    description: Optional[str] = None
) -> Any:
    """
    Create a new workflow pipeline template in Colba.
    name: Human readable template name (e.g. 'Vendor Invoice Approval').
    pipeline_config: Complete JSON workflow configuration complying strictly with docs://skills/workflow_json_creation specification. Must contain start_node_id and valid nodes list. Call get_pipeline_generation_rules tool first to inspect the required format.
    description: Optional human-readable description.
    Returns: Created pipeline template details including template_id.
    """
    async def _call(client: ColbaClient):
        return await client.create_pipeline(
            name=name,
            pipeline_config=pipeline_config,
            description=description
        )
    return await handle_mcp_call(_call)


# ---------------------------------------------------------------------------
# Resources & Prompts
# ---------------------------------------------------------------------------


@mcp.resource("docs://skills/workflow_json_creation")
def get_workflow_json_creation_doc() -> str:
    """
    Returns the official Workflow JSON Creation guide, containing strict rules, schema specifications,
    node hierarchies, output_enum validation, and escalation policies for generating new pipeline JSONs.
    """
    base_dir = os.path.dirname(__file__)
    parent_dir = os.path.dirname(base_dir)
    cwd = os.getcwd()

    candidate_paths = [
        os.path.join(base_dir, "docs", "workflow_json_creation.md"),
        os.path.join(base_dir, "workflow_json_creation.md"),
        os.path.join(parent_dir, "docs", "skills", "workflow_json_creation.md"),
        os.path.join(cwd, "docs", "skills", "workflow_json_creation.md"),
        os.path.join(cwd, "colba_mcp", "docs", "workflow_json_creation.md"),
    ]

    for path in candidate_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                    if content and len(content) > 50:
                        return content
            except Exception:
                pass

    raise RuntimeError("Workflow JSON Creation documentation file could not be located on disk.")


@mcp.prompt()
def generate_pipeline_json(user_requirements: str) -> str:
    """
    Prompt template to guide an LLM agent in generating a new pipeline JSON following strict Colba specification rules.
    """
    doc_content = get_workflow_json_creation_doc()
    return (
        f"You are an expert pipeline generator for Colba workflow engine.\n"
        f"Generate a valid pipeline JSON matching the user requirements below.\n\n"
        f"USER REQUIREMENTS:\n{user_requirements}\n\n"
        f"STRICT WORKFLOW SPECIFICATION AND VALIDATION RULES:\n"
        f"```markdown\n{doc_content}\n```\n\n"
        f"Output ONLY valid JSON matching the specification."
    )


