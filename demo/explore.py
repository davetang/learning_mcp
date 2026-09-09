#!/usr/bin/env python3
"""Exercise every primitive of server.py through the MCP client SDK, no LLM needed.

The client launches server.py as a subprocess and talks to it over stdio,
exactly as Claude Code or Claude Desktop would.
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

SERVER = Path(__file__).with_name("server.py")


def heading(text: str) -> None:
    print(f"\n{'=' * 8} {text} {'=' * 8}")


def show_tool_result(result) -> None:
    for block in result.content:
        print("   content:", getattr(block, "text", block))
    if result.structured_content is not None:
        print("   structured:", json.dumps(result.structured_content))
    if result.is_error:
        print("   is_error: True")


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with Client(params) as client:
        heading("handshake")
        print("server:      ", client.server_info)
        print("protocol:    ", client.protocol_version)
        print("instructions:", client.instructions)
        caps = client.server_capabilities
        print("capabilities:", [name for name, value in caps if value is not None])

        heading("tools/list")
        tools = (await client.list_tools()).tools
        for tool in tools:
            print(f"- {tool.name}: {tool.description}")
            print("  input schema:", json.dumps(tool.input_schema))

        heading("tools/call")
        for name, args in [
            ("gc_content", {"sequence": "ACGTGGCC"}),
            ("reverse_complement", {"sequence": "AACCGGTT"}),
            ("lookup_gene", {"symbol": "TP53"}),
            ("lookup_gene", {"symbol": "NOPE"}),  # deliberately unknown
        ]:
            print(f"> {name}({json.dumps(args)})")
            show_tool_result(await client.call_tool(name, args))

        heading("resources/list and resources/templates/list")
        for res in (await client.list_resources()).resources:
            print(f"- {res.uri}  ({res.mime_type}) {res.description}")
        for tmpl in (await client.list_resource_templates()).resource_templates:
            print(f"- {tmpl.uri_template}  ({tmpl.mime_type}) {tmpl.description}")

        heading("resources/read")
        for uri in ["genes://all", "genes://BRCA1"]:
            contents = (await client.read_resource(uri)).contents
            print(f"> {uri}")
            for item in contents:
                print("  ", item.text.replace("\n", "\n   "))

        heading("prompts/list")
        for prompt in (await client.list_prompts()).prompts:
            args = ", ".join(a.name + ("" if a.required else "?") for a in prompt.arguments or [])
            print(f"- {prompt.name}({args}): {prompt.description}")

        heading("prompts/get")
        result = await client.get_prompt("gene_report", {"symbol": "EGFR"})
        for message in result.messages:
            print(f"[{message.role}] {message.content.text}")


if __name__ == "__main__":
    asyncio.run(main())
