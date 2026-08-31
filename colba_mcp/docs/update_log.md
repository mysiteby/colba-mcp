# Colba MCP Server Update Log & Changelog

This document tracks updates, new tools, and changes made to the Colba Model Context Protocol (MCP) server.

> [!IMPORTANT]
> **MCP Client Schema Reloading**:
> Since MCP clients (like Claude Desktop, Cursor, or peer agents) cache the tool definitions schema on connection, **you must restart your client (or reload the MCP server)** whenever new tools are added for them to appear in your available tools list.

## [2026-08-31] - SuperProcess (Nadprocesy / Batches) Management Tools

### Purpose & Problem Solved

Added comprehensive orchestration and telemetry tools for **SuperProcesses (Надпроцессы / Nadprocesy)**. SuperProcesses allow orchestrating complex manufacturing orders, batch jobs, and multi-track workflows where multiple distinct processes are batch-launched under a single umbrella entity and monitored in real time until all sub-processes finish.

### New MCP Tools

1. **`get_compatible_super_process_templates`**:
   - Lists active workflow templates in the organization eligible for zero-payload batch launch.
   - Evaluates whether starting nodes (`collect_input`, `form_start`) can start without upfront data or if empty payloads `{}` pass schema validation.

2. **`create_super_process`**:
   - Creates and batch-launches a new SuperProcess containing multiple child processes.
   - Parameters:
     - `title`: Human-readable name for the batch/super-process (e.g. `"Production Batch #1042"` or `"Employee Onboarding Suite"`).
     - `template_ids`: Array of template UUID strings to instantiate.
     - `idempotency_key` (optional): Unique key for safe idempotent replay.
   - Handles partial failure isolation so one failing template does not crash the entire batch.

3. **`list_super_processes`**:
   - Paginated listing with substring search and status filtering.
   - Returns real-time aggregate counts (`total_items`, `running`, `waiting`, `completed`, `failed_to_start`, `failed`, `rejected`, `cancelled`) and computed status (`in_progress`, `completed`, `attention_required`, `partial_failed`) with zero N+1 database queries.

4. **`get_super_process`**:
   - Detailed telemetry snapshot for a specific SuperProcess.
   - Returns overall completion percentage, breakdown metrics, and all child process items with their current workflow stage labels (resolved dynamically from immutable execution pipelines) and direct links/IDs.

> [!IMPORTANT]
> **MCP Client Restart Required**:
> Restart your MCP client (Cursor, Claude Desktop, Antigravity) to reload the MCP tool schema.

---

## [2026-08-26] - Outcome-based Terminal Status Resolution from End Nodes

### Purpose & Problem Solved

Previously, the workflow engine determined terminal process status and `terminal_outcome` from the incoming edge's `transition_key` (such as `"false"` or `"true"` from an upstream `condition` node) rather than the `end` node itself. Consequently, when a process transitioned to a terminal node via a condition's `"false"` branch, the frontend misidentified the outcome as an execution failure (`failed` / Error), even when the process terminated normally.

### New Behavior & Agent Guidelines

1. **End Node Terminal Outcome & Status**:
   - Terminal process status (`completed`, `rejected`, `failed`) and outcome label are now explicitly configured on the `end` node.
   - `config.outcome_status`: `"completed"` (default, success), `"rejected"` (process rejected), or `"failed"` (execution failure).
   - `config.outcome_name`: Custom outcome display label (defaults to node `name` or `outcome_status`).
   - If `outcome_status` is omitted, the engine infers status from the node name (e.g. names containing "reject/отклон" resolve to `rejected`, "fail/error/ошибк" resolve to `failed`, others to `completed`).
   - Incoming transition keys (such as `"false"`) no longer overwrite the process outcome.

2. **Condition Field Identifier Validation**:
   - In `condition` nodes, agents must always bind `config.field` to the exact payload key (`name`, e.g. `field_8816`, `amount`), not the human-readable UI label or placeholder.

3. **Frontend & Visual Editor**:
   - In the pipeline editor, `end` nodes now provide a status selector (`outcome_status`) and optional outcome label.
   - Process listings and detail pages display the actual `end` node name and status in the Stepper Path.

## [2026-08-23] - Workflow email action for MCP agents

### Purpose

Agents can add a workflow action that builds an email from process data and
places it into Colba's durable email delivery queue. Use it for notifications,
requests for review, operational alerts, and other process-driven messages.
The action finishes after durable queueing; it does not wait for SMTP delivery.
The result contains `delivery_id` and `delivery_status` for correlation with
the process.

