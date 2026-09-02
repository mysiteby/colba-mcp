# Skill: Workflow Pipeline JSON Creation

This skill is a practical guide for creating, editing, and validating `pipeline_config` JSON for the Colba workflow engine.

Use it when you need to:
- build a pipeline from scratch,
- edit an existing pipeline JSON,
- understand which node configs are supported,
- wire `global_fields`, formulas, and launch validation correctly,
- make sure the JSON matches what the engine, UI, and launch endpoints expect.

## What A Valid Pipeline Looks Like

At minimum, a pipeline config usually contains:
- `prefix`: short human-readable two-level prefix for generated process numbers (Scheme B: `[DEPT]-[PROCESS]`, e.g., `FIN-INV`, `HR-ONB`, `IT-ACC`),
- `start_node_id`: the entry node,
- `header_schema`: for API-triggered input pipelines, the complete JSON Schema for fields accepted in the initial payload,
- `is_client_enabled`: optional client portal flag,
- `nodes`: array of node objects.

Example:

```json
{
  "prefix": "FIN-INV",
  "start_node_id": "start_1",
  "header_schema": {
    "type": "object",
    "properties": {
      "article_title": { "type": "string" },
      "article_body": { "type": "string" }
    },
    "required": ["article_title", "article_body"],
    "additionalProperties": false
  },
  "is_client_enabled": true,
  "nodes": [
    {
      "id": "start_1",
      "name": "Initial Stage",
      "type": "collect_input",
      "config": {
        "fields": [
          { "name": "amount", "type": "number", "label": "Amount", "required": true }
        ]
      },
      "transitions": {
        "default": "approval_1"
      },
      "position": { "x": 100, "y": 100 }
    }
  ]
}
```

## Core JSON Contract

### Root fields

- `prefix`: optional, used for process numbering and readable IDs.
- `start_node_id`: required for a valid executable graph.
- `header_schema`: required for API-triggered pipelines; it must be a non-empty object schema and describe every article/payload field accepted at launch.
- `is_client_enabled`: optional boolean for client portal flows.
- `nodes`: required array of node definitions.

### Process visibility and launch access

For member-facing processes, configure access once at the root of
`pipeline_config` under `process_access`. Do not store it inside a step config.
The policy has two independent rules:

```json
"process_access": {
  "view": { "type": "department", "ids": ["finance", "legal"] },
  "launch": { "type": "job_title", "ids": ["Accountant", "Buyer"] }
}
```

Supported rule types are:

- `all_members`: all active members of the organization; no `id` is needed;
- `department`: a department workgroup UUID, key, or name;
- `job_title`: an organization business position matching `profile.job_title`;
- `individual`: a member UUID or email address.

Use `id` for one target or `ids` for multiple targets of the same type. A member
matches when any target matches. Duplicate targets are normalized; empty lists
and more than 1000 targets are rejected.

**Important agent rule:** in the persisted `pipeline_config.process_access`
object, always write the normalized `view`/`launch` shape with `type` and
`ids` (or a single `id`). Do not write `values`, `view_values`, or
`launch_values` inside the pipeline JSON. Those `*_values` names belong only to
the `set_pipeline_access` MCP tool arguments; mixing them into a generated
pipeline config makes create/update approval validation fail with
`INVALID_PROCESS_ACCESS`.

`view` controls whether a member sees the process in the available-process
catalog. `launch` controls whether the member may validate and start it.
Existing processes without `process_access` remain available to all members.
Administrators and superadmins have full visibility and launch rights.
An explicitly present but malformed policy is denied (fail-closed) and rejected
when the template is saved or activated. Prefer a department workgroup UUID over
a mutable name or key.
Do not use `assignment_target` for this policy: it assigns approval/task work
inside an already started process and is a separate concern.

### Automated Recurring Execution (Schedules / Cron)

Workflows can be scheduled to run automatically on a recurring cadence (e.g. daily synchronization, hourly checks, monthly reporting).

Agents can configure and inspect schedules for any workflow template using the following MCP tools:
- `create_workflow_schedule`: attaches a recurring cron trigger to a template with a specific timezone (`Europe/Warsaw`, `UTC`, `America/New_York`), initial `payload`, and `concurrency_policy` (`allow` or `skip_if_running`).
- `list_workflow_schedules`: lists all schedules or filters by `template_id`.
- `update_workflow_schedule`: modifies cadence, payload, timezone, or concurrency policy.
- `toggle_workflow_schedule`: pauses or resumes an automated schedule.
- `trigger_workflow_schedule`: executes an immediate test run ("Run Now").
- `get_workflow_schedule_runs`: inspects run execution history, status (`completed`, `failed`, `skipped`), and linked process instances.
- `validate_schedule_cron`: verifies cron expression syntax and computes upcoming projected run times.

Supported Cron Syntax:
- Standard 5-field: `minute hour day-of-month month day-of-week` (e.g. `0 9 * * 1-5` for weekdays at 9:00 AM).
- Step syntax: `*/15 * * * *` (every 15 minutes).
- Predefined macros: `@hourly`, `@daily`, `@weekly`, `@monthly`, `@yearly`.
- Timezone awareness: all schedules calculate exact execution times respecting IANA timezones and daylight saving time (DST) shifts.

### Node fields

Every node can use:
- `id`: unique node identifier. For newly generated persisted/API pipelines,
  prefer UUIDs; keep readable aliases in `semantic_id` instead.
- `name`: human-facing label.
- `type`: handler type.
- `config`: node-specific settings.
- `transitions`: map of transition key to target node.
- `position`: UI layout coordinates.

### Transition format

Transitions are stored as an object map:

```json
{
  "default": "next_node_id",
  "approved": { "target": "done_node_id", "loopback": false }
}
```

Supported transition shapes:
- legacy plain string target,
- object form with `target` and optional `loopback`.

Rules:
- use `default` for the normal path,
- use explicit keys for buttons, branches, and special outcomes,
- keep action keys aligned with node button/action IDs,
- use `loopback: true` when the next node should be re-entered as a fresh revision path.

## Supported Node Types

The codebase currently supports these practical node families:

- `collect_input`
- `form_start` (public launch form and pipeline entry point)
- `approval_request`
- `task`
- `condition`
- `conditional`
- `action` (including generic integration actions like `create_document`)
- `outbound_webhook`
- `outbound_integration` (legacy)
- `llm_request`
- `load_test` (automated k6 stress testing)
- `wait_for_callback` (pause execution until an external HTTP callback is received)
- `create_vendor` (legacy)
- `create_po` (legacy)
- `create_invoice` (legacy)
- `end`

> **Hierarchy of Node Types for New Pipelines**:
> For all newly generated pipelines, always prefer `action` with `action_type: "integration"` (provider `"colba"`, action `"create_document"`) over the legacy typed action nodes (`create_po`, `create_invoice`, `create_vendor`, `outbound_integration`). The legacy typed nodes are retained solely for backward compatibility with existing pipelines and MUST NOT be used when generating new JSON workflows.

The editor may display some of these under simpler visual buckets, but the JSON should keep the actual `type` used by the engine.

## Node-by-Node Guide

### `collect_input`

Use this node to pause the process and collect form data.

The rules for the initial node depend on its type:

- If the start node is `collect_input`, its `config.fields` is the authoritative launch
  form. For API-triggered launches, copy those fields into the root `header_schema` with
  matching names, types, and required status. This is the correct shape when the API
  payload is intended to enter the input form.
- If the start node is any other type, do not invent a `collect_input` schema. The node
  must be executable with the launch context it receives, and its required inputs must
  be declared according to that node type's configuration. For API-triggered launches,
  any accepted payload fields still must be declared in the root `header_schema`; fields
  hidden only inside a later `task` are not launch inputs.

A `task` start node is valid for workflows that intentionally begin with a human task,
but it does not define an input form and cannot be used as a substitute for
`collect_input` when the launch contract requires form fields.

The `start_node_id` value MUST equal the literal `id` of that node in the `nodes` array
(usually a UUID if the pipeline was persisted). Never put a semantic name, node label,
or generated alias there unless that exact value is also the node's `id`.

Common config fields:
- `fields`
- `required_fields`
- `recipient_type`
- `recipient_member_id`
- `form_id`
- `label`

### `form_start`

Use this node when the pipeline must be launched by a form embedded on an
external website. It must be the pipeline's `start_node_id`, and a pipeline
may contain only one `form_start` node.

The node owns the public form contract. Its `config` may contain `title`,
`description`, `submit_label`, `success_message`, `fields`, and optional
`required_fields`. Field definitions use the same names, types, options, and
required flags as the launch validator.

#### Public-form generation contract

When generating a public-form pipeline, agents MUST treat the form as a
complete launch contract, not only as a visual node:

- Use exactly one `form_start` node and set `start_node_id` to that node's
  exact persisted `id`.
- Generate UUIDs for all new node `id` values and use those same UUID strings in
  `start_node_id` and transition targets. Keep a human-readable `semantic_id`
  separately when useful. Do not use `"form_start"`, a node name, or another
  semantic alias as `start_node_id` when the node has a UUID `id`.
- Keep the field contract duplicated and synchronized in
  `form_start.config.fields`, `form_start.config.required_fields`, and the
  root `header_schema`. Names, types, required flags, and select values must
  match exactly.
- Use stable ASCII `snake_case` field names. Labels, descriptions, and
  presentation text may be localized, but must not be used as payload keys.
- Add server-side constraints where known: `format: "email"`,
  `min_length`, `max_length`, `pattern`, and canonical select
  `options.choices`. Browser validation is only a usability aid; it is not a
  security boundary.
- Use machine-stable select values and human-readable labels. Do not make a
  downstream transition or integration depend on a label that may be renamed.
- Keep the business payload separate from system metadata. Honeypot fields,
  `started_at_ms`, `form_meta`, and `idempotency_key` are generated or checked
  by the widget/endpoint and MUST NOT be added to form fields or
  `header_schema`.
- Define the downstream data contract: identify which fields are used by
  later nodes, notifications, integrations, routing, or analytics. Do not
  collect a field that has no documented purpose.
- Do not put credentials, bearer tokens, organization secrets, internal hook
  URLs, or tenant-private configuration in the public node or widget.

The safest creation shape is therefore:

```json
{
  "start_node_id": "550e8400-e29b-41d4-a716-446655440000",
  "nodes": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "semantic_id": "form_start",
      "type": "form_start",
      "config": { "fields": [] },
      "transitions": { "default": "650e8400-e29b-41d4-a716-446655440000" }
    },
    {
      "id": "650e8400-e29b-41d4-a716-446655440000",
      "type": "end",
      "transitions": {}
    }
  ]
}
```

Do not rely on the graph's semantic-ID normalization to repair this
relationship. The editor and the persisted pipeline compare the literal
`start_node_id` with the node `id`; mixing a semantic alias with a persisted
UUID produces `Start Form node must be the pipeline start node`.

