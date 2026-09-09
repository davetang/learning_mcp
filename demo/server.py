#!/usr/bin/env python3
"""A small MCP server that exposes the three core primitives.

* Tools: functions the model can decide to call.
* Resources: read-only data addressed by URI that the host can load.
* Prompts: reusable templates the user can invoke.

Everything is in-memory so the demo has no external dependencies.

Two ways to run it, with identical tools either way:

    python server.py            stdio: waits for JSON-RPC on stdin, so a host
                                launches it as a subprocess (see explore.py,
                                raw.py and chat.py)
    python server.py --http     Streamable HTTP: a long-lived service on
                                http://127.0.0.1:8000/mcp that many clients
                                can connect to at once

Only the last three lines of this file differ between the two. The tools,
resources and prompt below know nothing about the transport.
"""

import argparse
import json
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

# A toy "database". A real server would query Ensembl, a LIMS, Postgres, etc.
GENES = {
    "TP53": {
        "symbol": "TP53",
        "full_name": "tumor protein p53",
        "chromosome": "17",
        "summary": "Tumour suppressor and transcription factor; the most frequently mutated gene in human cancers.",
    },
    "BRCA1": {
        "symbol": "BRCA1",
        "full_name": "BRCA1 DNA repair associated",
        "chromosome": "17",
        "summary": "Involved in homologous-recombination DNA repair; germline variants raise breast and ovarian cancer risk.",
    },
    "EGFR": {
        "symbol": "EGFR",
        "full_name": "epidermal growth factor receptor",
        "chromosome": "7",
        "summary": "Receptor tyrosine kinase; activating mutations are a drug target in non-small-cell lung cancer.",
    },
    "GAPDH": {
        "symbol": "GAPDH",
        "full_name": "glyceraldehyde-3-phosphate dehydrogenase",
        "chromosome": "12",
        "summary": "Glycolytic enzyme; commonly used as a housekeeping control in expression assays.",
    },
}

COMPLEMENT = str.maketrans("ACGTacgt", "TGCAtgca")

server = MCPServer(
    name="bio-demo",
    version="0.1.0",
    instructions=(
        "You have tools for basic DNA sequence calculations and for looking up "
        "a small table of human genes. Prefer the tools over guessing."
    ),
    log_level="WARNING",  # keep the server's own logging off the client's screen
)


def _clean_dna(sequence: str) -> str:
    seq = "".join(sequence.split()).upper()
    bad = set(seq) - set("ACGT")
    if bad:
        raise ToolError(f"not a DNA sequence: unexpected characters {sorted(bad)}")
    if not seq:
        raise ToolError("sequence is empty")
    return seq


# --------------------------------------------------------------------------- tools
# The function name becomes the tool name, the docstring its description and the
# type hints its JSON Schema. Returning a dict gives the client structured output.


@server.tool()
def gc_content(sequence: str) -> dict:
    """Compute the GC content of a DNA sequence (A, C, G, T only)."""
    seq = _clean_dna(sequence)
    gc = sum(seq.count(b) for b in "GC")
    return {"length": len(seq), "gc_count": gc, "gc_fraction": round(gc / len(seq), 4)}


@server.tool()
def reverse_complement(sequence: str) -> str:
    """Return the reverse complement of a DNA sequence."""
    return _clean_dna(sequence).translate(COMPLEMENT)[::-1]


@server.tool()
def lookup_gene(symbol: str) -> dict:
    """Look up a human gene by its HGNC symbol (e.g. TP53) in the demo gene table."""
    gene = GENES.get(symbol.upper())
    if gene is None:
        # ToolError becomes a result with is_error=True and this message as the content,
        # so the model can read it. Any other exception is reported as an opaque crash.
        raise ToolError(f"unknown gene symbol {symbol!r}; known symbols: {', '.join(GENES)}")
    return gene


# ----------------------------------------------------------------------- resources
# A static resource has a fixed URI; a template resource has {placeholders}.


@server.resource("genes://all", mime_type="application/json")
def all_genes() -> str:
    """The complete demo gene table."""
    return json.dumps(list(GENES.values()), indent=2)


@server.resource("genes://{symbol}", mime_type="application/json")
def one_gene(symbol: str) -> str:
    """A single gene record from the demo gene table."""
    gene = GENES.get(symbol.upper())
    if gene is None:
        raise ResourceError(f"no gene {symbol!r} in the demo table")
    return json.dumps(gene, indent=2)


# ------------------------------------------------------------------------- prompts
# A prompt returns the text (or messages) that the host feeds to the model.


@server.prompt()
def gene_report(symbol: str) -> str:
    """Write a short, structured report about a gene using the server's tools."""
    return (
        f"Write a short report about the human gene {symbol}. Steps:\n"
        f"1. Call lookup_gene to fetch what the demo table knows about {symbol}.\n"
        "2. State its full name and chromosome.\n"
        "3. Summarise its biological role in two sentences.\n"
        "Only use facts returned by the tool; if the symbol is unknown, say so."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the bio-demo MCP server.")
    parser.add_argument("--http", action="store_true",
                        help="serve over Streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to bind in --http mode (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="port to bind in --http mode (default: 8000)")
    args = parser.parse_args()

    if args.http:
        print(f"bio-demo listening on http://{args.host}:{args.port}/mcp", file=sys.stderr)
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        server.run()  # stdio: the default, and what a host launches as a subprocess
