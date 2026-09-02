# Colba Model Context Protocol (MCP) Server

This server implements MCP `2026-07-28` using the stable Python SDK v2 and the Streamable HTTP transport. It enables AI agents (e.g., Claude Desktop, Cursor, or custom autonomous agents) to interact with approval requests, processes, and business workflow creation directly on behalf of users.

---

## 📦 Dependency Installation

The server is written in Python 3.12+ and uses the `mcp` library. We recommend using `uv` for fast, isolated execution.

### Option 1: Using `uv` (Recommended)
Ensure `uv` is installed. No pre-installation step is required — `uv` will execute the server and automatically manage dependencies.

### Option 2: Classical Installation via `pip`
From the directory containing `pyproject.toml`, run:
```bash
pip install -e .
```

---

## ⚙️ Environment Variables Configuration

The MCP server is configured via the following environment variables:

| Variable | Description | Default Value |
| :--- | :--- | :--- |
| `COLBA_API_URL` | Base URL of the running Colba REST API | `http://localhost:9000` |
| `COLBA_TOKEN` | Your personal API member token (`tk_live_...`) | *Required* |

> [!TIP]
> You can generate a member API token and a ready-to-use configuration file in the Colba Admin Panel under **Settings → MCP Agent Integration**.

---

## 🖥️ Connecting to Clients

### 1. Remote Streamable HTTP (recommended)

Agents with native remote MCP support connect to one endpoint:

```json
{
  "mcpServers": {
    "colba": {
      "url": "https://YOUR_COLBA_HOST/api/v1/mcp",
      "headers": {
        "Authorization": "Bearer tk_mcp_your_token_here"
      }
    }
  }
}
```

Do not append `/sse` or `/messages`. The client negotiates MCP `2026-07-28`; the SDK supplies protocol headers and request metadata.

Claude Desktop installations that still need a stdio-to-HTTP bridge can use `mcp-remote`:

```json
{
  "mcpServers": {
    "colba": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://YOUR_COLBA_HOST/api/v1/mcp",
        "--transport",
        "http-only",
        "--header",
        "Authorization: Bearer tk_mcp_your_token_here"
      ]
    }
  }
}
```

### 2. Local stdio

Edit your `claude_desktop_config.json`:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add the following entry to `mcpServers`:

```json
{
  "mcpServers": {
    "colba": {
      "command": "uv",
      "args": [
        "run",
        "--quiet",
        "--directory",
        "PATH_TO_PROJECT_ROOT",
        "python",
        "-m",
        "colba_mcp"
      ],
      "env": {
        "COLBA_API_URL": "http://localhost:9000",
        "COLBA_TOKEN": "tk_live_your_token_here"
      }
    }
  }
}
```

> [!IMPORTANT]
> Replace `PATH_TO_PROJECT_ROOT` with the absolute path to your cloned `colba` repository (e.g., `/Users/username/Projects/colba`).

---

### 3. Cursor local stdio

1. Go to **Settings > Features > MCP**.
2. Click **+ Add New MCP Server**.
3. Fill in the parameters:
   - **Name**: `colba`
   - **Type**: `command`
   - **Command**:
     ```bash
     uv --directory PATH_TO_PROJECT_ROOT run --quiet python -m colba_mcp
     ```
4. Add environment variables:
   - `COLBA_API_URL` = `http://localhost:9000`
   - `COLBA_TOKEN` = `tk_live_your_token_here`

---

## 🛠️ Available Tools

The MCP server exposes the following tools to AI agents:

### 1. `list_pipelines`
*Retrieve available workflow templates and required input header schemas.*
* **Example prompt**: *"What workflow pipelines can I start?"*

### 2. `start_process`
*Start a new workflow process instance for a template.*
* **Parameters**:
  - `template_id` (string, UUID): Template identifier.
  - `payload` (object): Initial form data payload.
* **Example prompt**: *"Start a 'Travel Expense' process with amount 1500 USD and purpose 'Conference'"*

