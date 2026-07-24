import re
import httpx
import logging
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
            elif response.status_code == 401:
                raise Exception("Authentication failed. Invalid COLBA_TOKEN.")
            else:
                raise Exception(
                    f"Failed to resolve organization context. Status code: {response.status_code}"
                )
        except httpx.HTTPError:
            # Do NOT include the URL in the message — it may contain auth details
            raise Exception("Failed to contact Colba API to resolve organization context.")

    async def list_pipelines(self) -> List[Dict[str, Any]]:
        response = await self.client.get("/api/v1/templates")
        response.raise_for_status()
        return response.json()

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
        self, template_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        validate_uuid(template_id, "template_id")
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
        response = await self.client.post(
            f"/api/v1/workflow/start/{template_id}",
            json=payload,
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
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
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
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
        
        payload: Dict[str, Any] = {"name": name, "type": type}
        if parent_id:
            validate_uuid(parent_id, "parent_id")
            payload["parent_id"] = parent_id
        if key:
            payload["key"] = key

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
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
        response = await self.client.delete(
            f"/api/v1/directory/workgroups/{workgroup_id}",
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
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
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
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
        response = await self.client.delete(
            f"/api/v1/workflow/fields/{field_id}",
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def create_vendor(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
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
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
        response = await self.client.delete(
            f"/api/v1/accounting/vendors/{vendor_id}",
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def archive_pipeline(self, template_id: str) -> Dict[str, Any]:
        validate_uuid(template_id, "template_id")
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
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
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id
            
        response = await self.client.put(
            f"/api/v1/templates/{template_id}",
            json=payload,
            headers=headers
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
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        await self._ensure_org_id()
        headers = {}
        if self.org_id:
            headers["X-Organization-ID"] = self.org_id

        payload = {
            "name": name,
            "description": description or "",
            "pipeline_config": pipeline_config,
        }
        response = await self.client.post(
            "/api/v1/templates/",
            json=payload,
            headers=headers
        )
        response.raise_for_status()
        return response.json()


