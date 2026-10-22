# ============================================================
# main.py — entrypoint CLI + MCP server cho Universal Orchestrator [AR §7.3]
# Cách dùng:
#   python main.py --query "..." --domain macro_econ
#   python main.py --query "..." --domain macro_econ --raw-file raw.json --bounds-file bounds.json
#   python main.py --serve-mcp --host 0.0.0.0 --port 8100
# ============================================================
import argparse
import asyncio
import json

from app.core.blackboard import SharedBlackboard
from app.core.smart_key_router import SmartKeyPoolRouter
from app.orchestrator.universal_pipeline import UniversalCoScientistPipeline
from app.plugins import load_plugins


async def run_once(query: str, domain_tag: str, raw_sources, parameter_bounds):
    load_plugins()
    router = SmartKeyPoolRouter()
    blackboard = SharedBlackboard(task_id="cli_session")
    pipeline = UniversalCoScientistPipeline(router, blackboard)
    try:
        output = await pipeline.run(
            query=query,
            domain_tag=domain_tag,
            raw_sources=raw_sources or [],
            parameter_bounds=parameter_bounds or [],
        )
        print(output.model_dump_json(indent=2))
        return output
    finally:
        await router.close()


def _load_json_file(path: str, default):
    if not path:
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def serve_mcp(host: str, port: int):
    import uvicorn
    from fastapi import FastAPI
    from app.mcp.server import create_mcp_router

    load_plugins()
    mcp_app = FastAPI(title="OSINTViet Universal Orchestrator MCP", version="2.0")
    mcp_app.include_router(create_mcp_router())
    uvicorn.run(mcp_app, host=host, port=port)


def main():
    parser = argparse.ArgumentParser(description="OSINTViet Universal Neuro-Symbolic Orchestrator v2.0")
    parser.add_argument("--query", help="Yêu cầu bài toán")
    parser.add_argument("--domain", default="general", help="domain_tag, ví dụ macro_econ")
    parser.add_argument("--raw-file", dest="raw_file", help="File JSON chứa list raw sources")
    parser.add_argument("--bounds-file", dest="bounds_file", help="File JSON chứa list MetricBoundSpec")
    parser.add_argument("--serve-mcp", action="store_true", help="Chạy Outbound MCP Server (Streamable HTTP)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()

    if args.serve_mcp:
        serve_mcp(args.host, args.port)
        return
    if not args.query:
        parser.error("--query là bắt buộc khi không chạy --serve-mcp")

    raw_sources = _load_json_file(args.raw_file, [])
    parameter_bounds = _load_json_file(args.bounds_file, [])
    asyncio.run(run_once(args.query, args.domain, raw_sources, parameter_bounds))


if __name__ == "__main__":
    main()
