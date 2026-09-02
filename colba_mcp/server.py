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
_PROCESS_ACCESS_TYPES = {"all_members", "department", "job_title", "individual"}
_CLIENT_CACHE_TTL_SECONDS = 300.0
_CLIENT_CACHE_MAX_SIZE = 64
_client_cache: dict[tuple[str, str], tuple[float, ColbaClient]] = {}
_ORG_CONTEXT_CACHE_TTL_SECONDS = 15.0
_organization_context_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_organization_context_semaphore = asyncio.Semaphore(16)

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
async def start_process(
    template_id: str,
    payload: dict,
    idempotency_key: Optional[str] = None,
    ctx: Context = None,
) -> Any:
    """
    Start a new workflow process under a template.
    template_id: UUID of the workflow template (MUST use the 'id' field from list_pipelines, NOT 'pipeline_id').
    payload: Input data matching the template's header_schema.
    idempotency_key: Optional UUID identifying one logical launch attempt. Reuse it only when retrying that attempt.
    Returns: The started process ID and initial status.
    """
    async def _call(client: ColbaClient):
        return await client.start_process(template_id, payload, idempotency_key)
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
    options: Optional[dict | list] = None,
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
    options: Optional choices list or dynamic-source object (for example, {"source": "job_roles"}).
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

    from src.workflow.application.input_validation_service import input_validation_service
    field_definitions = input_validation_service.extract_fields(pipeline_config)
    errors.extend(issue.message for issue in input_validation_service.validate_rule_definitions(field_definitions))

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


def _normalize_process_access_rule(
    rule_type: Optional[str],
    value: Optional[str],
    values: Optional[list[str]],
    field_name: str,
) -> dict[str, Any]:
    normalized_type = (rule_type or "").strip().lower()
    if normalized_type == "role":
        normalized_type = "job_title"
    if normalized_type not in _PROCESS_ACCESS_TYPES:
        raise ValueError(
            f"{field_name}_type must be one of: {', '.join(sorted(_PROCESS_ACCESS_TYPES))}"
        )
    raw_values = [*(values or [])]
    if value is not None:
        raw_values.insert(0, value)
    if normalized_type == "all_members":
        if raw_values:
            raise ValueError(f"{field_name}_value(s) must be omitted for all_members")
        return {"type": normalized_type}
    if not raw_values:
        raise ValueError(f"{field_name}_value or {field_name}_values is required for {normalized_type}")
    if len(raw_values) > 1000:
        raise ValueError(f"{field_name}_values cannot contain more than 1000 entries")

    normalized_values: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        normalized_value = str(raw_value or "").strip()
        if not normalized_value:
            raise ValueError(f"{field_name}_values cannot contain empty entries")
        if len(normalized_value) > 255:
            raise ValueError(f"{field_name}_value cannot exceed 255 characters")
        key = normalized_value.casefold()
        if key not in seen:
            seen.add(key)
            normalized_values.append(normalized_value)
    return (
        {"type": normalized_type, "id": normalized_values[0]}
        if len(normalized_values) == 1
        else {"type": normalized_type, "ids": normalized_values}
    )