### 3. `validate_process_input`
*Validate the first-stage payload without starting a process or reserving billing funds.*
* **Parameters**:
  - `template_id` (string, UUID): Template identifier.
  - `payload` (object): Initial form data payload, including table rows when required.
* Uses the same strict backend validator as the UI and the process-start API.

### 4. `list_processes`
*List workflow process instances with status and pagination filters.*
* **Parameters**:
  - `status` (string, optional): Filter status (`active`, `completed`, `rejected`, `failed`).
  - `pipeline_id` (string, optional): Filter by pipeline template UUID.
  - `limit` (integer, optional, default: 50, max: 200).
  - `offset` (integer, optional, default: 0).
* **Example prompt**: *"Show my last 10 active processes"*

### 4. `list_pending_requests`
*Fetch approval requests waiting for action by the current user/agent.*
* **Parameters**:
  - `limit` (integer, optional, default: 50).
  - `offset` (integer, optional, default: 0).
* **Example prompt**: *"Are there any pending requests requiring my approval?"*

### 5. `get_process_details`
*Get detailed state and context variables of a process instance.*
* **Parameters**:
  - `process_id` (string, UUID).
  - `verbose` (boolean, optional, default: `false`): If `true`, returns full pipeline structure (`pipeline_config`).
* **Example prompt**: *"What is the status of process abc-123?"*

### 6. `get_request_details`
*Retrieve complete approval request payload and valid available actions.*
* **Parameters**:
  - `request_id` (string, UUID).
* **Example prompt**: *"Show details for request xyz-456"*

### 7. `submit_decision`
*Submit an approval decision for a pending request.*
* **Parameters**:
  - `request_id` (string, UUID).
  - `status` (string): Selected action identifier (must match an ID from `available_actions`).
  - `comment` (string, optional).
* **Example prompt**: *"Approve request xyz-456 with comment 'Budget approved'"*

### 8. `get_pipeline_generation_rules`
*Retrieve the official specification and validation rules for generating pipeline JSONs.*
* **Example prompt**: *"Get the rules for creating a pipeline JSON"*

### 9. `create_pipeline`
*Create a new workflow pipeline template in Colba.*
* **Parameters**:
  - `name` (string): Template title (e.g., *"Procurement Invoice Approval"*).
  - `pipeline_config` (object): Valid pipeline JSON configuration matching `docs://skills/workflow_json_creation`.
  - `description` (string, optional): Human-readable summary.
* **Example prompt**: *"Create a new travel request pipeline template with manager approval and budget verification nodes"*

### 10. `set_pipeline_access`
*Configure which organization members can see and launch a workflow pipeline.*
* **Parameters**:
  - `template_id` (string, UUID): Template identifier.
  - `view_type` with `view_value` or `view_values` (optional): Visibility rule. Types are `all_members`, `department`, `job_title`, and `individual`.
  - `launch_type` with `launch_value` or `launch_values` (optional): Launch rule with the same types.
* Use the singular argument for one target or the plural argument for multiple department UUIDs/keys/names, job-title names, or member UUIDs/emails.
* Requires pipeline-management access and uses MCP HITL approval.
* **Example prompt**: *"Make the Procurement department the only group that can see and launch this process"*

### Public pipeline form widgets

For a pipeline whose `start_node_id` points to a `form_start` node, agents can
manage the external JavaScript form with four tools:

- `get_pipeline_embed(template_id)` returns the current status and ready-to-paste `script_tag`.
- `enable_pipeline_embed(template_id)` creates or enables the widget.
- `refresh_pipeline_embed(template_id)` regenerates it after configuration changes.
- `disable_pipeline_embed(template_id)` disables the widget and removes its static file.

The generated script contains no credentials. Agents must never expose the
Bearer-token API trigger or place organization secrets in `form_start.config`.
MCP publication mutations may return a pending HITL approval and are complete
only after that approval is resolved.

### 11. `list_custom_fields`
*Retrieve all registered global custom fields in the organization.*
* **Example prompt**: *"Show all custom fields configured in the system"*