For an active pipeline, Colba generates a self-contained public JavaScript
widget from this node. Agents can use `get_pipeline_embed` to read the ready-
to-paste `script_tag`, `enable_pipeline_embed` to publish it, and
`refresh_pipeline_embed` or `disable_pipeline_embed` to manage publication.
Changing the pipeline configuration regenerates the widget automatically.
The public browser form must use the generated widget; agents must not expose
the bearer-token API trigger or place secrets in the widget.

### Field types for `fields[]`

Supported field types used by the API/schema layer:

| Type | Use for | Notes |
| :--- | :--- | :--- |
| `string` / `text` | Short text values | Use `format: "email"` for email-like strings when useful. `text` is accepted as an alias. |
| `number` | Numeric values | Runtime validates numeric submissions with `float(...)`. |
| `boolean` | True/false values | Stored in schema as boolean. |
| `date` | Date or datetime values | Formula functions expect ISO-like date strings. |
| `select` | One option from a list/source | Use `options.source` for entity-backed values, or canonical `options.choices` for static values. |
| `array` | Tables / line items | Use `columns[]`; row formulas are supported. |
| `file` | File upload fields | If `options.multiple: true`, submitted value must be an array. |

Observed UI-friendly aliases:
- `text` is accepted as an alias for `string`.
- `table` is treated like `array` by formula evaluation in runtime code.
- `line_items` is NOT a valid field type for `header_schema` / `collect_input` fields — use `type: "array"` with `columns[]` instead. (You may use `name: "line_items"` as the field name, but its `type` must be `"array"`).
- `email` is better represented as `type: "string", "format": "email"` unless the UI explicitly supports a separate email type.
- long text can be represented as `type: "string"` plus UI metadata such as `widget: "textarea"` if needed.

### Common field properties

- `name`: stable payload key.
- `label`: human-facing field label.
- `type`: one of the supported field types.
- `required`: blocks submission when missing, unless the field has `formula`.
- `formula`: computed value expression.
- `options`: select/file/source metadata. For static dropdowns the canonical shape is `{"choices": [{"value": "...", "label": "..."}]}`. `options: ["A", "B"]` is accepted as a compact legacy input and is normalized to this shape; agents should emit the canonical shape.
- `columns`: table/array column definitions.
- `analytics`: when true, copies submitted value into analytics output.
- `custom_field_id`: UI/editor reference to an imported global/custom field; not required by the core engine.
- `x-binding`: семантический тег связывания (semantic binding tag), например, `vendor_id`, `total_amount`, `due_date`. Используется для автоматического сопоставления полей ввода с атрибутами финансовых документов в нодах автоматизации.

For public forms, also record the field's purpose and sensitivity in the
pipeline design (for example, whether an email or phone value is personal
data, who may receive it, and how it is used). These are design requirements;
do not invent unsupported runtime keys in `pipeline_config`.

Example:

```json
{
  "id": "vendor_form",
  "name": "Vendor Intake",
  "type": "collect_input",
  "config": {
    "label": "Vendor Information",
    "recipient_type": "role",
    "recipient_member_id": "ACCOUNTANT",
    "fields": [
      { "name": "vendor_name", "type": "string", "label": "Vendor Name", "required": true },
      { "name": "vendor_email", "type": "string", "label": "Vendor Email", "required": true, "format": "email" },
      { "name": "notes", "type": "string", "label": "Notes", "required": false }
    ]
  },
  "transitions": {
    "default": "approval_1"
  }
}
```

### Static dropdown fields

Use `type: "select"` and keep both the submitted value and displayed label explicit:

```json
{
  "name": "Grade",
  "label": "Grade",
  "type": "select",
  "options": {
    "choices": [
      { "value": "Junior", "label": "Junior" },
      { "value": "Middle", "label": "Middle" },
      { "value": "Senior", "label": "Senior" },
      { "value": "Lead", "label": "Lead" },
      { "value": "Manager", "label": "Manager" }
    ]
  }
}
```

Rules:
- static dropdowns must use `options.choices`, not a bare `options` array;
- every choice must contain a non-empty `value` and `label`;
- use `options.source` instead of `choices` for organization-backed/dynamic values;
- do not combine a dynamic `source` with static choices unless the UI explicitly needs a fallback;
- the field remains `type: "select"` even when the choices list is temporarily empty.

Important behavior:
- if `required_fields` is missing, the engine derives it from `fields[]` by taking fields where `required` is not `false`,
- fields marked `required: true` are enforced at launch or submission time,
- fields with `formula` are treated as computed and do not block launch,
- fields with `analytics: true` can be copied into analytics payloads.

### `approval_request`

Use this node for human approval.

Common config fields:
- `strategy`
- `assignment_target`
- `actions`

### Approval strategies

| Strategy | Meaning |
| :--- | :--- |
| `any` | The first non-pending decision/action wins and resumes the process. |
| `quorum` | A majority of assignments must choose the same action. If everyone votes and no action reaches majority, result becomes `failed_no_quorum`. |
| `unanimous` | Default. All assigned members must choose the same action. Diverging actions become `conflict`; a `rejected` action resolves to `rejected`. |

Use `any` for simple single-approver or "first responder" flows. Use `unanimous` when all assigned approvers must agree. Use `quorum` only when majority voting is intentional.

### `assignment_target`

`assignment_target` is resolved by `AssignmentResolver` into concrete member IDs.

Supported target types:

| Type | Required fields | Meaning |
| :--- | :--- | :--- |
| `individual` | `id` | Assign to one member. `id` may be a member UUID or email. |
| `workgroup` | `id` | Assign to all members of a workgroup by UUID or workgroup key. |
| `department` | `id` | Alias-style workgroup resolution by UUID or key. |
| `location` | `id` | Alias-style workgroup resolution by UUID or key. |
| `role` | `id` | Assign to members whose `profile.job_title` matches this position. This is not an access-role selector. |
| `manager` | `of_member_id` | Assign to direct manager of the given member UUID. Use `initiator` to resolve from process initiator. |
| `manager_manager` | `of_member_id` | Assign to manager's manager. |
| `grand_manager` | `of_member_id` | Same behavior as `manager_manager`. |

Examples:

```json
{ "type": "individual", "id": "person@example.com" }
```

```json
{ "type": "individual", "id": "b4fd2b0d-bf92-4b9e-9c51-0b0da86a22a8" }
```

```json
{ "type": "manager", "of_member_id": "initiator" }
```

```json
{ "type": "workgroup", "id": "finance" }
```

```json
{ "type": "role", "id": "CFO" }
```

Assigning to the initiator:
- `manager` supports `of_member_id: "initiator"` and resolves it to the process initiator's member ID.
- `task` nodes default to initiator at runtime if `assignment_target` is absent, but generated JSON should still include an explicit target because graph validation flags missing assignment targets.
- `approval_request` does not currently have a dedicated `type: "initiator"` resolver. If approval must be assigned to the initiator, pass the initiator member UUID into a field and use dynamic binding, or add resolver support before relying on `type: "initiator"`.

Dynamic binding is supported for `id` values in approval nodes:

```json
{ "type": "individual", "id": "{{approver_email}}" }
```

This resolves `approver_email` from `context.initial_payload` or submitted form data in `step_results`.

Example:

```json
{
  "id": "approval_1",
  "name": "Manager Approval",
  "type": "approval_request",
  "config": {
    "strategy": "any",
    "assignment_target": {
      "type": "manager",
      "of_member_id": "initiator"
    },
    "actions": [
      { "id": "approved", "label": "Approve", "style": "success" },
      { "id": "rejected", "label": "Reject", "style": "danger" }
    ]
  },
  "transitions": {
    "approved": "end_done",
    "rejected": "end_rejected"
  }
}
```

Rules:
- `assignment_target` is required,
- `actions` is optional. If omitted or empty, it defaults strictly to `["approved", "rejected"]`. Other action keys (like `posted` or `needs_fix`) are NOT supported unless explicitly defined in `actions`.
- action IDs should have matching transitions,
- manager-based assignment is allowed, but it can fail if the initiator has no manager in directory data.

### `task`

Use this for a human action step that is not strictly an approval.

Common config fields:
- `assignment_target`
- `actions`
- `label`

Rules are similar to approval nodes:
- `assignment_target` is required,
- `actions` is **mandatory** (task nodes do not have default actions),
- transitions should align with action IDs,
- use it for operational work, follow-up, or manual completion steps.

Example:

```json
{
  "id": "accounting_task",
  "name": "Post Invoice",
  "type": "task",
  "config": {
    "assignment_target": {
      "type": "role",
      "id": "accountant"
    },
    "actions": [
      { "id": "posted", "label": "Posted", "style": "success" },
      { "id": "needs_fix", "label": "Needs Fix", "style": "warning" }
    ]
  },
  "transitions": {
    "posted": "end_done",
    "needs_fix": "invoice_input"
  }
}
```

Runtime note:
- if a task has no `assignment_target`, `TaskHandler` defaults to the process initiator;
- graph validation still reports missing `assignment_target` as an error, so always include it in generated JSON.

### Escalations

Both `approval_request` and `task` nodes support `escalations` to handle delays when a human action is pending.

The `escalations` field is a list of objects in `config` evaluated sequentially by a background sweeper.

Supported escalation rule fields:
- `wait_minutes` (integer): Time in minutes to wait before triggering this escalation since the node was entered or since the last escalation.
- `action` (string): The escalation action to take. Supported values:
  - `"notify"`: Sends a notification. Must configure:
    - `recipient` (string): Target to notify. Supported values: `"assignee"`, `"initiator"`, or other roles/departments.
  - `"substitute"`: Adds additional/substitute approvers/assignees to the pending request. Must configure:
    - `recipient` (string): Target to add. Supported values: `"manager"` (manager of the current assignee), or workgroup/role names.
    - `recipient_id` (string, optional): Specific ID of the recipient target to add.
  - `"transition"`: Forces a transition to a different node, terminating the current waiting step. Must configure:
    - `transition_key` (string): The transition path to follow (e.g. `"rejected"`, `"approved"`).
    - `reason` (string, optional): Audit log explanation for the forced timeout transition.

Example with all three escalation actions configured on a node:
```json
{
  "id": "manager_approval",
  "name": "Manager Approval",
  "type": "approval_request",
  "config": {
    "strategy": "any",
    "assignment_target": {
      "type": "manager",
      "of_member_id": "initiator"
    },
    "escalations": [
      {
        "wait_minutes": 60,
        "action": "notify",
        "recipient": "assignee"
      },
      {
        "wait_minutes": 120,
        "action": "substitute",
        "recipient": "manager"
      },
      {
        "wait_minutes": 180,
        "action": "transition",
        "transition_key": "rejected",
        "reason": "auto_timeout"
      }
    ]
  },
  "transitions": {
    "approved": "accounting_task",
    "rejected": "send_rejected_email"
  }
}
```

### `condition` and `conditional`

Use these nodes to branch based on data.

`condition` and `conditional` currently resolve to the same `ConditionalHandler`. Prefer `condition` for new JSON. Keep `conditional` only for legacy configs or imported data.

