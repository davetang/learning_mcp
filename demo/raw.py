#!/usr/bin/env python3
"""Talk to server.py with hand-written JSON-RPC, no SDK on the client side.

This shows there is no magic: an MCP stdio server is just a process that
reads one JSON object per line on stdin and writes one per line on stdout.
"""

import json
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).with_name("server.py")

proc = subprocess.Popen(
    [sys.executable, str(SERVER)],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
)


def send(message: dict) -> None:
    line = json.dumps(message)
    print(f"--> {line}")
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def receive() -> dict:
    line = proc.stdout.readline()
    message = json.loads(line)
    print(f"<-- {json.dumps(message, indent=2)}")
    return message


# 1. Handshake: the client says which protocol version and capabilities it has.
send({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "raw-demo", "version": "0.1"},
    },
})
receive()

# 2. A notification (no id, so no reply) telling the server we are ready.
send({"jsonrpc": "2.0", "method": "notifications/initialized"})

# 3. Discover the tools.
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
receive()

# 4. Call one of them.
send({
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {"name": "gc_content", "arguments": {"sequence": "ACGTGGCC"}},
})
receive()

# 5. Closing stdin is how a stdio client shuts the server down.
proc.stdin.close()
proc.wait(timeout=5)
