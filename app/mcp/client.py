# ============================================================
# app/mcp/client.py
# Inbound MCP Client [DT §6.6 / mục 24]:
# Giữ kết nối tới các MCP Server để cung cấp tool cho Workers khi thử nghiệm.
# ============================================================
from typing import Any, Dict, List, Optional
import httpx


class MCPClient:
    def __init__(self, server_url: str, headers: Optional[Dict[str, str]] = None):
        self.server_url = server_url
        self.headers = headers or {}
        self._http = httpx.AsyncClient(timeout=120.0, headers=self.headers)
        self._next_id = 0

    async def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        res = await self._http.post(self.server_url, json=payload)
        res.raise_for_status()
        data = res.json()
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data.get("result", {})

    async def initialize(self) -> Dict[str, Any]:
        result = await self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "osintviet-orchestrator", "version": "2.0"},
        })
        # notification initialized (không id)
        self._next_id += 1
        await self._http.post(self.server_url, json={
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {}
        })
        return result

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self._rpc("tools/list")
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self._rpc("tools/call", {"name": name, "arguments": arguments})

    async def close(self):
        await self._http.aclose()