Common config fields:
- `field`
- `operator`
- `value`
- `expression`

Required transitions:
- `true`
- `false`

#### Dotted-path field resolution
The `field` config property supports dotted paths (e.g. `llm.invoice_risk.risk_level` or `step_results.collect_1.submitted_data.amount`). `ConditionalHandler` traverses `context.initial_payload` and `step_results` using dotted paths directly.
- **Rule**: Use dotted paths in `field` directly when branching on nested data or LLM JSON outputs. `output_enum` + flat field is only needed when you want the `llm_request` node itself to validate allowed enum values or perform error-transition routing upon validation failure.

#### Supported Structure Contract
The ONLY supported structure for a condition node is a single condition defined at `config` level with `field`/`operator`/`value` (or `expression`) and explicit `true`/`false` transitions.
A `conditions[]` array containing `error_message` and lacking `true`/`false` transitions is an **unsupported structure** — do NOT generate it, even if encountered in legacy datasets.

Supported operators:

| Operator | Meaning |
| :--- | :--- |
| `>` | numeric/string greater than |
| `<` | numeric/string less than |
| `==` | equal |
| `>=` | greater than or equal |
| `<=` | less than or equal |
| `!=` | not equal |
| `contains` | case-insensitive substring check |
| `not_contains` | inverse substring check |
| `startswith` | case-insensitive prefix check |
| `endswith` | case-insensitive suffix check |
| `is_empty` | actual value is empty after trimming |
| `not_empty` | actual value is not empty after trimming |

Not currently supported by the condition handler:
- `in`
- `not_in`
- `between`
- array membership operators

For set membership, model the value as a string/select and use `==`, `!=`, or add code support before using an `in`-style condition.

Example:

```json
{
  "id": "amount_check",
  "name": "High Value?",
  "type": "condition",
  "config": {
    "field": "amount",
    "operator": ">=",
    "value": 10000
  },
  "transitions": {
    "true": "approval_2",
    "false": "end_done"
  }
}
```

Rules:
- always provide both branches,
- the checked field must exist in a prior input or launch payload,
- do not branch on values that are only implied, not actually available in context.

### `action`

Use this for automated action nodes.

Common config fields:
- `action_type`: controls the execution mode (`"mutate_context"`, `"http_request"`, or `"integration"`)
- `on_error`: behavior on failure (`"fail"`, `"transition"`)
- `error_transition_key`: transition key to follow when `on_error: "transition"` is triggered
- `retry`: retry configuration, e.g., `{"max_attempts": 3, "backoff_seconds": 1.0}`

---

#### 1. Mutate Context Mode (`action_type: "mutate_context"`)

Used to transform, copy, or calculate context variables. Contains a list of `operations`.

Supported operations:
- `set`: Sets a target to a literal value or string template.
- `copy`: Copies a value from a source path to a target path, retaining its original data type.
- `concat`: Concatenates multiple sources with an optional separator.
- `strip_html`: Converts untrusted HTML from `source` into normalized plain text and writes it to `target`; scripts, styles, templates, and comments are discarded.
- `math`: Evaluates a mathematical expression (using `FormulaService`).

Example:
```json
{
  "id": "mutate_context_node",
  "name": "Format Fields",
  "type": "action",
  "config": {
    "action_type": "mutate_context",
    "operations": [
      {
        "op": "set",
        "target": "step_results.formatted_name",
        "value": "Name: {{initial_payload.vendor_name}}"
      },
      {
        "op": "copy",
        "target": "metadata.copied_vendor_id",
        "source": "step_results.create_vendor.vendor_id"
      },
      {
        "op": "concat",
        "target": "step_results.full_address",
        "sources": ["initial_payload.street", "initial_payload.city"],
        "separator": ", "
      },
      {
        "op": "strip_html",
        "target": "initial_payload.telegram_content.text",
        "source": "initial_payload.content"
      },
      {
        "op": "math",
        "target": "step_results.total_value",
        "expression": "qty * price"
      }
    ]
  },
  "transitions": {
    "default": "next_node"
  }
}
```

---

#### 2. HTTP Request Mode (`action_type: "http_request"`)

Used to make generic synchronous HTTP calls to external systems (CRM, ERP, custom APIs).

For new pipelines, an authenticated external HTTP call MUST be modeled as an
`outbound_webhook` with `config.auth_secret_name`. Do not use this action mode
with `Authorization`, `auth_token`, literal credentials, or `{{secrets.*}}`
placeholders. The legacy environment-variable placeholder mechanism is retained
only for backward compatibility with existing pipelines.

Config fields:
- `url`: target URL (supports placeholders)
- `method`: HTTP verb (`"GET"`, `"POST"`, `"PUT"`, `"DELETE"`)
- `headers`: headers dictionary for non-sensitive/static headers. Do not put credentials or `{{secrets.*}}` placeholders in authenticated outbound requests.
- `body` or `body_mapping`: payload definition
- `query_params` or `query_params_mapping`: query parameters
- `response_mapping`: maps response JSON keys to target context paths

Example:
```json
{
  "id": "crm_integration",
  "name": "Create CRM Contact",
  "type": "action",
  "config": {
    "action_type": "http_request",
    "url": "https://api.hubspot.com/v3/objects/contacts",
    "method": "POST",
    "headers": {
      "Content-Type": "application/json"
    },
    "body_mapping": {
      "email": "initial_payload.email",
      "firstname": "initial_payload.first_name",
      "lastname": "initial_payload.last_name"
    },
    "response_mapping": {
      "step_results.crm_contact_id": "id"
    },
    "on_error": "transition",
    "error_transition_key": "handle_http_error",
    "retry": {
      "max_attempts": 3,
      "backoff_seconds": 2.0
    }
  },
  "transitions": {
    "default": "next_node",
    "handle_http_error": "error_handling_task"
  }
}
```

---

#### 3. Integration Mode (`action_type: "integration"`)

Used to route inputs to pre-registered domain operations (`colba.*`), external ERP adapters (`quickbooks`, `xero`, `softledger`), or the shared Colba Telegram bot.

Config fields:
- `provider`: integration provider (`"colba"`, `"quickbooks"`, `"xero"`, `"softledger"`, `"telegram"`)
- `action`: provider-specific method (e.g., `"create_vendor"`, `"create_bill"`, `"post_bill"`)
- `inputs`: inputs mapped from the context to the adapter requirements
- `outputs`: output mapping from integration results back to target context paths

Supported integrations:
- **`provider: "colba"`**:
  - `action: "create_vendor"` (creates database vendor record; maps to `CreateVendorHandler`)
  - `action: "create_document"` (generic financial document creator; maps to `CreateFinancialDocumentHandler`. Requires `document_type` and supports semantic auto-binding).
  - `action: "submit_ksef_invoice"` (Polish National e-Invoice System KSeF API v2 submission node. See [KSeF Pipeline Agent Guide](docs/ksef-pipeline-agent-guide.md) or resource `docs://skills/ksef_pipeline_agent_guide`).
  - `action: "mark_ksef_offline_delivered"` (acknowledges delivery of an offline KSeF invoice so the background worker may submit it later).
  - Shortcut/legacy actions: `create_po`, `create_invoice`, `create_bill`, `create_rfq`, `create_quote`, `create_receipt` (each maps to `CreateFinancialDocumentHandler` and implicitly sets the document type).
- **`provider: "quickbooks"`**:
  - `action: "create_bill"`
  - `action: "create_purchase_order"`
- **`provider: "xero"` & `provider: "softledger"`**:
  - `action: "post_bill"`
  - `action: "post_purchase_order"`

##### Telegram channel publication (`provider: "telegram"`)

Use this integration when an automated workflow should publish text to Telegram after a prior human approval. The Telegram action does not create an approval by itself; place it after an `approval_request` node whose `approved` transition targets this action.

Required configuration:

- `action_type`: exactly `"integration"`.
- `provider`: exactly `"telegram"`.
- `action`: exactly `"send_message"`.
- `target.kind`: exactly `"channel"` for channel publication.
- `target.channel_id`: explicit numeric Telegram channel ID beginning with `-100`; do not use a username and do not invent a default.
- The channel must already be verified in the current organization through Telegram settings; the numeric ID alone is never authorization.
- `text`: a non-empty literal or placeholder such as `"{{initial_payload.text}}"`.
- `parse_mode`: optional Telegram formatting mode: `"HTML"`, `"Markdown"`, or `"MarkdownV2"`. If omitted, the text is sent as plain text.

For MCP agents, use `parse_mode` only when the text is authored with that
mode's syntax. The value may be a literal in the action config or a resolved
workflow input (`inputs.parse_mode`). Unsupported values fail the action before
the message is queued. A completed action confirms durable outbox enqueue; it
does not confirm that Telegram has accepted the message.

Example:

```json
{
  "id": "telegram_channel_post",
  "name": "Publish to Telegram channel",
  "type": "action",
  "config": {
    "action_type": "integration",
    "provider": "telegram",
    "action": "send_message",
    "target": {
      "kind": "channel",
      "channel_id": "-1001234567890"
    },
    "text": "{{initial_payload.text}}",
    "parse_mode": "HTML",
    "retry": { "max_attempts": 3, "backoff_seconds": 2 },
    "on_error": "fail"
  },
  "transitions": {
    "default": "published"
  }
}
```

Runtime behavior:

1. Colba checks that Telegram is configured for the organization.
2. Colba requires an active verified-channel registry row for the current organization and exact `chat_id`.
3. Channel ownership and Telegram permissions are verified during the one-time organization connection flow.
4. The message is encrypted and placed in the retryable Telegram outbox.
5. Immediately before `sendMessage`, the worker re-checks the enabled integration and exact tenant/destination binding. Disconnecting or revoking a destination cancels already queued delivery.
6. The worker sends the message and records delivery status. Telegram 400/401/403 errors become terminal delivery failures; transient failures are retried.

Telegram URL buttons are limited to 20 entries and accept only absolute HTTP(S) URLs without embedded credentials.

The action reports completion after durable enqueue. It does not wait for Telegram delivery confirmation; agents must not describe a completed action node as proof that the message was delivered.

The reusable production blueprint `Telegram Channel Publication with Approval` follows this pattern. It assigns approval to organization job title `publisher`, collects one required `text` field, and leaves the channel ID unset unless the production installer receives `--channel-id` or `TELEGRAM_CHANNEL_ID`.

##### Workflow Email Action (`provider: "colba"`, `action: "send_email"`)

Use this action to build an email from resolved process values and place it in
Colba's durable email delivery queue. It is appropriate for process
notifications, review requests, and operational alerts. The action completes
after queue admission; it does not wait for SMTP delivery.

Configuration:

```json
{
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
    "initial_payload.requester_name"
  ],
  "field_labels": {
    "initial_payload.request_number": "Request number",
    "initial_payload.requester_name": "Requester"
  }
}
```

