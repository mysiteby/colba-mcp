import os
import sys
import logging
import time
import asyncio
from typing import Optional, Any

from mcp.server.mcpserver import MCPServer, Context
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
_MAX_JOB_TITLE_LENGTH = 100
_CLIENT_CACHE_TTL_SECONDS = 300.0
_CLIENT_CACHE_MAX_SIZE = 64
_client_cache: dict[tuple[str, str], tuple[float, ColbaClient]] = {}

# ---------------------------------------------------------------------------
# Server init
# ---------------------------------------------------------------------------

mcp = MCPServer(
    "colba-mcp",
    title="Colba MCP",
    description="Workflow, approvals, directory, and accounting tools for Colba agents.",
    version="0.1.0",
)


def _resolve_connection_config(ctx: Optional[Context] = None) -> tuple[str, str]:
    api_url = os.getenv("COLBA_API_URL", "http://localhost:9000")
    token = None

    if ctx is not None and ctx.headers is not None:
        headers = {key.lower(): value for key, value in ctx.headers.items()}
        # HTTP transports must authenticate each request. Never fall back to
        # the process token here, otherwise a misconfigured public HTTP route
        # could act as the local stdio agent.
        auth_header = headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = headers.get("x-api-key")
        if not token:
            raise ValueError("The MCP HTTP request has no authentication token.")
    else:
        # Local stdio agents authenticate through their process environment.
        token = os.getenv("COLBA_TOKEN", "")

    if not token:
        raise ValueError(
            "COLBA_TOKEN not provided and the MCP request has no authentication token."
        )
    return api_url.rstrip("/"), token


def get_client(ctx: Optional[Context] = None) -> ColbaClient:
    api_url, token = _resolve_connection_config(ctx)
    return ColbaClient(api_url=api_url, token=token)


def _evict_stale_clients(now: float) -> list[ColbaClient]:
    stale_keys = [
        key
        for key, (last_used, _) in _client_cache.items()
        if now - last_used > _CLIENT_CACHE_TTL_SECONDS
    ]
    while len(_client_cache) - len(stale_keys) >= _CLIENT_CACHE_MAX_SIZE:
        oldest_key = min(_client_cache, key=lambda key: _client_cache[key][0])
        if oldest_key not in stale_keys:
            stale_keys.append(oldest_key)
        del _client_cache[oldest_key]
    stale_clients = []
    for key in stale_keys:
        cached = _client_cache.pop(key, None)
        if cached:
            stale_clients.append(cached[1])
    return stale_clients


async def _get_cached_client(ctx: Optional[Context] = None) -> ColbaClient:
    api_url, token = _resolve_connection_config(ctx)
    cache_key = (api_url, token)
    now = time.monotonic()
    cached = _client_cache.get(cache_key)
    if cached and now - cached[0] <= _CLIENT_CACHE_TTL_SECONDS:
        _client_cache[cache_key] = (now, cached[1])
        return cached[1]

    if cached:
        _client_cache.pop(cache_key, None)
        await cached[1].close()

    stale_clients = _evict_stale_clients(now)
    if stale_clients:
        await asyncio.gather(*(client.close() for client in stale_clients))

    client = ColbaClient(api_url=api_url, token=token)
    _client_cache[cache_key] = (now, client)
    return client


async def close_cached_clients() -> None:
    clients = [client for _, client in _client_cache.values()]
    _client_cache.clear()
    if clients:
        await asyncio.gather(*(client.close() for client in clients))