@mcp.tool()
async def set_pipeline_access(
    template_id: str,
    view_type: Optional[str] = None,
    view_value: Optional[str] = None,
    view_values: Optional[list[str]] = None,
    launch_type: Optional[str] = None,
    launch_value: Optional[str] = None,
    launch_values: Optional[list[str]] = None,
    ctx: Context = None,
) -> Any:
    """
    Configure who can see and launch a workflow pipeline.
    At least one of view_type or launch_type is required. Types are
    'all_members', 'department', 'job_title', or 'individual'. For department
    use one or more workgroup UUIDs, keys, or names; for job_title use one or
    more organization position names; for individual use member UUIDs or emails.
    Singular *_value arguments remain supported; use *_values for multiple targets.
    The mutation uses the normal pipeline-management capability and MCP HITL flow.
    """
    try:
        validate_uuid(template_id, "template_id")
        if view_type is None and launch_type is None:
            raise ValueError("Provide view_type, launch_type, or both")
        access_updates: dict[str, dict[str, Any]] = {}
        if view_type is not None:
            access_updates["view"] = _normalize_process_access_rule(
                view_type, view_value, view_values, "view",
            )
        if launch_type is not None:
            access_updates["launch"] = _normalize_process_access_rule(
                launch_type, launch_value, launch_values, "launch",
            )
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    async def _call(client: ColbaClient):
        return await client.set_pipeline_access(template_id, access_updates)
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
    Inspect active workflow templates for SuperProcess batch launch compatibility.
    Returns every active template with is_batch_compatible; only choose entries where it is true.
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
    template_ids: Ordered list of 1-50 template UUIDs. Duplicate IDs intentionally launch multiple instances.
    idempotency_key: Optional key for safe retries. Reuse it only with the exact same title and ordered template_ids.
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
    limit: Maximum items to return (1-100, default 50).
    offset: Pagination offset.
    search: Optional substring search query on title.
    status: Optional status filter ('in_progress', 'completed', 'attention_required', 'partial_failed').
    Returns: Paginated list of super process summaries with real-time breakdown of item states.
    """
    limit = max(1, min(limit, 100))
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


@mcp.tool()
async def get_organization_context(ctx: Context = None) -> Any:
    """
    Get consolidated organization context in a single call for agent discovery.
    Returns:
    - workgroups: list of workgroups with id, name, type, parent_id, active member counts
    - job_titles: list of business roles/job titles
    - custom_fields: list of organization custom workflow fields
    - active_pipelines: list of active workflow pipelines
    - total_members: count of active organization members
    """
    async def _call(client: ColbaClient):
        from copy import deepcopy

        await client._ensure_org_id()
        cache_key = (client.api_url, str(client.org_id))
        cached = _organization_context_cache.get(cache_key)
        now = time.monotonic()
        if cached and now - cached[0] < _ORG_CONTEXT_CACHE_TTL_SECONDS:
            return deepcopy(cached[1])

        # These are independent reads.  Running them concurrently keeps agent
        # discovery latency close to the slowest backend call instead of the
        # sum of all five calls.
        async with _organization_context_semaphore:
            workgroups, job_titles, custom_fields, pipelines, members = await asyncio.gather(
                client.list_workgroups(),
                client.list_job_titles(),
                client.list_custom_fields(),
                client.list_pipelines(status="active"),
                client.list_members(),
            )
        if not all(isinstance(value, list) for value in (workgroups, job_titles, custom_fields, pipelines, members)):
            raise ValueError("Colba API returned an invalid organization context")

        # Calculate member counts per workgroup
        wg_counts = {}
        for m in members:
            for wg in m.get("workgroups", []):
                wg_id = wg.get("id") or wg.get("name")
                if wg_id:
                    wg_counts[str(wg_id)] = wg_counts.get(str(wg_id), 0) + 1

        def enrich_workgroups(items: list[dict[str, Any]]) -> None:
            for wg in items:
                if not isinstance(wg, dict):
                    continue
                wg_id = str(wg.get("id"))
                wg_name = str(wg.get("name"))
                wg["member_count"] = wg_counts.get(wg_id, wg_counts.get(wg_name, 0))
                children = wg.get("children")
                if isinstance(children, list):
                    enrich_workgroups(children)

        enrich_workgroups(workgroups)
        compact_pipelines = [
            {key: pipeline.get(key) for key in ("id", "name", "description", "is_active", "activation_required")}
            for pipeline in pipelines
            if isinstance(pipeline, dict)
        ]
        result = {
            "workgroups": workgroups,
            "job_titles": job_titles,
            "custom_fields": custom_fields,
            "active_pipelines": compact_pipelines,
            "total_members": len(members),
        }
        _organization_context_cache[cache_key] = (now, deepcopy(result))
        # Bound cache cardinality for long-running multi-tenant MCP servers.
        if len(_organization_context_cache) > _CLIENT_CACHE_MAX_SIZE:
            oldest_key = min(_organization_context_cache, key=lambda key: _organization_context_cache[key][0])
            _organization_context_cache.pop(oldest_key, None)
        return result
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def preview_directory_changes(data: list, ctx: Context = None) -> Any:
    """
    Dry-run preview of proposed directory upserts without modifying state.
    Shows additions and field-level updates after MCP role normalization.
    """
    async def _call(client: ColbaClient):
        if not isinstance(data, list):
            return {"is_valid": False, "errors": ["data must be an array"], "warnings": []}
        if len(data) > 1000:
            return {"is_valid": False, "errors": ["data may contain at most 1000 entries"], "warnings": []}
        current_members = await client.list_members()
        current_emails = {m.get("email", "").lower(): m for m in current_members if m.get("email")}

        to_add = []
        to_update = []
        warnings = []
        errors = []
        seen_emails = set()

        for entry in data:
            if not isinstance(entry, dict):
                errors.append("Each directory entry must be an object")
                continue
            email = str(entry.get("email", "")).lower().strip()
            if not email or "@" not in email:
                errors.append("Each directory entry must contain a valid email")
                continue
            if email in seen_emails:
                errors.append(f"Duplicate email in proposed directory data: {email}")
                continue
            seen_emails.add(email)
            full_name = str(entry.get("full_name") or entry.get("name") or "").strip()
            if not full_name:
                errors.append(f"Directory entry '{email}' is missing a name")
                continue
            normalized_entry = {**entry, "email": email, "full_name": full_name, "role": "member"}
            normalized_entry.pop("name", None)
            requested_role = str(entry.get("role") or "member").strip().lower()
            if requested_role != "member":
                warnings.append(
                    f"Member '{email}' requested role '{requested_role}', which MCP sync normalizes to 'member'."
                )

            if email in current_emails:
                current = current_emails[email]
                changes = {
                    key: value for key, value in normalized_entry.items()
                    if current.get(key) != value
                }
                if changes:
                    to_update.append({"email": email, "changes": changes})
            else:
                to_add.append(normalized_entry)

        return {
            "is_valid": not errors,
            "summary": f"{len(to_add)} members to add, {len(to_update)} members to update",
            "to_add": to_add,
            "to_update": to_update,
            "errors": errors,
            "warnings": warnings,
        }
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def preview_pipeline_changes(
    pipeline_config: dict,
    template_id: Optional[str] = None,
    ctx: Context = None,
) -> Any:
    """
    Dry-run semantic preview of proposed pipeline changes.
    Shows total nodes, stage breakdown, normalized assignment targets, and warnings.
    """
    async def _call(client: ColbaClient):
        from src.workflow.application.llm_pipeline_normalizer import normalize_llm_pipeline_config
        from src.shared.infrastructure.hash_helper import generate_canonical_hash
        schema_validation = await validate_pipeline_schema(pipeline_config or {})
        normalized = normalize_llm_pipeline_config(pipeline_config or {})
        nodes = normalized.get("nodes", [])
        if not isinstance(nodes, list):
            nodes = []

        assignment_targets = [
            {
                "node_id": n.get("id"),
                "name": n.get("name"),
                "target": (n.get("config") or {}).get("assignment_target"),
            }
            for n in nodes
            if n.get("type") in ("approval_request", "task")
        ]

        diff = None
        if template_id:
            validate_uuid(template_id, "template_id")
            current_template = await client.get_pipeline(template_id)
            current_config = normalize_llm_pipeline_config(current_template.get("pipeline_config") or {})
            current_nodes = {
                str(node.get("id")): node
                for node in current_config.get("nodes", [])
                if isinstance(node, dict) and node.get("id")
            }
            proposed_nodes = {
                str(node.get("id")): node
                for node in nodes
                if isinstance(node, dict) and node.get("id")
            }
            diff = {
                "changed": generate_canonical_hash(current_config) != generate_canonical_hash(normalized),
                "added_node_ids": sorted(proposed_nodes.keys() - current_nodes.keys()),
                "removed_node_ids": sorted(current_nodes.keys() - proposed_nodes.keys()),
                "changed_node_ids": sorted(
                    node_id for node_id in current_nodes.keys() & proposed_nodes.keys()
                    if generate_canonical_hash(current_nodes[node_id]) != generate_canonical_hash(proposed_nodes[node_id])
                ),
            }

        return {
            "template_id": template_id,
            "normalized_config": normalized,
            "total_nodes": len(nodes),
            "stages_summary": [f"{n.get('name', n.get('id'))} ({n.get('type')})" for n in nodes],
            "assignments": assignment_targets,
            "schema_validation": schema_validation,
            "diff": diff,
            "warnings": [
                n.get("config", {}).get("assignment_fallback_note")
                for n in nodes
                if n.get("config", {}).get("assignment_fallback_note")
            ],
        }
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def verify_expected_route(
    pipeline_config: dict,
    sample_input: dict,
    expected_path: Optional[list] = None,
    ctx: Context = None,
) -> Any:
    """
    Simulate traversal of a workflow pipeline graph with a sample input payload to verify routing.
    Evaluates transitions and branch targets without creating a live process.
    """
    from copy import deepcopy
    from src.workflow.application.llm_pipeline_normalizer import normalize_llm_pipeline_config
    from src.workflow.domain.graph import PipelineGraph
    from src.workflow.domain.handlers.conditional import evaluate_condition_value

    if not isinstance(sample_input, dict):
        return {"traversed_path": [], "terminated_cleanly": False,
                "matches_expected": False, "errors": ["sample_input must be an object"]}

    normalized = normalize_llm_pipeline_config(pipeline_config or {})
    raw_nodes = normalized.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return {"traversed_path": [], "terminated_cleanly": False,
                "matches_expected": False, "errors": ["pipeline_config.nodes must be a non-empty list"]}

    # Run the canonical graph checks first, but keep this tool tolerant of
    # semantic (non-UUID) node ids used by draft pipelines.
    errors: list[str] = []
    try:
        graph_errors = PipelineGraph.model_validate(deepcopy(normalized)).validate_integrity()
        errors.extend(str(item.get("message")) for item in graph_errors if item.get("level") == "ERROR")
    except Exception as exc:
        errors.append(f"Invalid pipeline graph: {exc}")

    nodes = {str(node.get("id")): node for node in raw_nodes if isinstance(node, dict) and node.get("id")}
    start_id = str(normalized.get("start_node_id") or (raw_nodes[0].get("id") if raw_nodes else ""))
    if start_id not in nodes:
        errors.append(f"Start node '{start_id}' does not exist")

    initial_payload = sample_input.get("initial_payload") if isinstance(sample_input.get("initial_payload"), dict) else sample_input
    step_results = sample_input.get("step_results") if isinstance(sample_input.get("step_results"), dict) else {}
    transition_overrides = sample_input.get("_transitions") if isinstance(sample_input.get("_transitions"), dict) else {}
    override_positions: dict[str, int] = {}

    def nested_value(data: Any, path: str) -> Any:
        current = data
        for part in str(path or "").split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    def evaluate_condition(config: dict[str, Any]) -> bool:
        field = config.get("field")
        if not field:
            raise ValueError("condition requires config.field")
        actual = nested_value(initial_payload, field)
        if actual is None:
            for node_result in step_results.values():
                if isinstance(node_result, dict) and isinstance(node_result.get("submitted_data"), dict):
                    actual = nested_value(node_result["submitted_data"], field)
                    if actual is not None:
                        break
        if actual is None:
            actual = ""
        operator_name = str(config.get("operator") or "==")
        expected = config.get("value")
        return evaluate_condition_value(actual, operator_name, expected)

    def override_transition(node_id: str, node: dict[str, Any]) -> str | None:
        raw_choice = transition_overrides.get(node_id, transition_overrides.get(str(node.get("name") or "")))
        if isinstance(raw_choice, list):
            position = override_positions.get(node_id, 0)
            if position >= len(raw_choice):
                return None
            override_positions[node_id] = position + 1
            return str(raw_choice[position])
        return str(raw_choice) if raw_choice is not None else None

    visited: list[str] = []
    current_id = start_id
    try:
        requested_max_steps = int(normalized.get("max_route_steps") or 50)
    except (TypeError, ValueError):
        requested_max_steps = 50
        errors.append("max_route_steps must be an integer")
    max_steps = min(max(requested_max_steps, 1), 200)
    terminated = False

    while current_id:
        if current_id not in nodes:
            errors.append(f"Transition points to missing node '{current_id}'")
            break
        if len(visited) >= max_steps:
            errors.append(f"Route exceeded max_steps={max_steps}")
            break
        node = nodes[current_id]
        visited.append(str(node.get("name") or current_id))
        node_type = str(node.get("type") or "").lower()
        if node_type in {"end", "completed", "rejected"}:
            terminated = True
            break

        transitions = node.get("transitions")
        if not isinstance(transitions, dict):
            errors.append(f"Node '{current_id}' has invalid transitions")
            break
        transition_key = "default"
        if node_type in {"condition", "conditional"}:
            try:
                transition_key = "true" if evaluate_condition(node.get("config") or {}) else "false"
            except ValueError as exc:
                errors.append(f"Node '{current_id}': {exc}")
                break
        else:
            explicit_key = override_transition(current_id, node)
            if explicit_key:
                transition_key = explicit_key
            elif "default" not in transitions and len(transitions) == 1:
                transition_key = str(next(iter(transitions)))
            elif "default" not in transitions and len(transitions) > 1:
                errors.append(
                    f"Node '{current_id}' has multiple transitions; provide sample_input._transitions.{current_id}"
                )
                break
        transition = transitions.get(transition_key)
        if transition is None:
            errors.append(f"Node '{current_id}' has no '{transition_key}' transition")
            break
        next_target = transition.get("target") if isinstance(transition, dict) else transition
        if not next_target:
            errors.append(f"Node '{current_id}' transition '{transition_key}' has no target")
            break
        current_id = str(next_target)

    matches = expected_path is None or visited == expected_path
    return {
        "traversed_path": visited,
        "terminated_cleanly": terminated and not errors,
        "matches_expected": matches and not errors,
        "errors": errors,
    }


@mcp.tool()
async def get_pending_approvals(ctx: Context = None) -> Any:
    """
    Retrieve approvals awaiting review or execution recovery, with direct review URLs.

    Results may be in ``pending``, ``executing``, or ``execution_failed`` state.
    """
    async def _call(client: ColbaClient):
        approvals = await client.list_mcp_approvals()
        for a in approvals:
            a_id = a.get("id")
            # Keep this path aligned with the frontend route and with the
            # action URL emitted by the API.  /settings/approvals is not a
            # registered page and produced dead links for operators.
            a["review_url"] = f"/mcp-approve?approval_id={a_id}"
            a["next_action"] = "Open review URL in browser or call resolve_mcp_approval"
        return approvals
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def start_agent_run(user_goal: str, ctx: Context = None) -> Any:
    """Start a tenant-scoped, append-only audit run before agent discovery."""
    async def _call(client: ColbaClient):
        return await client.create_agent_run(user_goal)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def get_agent_run(run_id: str, ctx: Context = None) -> Any:
    """Read the current state and snapshots of an owned agent run."""
    validate_uuid(run_id, "run_id")
    async def _call(client: ColbaClient):
        return await client.get_agent_run(run_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def get_agent_run_events(run_id: str, limit: int = 200, ctx: Context = None) -> Any:
    """Read the append-only, redacted event log for an owned agent run."""
    validate_uuid(run_id, "run_id")
    async def _call(client: ColbaClient):
        return await client.list_agent_run_events(run_id, limit)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def transition_agent_run(
    run_id: str,
    state: str,
    error_message: Optional[str] = None,
    ctx: Context = None,
) -> Any:
    """Move an agent run through its documented state machine."""
    validate_uuid(run_id, "run_id")
    async def _call(client: ColbaClient):
        return await client.update_agent_run(
            run_id, "state", {"state": state, "error_message": error_message}
        )
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def record_agent_context(
    run_id: str,
    discovered_context: dict,
    assumptions: Optional[list] = None,
    ctx: Context = None,
) -> Any:
    """Persist bounded discovery context and assumptions for an agent run."""
    validate_uuid(run_id, "run_id")
    async def _call(client: ColbaClient):
        return await client.update_agent_run(
            run_id, "context", {
                "discovered_context": discovered_context,
                "assumptions": assumptions,
            }
        )
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def record_agent_draft(
    run_id: str,
    draft_pipeline: dict,
    validation_results: Optional[dict] = None,
    ctx: Context = None,
) -> Any:
    """Persist a bounded pipeline draft and its validation result."""
    validate_uuid(run_id, "run_id")
    async def _call(client: ColbaClient):
        return await client.update_agent_run(
            run_id, "draft", {
                "draft_pipeline": draft_pipeline,
                "validation_results": validation_results,
            }
        )
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def record_agent_mutation(
    run_id: str,
    tool_name: str,
    payload: dict,
    result: dict,
    ctx: Context = None,
) -> Any:
    """Append a redacted and size-bounded mutation event to an agent run."""
    validate_uuid(run_id, "run_id")
    async def _call(client: ColbaClient):
        return await client.update_agent_run(
            run_id, "mutation", {
                "tool_name": tool_name,
                "payload": payload,
                "result": result,
            }
        )
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def record_agent_approval(
    run_id: str,
    approval_id: str,
    ctx: Context = None,
) -> Any:
    """Append an HITL approval reference to an agent run."""
    validate_uuid(run_id, "run_id")
    validate_uuid(approval_id, "approval_id")
    async def _call(client: ColbaClient):
        return await client.update_agent_run(
            run_id, "approval", {"approval_id": approval_id}
        )
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def list_workflow_schedules(
    template_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    ctx: Context = None,
) -> Any:
    """
    List automated recurring workflow execution schedules in the organization.
    template_id: Optional UUID of the workflow template to filter by.
    is_active: Optional boolean to filter by active/paused state.
    limit: Max items to return (default: 50, max: 200).
    offset: Pagination offset (default: 0).
    """
    if limit > _MAX_LIMIT:
        return {"error": "invalid_input", "message": f"limit cannot exceed {_MAX_LIMIT}."}
    if offset < 0:
        return {"error": "invalid_input", "message": "offset cannot be negative."}

    async def _call(client: ColbaClient):
        return await client.list_schedules(
            template_id=template_id,
            is_active=is_active,
            limit=limit,
            offset=offset,
        )
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def get_workflow_schedule(
    schedule_id: str,
    ctx: Context = None,
) -> Any:
    """
    Get full details of a specific workflow schedule by UUID.
    schedule_id: UUID of the schedule.
    """
    try:
        validate_uuid(schedule_id, "schedule_id")
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    async def _call(client: ColbaClient):
        return await client.get_schedule(schedule_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def create_workflow_schedule(
    template_id: str,
    name: str,
    cron_expression: str,
    timezone: str = "UTC",
    payload: Optional[dict] = None,
    concurrency_policy: str = "allow",
    description: Optional[str] = None,
    is_active: bool = True,
    ctx: Context = None,
) -> Any:
    """
    Create a new automated recurring schedule for a workflow template.
    template_id: UUID of the workflow template to execute.
    name: Human readable name for the schedule (e.g. 'Daily Sync at 9am').
    cron_expression: Standard 5-field cron expression (e.g. '0 9 * * 1-5' or '@daily').
    timezone: IANA timezone name (default: 'UTC', e.g. 'Europe/Warsaw', 'America/New_York').
    payload: Optional initial input dictionary for the process launch.
    concurrency_policy: 'allow' (run on schedule regardless) or 'skip_if_running' (skip if previous run active).
    description: Optional description of this scheduled execution.
    is_active: Whether to enable the schedule immediately (default: True).
    """
    try:
        validate_uuid(template_id, "template_id")
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    name = name.strip()
    if not name:
        return {"error": "invalid_input", "message": "name cannot be empty."}

    cron_expression = cron_expression.strip()
    if not cron_expression:
        return {"error": "invalid_input", "message": "cron_expression cannot be empty."}

    if concurrency_policy not in {"allow", "skip_if_running"}:
        return {"error": "invalid_input", "message": "concurrency_policy must be 'allow' or 'skip_if_running'."}

    async def _call(client: ColbaClient):
        return await client.create_schedule(
            template_id=template_id,
            name=name,
            cron_expression=cron_expression,
            timezone=timezone,
            payload=payload,
            concurrency_policy=concurrency_policy,
            description=description,
            is_active=is_active,
        )
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def update_workflow_schedule(
    schedule_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    cron_expression: Optional[str] = None,
    timezone: Optional[str] = None,
    payload: Optional[dict] = None,
    concurrency_policy: Optional[str] = None,
    is_active: Optional[bool] = None,
    ctx: Context = None,
) -> Any:
    """
    Update an existing workflow schedule.
    schedule_id: UUID of the schedule to update.
    """
    try:
        validate_uuid(schedule_id, "schedule_id")
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    if concurrency_policy is not None and concurrency_policy not in {"allow", "skip_if_running"}:
        return {"error": "invalid_input", "message": "concurrency_policy must be 'allow' or 'skip_if_running'."}

    async def _call(client: ColbaClient):
        return await client.update_schedule(
            schedule_id=schedule_id,
            name=name,
            description=description,
            cron_expression=cron_expression,
            timezone=timezone,
            payload=payload,
            concurrency_policy=concurrency_policy,
            is_active=is_active,
        )
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def toggle_workflow_schedule(
    schedule_id: str,
    is_active: bool,
    ctx: Context = None,
) -> Any:
    """
    Pause or resume an automated workflow schedule.
    schedule_id: UUID of the schedule.
    is_active: True to enable/resume, False to pause.
    """
    try:
        validate_uuid(schedule_id, "schedule_id")
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    async def _call(client: ColbaClient):
        return await client.toggle_schedule(schedule_id, is_active)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def delete_workflow_schedule(
    schedule_id: str,
    ctx: Context = None,
) -> Any:
    """
    Delete an automated workflow schedule.
    schedule_id: UUID of the schedule to delete.
    """
    try:
        validate_uuid(schedule_id, "schedule_id")
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    async def _call(client: ColbaClient):
        return await client.delete_schedule(schedule_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def trigger_workflow_schedule(
    schedule_id: str,
    ctx: Context = None,
) -> Any:
    """
    Immediately trigger an execution of a workflow schedule on demand ('Run Now').
    schedule_id: UUID of the schedule to trigger.
    """
    try:
        validate_uuid(schedule_id, "schedule_id")
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    async def _call(client: ColbaClient):
        return await client.trigger_schedule_run(schedule_id)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def get_workflow_schedule_runs(
    schedule_id: str,
    limit: int = 50,
    offset: int = 0,
    ctx: Context = None,
) -> Any:
    """
    Get execution history and status logs for a specific workflow schedule.
    schedule_id: UUID of the schedule.
    limit: Max records to return (default: 50, max: 200).
    offset: Pagination offset (default: 0).
    """
    try:
        validate_uuid(schedule_id, "schedule_id")
    except ValueError as e:
        return {"error": "invalid_input", "message": str(e)}

    async def _call(client: ColbaClient):
        return await client.get_schedule_runs(schedule_id, limit=limit, offset=offset)
    return await handle_mcp_call(_call, ctx=ctx)


@mcp.tool()
async def validate_schedule_cron(
    cron_expression: str,
    timezone: str = "UTC",
    count: int = 5,
    ctx: Context = None,
) -> Any:
    """
    Validate a cron expression syntax and compute the next N projected run times.
    cron_expression: Standard 5-field cron expression or macro.
    timezone: IANA timezone name (default: 'UTC').
    count: Number of upcoming runs to calculate (default: 5).
    """
    cron_expression = cron_expression.strip()
    if not cron_expression:
        return {"error": "invalid_input", "message": "cron_expression cannot be empty."}

    async def _call(client: ColbaClient):
        return await client.validate_schedule_cron(cron_expression, timezone=timezone, count=count)
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
        f"For multi-step work, start an agent run and record discovery, draft, approvals, mutations, and final verification. Before applying a mutation, discover organization context, validate and preview the graph, verify representative conditional routes, and wait for HITL approval.\n\n"
        f"Output ONLY valid JSON matching the specification."
    )
