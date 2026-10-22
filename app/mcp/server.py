# ============================================================
# app/mcp/server.py
# Outbound MCP Server [DT §6.6 / mục 24]:
# Đóng gói 7 stage thành tool execute_universal_orchestrator
# (query, domain_tag, raw_sources, parameter_bounds) qua Streamable HTTP.
# ============================================================
import json
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, Response

from app.core.blackboard import SharedBlackboard
from app.core.smart_key_router import SmartKeyPoolRouter
from app.orchestrator.universal_pipeline import UniversalCoScientistPipeline

MCP_PROTOCOL_VERSION = "2025-03-26"

TOOL_DEFINITION = {
    "name": "execute_universal_orchestrator",
    "description": (
        "Chạy toàn bộ 7 stage của Universal Neuro-Symbolic Orchestrator "
        "(ingest -> workers -> proximity -> dual-critic + prolog -> Elo -> sandbox -> Pareto export)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Yêu cầu bài toán bằng tiếng Việt"},
            "domain_tag": {"type": "string", "description": "Nhãn miền, ví dụ macro_econ, distributed_sys"},
            "raw_sources": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Dữ liệu thô đầu vào (list dict)",
            },
            "parameter_bounds": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Danh sách MetricBoundSpec bổ sung cho Sandbox",
            },
        },
        "required": ["query", "domain_tag", "raw_sources"],
    },
}


def _default_pipeline_factory() -> UniversalCoScientistPipeline:
    router = SmartKeyPoolRouter()
    blackboard = SharedBlackboard(task_id="mcp_session")
    return UniversalCoScientistPipeline(router, blackboard)


class UniversalOrchestratorMCPHandler:
    def __init__(self, pipeline_factory: Optional[Callable[[], UniversalCoScientistPipeline]] = None):
        self.pipeline_factory = pipeline_factory or _default_pipeline_factory

    async def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """JSON-RPC 2.0 dispatch cho Streamable HTTP transport."""
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params", {}) or {}

        # Notification (không cần phản hồi)
        if msg_id is None:
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": params.get("protocolVersion", MCP_PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "osintviet-universal-orchestrator", "version": "2.0"},
            })

        if method == "ping":
            return self._result(msg_id, {})

        if method == "tools/list":
            return self._result(msg_id, {"tools": [TOOL_DEFINITION]})

        if method == "tools/call":
            tool_name = params.get("name")
            if tool_name != "execute_universal_orchestrator":
                return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")
            arguments = params.get("arguments", {}) or {}
            try:
                pipeline = self.pipeline_factory()
                try:
                    output = await pipeline.run(
                        query=arguments.get("query", ""),
                        domain_tag=arguments.get("domain_tag", "general"),
                        raw_sources=arguments.get("raw_sources", []) or [],
                        parameter_bounds=arguments.get("parameter_bounds", []) or [],
                    )
                finally:
                    router = getattr(pipeline, "router", None)
                    if router is not None and hasattr(router, "close"):
                        await router.close()
                return self._result(msg_id, {
                    "content": [{"type": "text", "text": output.model_dump_json()}],
                    "structuredContent": json.loads(output.model_dump_json()),
                    "isError": False,
                })
            except Exception as e:
                return self._result(msg_id, {
                    "content": [{"type": "text", "text": f"Lỗi orchestrator: {e}"}],
                    "isError": True,
                })

        return self._error(msg_id, -32601, f"Method not found: {method}")

    @staticmethod
    def _result(msg_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def create_mcp_router(
    pipeline_factory: Optional[Callable[[], UniversalCoScientistPipeline]] = None,
) -> APIRouter:
    """Trả về APIRouter để mount vào FastAPI app (Streamable HTTP tại POST /mcp)."""
    handler = UniversalOrchestratorMCPHandler(pipeline_factory)
    router = APIRouter()

    @router.post("/mcp")
    async def mcp_endpoint(request: Request, body: Dict[str, Any] = Body(...)):
        responses = []
        messages = body if isinstance(body, list) else [body]
        for msg in messages:
            response = await handler.handle(msg)
            if response is not None:
                responses.append(response)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses[0] if isinstance(body, dict) else responses)

    @router.get("/mcp")
    async def mcp_info():
        return {
            "server": "osintviet-universal-orchestrator",
            "transport": "streamable-http",
            "endpoint": "/mcp",
            "tool": TOOL_DEFINITION["name"],
        }

    return router
