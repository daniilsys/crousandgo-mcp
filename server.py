#!/usr/bin/env python3
"""MCP server exposing the Crous & Go Fonderie tools (stdio by default).

    python server.py                    # stdio, for any MCP client
    python server.py --http 8766 [--host H]   # streamable HTTP, default 127.0.0.1:8766
    python server.py --read-only        # do not register the write tools at all
"""
import argparse

from mcp.server.fastmcp import FastMCP

import tools


def build(read_only=False, host="127.0.0.1", port=8766):
    mcp = FastMCP("crousandgo", host=host, port=port, instructions=(
        "Orders the Crous & Go Fonderie (Mulhouse) 1 EUR student menu, paid with Izly. Orders open at "
        "13:00 the day before pickup and must be validated before 08:00 on pickup day; one 1 EUR meal per "
        "student per order. Every tool returns {ok, ...}; on ok=false, tell the user `message` as-is "
        "(e.g. code insufficient_funds means the Izly balance must be topped up). Never pass "
        "dry_run=False without the user's explicit go for that exact menu, and always give a `reason`."))
    for fn in tools.READ_TOOLS + ([] if read_only else tools.WRITE_TOOLS):
        mcp.tool()(fn)
    return mcp


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, metavar="PORT")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--read-only", action="store_true")
    a = ap.parse_args()
    build(a.read_only, host=a.host, port=a.http or 8766).run("streamable-http" if a.http else "stdio")
