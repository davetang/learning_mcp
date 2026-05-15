# Model Context Protocol

## TL;DR

MCP is a standard way to let an LLM use code you have already written, without baking that code into any particular LLM application. A concrete example: you have a database and some functions that query it.

1. You write your query functions in whatever language you like (Python, Go, TypeScript, etc.).
2. You wrap them in an **MCP server**, giving each function a name, a plain-English description, and a JSON Schema for its arguments.
3. An MCP-aware **host** (such as Claude Desktop or Claude Code) connects to your server and asks "what tools do you offer?".
4. The host passes those tool descriptions to the LLM as part of the conversation.
5. When the user asks a question (e.g. "how many orders did Acme place last month?"), the LLM decides which tool to call and emits a structured tool call.
6. The host forwards that call to your server over JSON-RPC. **Your server runs the actual function** against the database and returns the result.
7. The host feeds the result back to the LLM, which uses it to answer the user.

The LLM never executes your code; it only decides *which* function to call and *with what arguments*. The server does the work. That is why the server can be written in any language: the only contract between host and server is JSON messages on a pipe or a socket. And because the protocol is standardised, the same server can be reused by any MCP-aware client without rewriting the integration.

## Purpose

This repository is for learning about the Model Context Protocol (MCP). MCP is a protocol that enables a Large Language Model (LLM) to interact with external tools and data sources (such as databases, APIs, and file systems). Some things I am interested in:

