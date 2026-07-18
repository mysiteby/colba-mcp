# Colba Model Context Protocol (MCP) Server

This server implements the Model Context Protocol (MCP) specification for the Colba workflow automation platform, enabling AI agents (e.g., Claude Desktop, Cursor, or custom autonomous agents) to interact with approval requests, processes, and business workflow creation directly on behalf of users.

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

### 1. Claude Desktop

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

### 2. Cursor

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

### 3. `list_processes`
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

### 8. `create_pipeline`
*Create a new workflow pipeline template in Colba.*
* **Parameters**:
  - `name` (string): Template title (e.g., *"Procurement Invoice Approval"*).
  - `pipeline_config` (object): Valid pipeline JSON configuration matching `docs://skills/workflow_json_creation`.
  - `description` (string, optional): Human-readable summary.
* **Example prompt**: *"Create a new travel request pipeline template with manager approval and budget verification nodes"*

---

## 📚 Resources

### `docs://skills/workflow_json_creation`
The official specification and validation rules for creating pipeline JSON structures in Colba.
Includes node type hierarchies (prioritizing `action` with `action_type: "integration"`), `output_enum` validation, `escalations` policies, `condition` dotted-path syntax, form field types (`type: "array"` for line items), and validation checklists.

An external agent can fetch this resource via `read_resource` before generating a new pipeline JSON.

---

## 💬 Prompts

### `generate_pipeline_json`
System prompt template that automates instruction setup for an LLM agent.
* **Arguments**:
  - `user_requirements`: Textual description of desired business process requirements.
* **Output**: Loads the full specification `docs://skills/workflow_json_creation` and formats a strict generation prompt for the LLM.
