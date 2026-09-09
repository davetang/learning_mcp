# MCP demo

A small, self-contained demonstration of the Model Context Protocol in Python. It covers the three primitives a server exposes (tools, resources, prompts), shows the protocol at three levels of abstraction, and ends with a local LLM (via Ollama) driving the tools.

| File | What it is |
| --- | --- |
| `server.py` | An MCP server with three tools, two resources and one prompt. All in-memory, no network. |
| `explore.py` | A client built on the official SDK that calls every primitive. No LLM involved. |
| `raw.py` | The same protocol with hand-written JSON-RPC over stdin/stdout. No SDK on the client side. |
| `chat.py` | A minimal host: an Ollama model that can call the server's tools, plus slash commands for resources and prompts. |
| `requirements.txt` | The two dependencies: `mcp` (the SDK) and `ollama` (the Ollama client library). |

The server's domain is deliberately tiny so that you can check the results by eye:

* **Tools**: `gc_content`, `reverse_complement`, `lookup_gene`
* **Resources**: `genes://all` (static) and `genes://{symbol}` (a template)
* **Prompt**: `gene_report`

## Setup

Python 3.10 or newer. Create a virtual environment inside `demo/` and install the requirements. With plain `venv` and `pip`:

```bash
cd demo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
cd demo
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

**SDK version note.** This demo targets version 2 of the Python SDK (`mcp>=2`), released in 2026. Version 2 renamed the high-level `FastMCP` class to `MCPServer` and switched result fields to snake_case (`input_schema`, `is_error`, ...). The snippets in the main README use the older `FastMCP` name; if you want to run those unchanged, install `pip install "mcp<2"` instead. The protocol on the wire is the same either way.

## Step 1: the protocol without an LLM

```bash
python explore.py
```

This launches `server.py` as a subprocess and talks to it over stdio, exactly as Claude Code or Claude Desktop would. It prints each stage:

1. **Handshake**: server name and version, negotiated protocol version, the capabilities the server advertises, and its `instructions` (a hint the host can use as a system prompt).
2. **tools/list**: each tool's name, description and JSON Schema, all derived from the Python function signature and docstring.
3. **tools/call**: one call per tool, including a deliberate failure (`lookup_gene("NOPE")`) so you can see what an `is_error` result looks like.
4. **resources**: the list of static resources and templates, then a read of each kind.
5. **prompts**: the list of prompts with their arguments, then the expanded messages for `gene_report(symbol="EGFR")`.

## Step 2: the same thing in raw JSON-RPC

```bash
python raw.py
```

This one does not use the SDK on the client side at all. It starts the server with `subprocess`, writes four JSON messages to its stdin, and prints what comes back on stdout: the `initialize` request and response, the `notifications/initialized` notification (no `id`, so no reply), a `tools/list`, and a `tools/call`. It is the quickest way to convince yourself that an MCP server is just a process reading and writing one JSON object per line.

## Step 3: let a local model use the tools

Install [Ollama](https://ollama.com), start it (`ollama serve` if it is not already running as a service), and pull a model that supports tool calling. `qwen3` is the default here; `llama3.1`, `mistral-nemo` and `gpt-oss` also work.

```bash
ollama pull qwen3
python chat.py "What is the GC content of ACGTGGCCTTAA, and what is its reverse complement?"
```

The script prints each tool call the model makes and the result the server returns, then the model's final answer:

```
connected to bio-demo, model qwen3, tools: gc_content, reverse_complement, lookup_gene
  -> gc_content({"sequence": "ACGTGGCCTTAA"})
  <- {
  "length": 12,
  "gc_count": 6,
  "gc_fraction": 0.5
}
  -> reverse_complement({"sequence": "ACGTGGCCTTAA"})
  <- TTAAGGCCACGT

The sequence is 12 bp with a GC content of 50%, and its reverse complement is TTAAGGCCACGT.
```

Run it without arguments for an interactive session. Plain text is sent to the model; slash commands exercise the primitives that are not model-controlled:

```
/tools                       list the server's tools
/resources                   list resources and resource templates
/read genes://GAPDH          attach a resource to the conversation (application-controlled)
/prompts                     list the server's prompts
/prompt gene_report symbol=TP53   run a prompt template (user-controlled)
/quit
```

Two environment variables control the connection:

```bash
OLLAMA_MODEL=llama3.1 python chat.py        # a different model
OLLAMA_HOST=http://gpubox:11434 python chat.py   # Ollama on another machine
```

### How `chat.py` works

It is the smallest possible MCP host, and every real host does the same four things:

1. Launch the server and fetch its tool list (`list_tools`).
2. Convert each MCP tool into the schema the LLM API expects. For Ollama (and OpenAI-compatible APIs) that is `{"type": "function", "function": {"name", "description", "parameters"}}`, where `parameters` is the tool's `input_schema` passed through unchanged.
3. Send the conversation to the model with those tools attached.
4. If the reply contains tool calls, forward each to the server with `call_tool`, append the results as `tool` messages, and go back to step 3. When the reply is plain text, show it.

The LLM never runs any code. It only chooses a tool name and arguments; the server does the work.

## Step 4: the same server from Claude Code

Because the server speaks the standard protocol, any MCP host can use it. To add it to Claude Code, point it at the virtual environment's Python so that the `mcp` package is importable:

```bash
claude mcp add bio-demo -- /full/path/to/demo/.venv/bin/python /full/path/to/demo/server.py
```

Then start Claude Code, run `/mcp` to confirm `bio-demo` is connected, and ask something like "what is the reverse complement of GATTACA?".

## Step 5: poke at it with the MCP Inspector

```bash
npx @modelcontextprotocol/inspector .venv/bin/python server.py
```

The Inspector opens a browser page where you can list and call tools, read resources, expand prompts, and watch the raw JSON-RPC traffic.
