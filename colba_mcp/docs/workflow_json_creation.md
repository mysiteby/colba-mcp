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
- `prefix`: short human-readable prefix for generated process numbers,
- `start_node_id`: the entry node,
- `is_client_enabled`: optional client portal flag,
- `nodes`: array of node objects.

Example:

```json
{
  "prefix": "PRC",
  "start_node_id": "start_1",
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
- `is_client_enabled`: optional boolean for client portal flows.
- `nodes`: required array of node definitions.

### Node fields

Every node can use:
- `id`: unique node identifier, preferably semantic and readable.
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
- `approval_request`
- `task`
- `condition`
- `conditional`
- `action` (including generic integration actions like `create_document`)
- `outbound_webhook`
- `outbound_integration` (legacy)
- `llm_request`
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

Common config fields:
- `fields`
- `required_fields`
- `recipient_type`
- `recipient_member_id`
- `form_id`
- `label`

### Field types for `fields[]`

Supported field types used by the API/schema layer:

| Type | Use for | Notes |
| :--- | :--- | :--- |
| `string` | Short text values | Use `format: "email"` for email-like strings when useful. |
| `number` | Numeric values | Runtime validates numeric submissions with `float(...)`. |
| `boolean` | True/false values | Stored in schema as boolean. |
| `date` | Date or datetime values | Formula functions expect ISO-like date strings. |
| `select` | One option from a list/source | Use `options.source` for entity-backed values, or `options.choices/options`. |
| `array` | Tables / line items | Use `columns[]`; row formulas are supported. |
| `file` | File upload fields | If `options.multiple: true`, submitted value must be an array. |

Observed UI-friendly aliases:
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
- `options`: select/file/source metadata.
- `columns`: table/array column definitions.
- `analytics`: when true, copies submitted value into analytics output.
- `custom_field_id`: UI/editor reference to an imported global/custom field; not required by the core engine.
- `x-binding`: семантический тег связывания (semantic binding tag), например, `vendor_id`, `total_amount`, `due_date`. Используется для автоматического сопоставления полей ввода с атрибутами финансовых документов в нодах автоматизации.

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
| `role` | `id` | Assign to members with this organization role. |
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
{ "type": "role", "id": "admin" }
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

Config fields:
- `url`: target URL (supports placeholders)
- `method`: HTTP verb (`"GET"`, `"POST"`, `"PUT"`, `"DELETE"`)
- `headers`: headers dictionary (supports secrets placeholders like `{{secrets.API_KEY}}` to prevent committing credentials in JSON configs)
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
      "Authorization": "Bearer {{secrets.HUBSPOT_API_KEY}}",
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

Used to route inputs to pre-registered domain operations (`colba.*`) or external ERP adapters (`quickbooks`, `xero`, `softledger`).

Config fields:
- `provider`: integration provider (`"colba"`, `"quickbooks"`, `"xero"`, `"softledger"`)
- `action`: provider-specific method (e.g., `"create_vendor"`, `"create_bill"`, `"post_bill"`)
- `inputs`: inputs mapped from the context to the adapter requirements
- `outputs`: output mapping from integration results back to target context paths

Supported integrations:
- **`provider: "colba"`**:
  - `action: "create_vendor"` (creates database vendor record; maps to `CreateVendorHandler`)
  - `action: "create_document"` (generic financial document creator; maps to `CreateFinancialDocumentHandler`. Requires `document_type` and supports semantic auto-binding).
  - Shortcut/legacy actions: `create_po`, `create_invoice`, `create_bill`, `create_rfq`, `create_quote`, `create_receipt` (each maps to `CreateFinancialDocumentHandler` and implicitly sets the document type).
- **`provider: "quickbooks"`**:
  - `action: "create_bill"`
  - `action: "create_purchase_order"`
- **`provider: "xero"` & `provider: "softledger"`**:
  - `action: "post_bill"`
  - `action: "post_purchase_order"`

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
- `{{secrets.KEY_NAME}}` looks up `KEY_NAME` in environment variables.
- `{{metadata.key}}` looks up `key` in `context.metadata`.
- `{{initial_payload.key}}` looks up `key` in `context.initial_payload`.
- `{{step_results.node_id.key}}` looks up nested keys in step results.

### `outbound_webhook` and `outbound_integration`

Use these to send data outside the workflow engine.

Common config fields:
- `url`
- `method`
- `payload_mapping`

Rules:
- `url` is required,
- `method` defaults to `POST`,
- prefer explicit payload mappings and stable field names,
- `outbound_webhook` sends a direct HTTP request to an arbitrary URL,
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
- employee-like value -> member/global field,
- vendor-like value -> vendor/global field,
- cost center -> cost center/global field,
- account -> account/global field.

If the entity exists in org context, do not downgrade it to plain text unless there is a strong reason.

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
9. **AI Agent Rule - Standard Entity Templates**: При генерации пайплайнов для работы со стандартными сущностями (счета `Bill`, инвойсы `Invoice`, запросы предложений `RFQ`, заказы `PO`, предложения `Quote`, чеки `Receipt`), ИИ-агент **должен использовать готовые поля из шаблонов сущностей** (используя точные системные имена ключей, типы и `x-binding` из таблицы разделов выше). При этом:
   - Если в `global_fields` присутствуют поля для сущностей (например, контрагент `vendor` / `vendor_id`, валюта `currency` / `currency_code`, центр затрат `cost_center`), ИИ-агент должен привязывать их как глобальные поля, проставляя `custom_field_id` соответствующего глобального поля из контекста.
   - ИИ-агент должен **обязательно включать табличную часть (позиции документа) `line_items`** (тип `array` / Table) в коллекте (форме ввода) для документов, которые имеют детальные позиции (особенно `Bill`, `Invoice`, `PO`, `Quote`, `Receipt`, `RFQ`). Системное имя этого поля должно быть строго `line_items`, а тип — `array`.


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
