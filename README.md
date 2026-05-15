# Model Context Protocol

This repository is for learning about the Model Context Protocol (MCP). MCP is a protocol that enables a Large Language Model (LLM) to interact with external tools and data sources (such as databases, APIs, and file systems). Some things I am interested in:

* [What problem MCP is designed to solve and why it exists.](#what-problem-does-mcp-solve)
* [The architecture of MCP (clients, servers, transports, and message formats).](#the-architecture-of-mcp)
* How MCP compares to other approaches for tool use and function calling.
* How to build and run an MCP server.
* How to connect an MCP server to an LLM client (e.g. Claude Desktop, Claude Code).
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

A useful analogy is the Language Server Protocol (LSP). Before LSP, every code editor implemented its own integration for every programming language, leading to massive duplication of effort. LSP standardised the interface between editors and language tooling, so that one language server could serve any LSP-compatible editor. MCP aims to do the same for LLM applications and the tools and data they need to reach.
