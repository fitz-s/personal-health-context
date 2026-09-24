"""Official-SDK MCP server. stdio is the primary transport (Secure MCP Tunnel launches it as a subprocess).

stdout carries only MCP protocol; diagnostics go to a redacted stderr/log file.
"""
from __future__ import annotations

import base64
import logging
import sys
from pathlib import Path

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import __version__
from .config import Config
from .store import Store
from .tools import Result, ToolContext, Tools, as_text

INSTRUCTIONS = (Path(__file__).resolve().parents[2] / 'prompts' / 'foreground.md')
log = logging.getLogger('phctx.mcp')


def build(tools: Tools) -> Server:
    listed = [types.Tool(name=t['name'], description=t['description'], input_schema=t['inputSchema'],
                         annotations=types.ToolAnnotations(
                             read_only_hint=t['annotations']['readOnlyHint'],
                             destructive_hint=t['annotations']['destructiveHint'],
                             idempotent_hint=t['annotations']['idempotentHint'],
                             open_world_hint=t['annotations']['openWorldHint']),
                         _meta=t.get('_meta'))
              for t in tools.listed()]

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=listed)

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        name = params.name
        res: Result = await anyio.to_thread.run_sync(tools.call, name, params.arguments or {})
        status = 'error:' + str(res.data.get('error')) if res.is_error and isinstance(res.data, dict) else 'ok'
        log.info('tool=%s status=%s', name, status)
        content: list = [types.TextContent(text=as_text(res.data))]
        if res.image:
            data, mime = res.image
            content.append(types.ImageContent(data=base64.b64encode(data).decode(), mime_type=mime))
        structured = res.data if isinstance(res.data, dict) else {'result': res.data}
        return types.CallToolResult(content=content, structured_content=structured, is_error=res.is_error)

    instructions = INSTRUCTIONS.read_text() if INSTRUCTIONS.exists() else None
    return Server('personal-health-context', version=__version__, title='Personal Health Context',
                  instructions=instructions, on_list_tools=list_tools, on_call_tool=call_tool)


def tools_from_config(cfg: Config, profile: str | None = None) -> Tools:
    store = Store(cfg.root, cfg.profile)
    fetch_options: dict = {'fake_ip_ok': True} if cfg.trust_fake_ip_dns else {}
    if cfg.synthetic_test_ca and store.profile == 'synthetic':
        # Eval harness only: a loopback HTTPS file host signed by a throwaway CA. Ignored in production.
        import ssl
        fetch_options = {'allow_nonpublic': True, 'resolver': lambda h, p: ['127.0.0.1'],
                         'context': ssl.create_default_context(cafile=str(cfg.synthetic_test_ca))}
    return Tools(ToolContext(store=store, profile=profile or cfg.tool_profile,
                             allowed_download_hosts=cfg.allowed_download_hosts,
                             max_file_bytes=cfg.max_interactive_bytes, fetch_options=fetch_options))


def serve_stdio(cfg: Config, profile: str | None = None) -> None:
    server = build(tools_from_config(cfg, profile))

    async def main() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    anyio.run(main)


def setup_logging(path: Path | None) -> None:
    handler: logging.Handler = logging.FileHandler(path) if path else logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s'))
    handler.addFilter(Redactor())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


class Redactor(logging.Filter):
    """Last-line defense: strip URLs with query strings and bearer tokens from any log record."""
    import re as _re
    PATTERNS = [(_re.compile(r'https?://\S+'), '<url>'), (_re.compile(r'(?i)bearer\s+\S+'), 'Bearer <redacted>'),
                (_re.compile(r'(?i)(sig|signature|token|key)=[^&\s]+'), r'\1=<redacted>')]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat, rep in self.PATTERNS:
            msg = pat.sub(rep, msg)
        record.msg, record.args = msg, None
        return True