### Agent configuration

Create an `action` node with this integration identity:

```json
{
  "id": "notify_by_email",
  "name": "Notify by email",
  "type": "action",
  "config": {
    "action_type": "integration",
    "provider": "colba",
    "action": "send_email",
    "recipient_mode": "static",
    "recipient_email": "operations@example.com",
    "subject": "New request {{initial_payload.request_number}}",
    "message": "Please review the request.",
    "signature": "Operations team",
    "email_fields": [
      "initial_payload.request_number",
      "initial_payload.requester_name",
      "initial_payload.amount"
    ],
    "field_labels": {
      "initial_payload.request_number": "Request number",
      "initial_payload.requester_name": "Requester",
      "initial_payload.amount": "Amount"
    }
  },
  "transitions": {"default": "next_node"}
}
```

`recipient_mode` is either `static` or `field`. For a process recipient, use
`recipient_mode: "field"` and set `recipient_field`, for example
`initial_payload.requester_email`. The resolved value must be one strict email
address, not a display name, comma-separated list, or header value. For public
form data, configure `allowed_recipient_domains` such as
`["company.example"]` to prevent the workflow from becoming an open relay.

`email_fields` contains only explicit process paths. The older
`transmitted_fields` name is accepted for compatibility, but new agent-created
pipelines should use `email_fields`. Never include passwords, access tokens,
organization secrets, or other sensitive fields unless the business requirement
explicitly calls for it.

### Limits and behavior

- subject: required, maximum 255 characters, no control characters;
- message: maximum 20,000 characters;
- signature: maximum 2,000 characters;
- selected fields: maximum 50; each rendered value is bounded;
- serialized email payload: maximum 65,536 bytes by default;
- delivery admission is rate-limited per tenant and recipient and fails closed
  if Redis protection is unavailable;
- repeated attempts of the same node execution are idempotent; deliberate
  loopback/re-entry is treated as a new email execution;
- HTML output escapes process values and a plain-text alternative is sent;
- `delivery_status: "pending"` means queued, not confirmed delivered. Configure
  `on_error: "transition"` and an error transition when the process needs a
  visible failure branch for queue-admission errors.

No MCP tool schema changed. Agents should reload the update log or call
`get_pipeline_generation_rules` before generating a new pipeline, but an MCP
client restart is not required.

## [2026-08-23] - Public pipeline form widgets for MCP agents

### New agent functionality

Pipelines can expose their `form_start` node as a self-contained JavaScript
widget for insertion on an external website. The widget contains the public
form fields and presentation text, and sends submissions to Colba's public
embed ingress. It contains no bearer token or organization secret.

New MCP tools:

- `get_pipeline_embed(template_id)` reads publication state, widget version,
  widget URL, submit URL, and the ready-to-paste `script_tag`.
- `enable_pipeline_embed(template_id)` creates or enables the widget for an
  active pipeline with a `form_start` node.
- `refresh_pipeline_embed(template_id)` regenerates the static widget from
  the current pipeline configuration.
- `disable_pipeline_embed(template_id)` disables the form and removes its
  static JavaScript file.

Agent rules:

1. Call `get_pipeline_generation_rules` before creating a public-form pipeline.
2. Use exactly one `form_start` node and make it the `start_node_id`.
3. Keep public fields in `form_start.config.fields`; configure `title`,
   `description`, `submit_label`, and `success_message` in that node.
4. After `create_pipeline`, call `get_pipeline_embed` and return its
   `script_tag` to the operator. Active pipelines publish automatically.
5. After a form configuration change, the backend regenerates the widget.
   Use `refresh_pipeline_embed` for explicit publication or verification.
6. Never put credentials, bearer tokens, organization secrets, or internal
   hook URLs in the public form configuration.
7. MCP publication mutations use the normal HITL approval flow. Treat them as
   complete only after the pending approval is resolved.
8. Generate UUIDs for persisted node IDs and set `start_node_id` to the exact
   UUID of the `form_start` node. Keep aliases in `semantic_id`; never use
   `"form_start"` as `start_node_id` when the node itself has a UUID. This
   prevents the editor error `Start Form node must be the pipeline start node`.
9. Keep `form_start.config.fields`, `required_fields`, and root
   `header_schema` synchronized. Do not add widget-generated anti-spam
   metadata (`form_meta`, honeypot, timing, idempotency key) to the business
   form schema.

