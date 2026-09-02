import re
import uuid
import httpx
import logging
from urllib.parse import quote
from typing import Optional, Any, Dict, List

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def validate_uuid(value: str, name: str) -> None:
    """Raise ValueError if *value* is not a canonical UUID string."""
    if not _UUID_RE.match(value):
        raise ValueError(f"'{name}' must be a valid UUID (got: {value!r})")


class ColbaClient:
    def __init__(self, api_url: str, token: str):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.org_id: Optional[str] = None
        self.client = httpx.AsyncClient(
            base_url=self.api_url,
            headers={"X-API-Key": self.token},
            timeout=15.0,
            follow_redirects=True,
        )

    async def close(self):
        await self.client.aclose()

    async def _ensure_org_id(self):
        if self.org_id:
            return
        url = "/api/v1/directory/me/organizations"
        try:
            response = await self.client.get(url)
            if response.status_code == 200:
                orgs = response.json()
                if orgs and isinstance(orgs, list) and len(orgs) > 0:
                    self.org_id = orgs[0]["id"]
                    logger.info("Resolved organization ID for MCP session")
                else:
                    raise Exception("No active organization found for this member.")
            else:
                # Raise HTTPStatusError so handle_mcp_call maps 401→unauthorized, 403→forbidden, etc.
                response.raise_for_status()
        except httpx.HTTPStatusError:
            raise
        except httpx.HTTPError:
            # Do NOT include the URL in the message — it may contain auth details
            raise Exception("Failed to contact Colba API to resolve organization context.")

    def _build_mutation_headers(
        self,
        tool_name: str,
        payload: Any = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
        # An idempotency key identifies one logical attempt, not the resource
        # contents. Reusing a content-derived key would replay a stale cached
        # response when the same mutation is intentionally performed later.
        request_key = idempotency_key or str(uuid.uuid4())
        validate_uuid(request_key, "idempotency_key")
        headers["Idempotency-Key"] = request_key
        headers["X-Idempotency-Key"] = request_key
        headers["X-Colba-Tool"] = tool_name[:100]
        return headers


    async def list_pipelines(self, status: Optional[str] = None) -> List[Dict[str, Any]]:

        params = {}
        if status:
            params["status"] = status
        response = await self.client.get("/api/v1/templates", params=params)
        response.raise_for_status()
        return response.json()

    async def get_pipeline(self, template_id: str) -> Dict[str, Any]:
        validate_uuid(template_id, "template_id")
        await self._ensure_org_id()
        headers = {"X-Organization-ID": self.org_id} if self.org_id else {}
        response = await self.client.get(f"/api/v1/templates/{template_id}", headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Colba API returned an invalid pipeline object")
        return payload


    async def list_processes(
        self,
        status: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        # Validate UUID params before sending to backend
        if pipeline_id:
            validate_uuid(pipeline_id, "pipeline_id")

        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if pipeline_id:
            params["pipeline_id"] = pipeline_id

        response = await self.client.get("/api/v1/workflow/processes", params=params)
        response.raise_for_status()
        return response.json()

    async def list_pending_requests(
        self, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        params = {"status": "pending", "limit": limit, "offset": offset}
        response = await self.client.get("/api/v1/workflow/requests", params=params)
        response.raise_for_status()
        return response.json()

    async def get_process_details(self, process_id: str) -> Dict[str, Any]:
        validate_uuid(process_id, "process_id")
        response = await self.client.get(f"/api/v1/workflow/processes/{process_id}")
        response.raise_for_status()
        return response.json()

    async def get_request_details(self, request_id: str) -> Dict[str, Any]:
        validate_uuid(request_id, "request_id")
        response = await self.client.get(f"/api/v1/workflow/requests/{request_id}")
        response.raise_for_status()
        return response.json()

    async def start_process(
        self,
        template_id: str,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        validate_uuid(template_id, "template_id")
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
        # Process starts require an Idempotency-Key. Generate it once per tool
        # invocation so a transport retry can safely reuse the same request key.
        request_key = idempotency_key or str(uuid.uuid4())
        validate_uuid(request_key, "idempotency_key")
        headers["Idempotency-Key"] = request_key
        headers["X-Idempotency-Key"] = request_key
        response = await self.client.post(
            f"/api/v1/workflow/start/{template_id}",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def validate_process_input(
        self, template_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        validate_uuid(template_id, "template_id")
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
        response = await self.client.post(
            f"/api/v1/workflow/validate-input/{template_id}",
            json={"payload": payload},
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def submit_decision(
        self, request_id: str, status: str, comment: Optional[str] = None
    ) -> Dict[str, Any]:
        validate_uuid(request_id, "request_id")
        payload: Dict[str, Any] = {"status": status}
        if comment:
            payload["comment"] = comment
        response = await self.client.post(
            f"/api/v1/workflow/requests/{request_id}/decide",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def sync_directory(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        await self._ensure_org_id()
        headers = self._build_mutation_headers("sync_directory", data)
        response = await self.client.post(
            "/api/v1/directory/sync",
            json=data,
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def create_workgroup(
        self, name: str, type: str, parent_id: Optional[str] = None, key: Optional[str] = None
    ) -> Dict[str, Any]:
        await self._ensure_org_id()
        payload: Dict[str, Any] = {"name": name, "type": type}
        if parent_id:
            validate_uuid(parent_id, "parent_id")
            payload["parent_id"] = parent_id
        if key:
            payload["key"] = key

        headers = self._build_mutation_headers("create_workgroup", payload)
        response = await self.client.post(
            "/api/v1/directory/workgroups",
            json=payload,
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def delete_workgroup(self, workgroup_id: str) -> Dict[str, Any]:
        validate_uuid(workgroup_id, "workgroup_id")
        await self._ensure_org_id()
        headers = self._build_mutation_headers("delete_workgroup", {"id": workgroup_id})
        response = await self.client.delete(
            f"/api/v1/directory/workgroups/{workgroup_id}",
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def update_workgroup(
        self,
        workgroup_id: str,
        name: Optional[str] = None,
        type: Optional[str] = None,
        parent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        validate_uuid(workgroup_id, "workgroup_id")
        await self._ensure_org_id()
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if type is not None:
            payload["type"] = type
        if parent_id is not None:
            if parent_id != "":
                validate_uuid(parent_id, "parent_id")
                payload["parent_id"] = parent_id
            else:
                payload["parent_id"] = None

        headers = self._build_mutation_headers("update_workgroup", {"id": workgroup_id, **payload})
        response = await self.client.put(
            f"/api/v1/directory/workgroups/{workgroup_id}",
            json=payload,
            headers=headers
        )
        response.raise_for_status()
        return response.json()


    async def list_custom_fields(self) -> List[Dict[str, Any]]:
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
        response = await self.client.get(
            "/api/v1/workflow/fields",
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def create_custom_field(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_org_id()
        # Normalize list options to choices dictionary contract
        options = payload.get("options")
        if options is not None:
            normalized_options = {}
            if isinstance(options, list):
                choices = []
                for opt in options:
                    if isinstance(opt, dict):
                        choices.append(opt)
                    else:
                        choices.append({"value": str(opt), "label": str(opt)})
                normalized_options = {"choices": choices}
            elif isinstance(options, dict):
                normalized_options = options
            payload = {**payload, "options": normalized_options}

        headers = self._build_mutation_headers("create_custom_field", payload)
        response = await self.client.post(
            "/api/v1/workflow/fields",
            json=payload,
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def delete_custom_field(self, field_id: str) -> Dict[str, Any]:
        validate_uuid(field_id, "field_id")
        await self._ensure_org_id()
        headers = self._build_mutation_headers("delete_custom_field", {"id": field_id})
        response = await self.client.delete(
            f"/api/v1/workflow/fields/{field_id}",
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def create_vendor(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_org_id()
        headers = self._build_mutation_headers("create_vendor", payload)
        response = await self.client.post(
            "/api/v1/accounting/vendors",
            json=payload,
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def delete_vendor(self, vendor_id: str) -> Dict[str, Any]:
        validate_uuid(vendor_id, "vendor_id")
        await self._ensure_org_id()
        headers = self._build_mutation_headers("delete_vendor", {"id": vendor_id})
        response = await self.client.delete(
            f"/api/v1/accounting/vendors/{vendor_id}",
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def update_vendor(self, vendor_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        validate_uuid(vendor_id, "vendor_id")
        await self._ensure_org_id()
        headers = self._build_mutation_headers("update_vendor", {"id": vendor_id, **payload})
        response = await self.client.put(
            f"/api/v1/accounting/vendors/{vendor_id}",
            json=payload,
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def update_member(self, member_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        validate_uuid(member_id, "member_id")
        await self._ensure_org_id()
        headers = self._build_mutation_headers("update_member", {"id": member_id, **payload})
        response = await self.client.put(
            f"/api/v1/directory/members/{member_id}",
            json=payload,
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def list_job_titles(self) -> List[str]:
        """List organization job titles (business positions, not access roles)."""
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id

        response = await self.client.get(
            "/api/v1/directory/options",
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        titles = payload.get("job_titles", []) if isinstance(payload, dict) else []
        if not isinstance(titles, list):
            raise ValueError("Colba API returned an invalid job_titles list")
        return titles

    async def create_job_title(self, name: str) -> Dict[str, Any]:
        """Create an organization job title."""
        await self._ensure_org_id()
        payload = {"name": name}
        headers = self._build_mutation_headers("create_job_title", payload)
        response = await self.client.post(
            "/api/v1/directory/job-titles",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def update_job_title(self, current_name: str, new_name: str) -> Dict[str, Any]:
        """Rename an organization job title and update assigned members."""
        await self._ensure_org_id()
        payload = {"name": new_name}
        headers = self._build_mutation_headers("update_job_title", {"job_title": current_name, "name": new_name})
        encoded_name = quote(current_name, safe="")
        response = await self.client.put(
            f"/api/v1/directory/job-titles/{encoded_name}",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def delete_job_title(self, name: str) -> Dict[str, Any]:
        """Delete an organization job title and clear it from assigned members."""
        await self._ensure_org_id()
        headers = self._build_mutation_headers("delete_job_title", {"job_title": name})
        encoded_name = quote(name, safe="")
        response = await self.client.delete(
            f"/api/v1/directory/job-titles/{encoded_name}",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def archive_pipeline(self, template_id: str) -> Dict[str, Any]:
        validate_uuid(template_id, "template_id")
        await self._ensure_org_id()
        headers = self._build_mutation_headers("archive_pipeline", {"id": template_id})
        response = await self.client.delete(
            f"/api/v1/templates/{template_id}",
            headers=headers
        )

        response.raise_for_status()
        if response.status_code == 204:
            return {"status": "archived"}
        return response.json()

    async def resolve_mcp_approval(
        self,
        action: str,
        approval_id: Optional[str] = None,
        token: Optional[str] = None,
        session_key: Optional[str] = None
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"action": action}
        if approval_id:
            validate_uuid(approval_id, "approval_id")
            payload["approval_id"] = approval_id
        if token:
            payload["token"] = token

        headers = {}
        await self._ensure_org_id()
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id

        auth_token = session_key or self.token
        client = httpx.AsyncClient(
            base_url=self.api_url,
            headers={"X-API-Key": auth_token},
            timeout=15.0,
            follow_redirects=True,
        )
        try:
            response = await client.post(
                "/api/v1/mcp/approvals/action",
                json=payload,
                headers=headers
            )
            response.raise_for_status()
            return response.json()
        finally:
            await client.aclose()

    async def list_members(self, query: Optional[str] = None) -> List[Dict[str, Any]]:
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
        
        params = {}
        if query:
            params["query"] = query
            
        response = await self.client.get(
            "/api/v1/directory/members",
            headers=headers,
            params=params
        )
        response.raise_for_status()
        return response.json()

    async def list_workgroups(self) -> List[Dict[str, Any]]:
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
        response = await self.client.get(
            "/api/v1/directory/tree",
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def list_vendors(self) -> List[Dict[str, Any]]:
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
        response = await self.client.get(
            "/api/v1/accounting/vendors",
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def update_pipeline(self, template_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        validate_uuid(template_id, "template_id")
        await self._ensure_org_id()
        headers = self._build_mutation_headers("update_workflow_template", {"id": template_id, **payload})
        response = await self.client.put(
            f"/api/v1/templates/{template_id}",
            json=payload,
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def set_pipeline_access(
        self,
        template_id: str,
        process_access: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Atomically update access rules without replacing the pipeline graph."""
        validate_uuid(template_id, "template_id")
        if not isinstance(process_access, dict) or not process_access:
            raise ValueError("process_access must contain at least one access rule")
        await self._ensure_org_id()
        payload = dict(process_access)
        headers = self._build_mutation_headers(
            "set_pipeline_access",
            {"id": template_id, "process_access": payload},
        )
        response = await self.client.patch(
            f"/api/v1/templates/{template_id}/access",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


    async def get_pipeline_embed(self, template_id: str) -> Dict[str, Any]:
        """Read public-form widget status and the ready-to-paste script tag."""
        validate_uuid(template_id, "template_id")
        await self._ensure_org_id()
        headers = {"X-Organization-ID": self.org_id} if self.org_id else {}
        response = await self.client.get(
            f"/api/v1/templates/{template_id}/embed",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def enable_pipeline_embed(self, template_id: str) -> Dict[str, Any]:
        validate_uuid(template_id, "template_id")
        await self._ensure_org_id()
        headers = {"X-Organization-ID": self.org_id} if self.org_id else {}
        response = await self.client.post(
            f"/api/v1/templates/{template_id}/embed",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def refresh_pipeline_embed(self, template_id: str) -> Dict[str, Any]:
        validate_uuid(template_id, "template_id")
        await self._ensure_org_id()
        headers = {"X-Organization-ID": self.org_id} if self.org_id else {}
        response = await self.client.post(
            f"/api/v1/templates/{template_id}/embed/refresh",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def disable_pipeline_embed(self, template_id: str) -> Dict[str, Any]:
        validate_uuid(template_id, "template_id")
        await self._ensure_org_id()
        headers = {"X-Organization-ID": self.org_id} if self.org_id else {}
        response = await self.client.post(
            f"/api/v1/templates/{template_id}/embed/disable",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def update_custom_field(self, field_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        validate_uuid(field_id, "field_id")
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
        options = payload.get("options")
        if options is not None:
            normalized_options = {}
            if isinstance(options, list):
                choices = []
                for opt in options:
                    if isinstance(opt, dict):
                        choices.append(opt)
                    else:
                        choices.append({"value": str(opt), "label": str(opt)})
                normalized_options = {"choices": choices}
            elif isinstance(options, dict):
                normalized_options = options
            payload = {**payload, "options": normalized_options}

        response = await self.client.put(
            f"/api/v1/workflow/fields/{field_id}",
            json=payload,
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def list_blueprints(self, category: Optional[str] = None, query: Optional[str] = None) -> List[Dict[str, Any]]:
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
        params = {}
        if category:
            params["category"] = category
        if query:
            params["query"] = query
            
        response = await self.client.get(
            "/api/v1/blueprints/",
            headers=headers,
            params=params
        )
        response.raise_for_status()
        return response.json()

    async def get_blueprint(self, blueprint_id: str) -> Dict[str, Any]:
        validate_uuid(blueprint_id, "blueprint_id")
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
        response = await self.client.get(
            f"/api/v1/blueprints/{blueprint_id}",
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def instantiate_blueprint(self, blueprint_id: str) -> Dict[str, Any]:
        validate_uuid(blueprint_id, "blueprint_id")
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
        response = await self.client.post(
            f"/api/v1/blueprints/{blueprint_id}/instantiate",
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def create_pipeline(
        self,
        name: str,
        pipeline_config: Dict[str, Any],
        description: Optional[str] = None,
        is_draft: Optional[bool] = None,
        is_active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id

        payload: Dict[str, Any] = {
            "name": name,
            "description": description or "",
            "pipeline_config": pipeline_config,
        }
        if is_draft is not None:
            payload["is_draft"] = is_draft
        if is_active is not None:
            payload["is_active"] = is_active

        headers = self._build_mutation_headers("create_workflow_template", payload)
        response = await self.client.post(
            "/api/v1/templates/",
            json=payload,
            headers=headers
        )

        response.raise_for_status()
        return response.json()


    async def list_mcp_approvals(self) -> List[Dict[str, Any]]:
        await self._ensure_org_id()
        headers: Dict[str, str] = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
        response = await self.client.get(
            "/api/v1/mcp/approvals/pending",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def get_compatible_batch_templates(self) -> List[Dict[str, Any]]:
        await self._ensure_org_id()
        headers: Dict[str, str] = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
        response = await self.client.get(
            "/api/v1/workflow/super-processes/compatible-templates",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def create_super_process(
        self,
        title: str,
        template_ids: List[str],
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._ensure_org_id()
        normalized_title = title.strip() if isinstance(title, str) else ""
        if not normalized_title or len(normalized_title) > 255:
            raise ValueError("'title' must contain 1-255 non-whitespace characters")
        if not isinstance(template_ids, list) or not 1 <= len(template_ids) <= 50:
            raise ValueError("'template_ids' must contain between 1 and 50 UUIDs")
        for tid in template_ids:
            validate_uuid(tid, "template_ids item")
        headers: Dict[str, str] = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
        key = (idempotency_key or f"mcp-super-{uuid.uuid4()}").strip()
        if not key or len(key) > 255:
            raise ValueError("'idempotency_key' must contain 1-255 characters")
        headers["Idempotency-Key"] = key

        response = await self.client.post(
            "/api/v1/workflow/super-processes",
            json={"title": normalized_title, "template_ids": template_ids},
            headers=headers,
            timeout=300.0,
        )
        response.raise_for_status()
        return response.json()

    async def list_super_processes(
        self,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._ensure_org_id()
        if not 1 <= limit <= 100:
            raise ValueError("'limit' must be between 1 and 100")
        if offset < 0:
            raise ValueError("'offset' must be non-negative")
        headers: Dict[str, str] = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        if status:
            params["status"] = status
        response = await self.client.get(
            "/api/v1/workflow/super-processes",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def create_agent_run(self, user_goal: str) -> Dict[str, Any]:
        await self._ensure_org_id()
        payload = {"user_goal": user_goal}
        response = await self.client.post(
            "/api/v1/agent/runs",
            json=payload,
            headers=self._build_mutation_headers("create_agent_run", payload),
        )
        response.raise_for_status()
        return response.json()

    async def get_agent_run(self, run_id: str) -> Dict[str, Any]:
        validate_uuid(run_id, "run_id")
        await self._ensure_org_id()
        response = await self.client.get(
            f"/api/v1/agent/runs/{run_id}",
            headers={"X-Organization-ID": self.org_id} if self.org_id else {},
        )
        response.raise_for_status()
        return response.json()

    async def list_agent_run_events(self, run_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        validate_uuid(run_id, "run_id")
        await self._ensure_org_id()
        response = await self.client.get(
            f"/api/v1/agent/runs/{run_id}/events",
            params={"limit": min(max(int(limit), 1), 500)},
            headers={"X-Organization-ID": self.org_id} if self.org_id else {},
        )
        response.raise_for_status()
        return response.json()

    async def update_agent_run(self, run_id: str, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        validate_uuid(run_id, "run_id")
        if action not in {"state", "context", "draft", "mutation", "approval", "verification"}:
            raise ValueError("Unsupported agent run action")
        await self._ensure_org_id()
        response = await self.client.post(
            f"/api/v1/agent/runs/{run_id}/{action}",
            json=payload,
            headers=self._build_mutation_headers(f"agent_run_{action}", {"run_id": run_id, **payload}),
        )
        response.raise_for_status()
        return response.json()

    async def get_super_process(self, super_process_id: str) -> Dict[str, Any]:
        validate_uuid(super_process_id, "super_process_id")
        await self._ensure_org_id()
        headers: Dict[str, str] = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
        response = await self.client.get(
            f"/api/v1/workflow/super-processes/{super_process_id}",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def list_schedules(
        self,
        template_id: Optional[str] = None,
        is_active: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        await self._ensure_org_id()
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if template_id:
            validate_uuid(template_id, "template_id")
            params["template_id"] = template_id
        if is_active is not None:
            params["is_active"] = is_active
        headers = {"X-Organization-ID": self.org_id} if self.org_id else {}
        response = await self.client.get("/api/v1/schedules", params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    async def get_schedule(self, schedule_id: str) -> Dict[str, Any]:
        validate_uuid(schedule_id, "schedule_id")
        await self._ensure_org_id()
        headers = {"X-Organization-ID": self.org_id} if self.org_id else {}
        response = await self.client.get(f"/api/v1/schedules/{schedule_id}", headers=headers)
        response.raise_for_status()
        return response.json()

    async def create_schedule(
        self,
        template_id: str,
        name: str,
        cron_expression: str,
        timezone: str = "UTC",
        payload: Optional[Dict[str, Any]] = None,
        concurrency_policy: str = "allow",
        description: Optional[str] = None,
        is_active: bool = True,
    ) -> Dict[str, Any]:
        validate_uuid(template_id, "template_id")
        await self._ensure_org_id()
        body = {
            "template_id": template_id,
            "name": name,
            "description": description,
            "cron_expression": cron_expression,
            "timezone": timezone,
            "payload": payload or {},
            "concurrency_policy": concurrency_policy,
            "is_active": is_active,
        }
        headers = self._build_mutation_headers("create_schedule", body)
        response = await self.client.post("/api/v1/schedules", json=body, headers=headers)
        response.raise_for_status()
        return response.json()

    async def update_schedule(
        self,
        schedule_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        cron_expression: Optional[str] = None,
        timezone: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        concurrency_policy: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        validate_uuid(schedule_id, "schedule_id")
        await self._ensure_org_id()
        body: Dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if cron_expression is not None:
            body["cron_expression"] = cron_expression
        if timezone is not None:
            body["timezone"] = timezone
        if payload is not None:
            body["payload"] = payload
        if concurrency_policy is not None:
            body["concurrency_policy"] = concurrency_policy
        if is_active is not None:
            body["is_active"] = is_active
        headers = self._build_mutation_headers("update_schedule", body)
        response = await self.client.put(f"/api/v1/schedules/{schedule_id}", json=body, headers=headers)
        response.raise_for_status()
        return response.json()

    async def toggle_schedule(self, schedule_id: str, is_active: bool) -> Dict[str, Any]:
        validate_uuid(schedule_id, "schedule_id")
        await self._ensure_org_id()
        body = {"is_active": is_active}
        headers = self._build_mutation_headers("toggle_schedule", body)
        response = await self.client.post(f"/api/v1/schedules/{schedule_id}/toggle", json=body, headers=headers)
        response.raise_for_status()
        return response.json()

    async def delete_schedule(self, schedule_id: str) -> bool:
        validate_uuid(schedule_id, "schedule_id")
        await self._ensure_org_id()
        headers = self._build_mutation_headers("delete_schedule", {"schedule_id": schedule_id})
        response = await self.client.delete(f"/api/v1/schedules/{schedule_id}", headers=headers)
        response.raise_for_status()
        return True

    async def trigger_schedule_run(self, schedule_id: str) -> Dict[str, Any]:
        validate_uuid(schedule_id, "schedule_id")
        await self._ensure_org_id()
        headers = self._build_mutation_headers("trigger_schedule_run", {"schedule_id": schedule_id})
        response = await self.client.post(f"/api/v1/schedules/{schedule_id}/run-now", headers=headers)
        response.raise_for_status()
        return response.json()

    async def get_schedule_runs(self, schedule_id: str, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        validate_uuid(schedule_id, "schedule_id")
        await self._ensure_org_id()
        params = {"limit": limit, "offset": offset}
        headers = {"X-Organization-ID": self.org_id} if self.org_id else {}
        response = await self.client.get(f"/api/v1/schedules/{schedule_id}/runs", params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    async def validate_schedule_cron(self, cron_expression: str, timezone: str = "UTC", count: int = 5) -> Dict[str, Any]:
        body = {"cron_expression": cron_expression, "timezone": timezone, "count": count}
        response = await self.client.post("/api/v1/schedules/validate-cron", json=body)
        response.raise_for_status()
        return response.json()