async def handle_mcp_call(coro, ctx: Optional[Context] = None):
    try:
        client = await _get_cached_client(ctx=ctx)
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
            429: "rate_limited",
            422: "validation_error",
        }
        error_type = error_map.get(status_code, "api_error")
        error = {
            "error": error_type,
            "status_code": status_code,
            "message": f"Colba API error ({status_code}): {detail}",
        }
        if status_code == 429:
            retry_after = e.response.headers.get("Retry-After")
            if retry_after:
                error["retry_after"] = retry_after
        return error
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
# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_pipelines(status: Optional[str] = None, ctx: Context = None) -> Any:
    """
    Get a list of available workflow templates/pipelines.
    status: Optional filter: 'active' (default), 'draft', 'archived', or 'all'. Pass 'all' or 'draft' to include draft pipelines.
    Returns: List of pipeline templates with their required header schemas.
    Note: Always use the 'id' field (and not the legacy 'pipeline_id' field) to start a process or archive a pipeline.
    """
    async def _call(client: ColbaClient):
        pipelines = await client.list_pipelines(status=status)
        for p in pipelines:
            p.pop("pipeline_id", None)
            if isinstance(p.get("pipeline_config"), dict):
                p["pipeline_config"].pop("pipeline_id", None)
        return pipelines
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def start_process(template_id: str, payload: dict, ctx: Context = None) -> Any:
    """
    Start a new workflow process under a template.
    template_id: UUID of the workflow template (MUST use the 'id' field from list_pipelines, NOT 'pipeline_id').
    payload: Input data matching the template's header_schema.
    Returns: The started process ID and initial status.
    """
    async def _call(client: ColbaClient):
        return await client.start_process(template_id, payload)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def validate_process_input(template_id: str, payload: dict, ctx: Context = None) -> Any:
    """
    Validate input for the first stage of a workflow without starting a process.
    The same backend validator is used by the UI and the process-start API.
    Returns valid, schema, and structured field errors.
    """
    async def _call(client: ColbaClient):
        return await client.validate_process_input(template_id, payload)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def list_processes(
    status: Optional[str] = None,
    pipeline_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    ctx: Context = None,
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
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def list_pending_requests(limit: int = 50, offset: int = 0, ctx: Context = None) -> Any:
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
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def get_process_details(process_id: str, verbose: bool = False, ctx: Context = None) -> Any:
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
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def get_request_details(request_id: str, ctx: Context = None) -> Any:
    """
    Fetch detailed request information, including audit history, context payload,
    and available_actions. Always call this before submit_decision to know
    which action values are valid for this request.
    request_id: UUID of the pending approval request.
    """
    async def _call(client: ColbaClient):
        return await client.get_request_details(request_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def submit_decision(
    request_id: str, status: str, comment: Optional[str] = None, ctx: Context = None
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
    return await handle_mcp_call(_call, ctx=ctx)



@mcp.tool()
async def sync_directory(data: list, ctx: Context = None) -> Any:
    """
    Sync members of the organization.
    data: List of members mapping to the onboarding structure.
    """
    async def _call(client: ColbaClient):
        return await client.sync_directory(data)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def create_workgroup(name: str, type: str, parent_id: Optional[str] = None, key: Optional[str] = None, ctx: Context = None) -> Any:
    """
    Create a new workgroup (DEPARTMENT, LOCATION, etc.) in the organization.
    name: Name of the workgroup.
    type: 'DEPARTMENT', 'LOCATION', or 'SQUAD'.
    parent_id: Optional parent workgroup UUID.
    key: Optional unique workgroup key.
    """
    async def _call(client: ColbaClient):
        return await client.create_workgroup(name, type, parent_id, key)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def delete_workgroup(workgroup_id: str, ctx: Context = None) -> Any:
    """
    Delete a workgroup from the organization structure.
    workgroup_id: UUID of the workgroup to delete.
    """
    async def _call(client: ColbaClient):
        return await client.delete_workgroup(workgroup_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def update_workgroup(
    workgroup_id: str,
    name: Optional[str] = None,
    type: Optional[str] = None,
    parent_id: Optional[str] = None,
    ctx: Context = None
) -> Any:
    """
    Update an existing workgroup's details (name, type, or parent_id for hierarchy restructuring).
    workgroup_id: UUID of the workgroup to update.
    name: Optional new name of the workgroup.
    type: Optional new type ('DEPARTMENT', 'LOCATION', 'SQUAD').
    parent_id: Optional new parent workgroup UUID (pass empty string or null to move to root).
    """
    async def _call(client: ColbaClient):
        return await client.update_workgroup(workgroup_id, name=name, type=type, parent_id=parent_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def list_custom_fields(ctx: Context = None) -> Any:
    """
    List all registered global custom fields in the organization.
    Returns: A list of custom fields with their ID, name, label, type, validation, and options.
    """
    async def _call(client: ColbaClient):
        return await client.list_custom_fields()
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def create_custom_field(
    name: str,
    label: str,
    type: str,
    description: Optional[str] = None,
    validation: Optional[dict] = None,
    options: Optional[list] = None,
    is_active: bool = True,
    ctx: Context = None
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
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def delete_custom_field(field_id: str, ctx: Context = None) -> Any:
    """
    Delete a custom field from the system.
    field_id: UUID of the custom field to delete.
    """
    async def _call(client: ColbaClient):
        return await client.delete_custom_field(field_id)
    return await handle_mcp_call(_call, ctx=ctx)


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
    financial_details: Optional[list] = None,
    ctx: Context = None
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
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def delete_vendor(vendor_id: str, ctx: Context = None) -> Any:
    """
    Delete a vendor from accounting records.
    vendor_id: UUID of the vendor.
    """
    async def _call(client: ColbaClient):
        return await client.delete_vendor(vendor_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def update_vendor(
    vendor_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    tax_id: Optional[str] = None,
    account_number: Optional[str] = None,
    bank_country_code: Optional[str] = None,
    is_active: Optional[bool] = None,
    visible: Optional[bool] = None,
    ctx: Context = None
) -> Any:
    """
    Update details of an existing vendor.
    vendor_id: UUID of the vendor to update.
    name: Optional new vendor name.
    email: Optional new contact email.
    tax_id: Optional tax registration ID.
    account_number: Optional bank account number.
    bank_country_code: Optional country code.
    is_active: Optional active status flag.
    visible: Optional visibility flag.
    """
    payload = {}
    if name is not None:
        payload["name"] = name
    if email is not None:
        payload["email"] = email
    if tax_id is not None:
        payload["tax_id"] = tax_id
    if account_number is not None:
        payload["account_number"] = account_number
    if bank_country_code is not None:
        payload["bank_country_code"] = bank_country_code
    if is_active is not None:
        payload["is_active"] = is_active
    if visible is not None:
        payload["visible"] = visible

    async def _call(client: ColbaClient):
        return await client.update_vendor(vendor_id, payload)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def update_member(
    member_id: str,
    full_name: Optional[str] = None,
    role: Optional[str] = None,
    job_title: Optional[str] = None,
    is_active: Optional[bool] = None,
    manager_id: Optional[str] = None,
    substitute_id: Optional[str] = None,
    ctx: Context = None
) -> Any:
    """
    Update member details, access role, job title, status, or manager relationships.
    member_id: UUID of the member to update.
    full_name: Optional new full name.
    role: Optional assignable access role ('admin' or 'member'). The organization
        creator's 'superadmin' role cannot be assigned or changed through MCP.
    job_title: Optional organization position (for example 'CFO' or 'Accountant').
    is_active: Optional active status flag.
    manager_id: Optional manager member UUID.
    substitute_id: Optional substitute member UUID.
    """
    payload = {}
    if full_name is not None:
        payload["full_name"] = full_name
    if role is not None:
        if role not in {"admin", "member"}:
            raise ValueError("role must be 'admin' or 'member'; superadmin is reserved for the organization creator")
        payload["role"] = role
    if job_title is not None:
        payload["job_title"] = job_title
    if is_active is not None:
        payload["is_active"] = is_active
    if manager_id is not None:
        payload["manager_id"] = manager_id
    if substitute_id is not None:
        payload["substitute_id"] = substitute_id

    async def _call(client: ColbaClient):
        return await client.update_member(member_id, payload)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def validate_pipeline_schema(pipeline_config: dict) -> Any:
    """
    Dry-run validation for a workflow pipeline JSON structure.
    Checks required root fields (start_node_id, nodes array), node uniqueness,
    valid transitions target existence, non-terminal node transition coverage, and node type sanity.
    Returns: Dict with is_valid (bool), errors (list of strings), and node_count (int).
    """
    errors = []
    if not isinstance(pipeline_config, dict):
        return {"is_valid": False, "errors": ["pipeline_config must be a dictionary object"], "node_count": 0}

    nodes = pipeline_config.get("nodes")
    if not isinstance(nodes, list) or len(nodes) == 0:
        errors.append("pipeline_config must contain a non-empty 'nodes' array")

    start_node_id = pipeline_config.get("start_node_id")
    if not start_node_id:
        errors.append("pipeline_config is missing 'start_node_id'")

    if errors:
        return {"is_valid": False, "errors": errors, "node_count": len(nodes) if isinstance(nodes, list) else 0}

    node_ids = set()
    node_map = {}
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"Node at index {idx} is not an object")
            continue
        nid = node.get("id")
        if not nid:
            errors.append(f"Node at index {idx} is missing 'id'")
            continue
        if nid in node_ids:
            errors.append(f"Duplicate node id: '{nid}'")
        node_ids.add(nid)
        node_map[nid] = node

    if start_node_id and start_node_id not in node_ids:
        errors.append(f"start_node_id '{start_node_id}' does not match any node id in nodes array")

    valid_node_types = {
        "collect_input", "form_start", "approval_request", "task", "condition", "conditional",
        "action", "outbound_webhook", "outbound_integration", "llm_request",
        "load_test", "create_vendor", "create_po", "create_invoice",
        "wait_for_callback", "end"
    }

    has_end_node = False
    for nid, node in node_map.items():
        ntype = node.get("type")
        if not ntype:
            errors.append(f"Node '{nid}' is missing 'type'")
        elif ntype not in valid_node_types:
            errors.append(f"Node '{nid}' has unrecognized type '{ntype}'")

        if ntype == "form_start" and nid != start_node_id:
            errors.append(f"Node '{nid}' of type 'form_start' must be the pipeline start node '{start_node_id}'")

        if ntype == "end":
            has_end_node = True

        transitions = node.get("transitions", {})
        if isinstance(transitions, dict):
            for t_key, target in transitions.items():
                target_id = target.get("target") if isinstance(target, dict) else target
                if target_id and target_id not in node_ids:
                    errors.append(f"Node '{nid}' transition '{t_key}' points to non-existent node '{target_id}'")
        elif ntype != "end":
            errors.append(f"Node '{nid}' has invalid transitions structure")

        if ntype != "end" and not transitions:
            errors.append(f"Non-terminal node '{nid}' has no outgoing transitions")

    if not has_end_node and node_ids:
        errors.append("Pipeline contains no 'end' node")

    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "node_count": len(nodes)
    }


@mcp.tool()
async def archive_pipeline(template_id: str, ctx: Context = None) -> Any:
    """
    Deactivate/archive a workflow pipeline template.
    template_id: UUID of the template (MUST use the 'id' field from list_pipelines, NOT 'pipeline_id').
    """
    async def _call(client: ColbaClient):
        return await client.archive_pipeline(template_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def resolve_mcp_approval(
    action: str,
    approval_id: Optional[str] = None,
    token: Optional[str] = None,
    session_key: Optional[str] = None,
    ctx: Context = None
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
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def list_mcp_approvals(ctx: Context = None) -> Any:
    """
    List all pending MCP approval requests for this organization that are waiting
    for a human operator to confirm or reject via resolve_mcp_approval.

    Use this tool to:
    - Check if a previous create_pipeline / create_workgroup / sync_directory call
      is still waiting for approval before retrying.
    - Retrieve the approval_id needed to call resolve_mcp_approval.

    Returns: A list of pending approvals with their IDs, tool names, and expiry times.
    """
    async def _call(client: ColbaClient):
        return await client.list_mcp_approvals()
    return await handle_mcp_call(_call, ctx=ctx)


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
    description: Optional[str] = None,
    is_draft: Optional[bool] = None,
    is_active: Optional[bool] = None,
    ctx: Context = None
) -> Any:
    """
    Create a new workflow pipeline template in Colba.
    name: Human readable template name (e.g. 'Vendor Invoice Approval').
    pipeline_config: Complete JSON workflow configuration complying strictly with docs://skills/workflow_json_creation specification. Must contain start_node_id and valid nodes list. Call get_pipeline_generation_rules tool first to inspect the required format.
    description: Optional human-readable description.
    is_draft: Optional boolean (True to explicitly save as draft with activation_required=True).
    is_active: Optional boolean (False to save as draft).
    Returns: Created pipeline template details including template_id.
    """
    async def _call(client: ColbaClient):
        return await client.create_pipeline(
            name=name,
            pipeline_config=pipeline_config,
            description=description,
            is_draft=is_draft,
            is_active=is_active
        )
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def list_members(query: Optional[str] = None, ctx: Context = None) -> Any:
    """
    List all active members (users/employees) in the organization.
    query: Optional search string to filter members by full name.
    Returns: A list of members with their IDs, names, emails, roles, and status.
    """
    async def _call(client: ColbaClient):
        return await client.list_members(query)
    return await handle_mcp_call(_call, ctx=ctx)


def _normalize_job_title(value: str, field_name: str = "name") -> str:
    """Validate and normalize an organization position name before an API call."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    if len(normalized) > _MAX_JOB_TITLE_LENGTH:
        raise ValueError(
            f"{field_name} must be {_MAX_JOB_TITLE_LENGTH} characters or fewer"
        )
    return normalized


@mcp.tool()
async def list_job_titles(ctx: Context = None) -> Any:
    """
    List organization positions used for business routing.
    These are job titles (for example, 'Legal Counsel' or 'Project Manager'),
    not fixed access roles such as admin/member/superadmin.
    """
    async def _call(client: ColbaClient):
        return await client.list_job_titles()
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def create_job_title(name: str, ctx: Context = None) -> Any:
    """
    Create an organization position/job title.
    Mutations are subject to the directory capability and MCP HITL approval.
    """
    try:
        name = _normalize_job_title(name)
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    async def _call(client: ColbaClient):
        return await client.create_job_title(name)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def update_job_title(
    current_name: str,
    new_name: str,
    ctx: Context = None,
) -> Any:
    """
    Rename an organization position/job title.
    Assigned members are updated by the backend as part of the rename.
    Mutations are subject to the directory capability and MCP HITL approval.
    """
    try:
        current_name = _normalize_job_title(current_name, "current_name")
        new_name = _normalize_job_title(new_name, "new_name")
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    async def _call(client: ColbaClient):
        return await client.update_job_title(current_name, new_name)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def delete_job_title(name: str, ctx: Context = None) -> Any:
    """
    Delete an organization position/job title.
    Assigned members are unassigned from this job title by the backend.
    Mutations are subject to the directory capability and MCP HITL approval.
    """
    try:
        name = _normalize_job_title(name)
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    async def _call(client: ColbaClient):
        return await client.delete_job_title(name)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def list_workgroups(ctx: Context = None) -> Any:
    """
    List the organizational workgroups hierarchy (departments and locations).
    Returns: A tree structure of workgroups with their member details and counts.
    """
    async def _call(client: ColbaClient):
        return await client.list_workgroups()
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def list_vendors(ctx: Context = None) -> Any:
    """
    List all registered vendors/counterparties in the organization.
    Returns: A list of vendors with their IDs, names, and profiles.
    """
    async def _call(client: ColbaClient):
        return await client.list_vendors()
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def update_pipeline(
    template_id: str,
    pipeline_config: Optional[dict] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    ctx: Context = None
) -> Any:
    """
    Update an existing workflow pipeline template.
    template_id: UUID of the template to update.
    pipeline_config: Optional updated JSON workflow configuration.
    name: Optional updated name.
    description: Optional updated description.
    """
    async def _call(client: ColbaClient):
        payload = {}
        if pipeline_config is not None:
            payload["pipeline_config"] = pipeline_config
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        return await client.update_pipeline(template_id, payload)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def get_pipeline_embed(template_id: str, ctx: Context = None) -> Any:
    """
    Read the public form widget status for a pipeline.
    Returns whether it is enabled, its public widget URL, submit URL, version,
    and a ready-to-paste script_tag. This is read-only.
    """
    async def _call(client: ColbaClient):
        return await client.get_pipeline_embed(template_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def enable_pipeline_embed(template_id: str, ctx: Context = None) -> Any:
    """
    Enable or create the public JavaScript form widget for an active pipeline.
    The pipeline must have a form_start node. MCP requests may require HITL
    approval. Returns the ready-to-paste script_tag after approval.
    """
    async def _call(client: ColbaClient):
        return await client.enable_pipeline_embed(template_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def refresh_pipeline_embed(template_id: str, ctx: Context = None) -> Any:
    """
    Regenerate the static public form widget from the current form_start node.
    Use this after changing form fields or presentation settings. MCP requests
    may require HITL approval.
    """
    async def _call(client: ColbaClient):
        return await client.refresh_pipeline_embed(template_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def disable_pipeline_embed(template_id: str, ctx: Context = None) -> Any:
    """
    Disable a pipeline's public form widget and remove its static JavaScript
    file. MCP requests may require HITL approval.
    """
    async def _call(client: ColbaClient):
        return await client.disable_pipeline_embed(template_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def update_custom_field(
    field_id: str,
    name: Optional[str] = None,
    label: Optional[str] = None,
    type: Optional[str] = None,
    description: Optional[str] = None,
    validation: Optional[dict] = None,
    options: Optional[dict] = None,
    is_active: Optional[bool] = None,
    ctx: Context = None
) -> Any:
    """
    Update an existing custom field / global field registration.
    field_id: UUID of the custom field to update.
    name: Optional system name.
    label: Optional display label.
    type: Optional field type.
    description: Optional description.
    validation: Optional validation schema.
    options: Optional dropdown options or dynamic source settings.
    is_active: Optional active status.
    """
    async def _call(client: ColbaClient):
        payload = {}
        if name is not None:
            payload["name"] = name
        if label is not None:
            payload["label"] = label
        if type is not None:
            payload["type"] = type
        if description is not None:
            payload["description"] = description
        if validation is not None:
            payload["validation"] = validation
        if options is not None:
            payload["options"] = options
        if is_active is not None:
            payload["is_active"] = is_active
        return await client.update_custom_field(field_id, payload)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def get_update_log() -> str:
    """
    Retrieve the Colba MCP Server Update Log and Changelog.
    Contains lists of new tools, changes, and alerts about required client restarts.
    """
    base_dir = os.path.dirname(__file__)
    path = os.path.join(base_dir, "docs", "update_log.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return "Update log file not found."


@mcp.resource("docs://mcp/update_log")
def get_mcp_update_log_resource() -> str:
    """
    Returns the Colba MCP Server Update Log and Changelog.
    """
    base_dir = os.path.dirname(__file__)
    path = os.path.join(base_dir, "docs", "update_log.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return "Update log file not found."


@mcp.tool()
async def list_blueprints(category: Optional[str] = None, query: Optional[str] = None, ctx: Context = None) -> Any:
    """
    List all available workflow pipeline blueprints that can be instantiated.
    category: Optional category filter (e.g. 'finance', 'hr', 'it').
    query: Optional search query.
    Returns: A list of blueprints with their details and base configurations.
    """
    async def _call(client: ColbaClient):
        return await client.list_blueprints(category, query)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def get_blueprint(blueprint_id: str, ctx: Context = None) -> Any:
    """
    Retrieve the full configuration of a specific pipeline blueprint.
    blueprint_id: UUID of the blueprint.
    Returns: Complete blueprint details including its pipeline_config baseline.
    """
    async def _call(client: ColbaClient):
        return await client.get_blueprint(blueprint_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def instantiate_blueprint(blueprint_id: str, ctx: Context = None) -> Any:
    """
    Create a new pipeline template in the current organization based on a blueprint.
    blueprint_id: UUID of the blueprint to instantiate.
    Returns: Status of the instantiation and the created template_id (requires approval).
    """
    async def _call(client: ColbaClient):
        return await client.instantiate_blueprint(blueprint_id)
    return await handle_mcp_call(_call, ctx=ctx)



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


def get_ksef_pipeline_agent_guide_doc() -> str:
    """
    Returns the official KSeF Pipeline Agent Guide containing binding rules, auto-settings resolution,
    and pipeline control flow for Polish e-invoicing nodes.
    """
    base_dir = os.path.dirname(__file__)
    parent_dir = os.path.dirname(base_dir)
    cwd = os.getcwd()

    candidate_paths = [
        os.path.join(parent_dir, "docs", "ksef-pipeline-agent-guide.md"),
        os.path.join(cwd, "docs", "ksef-pipeline-agent-guide.md"),
        os.path.join(base_dir, "docs", "ksef-pipeline-agent-guide.md"),
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

    raise RuntimeError("KSeF Pipeline Agent Guide file could not be located on disk.")


@mcp.resource("docs://skills/ksef_pipeline_agent_guide")
def get_ksef_pipeline_agent_guide_resource() -> str:
    """
    Returns the official KSeF Pipeline Agent Guide.
    """
    return get_ksef_pipeline_agent_guide_doc()


@mcp.tool()
async def get_ksef_pipeline_guide() -> Any:
    """
    Get the official Agent Guide for integrating and binding the Polish KSeF e-Invoicing node (action_type: 'integration', provider: 'colba', action: 'submit_ksef_invoice') in Colba workflow pipelines.
    Returns: Complete Markdown documentation for KSeF pipeline integration.
    """
    return get_ksef_pipeline_agent_guide_doc()


# ---------------------------------------------------------------------------
# SuperProcess (Nadprocesy / Batches) Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_compatible_super_process_templates(ctx: Context = None) -> Any:
    """
    Get active workflow templates in the organization eligible for batch launching in a SuperProcess (nadproces).
    Returns: List of templates with compatibility status and reason if ineligible.
    """
    async def _call(client: ColbaClient):
        return await client.get_compatible_batch_templates()
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def create_super_process(
    title: str,
    template_ids: list[str],
    idempotency_key: Optional[str] = None,
    ctx: Context = None,
) -> Any:
    """
    Create and batch-launch a new SuperProcess (nadproces) consisting of multiple workflow process templates.
    title: The human-readable title/name for the batch (e.g. 'Production Batch #1042' or 'Onboarding Suite').
    template_ids: List of workflow template UUIDs to instantiate and start.
    idempotency_key: Optional idempotency key for safe retries.
    Returns: The created super process record with initiated items, process IDs, and status.
    """
    async def _call(client: ColbaClient):
        return await client.create_super_process(
            title=title,
            template_ids=template_ids,
            idempotency_key=idempotency_key,
        )
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def list_super_processes(
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    status: Optional[str] = None,
    ctx: Context = None,
) -> Any:
    """
    List SuperProcesses (nadprocesy) with real-time aggregated progress and status counts.
    limit: Maximum items to return (1-200, default 50).
    offset: Pagination offset.
    search: Optional substring search query on title.
    status: Optional status filter ('in_progress', 'completed', 'attention_required', 'partial_failed', 'cancelled').
    Returns: Paginated list of super process summaries with real-time breakdown of item states.
    """
    limit = max(1, min(limit, _MAX_LIMIT))
    offset = max(0, offset)
    async def _call(client: ColbaClient):
        return await client.list_super_processes(
            limit=limit,
            offset=offset,
            search=search,
            status=status,
        )
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def get_super_process(
    super_process_id: str,
    ctx: Context = None,
) -> Any:
    """
    Get detailed status, progress metrics, and process items for a specific SuperProcess (nadproces).
    super_process_id: UUID of the super process to retrieve.
    Returns: Complete snapshot with overall percentage, individual process IDs, current workflow stage labels, and execution states.
    """
    validate_uuid(super_process_id, "super_process_id")
    async def _call(client: ColbaClient):
        return await client.get_super_process(super_process_id=super_process_id)
    return await handle_mcp_call(_call, ctx=ctx)


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