Use `recipient_mode: "field"` with `recipient_field` when the recipient comes
from process data, for example `initial_payload.requester_email`. The resolved
value must be one strict email address. For public form data, set
`allowed_recipient_domains` to a list such as `["company.example"]`. New
pipelines should use `email_fields`; `transmitted_fields` is a legacy alias.
Select only the required process data and never expose passwords, tokens,
organization secrets, or unnecessary personal data.

Limits are 255 characters for the subject, 20,000 for the message, 2,000 for
the signature, 50 selected fields, and 65,536 bytes for the serialized email
payload by default. Subject control characters are rejected. The action is
protected by tenant and recipient rate limits, is idempotent for retries of
the same node execution, and returns `delivery_id`, `delivery_status`,
`email_execution`, `field_count`, and `recipient_mode` in the node output.
`delivery_status: "pending"` means queued, not that SMTP delivery has
completed. Configure `on_error: "transition"` and `error_transition_key` if
queue-admission errors need a visible workflow branch. Redis protection fails
closed when unavailable.

##### Generic Document Creator (`action: "create_document"`)

При использовании `provider: "colba"` и `action: "create_document"`, в `config` также передаются:
- `document_type`: тип создаваемого финансового документа (`"bill"`, `"invoice"`, `"rfq"`, `"purchase_order"`, `"quote"`, `"receipt"`). На бэкенде типы нормализуются в верхний регистр, при этом `"invoice"` и `"receipt"` маппятся на тип `"RECEIVABLE"`.
- `result_key` (optional, default `"document_id"`): ключ, под которым UUID созданного документа сохранится в `step_results` ноды.
- `parent_key` (optional): ключ родительского документа в `step_results` или в payload (например, `"po_id"` для связывания счета с заказом на закупку).

Пример создания счета (Bill):
```json
{
  "id": "create_bill_node",
  "name": "Create Bill",
  "type": "action",
  "config": {
    "action_type": "integration",
    "provider": "colba",
    "action": "create_document",
    "document_type": "bill",
    "result_key": "bill_id",
    "parent_key": "po_id",
    "inputs": {
      "vendor_id": "initial_payload.vendor_id",
      "currency_code": "initial_payload.currency",
      "issue_date": "initial_payload.issue_date",
      "due_date": "initial_payload.due_date",
      "total_net": "initial_payload.net_amount",
      "total_amount": "initial_payload.total_amount",
      "bill_number": "initial_payload.bill_number",
      "line_items": "initial_payload.line_items"
    },
    "outputs": {
      "step_results.created_bill_id": "document_id"
    }
  },
  "transitions": {
    "default": "next_step"
  }
}
```

##### Семантическое связывание (`x-binding`) и автосопоставление полей

Для упрощения настройки `create_document` визуальный редактор Colba поддерживает механизм **семантического связывания** (`x-binding`).

1. **Задание тегов**: При редактировании полей ввода в `collect_input` или `task` (в визуальном инспекторе) можно указать тег `x-binding` (например, `vendor_id`, `total_amount`, `due_date`).
2. **Автоматическое сопоставление**: Когда вы настраиваете ноду `action` с действием `create_document`, редактор автоматически ищет во всех входящих полях (upstream fields) совпадения:
   - По семантическому тегу: если `x-binding` входящего поля совпадает с системным ключом атрибута документа или входит в его список поддерживаемых синонимов (`bindings`).
   - По имени: если имя входящего поля (`name`) совпадает с системным ключом атрибута документа.
3. **Визуальная индикация в интерфейсе**:
   - **Semantic Match** (зелёный бейдж): поле сопоставлено автоматически по семантическому тегу `x-binding`.
   - **Name Match** (синий бейдж): поле сопоставлено по совпадению имен.
   - **Manual Override** / **Custom** (белый/серый): ручной выбор поля пользователем.
4. **Импорт шаблонов сущностей (Entity Templates)**: При настройке полей ввода в нодах `collect_input` или `task` визуальный инспектор позволяет быстро наполнить список полей по стандартному шаблону (выпадающий список **"Import Entity Template..."**). Поддерживаются шаблоны для всех типов финансовых документов (`Bill`, `Invoice`, `RFQ`, `PO`, `Quote`, `Receipt`). При выборе шаблона в ноду автоматически добавляются недостающие поля с предзаполненными системными именами (`name`), метками (`label`), типами (`type`) и соответствующими тегами семантической привязки `x-binding`. Это гарантирует мгновенное автосопоставление с последующими нодами создания документов.

##### Системные ключи атрибутов и поддерживаемые привязки (Bindings)

| Тип документа | Атрибут документа | Описание | Поддерживаемые `x-binding` |
| :--- | :--- | :--- | :--- |
| **Все типы** | `vendor_id` | ID поставщика (UUID) | `vendor_id` |
| | `currency_code` | Валюта (например, "USD") | `currency`, `currency_code` |
| | `issue_date` | Дата выставления | `issue_date` |
| | `line_items` | Таблица позиций (Array) | `line_items` |
| **bill** | `due_date` | Срок оплаты | `due_date` |
| | `total_net` | Чистая сумма | `total_net` |
| | `total_amount` | Полная сумма | `total_amount` |
| | `bill_number` | Номер счета поставщика | `bill_number` |
| | `invoice_reference` | Дополнительная ссылка | `invoice_reference` |
| **invoice** | `due_date` | Срок оплаты | `due_date` |
| | `total_net` | Чистая сумма | `total_net` |
| | `total_amount` | Полная сумма | `total_amount` |
| | `invoice_number` | Номер счета | `invoice_number` |
| **purchase_order**| `total_net` | Чистая сумма | `total_net` |
| | `total_amount` | Полная сумма | `total_amount` |
| | `po_number` | Номер PO | `po_number` |
| | `delivery_date` | Дата доставки | `delivery_date` |
| **quote** | `total_net` | Чистая сумма | `total_net` |
| | `total_amount` | Полная сумма | `total_amount` |
| | `quote_number` | Номер предложения | `quote_number` |
| | `valid_until` | Действителен до | `valid_until` |
| | `rfq_id` | Ссылка на RFQ | `rfq_id` |
| **rfq** | `rfq_number` | Номер запроса | `rfq_number` |
| | `request_deadline` | Срок ответа | `request_deadline` |
| **receipt** | `total_net` | Чистая сумма | `total_net` |
| | `total_amount` | Полная сумма | `total_amount` |
| | `receipt_number` | Номер чека | `receipt_number` |
| | `merchant_name` | Название продавца | `merchant_name` |
| | `payment_method` | Способ оплаты | `payment_method` |

Example:
```json
{
  "id": "accounting_integration",
  "name": "Sync Bill to QuickBooks",
  "type": "action",
  "config": {
    "action_type": "integration",
    "provider": "quickbooks",
    "action": "create_bill",
    "inputs": {
      "vendor_name": "step_results.create_vendor.vendor_name",
      "total_amount": "initial_payload.total_amount",
      "description": "initial_payload.description"
    },
    "outputs": {
      "step_results.qbo_invoice_id": "response.external_id"
    }
  },
  "transitions": {
    "default": "next_node"
  }
}
```

---

#### Placeholder and Secrets Resolution

The `action` node dynamically resolves placeholders wrapped in `{{ ... }}` within configuration strings:
- `{{secrets.KEY_NAME}}` looks up `KEY_NAME` in environment variables. This is a legacy mechanism and MUST NOT be used for credentials in new pipelines.
- `{{metadata.key}}` looks up `key` in `context.metadata`.
- `{{initial_payload.key}}` looks up `key` in `context.initial_payload`.
- `{{step_results.node_id.key}}` looks up nested keys in step results.

### `outbound_webhook` and `outbound_integration`

Use these to send data outside the workflow engine.

Common config fields:
- `url`
- `method`
- `payload_mapping`
- `auth_secret_name` (required when the external destination requires authentication)

Rules:
- **HARD AGENT SECURITY RULE:** An unauthenticated `outbound_webhook` MAY omit credentials. If the external destination requires authentication, the node MUST use `config.auth_secret_name` and an organization-managed secret. The agent MUST NOT generate `auth_token`, a literal `Authorization` header, or `{{secrets.*}}` for credentials.
- The value of `auth_secret_name` is only the secret name (for example, `CONTENT_API_TOKEN`), never the secret value and never a `Bearer ...` string.
- If an external service requires authentication and no secret name is available, the agent MUST ask for/configure the organization secret name or leave the pipeline uncreated; it MUST NOT inline or invent a token.
- The organization secret must contain the raw credential, and its admin-managed `allowed_origins` must include the webhook origin.
- The secret is resolved at execution time before each webhook attempt, so rotations apply to retries and later process steps.
- `url` is required,
- `method` defaults to `POST`,
- prefer explicit payload mappings and stable field names,
- `outbound_webhook` accepts only HTTP(S) targets allowed by the outbound SSRF policy,
- `auth_secret_name` resolves an encrypted secret from the organization that owns
  the running process. The target origin must also be present in that secret's
  admin-managed `allowed_origins` list. Never put customer tokens in pipeline JSON
  or the shared production environment.
- `outbound_integration` calls a built-in provider adapter/action such as QuickBooks.

### `outbound_webhook`

Use `outbound_webhook` when you need a generic HTTP callback.

Example:

```json
{
  "id": "notify_erp",
  "name": "Notify ERP",
  "type": "outbound_webhook",
  "config": {
    "url": "https://api.example.com/invoices/receive",
    "method": "POST",
    "timeout": 15,
    "auth_secret_name": "ERP_API_TOKEN",
    "headers": {
      "Content-Type": "application/json"
    },
    "payload_mapping": {
      "vendor_name": "initial_payload.vendor_name",
      "total_amount": "initial_payload.total_amount",
      "invoice_id": "step_results.invoice_id",
      "organization_id": "metadata.organization_id"
    }
  },
  "transitions": {
    "default": "end_done"
  }
}
```

Supported `payload_mapping` sources:
- `initial_payload.<key>`
- `step_results.<key>`
- `metadata.<key>`

The webhook handler automatically adds `_process_id` to the outgoing payload.

### `outbound_integration`

Use `outbound_integration` when a built-in adapter should handle a known provider/action.

Currently implemented adapter/action:
- provider `quickbooks`
- action `create_bill`

Example:

```json
{
  "id": "quickbooks_bill",
  "name": "Create QuickBooks Bill",
  "type": "outbound_integration",
  "config": {
    "provider": "quickbooks",
    "action": "create_bill"
  },
  "transitions": {
    "default": "end_done"
  }
}
```

Current `create_bill` reads:
- `initial_payload.vendor_name`
- `initial_payload.currency`, default `USD`
- `initial_payload.amount`, default `0.0`
- `initial_payload.description`, default `Workflow generated bill`

It writes:
- `step_results[config.id].external_invoice_id`

### `llm_request`

Use this to call an LLM during the process.

An LLM node is useful when the workflow needs to:
- summarize submitted data,
- classify a request,
- extract structured values from free text,
- draft a recommendation for a human approver,
- normalize messy input before a downstream task or webhook.

