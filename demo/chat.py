#!/usr/bin/env python3
"""A minimal MCP host: a local Ollama model driving the tools of server.py.

Usage:
    python chat.py                          # interactive
    python chat.py "GC content of ACGTGC?"  # one question, then exit

Environment:
    MCP_URL             connect to an already-running server over Streamable HTTP
                        (e.g. http://127.0.0.1:8000/mcp) instead of launching
                        server.py as a subprocess over stdio
    OLLAMA_MODEL        model to use (default: qwen3); it must support tool calling
    OLLAMA_HOST         where Ollama is listening (default: http://localhost:11434)
    OLLAMA_THINK=1      let a thinking-capable model (qwen3, gpt-oss) reason before answering;
                        off by default because it can run for a very long time
    OLLAMA_NUM_PREDICT  cap on tokens generated per model call (default: 2048)

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
MCP_URL = os.environ.get("MCP_URL")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3")
THINK = os.environ.get("OLLAMA_THINK", "0") == "1"
NUM_PREDICT = int(os.environ.get("OLLAMA_NUM_PREDICT", "2048"))

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


async def ask_model(llm: ollama.AsyncClient, messages: list, tools: list[dict], think) -> ollama.Message:
    """One model call, streamed so that progress is visible, returned as a whole message."""
    content, thinking, tool_calls = "", "", []
    stream = await llm.chat(
        model=MODEL,
        messages=messages,
        tools=tools,
        stream=True,
        think=think,
        options={"num_predict": NUM_PREDICT},
    )
    async for chunk in stream:
        part = chunk.message
        if part.thinking:
            if not thinking:
                print("[thinking] ", end="")
            print(part.thinking, end="", flush=True)
            thinking += part.thinking
        if part.content:
            if not content and thinking:
                print("\n[answer] ", end="")
            print(part.content, end="", flush=True)
            content += part.content
        if part.tool_calls:
            tool_calls.extend(part.tool_calls)
        if chunk.done and chunk.done_reason == "length":
            print(f"\n[stopped: hit the OLLAMA_NUM_PREDICT cap of {NUM_PREDICT} tokens]", end="")
    if content or thinking:
        print()
    return ollama.Message(
        role="assistant", content=content, thinking=thinking or None, tool_calls=tool_calls or None
    )


async def run_turn(mcp: Client, llm: ollama.AsyncClient, messages: list, tools: list[dict], think) -> None:
    """Send the conversation to the model and loop until it stops calling tools."""
    while True:
        reply = await ask_model(llm, messages, tools, think)
        messages.append(reply)
        if not reply.tool_calls:
            print()
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


async def check_model(llm: ollama.AsyncClient):
    """Make sure the model exists and can call tools; decide whether to let it think."""
    info = await llm.show(MODEL)
    caps = list(info.capabilities or [])
    if "tools" not in caps:
        sys.exit(f"model {MODEL} does not support tool calling (capabilities: {caps}); "
                 "try OLLAMA_MODEL=qwen3 or llama3.1")
    # Only pass `think` to models that understand it; others reject the parameter.
    return THINK if "thinking" in caps else None


async def main() -> None:
    # A URL connects to a running server; otherwise we launch one over stdio.
    target = MCP_URL or StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    llm = ollama.AsyncClient()
    think = await check_model(llm)
    async with Client(target) as mcp:
        tools = to_ollama_tools((await mcp.list_tools()).tools)
        messages: list = []
        if mcp.instructions:  # the server's own guidance becomes the system prompt
            messages.append({"role": "system", "content": mcp.instructions})
        print(f"connected to {mcp.server_info.name} over "
              f"{'Streamable HTTP' if MCP_URL else 'stdio'}, model {MODEL}"
              f"{' (thinking on)' if think else ''}, tools: "
              + ", ".join(t["function"]["name"] for t in tools))

        if len(sys.argv) > 1:  # one-shot mode
            messages.append({"role": "user", "content": " ".join(sys.argv[1:])})
            await run_turn(mcp, llm, messages, tools, think)
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
                await run_turn(mcp, llm, messages, tools, think)
            except Exception as exc:  # e.g. unknown prompt name or resource URI
                print(f"error: {exc}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConnectionError as exc:
        sys.exit(f"cannot reach Ollama ({exc}); is `ollama serve` running?")
    except ollama.ResponseError as exc:
        sys.exit(f"Ollama error: {exc.error}\n(if the model is missing, run: ollama pull {MODEL})")
    except KeyboardInterrupt:
        sys.exit("\ninterrupted; Ollama stops generating when the connection closes")
