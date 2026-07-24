# Colba MCP Server Update Log & Changelog

This document tracks updates, new tools, and changes made to the Colba Model Context Protocol (MCP) server.

> [!IMPORTANT]
> **MCP Client Schema Reloading**:
> Since MCP clients (like Claude Desktop, Cursor, or peer agents) cache the tool definitions schema on connection, **you must restart your client (or reload the MCP server)** whenever new tools are added for them to appear in your available tools list.

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