The browser submission path performs server-side schema validation, honeypot
and completion-time checks, body-size and rate limits, and idempotency
protection. A valid submission is durably queued and returns `202 Accepted`;
the dedicated `workflow` worker starts the process with concurrent lease/retry
handling. Queue admission is bounded and fails closed if Redis rate limiting is
unavailable; terminal payload copies are scrubbed and retained metadata expires.
The public form is separate from the bearer-token API trigger.

### Schema change

Four MCP tools were added. Restart the MCP client or reload the MCP connection
after deployment to expose the new tool schemas.

## [2026-08-17] - Telegram `parse_mode` for MCP workflow actions

MCP agents may set an optional `parse_mode` on a Telegram `send_message` action.
The accepted values are exactly `HTML`, `Markdown`, and `MarkdownV2`; when the
field is omitted, Telegram receives the message as plain text. The selected
mode is carried through the encrypted outbox to the final `sendMessage` call.

Agent behavior:

- Set `parse_mode` only when the message content uses the corresponding
  Telegram syntax; do not mix Markdown and HTML markup.
- A value may be supplied directly in the action config or resolved from the
  workflow context through `inputs.parse_mode`.
- Invalid values fail the action before queueing delivery and follow the
  node's configured retry/error policy.
- After `start_process`, describe the action as queued only after the process
  reaches that node; a completed action means durable enqueue, not confirmed
  Telegram delivery.

No MCP tool schema changed; a client schema restart is not required.

## [2026-08-13] - Mandatory organization-secret authentication for generated pipelines

- Updated the workflow JSON generation rules returned by
  `get_pipeline_generation_rules`.
- Unauthenticated `outbound_webhook` deliveries may omit credentials. When an
  external destination requires authentication, agents MUST use `auth_secret_name`
  with an organization-managed secret.
- Agents MUST NOT generate `auth_token`, literal `Authorization` credentials,
  or `{{secrets.*}}` for new authenticated outbound requests.
- The legacy `{{secrets.*}}` environment-variable mechanism remains documented
  only for backward compatibility with existing pipelines.
- No MCP tool schema changed; an MCP client schema reload is not required.

## [2026-08-11] - Resilient Streamable HTTP interaction

- Remote `/api/v1/mcp` now uses stateless JSON transport. MCP requests are no
  longer tied to an in-memory worker-local session registry, so deploys,
  restarts, and worker routing do not turn a stale `Mcp-Session-Id` into a
  misleading `404`.
- MCP credentials use a dedicated configurable limit via
  `MCP_RATE_LIMIT_PER_MINUTE` (default `300` per member/minute), covering both
  protocol requests and REST calls made by MCP tools. The previous `10 POST /
  minute` bucket is no longer applied to either current `mcp` keys or legacy
  MCP `integration` keys. Counter increment and expiry are atomic in Redis.
- Rate-limit responses include `Retry-After` and the MCP client maps `429` to a
  structured `rate_limited` result.
- No MCP schema change was made; a client tool-schema reload is not required.

## [2026-08-11] - Telegram channel publication workflows via MCP

### Capability

MCP agents can create and run approval-gated text publication workflows that post to a Telegram channel after human approval.

The supported graph is:

```text
collect_input(text)
  -> approval_request
  -> action(provider=telegram, action=send_message, target.kind=channel)
  -> end
```

### Agent contract

- Call `get_pipeline_generation_rules` before generating pipeline JSON.
- Declare a required non-empty `text` field in both `header_schema` and the first `collect_input.config.fields`.
- Give `approval_request` an explicit `assignment_target`. Reusable pipelines should use `{"type": "role", "id": "publisher"}`; `publisher` is an organization `job_title`, not the fixed access role `admin`, `member`, or `superadmin`.
- Keep approval action IDs aligned with transition keys, normally `approved` and `rejected`.
- Use `action_type: "integration"`, `provider: "telegram"`, and `action: "send_message"`.
- Use an explicit numeric channel target: `{"kind": "channel", "channel_id": "-100..."}`. There is no default channel and `@channel_username` is not accepted by the workflow action.
- The bot must be a channel administrator with `can_post_messages`. Colba validates the destination and bot permission before queueing the post.
- A channel ID is not authorization: an organization admin must first connect it from Telegram settings using a one-time private-chat link. The confirming user must have channel management permission, and runtime actions require the active verified channel row for the current tenant and exact `chat_id`.
- Delivery authorization is checked again by the outbox immediately before contacting Telegram, so queued posts cannot bypass a later disconnect, tenant reassignment, integration disable, or bot permission downgrade.

