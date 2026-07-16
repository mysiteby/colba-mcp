# Colba Model Context Protocol (MCP) Server

This Python package implements the Model Context Protocol (MCP) server for the Colba platform, enabling AI agents (like Claude Desktop or Cursor) to interact with approval workflows and processes directly on behalf of a member.

---

## 📦 Requirements

- **Python**: `>=3.10`
- **Dependencies**: `mcp`, `httpx`

Using the package manager `uv` is highly recommended for zero-install execution.

---

## ⚙️ Configuration

The server expects the following environment variables:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `COLBA_API_URL` | Base URL of the Colba REST API | `http://localhost:9000` |
| `COLBA_TOKEN` | Your member API key (`tk_live_...`) | *Required* |

---

## 🖥️ Client Integration

### 1. Claude Desktop
Add this to your `claude_desktop_config.json`:

```json
"colba": {
  "command": "uv",
  "args": [
    "run",
    "--quiet",
    "git+ssh://git@github.com/mysiteby/colba-mcp.git"
  ],
  "env": {
    "COLBA_API_URL": "https://app.colba.pl",
    "COLBA_TOKEN": "tk_live_..."
  }
}
```

### 2. Cursor
Add a new command MCP server in Cursor features:
* **Command**: `uv run git+ssh://git@github.com/mysiteby/colba-mcp.git`
* **Env Variables**:
  - `COLBA_API_URL`: `https://app.colba.pl`
  - `COLBA_TOKEN`: `tk_live_...`