* [What problem MCP is designed to solve and why it exists.](#what-problem-does-mcp-solve)
* [The architecture of MCP (clients, servers, transports, and message formats).](#the-architecture-of-mcp)
* [How MCP compares to other approaches for tool use and function calling.](#how-mcp-compares-to-other-approaches)
* [How to build and run an MCP server.](#how-to-build-and-run-an-mcp-server)
* [How to connect an MCP server to an LLM client (e.g. Claude Desktop, Claude Code).](#how-to-connect-an-mcp-server-to-an-llm-client)
* Practical examples of using MCP to expose tools, resources, and prompts.

## What problem does MCP solve?

LLMs on their own are powerful reasoners, but they are isolated. By default a model only knows what was in its training data and what fits in the current prompt. It cannot read a file on your machine, run a query against your database, check the status of a build, or call a third-party API. To be genuinely useful in real work, an LLM needs a way to reach beyond the prompt and interact with external systems.

Before MCP, every team that wanted to give an LLM access to tools and data had to build that bridge themselves. A code editor that wanted the model to read a repository, a chat client that wanted to query a ticketing system, and an internal app that wanted to call a SQL warehouse each built bespoke integrations. Even when two applications wanted to connect to the same backend (say, GitHub or Postgres), they typically wrote their own glue code with their own conventions for authentication, tool descriptions, error handling, and streaming.

This produces an "N times M" problem: N AI applications times M data sources or tools equals N by M integrations, most of which are reinventing the same wheel. It also makes integrations brittle, since a tool built for one client often cannot be reused by another.

The Model Context Protocol, introduced by Anthropic in late 2024, is an open standard that addresses this by defining a common protocol for how AI applications connect to external context and capabilities. The goals are:

* **Standardisation.** One shared protocol for exposing tools, resources, and prompts, so any compliant client can talk to any compliant server.
* **Reusability.** A single MCP server (for example, one that wraps a database or an internal API) can be used by any MCP-aware client, such as Claude Desktop, Claude Code, or a custom application.
* **Separation of concerns.** The team that owns a data source can ship and maintain an MCP server for it, while AI application developers focus on the user experience instead of writing one-off integrations.
* **Safety and control.** Because MCP servers run as separate processes that the user explicitly connects, users keep control over what data and capabilities a model can access.

## The architecture of MCP

MCP follows a client/server architecture, but with a small twist: the "client" is itself embedded inside a larger application (the host). At a high level there are three roles:

* **Host.** The user-facing LLM application, such as Claude Desktop, Claude Code, an IDE plugin, or a custom chat app. The host is responsible for the user interface, for calling the LLM, and for deciding which servers to connect to. A single host can connect to many MCP servers at once.
* **Client.** A component inside the host that maintains a one-to-one connection with a single MCP server. If the host is connected to five servers, it runs five clients internally. Each client handles the protocol details for its server (handshake, message routing, capability negotiation).
* **Server.** A separate process or service that exposes capabilities to a client. A server typically wraps some external system (a database, a filesystem, a GitHub API, a search index) and presents a clean MCP interface to it. Servers are independent of any specific host; the same server can be used by Claude Desktop, Claude Code, or any other MCP-aware application.

This separation matters: the host owns the LLM and the user experience, while servers own the integration with the outside world. They meet in the middle through a standard protocol.

### Primitives a server can expose

An MCP server exposes its functionality through a small set of well-defined primitives. The three core ones are:

* **Tools.** Functions the model can call, similar to function calling or tool use in other LLM APIs. Each tool has a name, a description, and a JSON Schema for its arguments. Tools are typically "model-controlled": the LLM decides when to invoke them.
* **Resources.** Read-only, file-like pieces of context (documents, database rows, log files, configuration) that the server can offer to the host. Resources are typically "application-controlled": the host or user decides which ones to load into context.
* **Prompts.** Reusable prompt templates or workflows that a server can offer. Prompts are typically "user-controlled": the user explicitly invokes them (for example, by picking one from a menu).

There are also some additional primitives, such as **sampling** (a server can ask the host to run an LLM completion on its behalf), **roots** (the host can tell a server which directories or URIs it is allowed to operate within), and **elicitation** (a server can ask the user for additional input mid-operation).

### Transports

The protocol itself is transport-agnostic; it just needs a way to send and receive messages. In practice there are two main transports:

* **stdio.** The server is launched as a subprocess of the host and communicates over standard input and standard output. This is the simplest option and is typical for local servers (filesystem access, local databases, command-line tools). Each MCP server in your Claude Desktop config, for instance, is usually a stdio server.
* **Streamable HTTP.** The server runs as a network service and the client connects to it over HTTP, with support for streaming responses. This is used for remote or shared servers, such as a hosted MCP server that wraps a SaaS API. (An earlier HTTP transport based on Server-Sent Events has been largely superseded by Streamable HTTP in recent versions of the spec.)

Choice of transport is mostly an operational concern; the message-level protocol is the same in either case.

### Message format

MCP messages are encoded as JSON-RPC 2.0. Every exchange falls into one of three shapes:

* **Requests** carry a method name, parameters, and an id, and expect a matching response.
* **Responses** carry either a `result` or an `error`, plus the id of the request they correspond to.
* **Notifications** are one-way messages that do not expect a response (used, for example, when a server announces that its list of tools has changed).

A connection follows a defined lifecycle:

1. **Initialize.** The client sends an `initialize` request advertising the protocol version it speaks and the capabilities it supports (sampling, roots, etc.). The server replies with its own version and the capabilities it offers (tools, resources, prompts, and so on). This handshake lets both sides negotiate a common feature set.
2. **Operation.** Once initialised, the client and server exchange normal requests and notifications: listing tools, calling tools, reading resources, fetching prompts, and so on.
3. **Shutdown.** Either side can close the connection cleanly when it is no longer needed.

Putting it all together: a host runs one or more clients, each client speaks JSON-RPC 2.0 over a transport (stdio or HTTP) to a server, and the server exposes tools, resources, and prompts that the host can surface to the LLM and the user.

## How MCP compares to other approaches

Giving an LLM access to tools and data is not a new idea. Several approaches existed before MCP and continue to coexist with it. Understanding where MCP sits in this landscape helps clarify what it does and does not replace.

### Native function calling / tool use in the model API

All the major LLM providers (Anthropic, OpenAI, Google, and others) expose some form of function calling or tool use directly in their API. The application sends the model a list of available tools, each described by a name, a description, and a JSON Schema for its parameters. The model responds with a structured request to call one of those tools, the application runs the corresponding code, and the result is fed back into the conversation.

This works well, but it is a per-application contract. Each app defines its own tools, writes its own dispatch logic, and is responsible for things like authentication, error handling, and streaming. If two different applications want to expose the same capability (say, "search Jira issues"), they each have to implement it from scratch.

MCP does not replace this mechanism; it sits one layer above it. The host application still uses the model's native tool use to let the LLM actually invoke a tool. The difference is where the tool definitions and implementations come from: with MCP, they come from an external server that any compliant host can connect to, rather than being hand-coded inside each host.

### Vendor-specific plugin systems

Some vendors have shipped their own plugin or extension systems, for example OpenAI's ChatGPT Plugins (built on top of OpenAPI specs) and "Custom GPTs" with Actions. These let third parties expose capabilities to a specific product.

The limitations are that they are tied to a single vendor and a single product surface. A plugin written for ChatGPT cannot be used by Claude, by a local editor, or by a custom internal application without being rewritten. MCP, by contrast, is an open protocol with no single owner of the client side, so the same server can be reused across many hosts.

### Agent frameworks and tool libraries

Libraries such as LangChain, LlamaIndex, Haystack, and various agent frameworks provide pre-built "tools" or "connectors" for common systems (databases, search engines, web APIs, file stores). These are very useful, but they operate at the library level: the integration code runs inside the host process, and is specific to the framework's abstractions and to the programming language it is written in.

MCP is at the protocol level instead of the library level. Because the server runs as a separate process and communicates over JSON-RPC, it can be implemented in any language and consumed by any client, regardless of what language or framework the host is written in. You can write an MCP server in Python and use it from a TypeScript host, or vice versa, without sharing any code.

### Hand-rolled REST or RPC integrations

The most general approach is also the oldest: the host application calls external systems directly via REST, gRPC, GraphQL, database drivers, and so on, and exposes whichever pieces it wants to the LLM as tools. This is maximally flexible but maximally expensive in engineering effort, and the resulting integrations are not portable to other hosts.

MCP can be thought of as a shared convention that absorbs the repetitive parts of these hand-rolled integrations (capability discovery, schema declaration, lifecycle, streaming, errors) while still letting the server do whatever it needs to behind the scenes.

### Summary

| Approach | Where the integration lives | Reusable across hosts? | Standardised? |
| --- | --- | --- | --- |
| Native function calling | Inside each host application | No | Per-vendor API |
| Vendor plugin systems | Tied to a specific product | No | Vendor-specific |
| Agent frameworks / tool libraries | Inside the host, as library code | Only within the same framework/language | Library-level |
| Hand-rolled REST/RPC | Inside the host, fully custom | No | None |
| **MCP** | In a separate server process | **Yes**, across any MCP-aware host | **Yes**, open protocol |

The short version: MCP does not compete with the model's tool-use API; it standardises and externalises the layer where tools and data sources are defined, so that the same integration can be reused everywhere instead of being rebuilt in every application.

A useful analogy is the Language Server Protocol (LSP). Before LSP, every code editor implemented its own integration for every programming language, leading to massive duplication of effort. LSP standardised the interface between editors and language tooling, so that one language server could serve any LSP-compatible editor. MCP aims to do the same for LLM applications and the tools and data they need to reach.

## How to build and run an MCP server

In practice, building an MCP server means three things:

1. Pick a Software Development Kit (SDK) in your preferred language.
2. Write your tools (and optionally resources and prompts) as ordinary functions.
3. Run the server, either over stdio (for local use) or over HTTP (for remote use).

A **Software Development Kit (SDK)** is just a packaged set of code (a library) that someone has written so you don't have to start from zero. In this case, the MCP SDK takes care of all the low-level protocol details (the JSON-RPC messages, the initialise handshake, schema generation, transport handling) so you can focus on writing the actual functions you want the LLM to call. You install it the same way you install any other library in your language (for example, `pip install` for Python or `npm install` for TypeScript).

The protocol can be implemented from scratch on top of JSON-RPC 2.0, but in practice almost everyone uses an official SDK. Anthropic maintains SDKs in several languages; the most mature are **Python** and **TypeScript**, with **Java**, **C#**, **Kotlin**, **Swift**, and others available too.

### A minimal server in Python

The Python SDK ships a high-level `FastMCP` helper that handles most of the boilerplate. You describe each tool by writing a normal Python function with type hints and a docstring; the SDK turns those into the name, description, and JSON Schema that MCP expects.

Install it:

```bash
pip install mcp
```

A minimal server that exposes two database query functions might look like this:

```python
# server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("orders-db")

@mcp.tool()
def search_customers(name: str) -> list[dict]:
    """Search for customers whose name contains the given string."""
    # ... run your query against the database ...
    return [{"id": 1, "name": "Acme Corp"}]

@mcp.tool()
def get_order(order_id: int) -> dict:
    """Fetch a single order by its numeric ID."""
    # ... run your query against the database ...
    return {"id": order_id, "total": 199.99, "status": "shipped"}

if __name__ == "__main__":
    mcp.run()  # defaults to stdio transport
```

Key points:

* The function name becomes the tool name (`search_customers`, `get_order`).
* The docstring becomes the description that the LLM sees.
* The type hints (`name: str`, `order_id: int`) are turned into the JSON Schema for arguments.
* The return value is serialised to JSON and sent back as the tool result.

That is the whole server. The functions inside can do whatever you want: query a database, hit a REST API, read files, run a shell command, and so on.

### A minimal server in TypeScript

The equivalent in TypeScript using `@modelcontextprotocol/sdk` looks like:

```typescript
// server.ts
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const server = new McpServer({ name: "orders-db", version: "0.1.0" });

server.tool(
  "search_customers",
  "Search for customers whose name contains the given string.",
  { name: z.string() },
  async ({ name }) => ({
    content: [{ type: "text", text: JSON.stringify([{ id: 1, name: "Acme Corp" }]) }],
  }),
);

await server.connect(new StdioServerTransport());
```

Same shape, same primitives, just expressed in the host language's idioms.

### Running it

For local development, the stdio transport is the default. The server is launched as a subprocess by whatever host you connect, so you typically do not run it directly yourself. The host is configured with a command line like `python server.py` or `node server.js`, and it manages the process lifecycle.

If you want to run a server over the network (for example, a shared internal MCP server), you switch the transport to Streamable HTTP. With the Python SDK, that looks roughly like `mcp.run(transport="streamable-http", port=8080)`.

### Testing your server

You don't need to plug a server into Claude Desktop or Claude Code to try it out. The community provides the **MCP Inspector**, a small web UI that connects to any MCP server, lists its tools/resources/prompts, and lets you invoke them by hand. It is the fastest way to verify that your server is behaving correctly before wiring it into an LLM.

```bash
npx @modelcontextprotocol/inspector python server.py
```

This launches your server, connects to it, and opens a browser UI where you can:

* See the result of the `initialize` handshake and the capabilities your server advertises.
* List your tools and their schemas.
* Call a tool with arguments and inspect the response.
* Watch the raw JSON-RPC messages going back and forth, which is invaluable when learning.

Once the Inspector shows your server working as expected, the next step is to connect it to an actual host so that an LLM can drive it. That is covered in the next section.

## How to connect an MCP server to an LLM client

Building a server is only half the picture. To actually use it, a host (an LLM application) needs to launch it, connect to it, and surface its tools to the model. Each host has its own configuration mechanism, but they all do the same three things:

1. Give the server a name (used internally to identify it).
2. Specify how to start it (a command and arguments, for a local server) or where to reach it (a URL, for a remote server).
3. Optionally set a scope: just this project, all your projects, or shared with teammates.

The example below uses **Claude Code** as the host, continuing with the `orders-db` server from the previous section.

### Adding a server with `claude mcp add`

The simplest way to register a local stdio server with Claude Code is the `claude mcp add` command:

```bash
claude mcp add orders-db -- python /path/to/server.py
```

Breakdown:

* `orders-db` is the name Claude Code will use to identify the server.
* Everything after `--` is the command line Claude Code will run to launch it. The server is started as a subprocess and communicates over stdio.

By default this registers the server at the **local** scope (just this project on this machine). To make the same server available across all your projects, use `-s user`:

```bash
claude mcp add -s user orders-db -- python /path/to/server.py
```

To share it with teammates working on the same repository, use `-s project`. That writes the configuration into a `.mcp.json` file at the root of the project, which you can commit to git so that everyone working on the project picks it up automatically.

### Verifying the connection

After adding a server, you can inspect what's configured from the shell:

```bash
claude mcp list           # all configured servers
claude mcp get orders-db  # details of one server
```

Inside a Claude Code session, the `/mcp` slash command shows which servers are currently connected, whether each is healthy, and which tools, resources, and prompts each one exposes. This is the quickest way to confirm that the LLM can actually see your tools.

### What the configuration file looks like

Under the hood, `claude mcp add` writes to a JSON configuration file. The format is shared across most MCP-aware hosts and looks like this:

```json
{
  "mcpServers": {
    "orders-db": {
      "command": "python",
      "args": ["/path/to/server.py"],
      "env": {
        "DATABASE_URL": "postgres://localhost/orders"
      }
    }
  }
}
```

You can also edit this file by hand instead of using the command. The `env` block is useful for passing secrets or configuration (database URLs, API keys) to your server without baking them into the code.

### Connecting to a remote (HTTP) server

If your server runs as a network service rather than a local subprocess, use the HTTP transport instead:

```bash
claude mcp add --transport http remote-search https://mcp.example.com/v1
```

Claude Code will open a Streamable HTTP connection to that URL rather than launching a subprocess.

### Using the tools

Once a server is connected, no further wiring is required on your end. The host fetches the tool list from the server, presents the tools to the LLM as part of the conversation, and the LLM decides when to call them. If you ask Claude "what was Acme's most recent order?", it will see that `orders-db` exposes `search_customers` and `get_order`, chain them as needed, and weave the results into its reply.

In other words, the connection step is mostly a one-time setup: register the server, confirm it's healthy, and from that point on the model treats its tools as just another capability it can reach for when it needs them.
