# Colba MCP Server (Git-native)

This repository contains the Model Context Protocol (MCP) server for interacting with the [Colba](https://app.colba.pl) API.

AI agents (such as Claude Desktop or Cursor) can run this server directly from GitHub without needing to clone or configure the repository manually.

---

## 🖥️ Configuration & Quick Start

### 1. Claude Desktop

Edit your Claude Desktop configuration file:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add the following entry under the `mcpServers` object:

```json
{
  "mcpServers": {
    "colba": {
      "command": "uv",
      "args": [
        "run",
        "--quiet",
        "git+ssh://git@github.com/mysiteby/colba-mcp.git"
      ],
      "env": {
        "COLBA_API_URL": "https://app.colba.pl",
        "COLBA_TOKEN": "tk_live_YOUR_PERSONAL_TOKEN"
      }
    }
  }
}
```

> **Note**: You can generate your `COLBA_TOKEN` in the Colba UI dashboard under **Settings → MCP Agent Integration**.

---

### 2. Cursor

1. Go to **Settings > Features > MCP**.
2. Click the **+ Add New MCP Server** button.
3. Fill in the parameters:
   - **Name**: `colba`
   - **Type**: `command`
   - **Command**:
     ```bash
     uv run git+ssh://git@github.com/mysiteby/colba-mcp.git
     ```
4. Add the following environment variables:
   - `COLBA_API_URL` = `https://app.colba.pl`
   - `COLBA_TOKEN` = `tk_live_YOUR_PERSONAL_TOKEN`

---

## 🛠️ Available Tools

The server exposes the following tools to the AI agent:
* `list_pipelines` — List workflow templates available to start.
* `start_process` — Start a new workflow process under a template.
* `list_processes` — Query processes in your organization (paginated).
* `list_pending_requests` — List active approval tasks assigned to you.
* `get_process_details` — Fetch detailed status and context for a process.
* `get_request_details` — Retrieve detailed request info, options, and actions.
* `submit_decision` — Approve, reject, or perform custom actions on a request.
