# Model Context Protocol

This repository is for learning about the Model Context Protocol (MCP). MCP is a protocol that enables a Large Language Model (LLM) to interact with external tools and data sources (such as databases, APIs, and file systems). Some things I am interested in:

* [What problem MCP is designed to solve and why it exists.](#what-problem-does-mcp-solve)
* The architecture of MCP (clients, servers, transports, and message formats).
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

A useful analogy is the Language Server Protocol (LSP). Before LSP, every code editor implemented its own integration for every programming language, leading to massive duplication of effort. LSP standardised the interface between editors and language tooling, so that one language server could serve any LSP-compatible editor. MCP aims to do the same for LLM applications and the tools and data they need to reach.
