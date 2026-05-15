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

Libraries such as LangChain, LlamaIndex, Haystack, and various agent frameworks provide pre-built "tools" or "connectors" for common systems (databases, search engines, web APIs, file stores). These are very useful, but they operate at the SDK level: the integration code runs inside the host process, and is specific to the framework's abstractions and to the programming language it is written in.

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
