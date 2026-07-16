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