### Recommended MCP execution sequence

1. Find the reusable template with `list_blueprints(query="Telegram Channel Publication")`, or generate the graph after reading the workflow rules.
2. Use `get_blueprint(blueprint_id)` to inspect the baseline.
3. Ensure the `publisher` job title has an active member. Use `list_job_titles`, `create_job_title`, `list_members`, and `update_member(job_title=...)` as needed.
4. Call `instantiate_blueprint(blueprint_id)` or `create_pipeline(...)`. A template mutation may create a separate MCP HITL approval; resolve it before continuing.
5. Call `list_pipelines` and use the returned `id` as `template_id`.
6. Call `validate_process_input(template_id, {"text": "..."})`.
7. Call `start_process(template_id, {"text": "..."})`.
8. Find the workflow approval with `list_pending_requests`, then call `get_request_details` to read valid action IDs.
9. The human approver selects `approved` in Telegram or through `submit_decision(request_id, status="approved", comment="...")`.
10. Confirm the result with `get_process_details(process_id)` or `list_processes`. Telegram delivery uses the encrypted retryable outbox; channel permission or delivery failures are recorded and follow the configured action failure path.

### Reusable production blueprint

- Stable blueprint ID: `9b8b8c3d-36e3-4cd3-8a79-6d5d9b1a6f2e`.
- Installer: `scripts/install_telegram_channel_publication_blueprint.py`.
- The installer is separate from `seed_blueprints.py`, idempotent, and has no channel default.
- Production deployment must pass `--channel-id -100...` or set `TELEGRAM_CHANNEL_ID`; `--clear-channel` removes the channel from an existing blueprint.

This update adds no MCP tools, so an MCP client schema restart is not required. Agents can fetch this guidance with `get_update_log` or `docs://mcp/update_log`.

## [2026-08-06] - MCP 2026-07-28 and Streamable HTTP

- Upgraded the official Python SDK from v1 to stable `mcp==2.0.0`.
- Replaced `FastMCP` with `MCPServer`.
- Replaced legacy `/api/v1/mcp/sse` and `/api/v1/mcp/messages` with one Streamable HTTP endpoint: `/api/v1/mcp`.
- Removed transport-session token storage; remote authentication is validated on every request.
- Added the MCP session manager to the FastAPI application lifespan.
- Updated generated agent configurations and transport tests for the `2026-07-28` request envelope.

Existing agents must replace the old `/api/v1/mcp/sse` URL with `/api/v1/mcp`, replace `sse-only` with `http-only`, and reload the MCP connection. Existing Colba tokens remain valid.

## [2026-08-06] - Organization job-title CRUD tools

### New MCP tools

- `list_job_titles` lists organization positions used by workflow `assignment_target.type: "role"`.
- `create_job_title` creates a position after directory capability and MCP HITL checks.
- `update_job_title` renames a position and updates assigned member profiles.
- `delete_job_title` removes a position and clears it from assigned member profiles.

Job titles remain separate from fixed access roles (`superadmin`, `admin`, `member`).
Restart the MCP client or reload the MCP connection to expose the new tool schemas.

## [2026-08-05] - Fixed access roles and organization job titles

### Access model

- `role` is a fixed access role: `superadmin`, `admin`, or `member`.
- `superadmin` is bootstrap-only for the organization creator. MCP and member APIs can assign only `admin` or `member`.
- `job_title` is the organization-specific position, such as `CFO`, `Accountant`, or `Department Manager`.
- Custom access roles cannot be created, renamed, deleted, or added to the RBAC matrix.
- Existing invalid access-role values and non-owner `superadmin` assignments are normalized to `member` by migration `e4f5a6b7c8d9`; legacy `profile.roles` is removed.

### MCP behavior

- `update_member` accepts only assignable roles `admin` and `member`; attempts to pass `superadmin` fail before the API call.
- `update_member` now accepts `job_title` for changing a member's organization position.
- `job_title` is available as the seeded global select field backed by dynamic source `job_roles`.
- Organization job titles now have dedicated create/update/delete routes under `/api/v1/directory/job-titles`; MCP-originated mutations use approval actions `create_job_title`, `update_job_title`, and `delete_job_title`.
- Workflow assignment targets using `type: "role"` resolve against `job_title`; they must not use `admin`, `member`, or `superadmin` as business positions.
- Attempts to use the deprecated custom-role endpoints or MCP approval actions are rejected.