It is not a replacement for deterministic routing when a simple `condition` or formula can do the job.

### Runtime requirements

`llm_request` requires:
- a database session,
- `metadata.organization_id` in process context,
- organization LLM settings enabled,
- a configured default provider in organization settings,
- active provider credentials.

If any of these are missing, the node fails unless `on_error: "transition"` is configured.

Supported config fields:
- `prompt_source`
- `static_prompt`
- `prompt_field`
- `template_prompt`
- `system_prompt`
- `output_target`
- `response_format`
- `temperature`
- `max_tokens`
- `on_error`
- `error_transition_key`

### Prompt sources

`prompt_source` controls where the user prompt comes from.

| Source | Required config | Behavior |
| :--- | :--- | :--- |
| `static` | `static_prompt` | Uses a fixed prompt from node config. Best for generic instructions. |
| `context_field` | `prompt_field` | Reads a value from runtime context and sends it as the prompt. |
| `template` | `template_prompt` | Renders a small handlebars-like template using runtime context. Best default for generated pipelines. |

Use `template` for most pipeline JSON generation because it is explicit and inspectable.

### Template context

`template_prompt` can reference:
- fields from `context.initial_payload` directly at the root,
- `step_results` under `step_results`.

Examples:

```text
{{vendor_name}}
{{total_amount}}
{{line_items}}
{{step_results.create_vendor.vendor_id}}
```

If a referenced value is missing, it renders as an empty string. If the value is an object or array, it is JSON-stringified.

### Output behavior

The handler writes the LLM result into `context.initial_payload` at `output_target`.

Examples:
- `output_target: "llm.summary"` creates/updates `initial_payload.llm.summary`.
- `output_target: "classification"` creates/updates `initial_payload.classification`.
- `output_target: "risk.score"` creates/updates `initial_payload.risk.score`.

It also writes execution metadata under `step_results[node_id]`:

```json
{
  "submitted": true,
  "status": "completed",
  "llm_output_target": "llm.summary",
  "llm_provider": "google",
  "llm_model": "gemini-..."
}
```

### `load_test`

Use this node to run automated k6 website / API load and stress tests as an asynchronous background workflow step.

Supported config fields:
- `target_url` (required string or template): target URL to perform stress testing against (e.g. `"https://example.com/api"` or `"{{target_site_url}}"`). Validated against SSRF rules at runtime.
- `stages` (array of stage objects): multi-step ramping schedule for k6 VUs (virtual users). Each stage requires `duration` (e.g. `"10s"`, `"1m"`) and `target` (integer number of VUs).
- `thresholds` (optional map): metric threshold rules for performance pass/fail assertion (e.g. `{"http_req_failed": ["rate<0.01"], "http_req_duration": ["p(95)<500"]}`).

Example:
```json
{
  "id": "run_load_test",
  "name": "Execute k6 Load Test",
  "type": "load_test",
  "config": {
    "target_url": "{{target_url}}",
    "stages": [
      { "duration": "10s", "target": 5 },
      { "duration": "30s", "target": 20 }
    ],
    "thresholds": {
      "http_req_failed": ["rate<0.01"]
    }
  },
  "transitions": {
    "default": "report_results"
  }
}
```

Output metrics written into `submitted_data` / `step_results[node_id]`:
- `error_rate`: float percentage of failed requests.
- `p95_latency_ms`: 95th percentile latency in milliseconds.
- `http_reqs`: total HTTP requests executed.
- `status`: `"completed"`, `"threshold_failed"`, or `"failed"`.

### Response format

| `response_format` | Stored value |
| :--- | :--- |
| `json` | Stores the provider JSON response as an object. |
| `text` | Stores `assistant_message` if present, otherwise JSON-stringifies the response. |

Prefer `json` when a later node will branch on a structured field. Prefer `text` when the output is only displayed to a human.

> **Crucial Rule on System Prompts and Response Formats**:
> If `system_prompt` instructs the LLM to return a JSON object, either:
> (a) use `response_format: "json"` and read the specific key downstream via dotted-path in `condition` or templates, OR
> (b) if `response_format: "text"` is required for a human-readable field, the `system_prompt` MUST instruct the model to return a plain JSON object with exactly one key named `assistant_message` containing the human-readable text (e.g. `{"assistant_message": "..."}`) — NOT a JSON object with custom key names. Otherwise, text extraction will fail to find `assistant_message` and fallback to storing raw JSON string.

### Output Enum Validation

To enforce that the LLM response is valid against a strict set of values, configure `output_enum` under `config`:
- `path` (string): The dot-notation path inside the LLM JSON response where the value is located (e.g. `assistant_message` or `result.status`).
- `values` (array of strings): An array of allowed string values (e.g. `["low", "medium", "high"]`).

If validation fails, the behavior is determined by `on_error`:
- If `on_error` is `"transition"`, the node routes to `error_transition_key` and saves the validation error message.
- If `on_error` is `"fail"`, the process fails.

Example config for validation:
```json
"config": {
  "prompt_source": "template",
  "template_prompt": "Rate the risk of this request...",
  "output_target": "risk_level",
  "response_format": "text",
  "output_enum": {
    "path": "assistant_message",
    "values": ["low", "medium", "high"]
  },
  "on_error": "transition",
  "error_transition_key": "llm_error"
}
```

### Error behavior

| `on_error` | Behavior |
| :--- | :--- |
| `fail` | Default. Fails the process if prompt/provider/runtime fails. |
| `transition` | Completes the node with `error_transition_key` and stores `llm_error` in output data. |

If `on_error: "transition"` is used, include a matching transition key:

```json
"transitions": {
  "default": "next_step",
  "llm_error": "manual_review"
}
```

### Minimal static prompt example

```json
{
  "id": "llm_static_check",
  "name": "Static LLM Check",
  "type": "llm_request",
  "config": {
    "prompt_source": "static",
    "static_prompt": "Return JSON: {\"ok\": true, \"reason\": \"health check\"}",
    "output_target": "llm.health_check",
    "response_format": "json",
    "on_error": "fail"
  },
  "transitions": {
    "default": "next_step"
  }
}
```

### Template prompt example

```json
{
  "id": "llm_summarize",
  "name": "Summarize Request",
  "type": "llm_request",
  "config": {
    "prompt_source": "template",
    "template_prompt": "Summarize the request: {{vendor_name}} / {{amount}}",
    "system_prompt": "Return concise JSON only.",
    "output_target": "llm.summary",
    "response_format": "json",
    "temperature": 0.2,
    "max_tokens": 800,
    "on_error": "transition",
    "error_transition_key": "llm_error"
  },
  "transitions": {
    "default": "next_step",
    "llm_error": "end_failed"
  }
}
```

Behavior:
- `prompt_source=context_field` requires `prompt_field`,
- `prompt_source=template` requires `template_prompt`,
- `prompt_source=static` uses `static_prompt`,
- `output_target` is where the result gets written into `context.initial_payload`,
- when `response_format=json`, the raw JSON result is stored,
- when `response_format=text`, the assistant message is preferred if available,
- `on_error=transition` requires an error transition key.

### Context field prompt example

Use this when a previous form field already contains the complete prompt.

```json
{
  "id": "llm_from_prompt_field",
  "name": "Run User Prompt",
  "type": "llm_request",
  "config": {
    "prompt_source": "context_field",
    "prompt_field": "analysis_prompt",
    "output_target": "llm.analysis",
    "response_format": "json",
    "on_error": "transition",
    "error_transition_key": "llm_error"
  },
  "transitions": {
    "default": "human_review",
    "llm_error": "human_review"
  }
}
```

### Classification & Dotted-Path Routing Pattern

If a later `condition` needs a value produced by the LLM, you can store structured JSON in the context and branch directly using nested dotted-paths.

The `condition` handler fully supports reading `config.field` nested keys from `initial_payload` (e.g. `llm.invoice_risk.risk_level`) or submitted form data.

Good pattern for structured LLM execution and downstream dotted-path routing:

```json
{
  "id": "llm_classify_invoice",
  "name": "Classify Invoice Risk",
  "type": "llm_request",
  "config": {
    "prompt_source": "template",
    "system_prompt": "Return only JSON with keys: risk_level, reason. risk_level must be low, medium, or high.",
    "template_prompt": "Classify this invoice. Vendor={{vendor_name}} Total={{total_amount}} Line items={{line_items}}",
    "output_target": "llm.invoice_risk",
    "response_format": "json",
    "on_error": "transition",
    "error_transition_key": "llm_error"
  },
  "transitions": {
    "default": "risk_gate",
    "llm_error": "manual_review"
  }
}
```

Then the condition can branch directly on the nested field:

```json
{
  "id": "risk_gate",
  "name": "High Risk?",
  "type": "condition",
  "config": {
    "field": "llm.invoice_risk.risk_level",
    "operator": "==",
    "value": "high"
  },
  "transitions": {
    "true": "manual_review",
    "false": "auto_continue"
  }
}
```

### How to create an LLM node

1. Decide what the LLM should produce: summary, classification, extracted JSON, or recommendation.
2. Prefer `prompt_source: "template"` and reference explicit fields from runtime context.
3. Add a strict `system_prompt` that constrains the output shape.
4. Choose `response_format: "json"` for structured downstream use, or `text` for display-only output.
5. Pick an `output_target` that later nodes can read predictably.
6. Add `on_error: "transition"` for business-critical flows where a human fallback should handle provider failure.
7. Add transitions for both success and error paths when error routing is enabled.

### Good LLM node practices

- Keep prompts short and grounded in fields that exist.
- Never include secrets in prompt templates.
- Do not ask the LLM to invent assignment targets, vendor IDs, account IDs, or other entity IDs.
- Use deterministic `condition` nodes for numeric thresholds instead of asking the LLM to decide them.
- Use human review after LLM output when the result changes money, approvals, legal status, or external systems.
- Store outputs under a clear namespace such as `llm.summary` or `llm.invoice_risk`.

### `create_vendor`, `create_po`, `create_invoice`

These are domain-specific automation nodes.

Current expectation:
- they read from process payload/context,
- they create the corresponding business record,
- they write useful identifiers back into context for downstream nodes.

Important:
- do not use them without confirming the payload contains the fields the handler expects,
- if the handler depends on mapped field names, document that mapping in the node config.

### `create_vendor`

Creates a vendor record from workflow payload data.

Example:

```json
{
  "id": "create_vendor",
  "name": "Create Vendor",
  "type": "create_vendor",
  "config": {
    "field_map": {
      "name": "vendor_name",
      "email": "vendor_email"
    }
  },
  "transitions": {
    "default": "next_step"
  }
}
```

Reads:
- `metadata.organization_id`
- `initial_payload[field_map.name]`, default key `vendor_name`
- `initial_payload[field_map.email]`, default key `vendor_email`
- `initial_payload.bank_details`
- `initial_payload.address`
- `initial_payload.tax_id`

Requires:
- `vendor_name` or mapped name field

