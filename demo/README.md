# MCP demo

A small, self-contained demonstration of the Model Context Protocol in Python. It covers the three primitives a server exposes (tools, resources, prompts), shows the protocol at three levels of abstraction, and ends with a local LLM (via Ollama) driving the tools.

| File | What it is |
| --- | --- |
| `server.py` | An MCP server with three tools, two resources and one prompt. All in-memory. Runs over stdio, or as an HTTP service with `--http`. |
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

The script prints each tool call the model makes and the result the server returns, then streams the model's final answer:

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
The GC content of the sequence **ACGTGGCCTTAA** is 50% (6 out of 12 bases are G or C).

The reverse complement of the sequence is **TTAAGGCCACGT**.
```

The whole run takes a few seconds once the model is loaded. The model never computed anything itself: it chose the two tools and their arguments, the server ran them, and the model wrote the answer from the results.

Run it without arguments for an interactive session. Plain text is sent to the model; slash commands exercise the primitives that are not model-controlled:

```
/tools                       list the server's tools
/resources                   list resources and resource templates
/read genes://GAPDH          attach a resource to the conversation (application-controlled)
/prompts                     list the server's prompts
/prompt gene_report symbol=TP53   run a prompt template (user-controlled)
/quit
```

Four environment variables control the model:

```bash
OLLAMA_MODEL=llama3.1 python chat.py             # a different model (must support tool calling)
OLLAMA_HOST=http://gpubox:11434 python chat.py   # Ollama on another machine
OLLAMA_THINK=1 python chat.py                    # let qwen3 / gpt-oss "think" before answering
OLLAMA_NUM_PREDICT=512 python chat.py            # tighter cap on tokens per model call
```

### If it looks stuck

Every tool call is printed as it happens, so if the last thing on screen is a tool result, the process is waiting for Ollama to generate the next reply. The script streams the model's output token by token, so a healthy model shows text within a few seconds (longer the first time, while the model loads). Ctrl-C stops the script, and Ollama abandons the generation when the connection closes.

The first version of this script hung for over fifteen minutes at full GPU load after the second tool call. The cause was Ollama's "thinking" mode, which is on by default for `qwen3`: after receiving tool results the model produced an open-ended reasoning trace, and nothing capped it. The script now guards against this in three ways:

* **Thinking is off by default.** Set `OLLAMA_THINK=1` to turn it on; the trace is then streamed to the screen so you can watch what the model is doing.
* **Generation is capped.** Ollama's default is unlimited. Each model call is limited to `OLLAMA_NUM_PREDICT` tokens (2048 by default) and a note is printed if the cap is hit.
* **Capabilities are checked on startup.** The script asks Ollama what the model supports, refuses models that cannot call tools (they would answer without ever using the server), and only passes the thinking flag to models that understand it.

`ollama ps` shows which model is loaded and whether it is running on the GPU or the CPU.

### How `chat.py` works

In MCP's vocabulary `chat.py` is a **host**: the application the user talks to. Inside it, the `Client` object is the **client**, the component that owns one connection to one server. Claude Code and Claude Desktop are hosts in exactly the same sense, each running one client per configured server. Ollama is none of these. It runs the model and knows nothing about MCP, which is why a host has to sit between the two:

```
  server.py  <--JSON-RPC over stdio or HTTP-->  chat.py  <--HTTP-->  Ollama
  (MCP server)                                (MCP host)            (runs qwen3)
```

Every real host does the same four things:

1. Launch the server and fetch its tool list (`list_tools`).
2. Convert each MCP tool into the schema the LLM API expects. For Ollama (and OpenAI-compatible APIs) that is `{"type": "function", "function": {"name", "description", "parameters"}}`, where `parameters` is the tool's `input_schema` passed through unchanged.
3. Send the conversation to the model with those tools attached.
4. If the reply contains tool calls, forward each to the server with `call_tool`, append the results as `tool` messages, and go back to step 3. When the reply is plain text, show it.

The model call is streamed so that partial output is visible, and the assistant message (including its tool calls) is appended to the conversation before the tool results, which is the order the model expects to see.

The LLM never runs any code. It only chooses a tool name and arguments; the server does the work.

## Step 4: the other transport, run it as a service

Everything above used the **stdio** transport, where the client launches the server as its own private subprocess. The same server also speaks **Streamable HTTP**, where it is a long-lived service that clients reach by URL. This is the shape most people picture when they hear "server".

Start it in one terminal:

```bash
python server.py --http                          # http://127.0.0.1:8000/mcp
python server.py --http --port 8123              # a different port
```

Point any client at the URL from another:

```bash
python explore.py http://127.0.0.1:8000/mcp
MCP_URL=http://127.0.0.1:8000/mcp python chat.py "reverse complement of GATTACA?"
```

The output is identical to the stdio runs, because only the transport changed. In `explore.py` the sole difference is what gets handed to the client constructor: a `StdioServerParameters` describing how to launch a process, or a URL string. Every request after that is the same. In `server.py` only the last three lines differ; the tools, resources and prompt know nothing about the transport.

The two shapes behave differently in ways worth knowing:

| | stdio | Streamable HTTP |
| --- | --- | --- |
| Who starts the server | the client, automatically | you, ahead of time |
| Lifetime | dies with the client | runs until you stop it |
| Concurrent clients | one private copy each | many, sharing one process |
| Location | same machine only | any machine on the network |
| Authentication | none needed, runs as you | needed, OAuth 2.1 in the spec |

You can watch that difference. Running two clients against one HTTP server, both are served by the single process you started. Running two clients over stdio instead, each gets a private copy that exits when its client does:

```
2 stdio clients connected -> 2 private server.py copies: ['687', '689']
after both clients exit    -> 0 stdio copies left
```

A word of caution on `--http`. The server binds to `127.0.0.1` by default, so only your machine can reach it. There is no authentication in this demo, so anything that can reach the port can call the tools. Do not bind it to a public address, and see the [authorization section](../README.md#authorization-for-remote-servers) of the main README for what a real remote server needs.

## Step 5: the same server from Claude Code

Because the server speaks the standard protocol, any MCP host can use it. To add it to Claude Code, point it at the virtual environment's Python so that the `mcp` package is importable:

```bash
claude mcp add bio-demo -- /full/path/to/demo/.venv/bin/python /full/path/to/demo/server.py
```

Then start Claude Code, run `/mcp` to confirm `bio-demo` is connected, and ask something like "what is the reverse complement of GATTACA?".

## Step 6: poke at it with the MCP Inspector

```bash
npx @modelcontextprotocol/inspector .venv/bin/python server.py
```

The Inspector opens a browser page where you can list and call tools, read resources, expand prompts, and watch the raw JSON-RPC traffic.