The existing `update_member` tool schema changed, so restart the MCP client or reload the MCP connection to expose the new `job_title` parameter and corrected role description.

---

## [2026-08-03] - API запуск процесса и общая валидация входных данных

### 🆕 Новый MCP-инструмент `validate_process_input`
- Проверяет входные данные процесса против схемы первого stage до запуска.
- Поддерживает вложенный JSON и использует тот же сервис валидации, что API и frontend.
- Отклоняет запуск при отсутствии обязательных данных; биллинг при этом не обходится.

### 🔌 MCP bypass для старта процесса
- MCP-запуск принимает инициатора `bot` и проходит через общий `ProcessStartService`.
- Текущий файловый bypass передаёт имя файла без сохранения содержимого.

### 📢 Требуется перезагрузка MCP-клиента
Так как добавлен новый инструмент, перезагрузите MCP-клиент или MCP-соединение, чтобы обновилась схема доступных tools.

## [2026-08-02] - Indefinite Callback Tokens, Multi-Level Payload Extraction & Reissue Endpoint

### 🆕 Indefinite Token Lifetime (Optional Timeout)
- **No Expiration by Default**: `timeout_minutes` in `wait_for_callback` is now **optional**. When omitted, `expires_at` is `null` and the callback token **never expires automatically**, allowing workflows to wait indefinitely for external system callbacks, offline manual processes, or long-running operations.
- **Audit & Log Visibility**: Process execution logs record `timeout_mode: "indefinite"` and audit entries note `Callback node initialized. Token enqueued (indefinite, no timeout)`.

### 🔄 Multi-Level Data Payload Extraction & Schema Validation
- **Nested JSON & Wrapper Extraction**: Inbound callbacks wrapping business data inside `"result"`, `"data"`, `"payload"`, or `"content"` automatically extract the target object (`extract_callback_data`).
- **Deep Schema Validation**: `payload_schema` and `schema_validation` validate against the extracted nested payload, preserving multi-level structures (nested dicts, arrays, sub-objects) in `execution_result` and `received_data`.

### 🔐 Token Reissue API Endpoint
- **Endpoint**: `POST /api/v1/workflow/processes/{process_id}/nodes/{node_id}/reissue-callback-token`
- **Functionality**: Re-issues a fresh callback token for a paused process node if the previous token was expired or cancelled. Invalidates old tokens and enqueues a new delivery payload.
- **Protection**: Restricted to workspace administrators (`admin` / `owner`) with tenant isolation.

---

## [2026-08-01] - `wait_for_callback` Node & External Callback Support

### 📢 CRITICAL: Restart Required
The `wait_for_callback` node type is now fully supported in the pipeline engine and registered in `validate_pipeline_schema`. If you are using `create_pipeline` or `validate_pipeline_schema` with this node type, **restart your MCP client** to pick up the updated tool schema.

### 🆕 New Node Type: `wait_for_callback`

A new first-class pipeline node that **pauses a process and waits for an external HTTP callback** before continuing execution.

#### Purpose
Enables pipelines to integrate with external systems (payment gateways, e-signature services, third-party portals) that confirm results asynchronously via webhook or email. The engine issues a one-time signed token, delivers it to the target, and resumes the process when the external system calls back.

#### `config` fields

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `name` | string | Yes | Human-readable label shown in email subjects and logs. |
| `delivery_mode` | string | Yes | `"webhook"` or `"email"`. |
| `timeout_minutes` | integer | No (default 60) | Token lifetime in minutes. Max 10080 (7 days). |
| `target_url` | string | webhook only | Public HTTPS/HTTP URL to POST the callback invitation to. RFC-1918 private ranges are blocked (SSRF protection). |
| `recipient_email` | string | email only | Destination address for the callback instructions email. |
| `secret_name` | string | No | Tenant secret key for HMAC signing the webhook delivery. |
| `secret_header` | string | No | Header name for the secret (default: `X-Callback-Secret`). |
| `payload_schema` | object | No | JSON Schema (draft-07) to validate the callback body before resuming. |

#### Outgoing transitions