Writes:
- `step_results.vendor_id`
- `step_results.vendor_name`

### `create_po` and `create_invoice`

Both use `CreateFinancialDocumentHandler`; the node type selects the handler registration, while config controls the document details.

Example PO:

```json
{
  "id": "create_po",
  "name": "Create Purchase Order",
  "type": "create_po",
  "config": {
    "document_type": "PURCHASE_ORDER",
    "result_key": "po_id"
  },
  "transitions": {
    "default": "end_done"
  }
}
```

Example invoice linked to a previous PO:

```json
{
  "id": "create_invoice",
  "name": "Create Invoice",
  "type": "create_invoice",
  "config": {
    "document_type": "INVOICE",
    "result_key": "invoice_id",
    "parent_key": "po_id"
  },
  "transitions": {
    "default": "end_done"
  }
}
```

Reads:
- `metadata.organization_id`
- `step_results.vendor_id`, if available
- `step_results[config.parent_key]`, if `parent_key` is set
- `initial_payload.total_amount`, default `0`
- `initial_payload.line_items`, default `[]`
- `initial_payload.currency`, default `USD`
- `initial_payload.reference`, or `po_number`, or `invoice_number`

Writes:
- `step_results[config.result_key]`, for example `po_id` or `invoice_id`

---

### `wait_for_callback`

Pauses the pipeline process and waits for an **external HTTP callback** before continuing.
The engine issues a one-time signed token, delivers it to the configured destination (webhook or email),
and resumes execution when the external system calls back.

#### When to use

Use `wait_for_callback` when a pipeline step depends on a result from an **external system**:
- A third-party payment processor that calls back upon settlement.
- An external approval portal not integrated into Colba.
- Any manual or semi-automated external workflow step.

Do **not** use it as a replacement for `approval_request` (which is for Colba members).

#### Required config fields

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `name` | string | Yes | Human-readable name for this step (shown in email subjects and logs). |
| `delivery_mode` | string | Yes | `"webhook"` or `"email"`. |
| `timeout_minutes` | integer | No (default: unlimited) | Optional minutes before the token expires. If omitted, the token never expires and waits indefinitely for an external callback. |
| `target_url` | string | webhook only | Full HTTPS/HTTP URL to POST the callback invitation to. Must be a public internet address. Internal/private IP ranges are blocked. |
| `recipient_email` | string | email only | Email address to send the callback instructions to. |
| `secret_name` | string | No | Name of an organization secret to use for HMAC signing. Its allowed origins must include `target_url`. If set, `X-Callback-Secret` (or `secret_header`) is attached to the webhook delivery. |
| `secret_header` | string | No | Custom header name for the secret (default: `X-Callback-Secret`). |
| `payload_schema` | object | No | JSON Schema (draft-07) to validate the callback payload before resuming the process. |

#### Indefinite Token Timeout Behavior (Important for MCP Agents)

- **Default (No Timeout)**: If `timeout_minutes` is **omitted**, the generated callback token has `expires_at: null` and will **never expire automatically**. The process will wait indefinitely for the external HTTP callback. Use this default for external systems, offline processing, or long-running workflows.
- **Explicit Timeout**: Only specify `timeout_minutes` (e.g., `60`, `1440`) if the business logic requires timing out after a fixed duration and routing through the `timeout` transition.
- **Audit Trail & Logging**: Upon node initialization, Colba writes audit entries (`Callback node initialized. Token enqueued (indefinite, no timeout)`) and logs `timeout_mode: "indefinite"` and `expires_at: "never"`.

#### Delivery modes

**`webhook`**: The engine POSTs a JSON body to `target_url`:
```json
{ "callback_url": "https://<api>/api/v1/workflow/pipeline-callbacks/<token>" }
```
The external system must then POST back to `callback_url` with the result payload.

**`email`**: The engine sends an email to `recipient_email` containing the `callback_url`.
The recipient copies it and sends the callback from any HTTP client.

#### Outgoing transitions

Define **at least two** outgoing transitions:

| Transition key | When fired |
| :--- | :--- |
| `completed` | External system sent a valid callback with `status: "completed"`. |
| `timeout` | Token expired before a callback arrived. |

Optional additional transitions:

| Transition key | When fired |
| :--- | :--- |
| `failed` | External system explicitly sent `status: "failed"` in the callback. |

#### Callback endpoint (external system must call)

```
POST /api/v1/workflow/pipeline-callbacks/{raw_token}
Content-Type: application/json

{
  "status": "completed",      // required: "completed" or "failed"
  "result": { ... }           // optional: any JSON object, available in step_results
}
```

The endpoint is **publicly accessible** (no Colba auth required — the signed token is the credential).

#### Getting the token for a waiting process (as a Colba member)

```
GET /api/v1/workflow/processes/{process_id}/nodes/{node_id}/callback-token
Authorization: Bearer <member_token>
```

Returns the active callback credentials:
```json
{
  "token": "clb_cbk_...",
  "callback_url": "https://<api>/api/v1/workflow/pipeline-callbacks/<token>",
  "secret_name": null,
  "secret_header": "X-Callback-Secret",
  "status": "pending",
  "expires_at": "2026-08-02T12:00:00+00:00"
}
```
The returned token can be used to call the callback URL manually.

#### Context output

After resumption the following is written to `step_results[node_id]`:
```json
{
  "status": "completed",
  "execution_result": { /* whatever the external system sent in 'result' */ },
  "transition_key": "completed"
}
```

#### Minimal example

```json
{
  "id": "wait_payment_confirm",
  "type": "wait_for_callback",
  "label": "Wait for payment confirmation",
  "config": {
    "name": "Payment Gateway Callback",
    "delivery_mode": "webhook",
    "target_url": "https://hooks.payment-provider.com/notify",
    "timeout_minutes": 1440,
    "secret_name": "payment_gateway_hmac",
    "payload_schema": {
      "type": "object",
      "required": ["transaction_id"],
      "properties": {
        "transaction_id": { "type": "string" },
        "amount": { "type": "number" }
      }
    }
  },
  "transitions": {
    "completed": { "target": "mark_paid" },
    "timeout":   { "target": "notify_team" },
    "failed":    { "target": "escalate" }
  }
}
```

#### Email delivery example

```json
{
  "id": "wait_external_sign",
  "type": "wait_for_callback",
  "label": "Wait for e-signature",
  "config": {
    "name": "Document Signature Request",
    "delivery_mode": "email",
    "recipient_email": "signer@partner.org",
    "timeout_minutes": 4320
  },
  "transitions": {
    "completed": { "target": "archive" },
    "timeout":   { "target": "send_reminder" }
  }
}
```

#### Constraints and rules

- `target_url` must resolve to a **public** IP address. Loopback, RFC-1918 private ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16), link-local (169.254.x.x), and multicast addresses are **blocked** to prevent SSRF.
- Each token is **single-use**. Once the callback is received the token is invalidated.
- Retries: the delivery worker retries webhook POSTs up to 3 attempts with exponential back-off before marking the delivery as failed.
- The callback token itself is **Fernet-encrypted at rest** in the delivery outbox.
- Do **not** use `wait_for_callback` for human approval decisions inside Colba — use `approval_request` instead.

---

### `end`

Terminal node.

Rules:
- should not need operational config,
- use it to terminate successful or failed paths cleanly.

## Runtime Context Shape

Handlers exchange data through `ProcessContext`.

Available top-level context fields:

| Field | Shape | Meaning |
| :--- | :--- | :--- |
| `initial_payload` | object | Launch payload plus submitted form data merged by handlers. Most business fields live here. |
| `step_results` | object | Outputs from nodes, keyed by node UUID/semantic ID or direct result keys for some handlers. |
| `audit_trail` | array of strings | Runtime audit messages. |
| `tenant_id` | UUID/string | Tenant/org boundary used by resolver and persistence. |
| `initiator_id` | UUID/string | Member who started the process. |
| `metadata` | object | Runtime metadata such as `organization_id`, `display_id`, `pipeline_name`, strategies, stage configs. |

Common read paths:

```text
initial_payload.vendor_name
initial_payload.total_amount
initial_payload.line_items
step_results.vendor_id
step_results.po_id
metadata.organization_id
metadata.display_id
```

Where data is written:
- `collect_input` merges submitted form data into `initial_payload`.
- `create_vendor` writes `step_results.vendor_id` and `step_results.vendor_name`.
- `create_po` / `create_invoice` write `step_results[config.result_key]`.
- `outbound_webhook` writes `step_results[node_id].status_code` and `step_results[node_id].response_preview`.
- `outbound_integration` writes provider-specific results under `step_results[config.id]`.
- `llm_request` writes the LLM result into `initial_payload` at `config.output_target`.

Template syntax differences:
- `llm_request.template_prompt` uses `{{path.to.value}}` against a merged context containing initial payload keys at the root and `step_results` under `step_results`.
- `outbound_webhook.payload_mapping` does not use handlebars; it uses dot paths such as `initial_payload.total_amount`, `step_results.invoice_id`, and `metadata.organization_id`.
- `assignment_target.id` dynamic binding supports simple `{{field_name}}` for values found in `initial_payload` or submitted form data.

## Required Fields

Use `required: true` on `collect_input.config.fields[]` when the pipeline cannot work correctly without the value.

Example:

```json
{
  "name": "invoice_total",
  "type": "number",
  "label": "Invoice Total",
  "required": true
}
```

Use `required: true` when the field is needed by:
- a later condition,
- an assignment target,
- a webhook payload,
- an LLM prompt,
- a formula,
- an action parameter,
- a launch-time validation rule.

Keep a field optional when it is only useful for context, notes, or enrichment.

Important launch rule:
- if a field is required and not formula-driven, the launch endpoint will reject missing values,
- if a field has a formula, it is treated as computed and should not block launch as a manual required input.

## Formulas

Formulas are supported on form fields and table/array columns.

### Top-level field formulas

Example:

```json
{
  "name": "tax_amount",
  "type": "number",
  "label": "Tax Amount",
  "formula": "amount * 0.2"
}
```

### Table or array column formulas

Example:

```json
{
  "name": "line_items",
  "type": "array",
  "label": "Line Items",
  "columns": [
    { "name": "qty", "type": "number", "label": "Qty" },
    { "name": "unit_price", "type": "number", "label": "Unit Price" },
    { "name": "line_total", "type": "number", "label": "Line Total", "formula": "qty * unit_price" }
  ]
}
```

Supported formula patterns in code:
- basic arithmetic with field names,
- `sum(array.field)`,
- `workdays(start_date, end_date)`,
- `hours(start_date, end_date)`.

Formula rules:
- use field names that exist in the same form context,
- keep field names simple and consistent,
- if a formula depends on another field, that source field must exist and be available in the evaluation context,
- row formulas in arrays/tables run before top-level formulas,
- top-level formulas can use the full merged context.

### Practical formula guidance

Use formulas for:
- totals,
- durations,
- date differences,
- simple computed values,
- derived fields that should not be typed manually.