### 12. `list_members`
*List all active members (users/employees) in the organization.*
* **Parameters**:
  - `query` (string, optional): Search string to filter members by name.
* **Example prompt**: *"Show all members or search for 'Alice'"*

### 13. `list_job_titles`
*List organization positions used for business workflow assignment.*
*These are `job_title` values, not fixed access roles such as `admin`, `member`, or `superadmin`.*
* **Example prompt**: *"Show all organization job titles"*

### 14. `create_job_title`
*Create an organization position/job title.*
* **Parameters**:
  - `name` (string, 1–100 characters): Position name.
* Mutations require directory-management capability and go through MCP HITL approval.

### 15. `update_job_title`
*Rename an organization position and update assigned members.*
* **Parameters**:
  - `current_name` (string): Existing position name.
  - `new_name` (string, 1–100 characters): Replacement position name.
* Mutations require directory-management capability and go through MCP HITL approval.

### 16. `delete_job_title`
*Delete an organization position and clear it from assigned members.*
* **Parameters**:
  - `name` (string): Position name.
* Mutations require directory-management capability and go through MCP HITL approval.

### 17. `list_workgroups`
*List the organizational hierarchy (departments and locations).*
* **Example prompt**: *"Show the departments tree"*

### Directory terminology

- Access roles are fixed: `superadmin`, `admin`, and `member`. They control permissions and are not organization positions. Only `admin` and `member` are assignable; `superadmin` belongs exclusively to the organization creator.
- Organization positions are stored as `job_title` (for example `CFO` or `Accountant`).
- `job_title` is also exposed as the global select field backed by the `job_roles` dynamic source.
- Workflow assignment targets with `type: "role"` match `job_title`; do not use `admin`, `member`, or `superadmin` for business routing.
- Custom access-role creation and the deprecated role CRUD endpoints are rejected. Use `update_member(job_title=...)` to update a position.

### 18. `list_vendors`
*List all registered vendors/counterparties in the organization.*
* **Example prompt**: *"Show all vendors"*

### 19. `update_pipeline`
*Update an existing workflow pipeline template.*
* **Parameters**:
  - `template_id` (string, UUID): Template identifier.
  - `pipeline_config` (object, optional): Updated JSON configuration.
  - `name` (string, optional): New template name.
  - `description` (string, optional): New description.
* **Example prompt**: *"Rename pipeline template 'abc' to 'xyz'"*

### 20. `update_custom_field`
*Update an existing custom field or global field registration.*
* **Parameters**:
  - `field_id` (string, UUID): Custom field identifier.
  - `label` (string, optional): New display label.
  - `options` (object/array, optional): New choices or source.
  - `is_active` (boolean, optional): Active status.
* **Example prompt**: *"Mark custom field 'tax_rate' as inactive"*

### 21. `get_update_log`
*Retrieve the update log and changelog of the Colba MCP server.*
* **Example prompt**: *"Show recent MCP server updates and changelog"*

### 22. `list_blueprints`
*List all available workflow pipeline blueprints that can be instantiated.*
* **Parameters**:
  - `category` (string, optional): Filter by category.
  - `query` (string, optional): Search query to filter by name.
* **Example prompt**: *"Show all HR blueprints"*

### 23. `get_blueprint`
*Retrieve the full configuration of a specific pipeline blueprint.*
* **Parameters**:
  - `blueprint_id` (string, UUID): Blueprint identifier.
* **Example prompt**: *"Get details for blueprint 'xyz'"*

### 24. `instantiate_blueprint`
*Create a new pipeline template in the current organization based on a blueprint.*
* **Parameters**:
  - `blueprint_id` (string, UUID): Blueprint identifier.
* **Example prompt**: *"Create template from blueprint 'abc'"*

### 25. `get_compatible_super_process_templates`
*Inspect active templates for batch launching in a SuperProcess (nadproces). The response includes incompatible templates; agents must select only entries where `is_batch_compatible` is `true`.*
* **Example prompt**: *"Which process templates can be launched together in a super process batch?"*