| Key | Fired when |
| :--- | :--- |
| `completed` | External system sent a valid callback with `status: "completed"`. |
| `timeout` | Token expired before callback arrived. |
| `failed` | External system explicitly sent `status: "failed"`. |

#### How the external system calls back

```
POST /api/v1/workflow/pipeline-callbacks/{raw_token}
Content-Type: application/json

{
  "status": "completed",
  "result": { "transaction_id": "...", "amount": 299.00 }
}
```

The endpoint is **publicly accessible** — the signed token is the sole credential.

#### New API endpoint available to Colba members

```
GET /api/v1/workflow/processes/{process_id}/nodes/{node_id}/callback-token
Authorization: Bearer <member_token>
```

Returns the raw token that can be used to construct the callback URL manually (useful for testing or manual escalation).

#### Security properties
- TCP connections are pinned to the pre-resolved IP (DNS rebinding protection).
- TLS SNI and certificate verification use the original hostname (no cert mismatch on HTTPS webhooks).
- Token is Fernet-encrypted at rest in the delivery outbox.
- Fail-closed on DNS resolution errors.

#### Minimal JSON example

```json
{
  "id": "wait_payment",
  "type": "wait_for_callback",
  "label": "Wait for payment confirmation",
  "config": {
    "name": "Payment Gateway Callback",
    "delivery_mode": "webhook",
    "target_url": "https://hooks.payment-provider.com/notify",
    "timeout_minutes": 1440
  },
  "transitions": {
    "completed": { "target": "mark_paid" },
    "timeout":   { "target": "notify_team" }
  }
}
```

### 🔧 Bug Fixes

#### Migration `0045_add_wait_for_callback_tables`
- **Revision ID shortened** from 33 chars to `a3f9c2e10045` — previous ID exceeded the `alembic_version` varchar(32) limit causing `StringDataRightTruncationError` on first apply.
- **Multi-statement `op.execute()` split** — `CREATE FUNCTION` and `GRANT EXECUTE` now issued as separate calls. asyncpg rejects multi-command prepared statements, previously causing `PostgresSyntaxError` on startup.

### 📝 Draft Pipeline Creation & Filtering Enhancements

#### 1. Explicit Draft Flag Support (`is_draft` / `is_active`)
* **Endpoint / Tool Updates**: `POST /api/v1/templates/` and `@mcp.tool() create_pipeline` now accept explicit `is_draft: bool` or `is_active: bool` parameters.
* **Behavior**:
  - Setting `is_draft=True` or `is_active=False` explicitly creates the pipeline in **Draft** state (`is_active=False, activation_required=True`), regardless of whether the caller is an MCP agent or a regular session user.
  - If omitted, MCP agent calls default to `is_draft=True`, while user session calls default to `is_active=True`.

#### 2. MCP Pipeline Filtering (`list_pipelines`)
* **Parameter Added**: `@mcp.tool() list_pipelines` now accepts an optional `status` parameter (`'active'`, `'draft'`, `'archived'`, or `'all'`).
* **Usage**: Pass `status='draft'` or `status='all'` to retrieve pipelines created in draft mode waiting for activation.

---


## [2026-07-29] - Entity Update Tools, Hierarchy Restructuring & Pipeline Validation

### 📢 CRITICAL: Restart Required
New entity update tools (`update_workgroup`, `update_vendor`, `update_member`) and pipeline schema validation (`validate_pipeline_schema`) have been added. If you do not see these in your available tools, **please restart your MCP client (Claude Desktop / Cursor) or reload the connection.**

### 🆕 New Tools & Enhancements Added

#### 1. `update_workgroup` (Expanded)
* **New Parameters**: `type` ('DEPARTMENT', 'LOCATION', 'SQUAD'), `parent_id` (UUID of parent workgroup).
* **Benefit**: Allows moving departments/teams under new parent workgroups to restructure organizational hierarchy in-place without deleting and recreating nodes. Includes cycle detection.

#### 2. `update_vendor` (New Tool)
* **Parameters**: `vendor_id`, `name`, `email`, `tax_id`, `account_number`, `bank_country_code`, `is_active`, `visible`.
* **Benefit**: Update metadata, tax info, and active status for existing counterparties.

#### 3. `update_member` (New Tool)
* **Parameters**: `member_id`, `full_name`, `role` ('admin', 'member'), `job_title`, `is_active`, `manager_id`, `substitute_id`.
* **Benefit**: Update member profile, fixed access role, organization position, active status, or manager relationships.