Do not use formulas for:
- business logic that needs branching or approvals,
- values that depend on external systems,
- fields that must be manually reviewed and changed by users.

## Invoice With Line Items

This is the canonical pattern for an invoice-like process where users enter a vendor, add line items, calculate each row total, then calculate the final amount and route the process based on a threshold.

### What this flow does

- collects invoice header data and line items in one input stage,
- calculates each line item total from quantity, price, and tax,
- calculates the grand total from the line totals,
- branches on the final amount,
- sends large invoices to approval,
- sends smaller invoices directly to ERP posting,
- ends the process after posting or rejection.

### The key idea

Use two levels of formulas:

- row-level formula for each line item, for example `row_total = qty * price * (1 + tax / 100)`,
- top-level formula for the invoice total, for example `total_amount = sum(line_items.row_total)`.

This is already supported by the `collect_input` handler:
- array/table rows are evaluated first,
- then top-level formulas are evaluated against the merged context,
- computed values are written back into submitted data.

### Recommended JSON shape

```json
{
  "nodes": [
    {
      "id": "invoice_input",
      "name": "Invoice Data",
      "type": "collect_input",
      "config": {
        "fields": [
          {
            "name": "vendor",
            "type": "select",
            "label": "Vendor",
            "options": { "source": "vendors" },
            "required": false,
            "custom_field_id": "2c8136d3-5ec7-40ef-a940-726faf6e2800"
          },
          {
            "name": "line_items",
            "type": "array",
            "label": "Спецификация (Позиции)",
            "columns": [
              { "name": "desc", "type": "string", "label": "Описание" },
              { "name": "qty", "type": "number", "label": "Кол-во" },
              { "name": "price", "type": "number", "label": "Цена" },
              { "name": "tax", "type": "number", "label": "НДС %" },
              {
                "name": "row_total",
                "type": "number",
                "label": "Сумма строки",
                "formula": "qty * price * (1 + tax / 100)"
              }
            ]
          },
          {
            "name": "total_amount",
            "type": "number",
            "label": "ИТОГО К ОПЛАТЕ",
            "formula": "sum(line_items.row_total)"
          }
        ]
      },
      "transitions": {
        "default": "check_total"
      }
    },
    {
      "id": "check_total",
      "name": "Сумма > 100 000?",
      "type": "condition",
      "config": {
        "field": "total_amount",
        "value": "100000",
        "operator": ">"
      },
      "transitions": {
        "true": "manager_approval",
        "false": "erp_push"
      }
    },
    {
      "id": "manager_approval",
      "name": "Manager Approval",
      "type": "approval_request",
      "config": {
        "actions": [
          { "id": "approve", "label": "Yes", "style": "success" },
          { "id": "reject", "label": "No", "style": "danger" }
        ],
        "strategy": "unanimous",
        "assignment_target": {
          "id": "by.mysite@gmail.com",
          "type": "individual"
        }
      },
      "transitions": {
        "reject": "end",
        "approve": "erp_push"
      }
    },
    {
      "id": "erp_push",
      "name": "Проводка в бухгалтерии",
      "type": "action",
      "config": {
        "handler": "erp_sync"
      },
      "transitions": {
        "default": "end"
      }
    },
    {
      "id": "end",
      "name": "Завершено",
      "type": "end"
    }
  ]
}
```

### How to think about each field

- `vendor`: usually a reusable select/global field, often backed by organization data.
- `line_items`: array/table input for multiple invoice rows.
- `desc`: free text description for the row.
- `qty`: numeric quantity.
- `price`: unit price.
- `tax`: VAT or tax rate in percent.
- `row_total`: computed row amount, not manually entered.
- `total_amount`: computed invoice sum, not manually entered.

### Important details for this pattern

- Keep `row_total` as a computed column inside the array.
- Keep `total_amount` as a computed top-level field.
- Make the downstream condition depend on `total_amount`, not on raw row data.
- If you need a vendor selector from org data, prefer a global field/select-backed field instead of plain text.
- If `custom_field_id` is present, treat it as a field-import reference for the editor/UI rather than a core engine requirement.

### Why this works

The formula engine supports:
- arithmetic expressions,
- row-level evaluation in arrays/tables,
- `sum(array.field)` across the computed row field,
- launch-time and runtime recalculation of the same values.

The result is a clean pipeline:
- user enters rows once,
- the process computes row totals automatically,
- the total is always derived, not typed by hand,
- routing uses the final amount consistently.

## Global Fields

`global_fields` are reusable organization-level fields that can be referenced in templates and launch schemas.

In the runtime, they are usually exposed through `template.header_schema` or a similar schema payload, and the UI resolves them with binding metadata.

### What global fields are for

Use global fields for:
- common business identifiers,
- member selectors,
- vendor selectors,
- cost center selectors,
- account selectors,
- tax rate selectors,
- other reusable org-wide values.

### How global fields appear in JSON

The launch schema may use standard JSON Schema or a flat schema shape.
The important pieces are:
- `properties`
- `required`
- `x-binding`
- `label`
- `type`
- optional `options`, `choices`, or similar option metadata

Example:

```json
{
  "type": "object",
  "properties": {
    "vendor_id": {
      "type": "string",
      "label": "Vendor",
      "x-binding": "vendor_id",
      "display_ref": "vendor_id"
    },
    "cost_center_id": {
      "type": "string",
      "label": "Cost Center",
      "x-binding": "cost_center_id",
      "display_ref": "cost_center_id"
    },
    "amount": {
      "type": "number",
      "label": "Amount"
    }
  },
  "required": ["vendor_id", "amount"]
}
```

### How the code uses global fields

- `header_schema.required` defines required launch inputs.
- `x-binding` lets the data resolver map a semantic name to a schema field.
- `display_ref` helps the UI show the right linked entity or identifier.
- `options` / `choices` support select-like rendering.
- fields with `formula` are excluded from launch-required validation.

### Recommended global field rules

- use `x-binding` for semantic lookup keys,
- use stable names like `vendor_id`, `member_id`, `cost_center_id`, `account_id`,
- prefer entity-backed fields over free text when the organization already has structured data,
- keep required launch fields aligned between `header_schema.required` and the UI.

### Mapping examples

Prefer these mappings when the entity exists:
- employee-like value -> member/global field (employe),
- vendor-like value -> vendor/global field (vendors),
- cost center -> cost center/global field (cost_center),
- account -> account/global field,
- bank country code -> bank country code/global field (bank_country_code),
- task or process priority -> priority/global field (priority).

If the entity exists in org context, do not downgrade it to plain text unless there is a strong reason.


## Pipeline Blueprints

`blueprints` are global, pre-configured workflow templates (e.g., standard Hiring Process, Bill Approval, Procurement) that serve as a foundation for generating organization-specific pipelines.

### How to use blueprints as a baseline:

1. **Discover blueprints**: Call `list_blueprints()` to find an existing blueprint matching the required business process.
2. **Fetch baseline configuration**: Call `get_blueprint(blueprint_id)` to retrieve the full baseline `pipeline_config` JSON.
3. **Customize the baseline**:
   - Do **NOT** invent UUIDs for members or fields.
   - Fetch the active custom fields for the current organization using `list_custom_fields()` and replace generic inputs with the correct `custom_field_id` (e.g., matching the `department` or `priority` field).
   - Fetch real workgroups and members using `list_workgroups()` and `list_members()` to map assignment targets in approval/task nodes.
4. **Instantiate directly**: Alternatively, call `instantiate_blueprint(blueprint_id)` to automatically create a template copy in the organization, then use `update_pipeline` for post-instantiation adjustments.


## Validation Rules

The engine and related services validate several things before a pipeline is accepted or launched.

### Structural validation

Check:
- pipeline has nodes,
- `start_node_id` exists,
- `start_node_id` points to a real node,
- transitions point to real nodes,
- non-terminal nodes have outgoing transitions,
- there is at least one `end` node or terminal path.

### Node-specific validation

- `collect_input`: fields list should not be empty when the node is meant to collect data.
- `approval_request`: `assignment_target` must exist.
- `task`: `assignment_target` must exist, and `actions` array is **mandatory** (no default actions).
- `condition`: must have both `true` and `false` transitions, and must use single `field`/`operator`/`value` (or `expression`) config (not `conditions[]` array).
- `action`: `action_type` must be one of `"mutate_context"`, `"http_request"`, or `"integration"`. If `action_type: "integration"`, `provider` and `action` must be present (and if `provider: "colba"` / `action: "create_document"`, `document_type` must be present).
- `llm_request`: prompt source-specific requirements must be present. If `output_enum` is used, `path` and `values` must be present, and `on_error`/`error_transition_key` should be configured for graceful handling.
- `escalations` (on `approval_request`/`task`): each entry must have `wait_minutes` and a valid `action` (`"notify"`, `"substitute"`, `"transition"`). Entries with `"transition"` must specify a `transition_key` matching an actual transition on the node.
- `outbound_webhook`: `url` must be present.

### Launch validation

Launch-time checks are driven by `header_schema` and any required fields it defines.

Rules:
- for API-triggered pipelines, `header_schema` must be present and non-empty whenever the launch accepts payload fields; it must describe every accepted field.
- Every field accepted in the initial payload must be declared in `header_schema.properties`; otherwise launch validation returns `unknown_field` / `INPUT_VALIDATION_FAILED`.
- If the start node is `collect_input`, its `config.fields` must be copied into the root `header_schema` (including names, types, and required status); defining them only inside a later `task` does not make them launchable.
- If the start node is not `collect_input`, validate its own node-specific launch contract. Do not add `collect_input.config.fields` or require form fields unless the pipeline explicitly accepts them.
- In all cases, `start_node_id` must be the actual node `id`; the start node type determines which node-specific launch rules apply.
- required launch fields must be present in the payload,
- calculated fields with `formula` are skipped from the required-input check,
- missing required fields should fail early with a clear error,
- keep launch schema and collect-input schema consistent.

## Editor And JSON Round-Trip Rules

The visual editor and the JSON output should round-trip cleanly.

Rules:
- node IDs must stay stable,
- transitions must remain keyed by action IDs or branch names,
- positions should be preserved when the JSON came from the visual editor,
- if the user changes an action ID, transition keys should be updated to match,
- if a node has actions, make sure transitions exist for the relevant action IDs.

## Best Practices