### 26. `create_super_process`
*Create and batch-launch a new SuperProcess (nadproces) consisting of multiple workflow process templates.*
*Requires an MCP credential with pipeline write/agent scope. Read-only credentials cannot launch batches.*
* **Parameters**:
  - `title` (string): Human-readable name for the batch/super-process.
  - `template_ids` (array of 1-50 string UUIDs): Ordered template IDs to instantiate. Repeated IDs intentionally launch multiple instances.
  - `idempotency_key` (string, optional, max 255 characters): Key for safe retries. Reuse only with the exact same title and ordered template list.
* **Example prompt**: *"Launch a new super process 'Batch #1042' with template IDs ['...', '...']"*

### 27. `list_super_processes`
*List SuperProcesses (nadprocesy) with real-time aggregated progress and status counts.*
*Non-admin callers see only batches they created; organization admins see all organization batches.*
* **Parameters**:
  - `limit` (integer, optional, default: 50, max: 100).
  - `offset` (integer, optional, default: 0).
  - `search` (string, optional): Substring search query on title.
  - `status` (string, optional): Filter status (`in_progress`, `completed`, `attention_required`, `partial_failed`). Filtering is applied before pagination and `total` reflects the filtered result.
* **Example prompt**: *"List all in-progress super processes"*

### 28. `get_super_process`
*Get detailed status, progress metrics, and process items for a specific SuperProcess (nadproces).*
* **Parameters**:
  - `super_process_id` (string, UUID): Super process identifier.
* **Example prompt**: *"Get progress and stage details for super process 'xyz-123'"*

### Telegram publication workflow via MCP

For a process that accepts text, waits for human approval, and then posts to a Telegram channel:

1. Find the reusable blueprint with `list_blueprints(query="Telegram Channel Publication")`, or read `get_pipeline_generation_rules` and create the graph with `create_pipeline`.
2. Ensure the organization has a `publisher` job title assigned to an active member. Business assignment targets use `job_title`; they are not fixed access roles.
3. Instantiate or create the pipeline, then resolve any separate MCP HITL approval required for the template mutation.
4. Validate and start it:

```text
validate_process_input(template_id, {"text": "Draft announcement"})
start_process(template_id, {"text": "Draft announcement"})
```

5. Find the generated request with `list_pending_requests`, inspect valid action IDs using `get_request_details`, and let the human approver select `approved` in Telegram or via `submit_decision`.
6. Verify completion with `get_process_details` or `list_processes`.

The Telegram action must contain `target.kind = "channel"` and an explicit numeric `target.channel_id` beginning with `-100`. The shared bot must be a channel administrator with permission to post. Channel access is validated before delivery; messages are sent through the encrypted retryable outbox. An optional `parse_mode` accepts exactly `HTML`, `Markdown`, or `MarkdownV2`; if omitted, the message is sent as plain text. Invalid values fail before queueing. The production blueprint has no channel default and is installed separately with `scripts/install_telegram_channel_publication_blueprint.py`.

---

## 📚 Resources

### `docs://skills/workflow_json_creation`
The official specification and validation rules for creating pipeline JSON structures in Colba.
Includes node type hierarchies (prioritizing `action` with `action_type: "integration"`), `output_enum` validation, `escalations` policies, `condition` dotted-path syntax, form field types (`type: "array"` for line items), and validation checklists.

An external agent can fetch this resource via `read_resource` before generating a new pipeline JSON.

### `docs://mcp/update_log`
The official update log and changelog of the Colba MCP server, reflecting all newly added tools, features, and notifications about client restarts.

---

## 💬 Prompts

### `generate_pipeline_json`
System prompt template that automates instruction setup for an LLM agent.
* **Arguments**:
  - `user_requirements`: Textual description of desired business process requirements.
* **Output**: Loads the full specification `docs://skills/workflow_json_creation` and formats a strict generation prompt for the LLM.