#### 4. `validate_pipeline_schema` (New Tool)
* **Parameters**: `pipeline_config` (dict).
* **Returns**: `{ "is_valid": bool, "errors": [...], "node_count": int }`.
* **Benefit**: Dry-run validation of workflow JSONs (graph connectivity, valid transitions, node type checks) prior to creation or update.

#### 5. Documentation & Node Spec Sync
* Added complete specification for `type: "load_test"` node (k6 performance testing) to `workflow_json_creation.md` and synced with `get_pipeline_generation_rules`.

---

## [2026-07-29] - Polish KSeF E-Invoicing Node & Agent Guide Support

### 📢 CRITICAL: Restart Required
We have added the new KSeF E-Invoicing Agent Guide tool and resource to the MCP server. If you do not see `get_ksef_pipeline_guide` in your tools list, **please restart your MCP client (Claude Desktop / Cursor) or reload the connection.**

### 🆕 New Tools & Resources Added

#### 1. `get_ksef_pipeline_guide` (Tool)
* **Purpose**: Retrieves the official Markdown Agent Guide for binding and configuring the Polish KSeF e-invoicing node (`action_type: "integration"`, `provider: "colba"`, `action: "submit_ksef_invoice"`).
* **Returns**: Complete Markdown documentation with auto-settings resolution rules, upstream ➔ node field binding contracts, and pipeline error routing (`DELIVERY_UNKNOWN`, `OFFLINE_DELIVERED`, `DUPLICATE`, `MANUAL_REVIEW`).

#### 2. `docs://skills/ksef_pipeline_agent_guide` (Resource)
* **Purpose**: Exposes the KSeF Pipeline Agent Guide as a native MCP resource for LLM agents.

---

## [2026-07-24] - 9 New Tools, Master Data, & Blueprints Support

### 📢 CRITICAL: Restart Required
We have added 9 new tools to the MCP server. If you do not see them in your current session, **please restart your client (Claude Desktop / Cursor) or restart the MCP connection.**

### 🆕 New Tools Added

#### 1. `list_custom_fields`
* **Purpose**: List all registered custom fields / global fields in the organization.
* **Returns**: UUID, name, label, type, options, and validation settings for all fields.
* **Usage**: Use this to discover available global fields (like `department`, `currency`, `cost_center`, `priority`, `bank_country_code`) and bind their exact `custom_field_id` when creating or modifying pipelines.

#### 2. `list_members`
* **Purpose**: List active users/employees in the organization.
* **Parameters**: `query` (optional string) to filter by full name.
* **Usage**: Resolves user names to member UUIDs to assign tasks or approvals.

#### 3. `list_workgroups`
* **Purpose**: Get the organizational hierarchy tree (departments and locations) along with their member lists.
* **Usage**: Discover active departments or locations for form selection or conditional routing.

#### 4. `list_vendors`
* **Purpose**: List counterparties/vendors registered in the organization.
* **Usage**: Map vendor names to UUIDs when starting processes or managing procurement.

#### 5. `update_pipeline`
* **Purpose**: Update an existing pipeline template (JSON config, name, description) by its template UUID.
* **Benefit**: Allows editing existing templates without needing to archive and recreate them from scratch, preserving version history.

#### 6. `update_custom_field`
* **Purpose**: Edit settings, display labels, or select options for an existing custom/global field.
* **Benefit**: Modifies fields without breaking `custom_field_id` references in active pipelines.

#### 7. `list_blueprints`
* **Purpose**: List available global pipeline blueprints/templates.
* **Parameters**: `category` (optional), `query` (optional).
* **Usage**: Discover ready-to-use workflows (like Hiring Process, Bill Approval) to use as a baseline.

#### 8. `get_blueprint`
* **Purpose**: Fetch the complete JSON baseline config of a specific blueprint.
* **Usage**: Retrieve the original blueprint JSON configuration before modifying and creating a pipeline template.

#### 9. `instantiate_blueprint`
* **Purpose**: Instantiate a blueprint directly into a new template in the active organization.
* **Returns**: The created pipeline template_id.

---

## [Older Updates] - Initial Release
* Baseline MCP server with 9 core tools: `list_pipelines`, `start_process`, `list_processes`, `list_pending_requests`, `get_process_details`, `get_request_details`, `submit_decision`, `get_pipeline_generation_rules`, `create_pipeline`.
* Added manual MCP approval bypass resolvers for debugging and staging.