1. Use readable semantic IDs such as `vendor_form`, `manager_approval`, `send_to_erp`.
2. Keep the graph executable: every meaningful branch must resolve to a real target.
3. Put required values in `collect_input` only when they are truly needed downstream.
4. Use formulas for derivation, not for business policy.
5. Use `global_fields` for reusable organization-level bindings and entity selectors.
6. Prefer entity-backed fields over free text for member, vendor, account, and cost-center concepts.
7. Keep transition keys aligned with button/action IDs.
8. Always include an `end` path for success and, when useful, a failure or rejection path too.
9. **AI Agent Rule - Standard Entity Templates**: When generating pipelines for standard entities (bills `Bill`, invoices `Invoice`, RFQs `RFQ`, purchase orders `PO`, quotes `Quote`, receipts `Receipt`), the AI agent **MUST use the predefined fields from entity templates** (using exact system key names, types, and `x-binding` from the mapping tables above). Specifically:
   - If there are fields for entities in `global_fields` (for example, employee `employe` / `employee`, counterparty/vendor `vendors`, cost center `cost_center`, department `department`, location `location`, job title `job_title`, access role `role`, currency `currency`, tax rate `tax_rate`, bank country code `bank_country_code`, task/process priority `priority`), the AI agent MUST bind them as global fields, setting `custom_field_id` to the ID of the corresponding global field from context. Any reference to `employee`, `employee_id`, `employe_id`, or `member_id` must be bound to the `employe` global field. Priority and bank country code must similarly be mapped to `priority` and `bank_country_code` global fields respectively when used.
   - The AI agent MUST include a line items table named strictly `line_items` (type `array` / Table) in the input form for documents that have detailed line items (especially `Bill`, `Invoice`, `PO`, `Quote`, `Receipt`, `RFQ`).


## Common Patterns

### Simple approval flow

`collect_input` -> `approval_request` -> `end`

### Amount-based branching

`collect_input` -> `condition` -> `approval_request` or `end`

### Vendor creation flow

`collect_input` -> `create_vendor` -> `approval_request` -> `end`

### LLM-assisted flow

`collect_input` -> `llm_request` -> `task` or `approval_request` -> `end`

## Complete Multi-Step Example

This example combines a form, formula-derived totals, threshold routing, approval, document creation, webhook notification, and terminal states.

```json
{
  "prefix": "INV",
  "start_node_id": "invoice_input",
  "nodes": [
    {
      "id": "invoice_input",
      "name": "Invoice Input",
      "type": "collect_input",
      "config": {
        "label": "Invoice Data",
        "fields": [
          {
            "name": "vendor_name",
            "type": "string",
            "label": "Vendor Name",
            "required": true
          },
          {
            "name": "currency",
            "type": "select",
            "label": "Currency",
            "required": true,
            "options": {
              "choices": [
                { "value": "USD", "label": "USD" },
                { "value": "EUR", "label": "EUR" }
              ]
            }
          },
          {
            "name": "line_items",
            "type": "array",
            "label": "Line Items",
            "required": true,
            "columns": [
              { "name": "description", "type": "string", "label": "Description" },
              { "name": "qty", "type": "number", "label": "Qty" },
              { "name": "unit_price", "type": "number", "label": "Unit Price" },
              { "name": "tax", "type": "number", "label": "Tax %" },
              { "name": "row_total", "type": "number", "label": "Row Total", "formula": "qty * unit_price * (1 + tax / 100)" }
            ]
          },
          {
            "name": "total_amount",
            "type": "number",
            "label": "Total Amount",
            "formula": "sum(line_items.row_total)"
          }
        ]
      },
      "transitions": {
        "default": "amount_gate"
      }
    },
    {
      "id": "amount_gate",
      "name": "High Value Invoice?",
      "type": "condition",
      "config": {
        "field": "total_amount",
        "operator": ">",
        "value": 100000
      },
      "transitions": {
        "true": "manager_approval",
        "false": "create_invoice"
      }
    },
    {
      "id": "manager_approval",
      "name": "Manager Approval",
      "type": "approval_request",
      "config": {
        "strategy": "any",
        "assignment_target": {
          "type": "manager",
          "of_member_id": "initiator"
        },
        "actions": [
          { "id": "approved", "label": "Approve", "style": "success" },
          { "id": "rejected", "label": "Reject", "style": "danger" }
        ]
      },
      "transitions": {
        "approved": "create_invoice",
        "rejected": "end_rejected"
      }
    },
    {
      "id": "create_invoice",
      "name": "Create Invoice",
      "type": "create_invoice",
      "config": {
        "document_type": "INVOICE",
        "result_key": "invoice_id"
      },
      "transitions": {
        "default": "notify_erp"
      }
    },
    {
      "id": "notify_erp",
      "name": "Notify ERP",
      "type": "outbound_webhook",
      "config": {
        "url": "https://api.example.com/invoices",
        "method": "POST",
        "payload_mapping": {
          "invoice_id": "step_results.invoice_id",
          "vendor_name": "initial_payload.vendor_name",
          "total_amount": "initial_payload.total_amount",
          "currency": "initial_payload.currency",
          "organization_id": "metadata.organization_id"
        }
      },
      "transitions": {
        "default": "end_done"
      }
    },
    {
      "id": "end_done",
      "name": "Done",
      "type": "end"
    },
    {
      "id": "end_rejected",
      "name": "Rejected",
      "type": "end"
    }
  ]
}
```

Why this is a good generation reference:
- `collect_input` owns user-entered data and formulas.
- `condition` routes only on a computed field that exists in context.
- `approval_request` has explicit assignment, strategy, actions, and matching transitions.
- `create_invoice` stores `invoice_id` under a predictable key.
- `outbound_webhook` uses explicit `payload_mapping` paths.
- every branch terminates in an `end` node.

## Output Checklist

Before returning or saving the JSON, verify:
- `nodes` is not empty,
- `start_node_id` is present and valid,
- `start_node_id` exactly equals the `id` of an existing node,
- for a public form, the `form_start` node's `id` is exactly
  `start_node_id` (compare literal strings after any persistence round-trip),
- all newly generated public-form node IDs, `start_node_id`, and transition
  targets use the same UUID-based ID space; semantic aliases are kept only in
  `semantic_id`,
- if the start node is `collect_input`, `header_schema.properties` and its `config.fields` describe the same launch fields,
- if the start node is `form_start`, `header_schema.properties`,
  `form_start.config.fields`, and `required_fields` describe the same public
  form contract,
- if the start node is another type, its node-specific required inputs are valid and no unneeded `collect_input` fields/schema are generated,
- every non-terminal node has at least one transition,
- every `approval_request` and `task` has `assignment_target`,
- `task` nodes have explicit `actions` defined,
- legacy typed nodes (`create_po`, `create_invoice`, `create_vendor`, `outbound_integration`) are NOT used for new pipelines; `action` with `action_type` is used instead,
- `action` nodes with `action_type: "integration"` specify `provider`, `action`, and `document_type`,
- every `condition` has `true` and `false` transitions and uses valid config fields (no `conditions[]` arrays),
- every `llm_request` has the right prompt source fields, and if `output_enum` is present, it specifies `path` and `values` with error handling,
- `escalations` on `approval_request`/`task` nodes specify positive `wait_minutes` and valid `action` types (`notify`/`substitute`/`transition`),
- every required form field is actually available at launch or input time,
- `line_items` fields use `type: "array"`,
- every formula only references fields that exist in scope,
- `header_schema.required` matches the truly required launch fields,
- global/entity fields are used when an organization-level selector exists,
- the JSON is syntactically valid and round-trips through the editor.

## Why This Matters

The workflow engine does not just store JSON. It executes it, validates it, and uses it to derive forms, approvals, notifications, and launch checks.

If the JSON is vague, the runtime becomes vague.
If the JSON is explicit, the runtime becomes predictable.
## Agent execution and safety contract

Agents must follow this order for generated or changed pipelines:

1. Discover organization entities with `get_organization_context`.
2. Draft and normalize the pipeline.
3. Call `validate_pipeline_schema` and resolve every assignment target from the discovered context.
4. Call `preview_pipeline_changes`; do not apply a draft with validation errors.
5. For conditional graphs, call `verify_expected_route` with representative `sample_input` for each branch.
6. Submit mutations through HITL and wait for the approval to be resolved before reporting success.
7. Record the final verification result and report partial or failed execution explicitly.

Dynamic form fields use `visible_if`, `required_if`, `forbidden_if`, and `required_together`.
Their `field` references must point to fields in the same launch form; hidden fields must not be
submitted. Numeric boundaries use `validation.min`/`validation.max`, including zero, and invalid
regular expressions are pipeline errors rather than silently ignored.

`assignment_target` is a typed object: `individual`, `role`, `workgroup`, `department`, `location`,
or `manager`. Business roles are job titles, not access roles. Use `get_pending_approvals` to find
the `/mcp-approve?approval_id=...` review path. A mutation is complete only after approval execution
and post-apply verification succeed.

### Agent run and event log

Every multi-step agent change must start with `start_agent_run`. Keep the returned `run_id` and use
the agent-run tools throughout the operation:

- `record_agent_context` after discovery;
- `record_agent_draft` after normalization and schema validation;
- `record_agent_approval` for every HITL approval ID;
- `record_agent_mutation` after each attempted mutation, including failed attempts;
- `record_agent_verification` exactly once after post-apply checks;
- `get_agent_run_events` when an operator needs the immutable audit history.

Agent events are append-only and tenant scoped. Common secret-bearing keys are redacted, event data
is limited to 64 KiB, discovery context to 512 KiB, pipeline drafts to 1 MiB, and verification data
to 256 KiB. Do not place credentials or personal secrets in assumptions, goals, or free-form fields.

The legal state flow is:

```text
planning -> discover -> model -> draft -> validate -> preview -> hitl
         -> awaiting_approval -> applying -> verifying -> report -> completed
```

`awaiting_clarification`, `failed`, and `partially_completed` are explicit alternate states. A failed
run may restart at `planning`; completed and partially completed runs are terminal.

### Dynamic field rule examples

```json
{
  "name": "custom_finish",
  "type": "string",
  "visible_if": {"field": "finish_type", "equals": "custom"},
  "required_if": {"field": "finish_type", "equals": "custom"},
  "forbidden_if": {"field": "finish_type", "not_equals": "custom"},
  "required_together": ["custom_finish_code"],
  "validation": {"pattern": "^[A-Z0-9-]{1,30}$"}
}
```

Every referenced field must exist in the same launch form. Numeric `min` and `max` must be numbers,
`min` cannot exceed `max`, regular expressions may contain at most 500 characters, and nested
quantifiers such as `(a+)+` are rejected.

### Route verification sample contract

For simple conditions, `sample_input` may be the initial payload directly. For pipelines that depend
on previous form submissions or human action buttons, use the expanded form:

```json
{
  "initial_payload": {"amount": 1200},
  "step_results": {
    "review_form": {"submitted_data": {"risk": "high"}}
  },
  "_transitions": {
    "approval_node_id": "approved",
    "revision_node_id": ["revise", "approved"]
  }
}
```

`_transitions` supplies action keys for nodes with multiple outgoing actions. Arrays provide choices
for repeated loopback visits. Route verification uses the same condition evaluator as runtime and is
bounded to 200 steps.

### Approval execution failures

`execution_failed` is not equivalent to successful approval. The approval record includes a sanitized
error code, attempt count, and `execution_retryable`. Only transient server failures are retryable,
with at most three execution attempts. Validation and permission failures are terminal and require a
corrected mutation rather than replaying the same approval.
