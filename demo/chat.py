#!/usr/bin/env python3
"""A minimal MCP host: a local Ollama model driving the tools of server.py.

Usage:
    python chat.py                          # interactive
    python chat.py "GC content of ACGTGC?"  # one question, then exit

Environment:
    OLLAMA_MODEL  model to use (default: qwen3)
    OLLAMA_HOST   where Ollama is listening (default: http://localhost:11434)

This script does what every MCP host does:
  1. launch the server and fetch its tool list,
  2. hand those tools to the LLM in the format the LLM API expects,
  3. when the LLM asks for a tool call, forward it to the server and
     feed the result back, until the LLM answers in plain text.
Resources and prompts are exposed through slash commands, mirroring how
hosts treat them as user- or application-controlled rather than model-controlled.
"""

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

import ollama
from mcp import Client, StdioServerParameters

SERVER = Path(__file__).with_name("server.py")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3")

HELP = """\
commands:
  /tools                       list the server's tools
  /resources                   list resources and resource templates
  /read <uri>                  attach a resource to the conversation
  /prompts                     list the server's prompts
  /prompt <name> [k=v ...]     run a prompt template, e.g. /prompt gene_report symbol=TP53
  /quit                        exit
anything else is sent to the model as a question."""


def to_ollama_tools(tools) -> list[dict]:
    """MCP tool descriptions -> the OpenAI-style schema that Ollama expects."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


def result_to_text(result) -> str:
    """Flatten an MCP tool result into text the model can read."""
    text = "\n".join(block.text for block in result.content if getattr(block, "text", None))
    if not text and result.structured_content is not None:
        text = json.dumps(result.structured_content)
    return text  # on is_error the text already carries the server's error message


async def run_turn(mcp: Client, llm: ollama.AsyncClient, messages: list, tools: list[dict]) -> None:
    """Send the conversation to the model and loop until it stops calling tools."""
    while True:
        response = await llm.chat(model=MODEL, messages=messages, tools=tools)
        reply = response.message
        messages.append(reply)
        if not reply.tool_calls:
            print(f"\n{reply.content.strip()}\n")
            return
        for call in reply.tool_calls:
            name, args = call.function.name, dict(call.function.arguments)
            print(f"  -> {name}({json.dumps(args)})")
            result = await mcp.call_tool(name, args)
            text = result_to_text(result)
            print(f"  <- {text}")
            messages.append({"role": "tool", "tool_name": name, "content": text})


async def handle_command(mcp: Client, line: str, messages: list) -> bool:
    """Return True if the model should be called after this command."""
    cmd, *rest = shlex.split(line)
    if cmd == "/tools":
        for tool in (await mcp.list_tools()).tools:
            print(f"- {tool.name}: {tool.description}")
    elif cmd == "/resources":
        for res in (await mcp.list_resources()).resources:
            print(f"- {res.uri}: {res.description}")
        for tmpl in (await mcp.list_resource_templates()).resource_templates:
            print(f"- {tmpl.uri_template}: {tmpl.description}")
    elif cmd == "/read" and rest:
        contents = (await mcp.read_resource(rest[0])).contents
        text = "\n".join(item.text for item in contents)
        messages.append({"role": "user", "content": f"Contents of resource {rest[0]}:\n{text}"})
        print(f"attached {rest[0]} ({len(text)} chars); now ask a question about it")
    elif cmd == "/prompts":
        for prompt in (await mcp.list_prompts()).prompts:
            args = ", ".join(a.name for a in prompt.arguments or [])
            print(f"- {prompt.name}({args}): {prompt.description}")
    elif cmd == "/prompt" and rest:
        name, kv = rest[0], dict(item.split("=", 1) for item in rest[1:])
        result = await mcp.get_prompt(name, kv)
        for message in result.messages:
            print(f"[{message.role}] {message.content.text}")
            messages.append({"role": message.role, "content": message.content.text})
        return True
    else:
        print(HELP)
    return False


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    llm = ollama.AsyncClient()
    async with Client(params) as mcp:
        tools = to_ollama_tools((await mcp.list_tools()).tools)
        messages: list = []
        if mcp.instructions:  # the server's own guidance becomes the system prompt
            messages.append({"role": "system", "content": mcp.instructions})
        print(f"connected to {mcp.server_info.name}, model {MODEL}, tools: "
              + ", ".join(t["function"]["name"] for t in tools))

        if len(sys.argv) > 1:  # one-shot mode
            messages.append({"role": "user", "content": " ".join(sys.argv[1:])})
            await run_turn(mcp, llm, messages, tools)
            return

        print(HELP)
        while True:
            try:
                line = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            try:
                if line.startswith("/"):
                    if not await handle_command(mcp, line, messages):
                        continue
                else:
                    messages.append({"role": "user", "content": line})
                await run_turn(mcp, llm, messages, tools)
            except Exception as exc:  # e.g. unknown prompt name or resource URI
                print(f"error: {exc}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConnectionError as exc:
        sys.exit(f"cannot reach Ollama ({exc}); is `ollama serve` running?")
