"""Exercise real MCP framing and tool schemas. No network call, no credentials."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
READ = {"list_menus", "get_cart", "get_orders"}


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def tools(self, read_only):
        with tempfile.TemporaryDirectory() as temp:
            args = [str(ROOT / "server.py")] + (["--read-only"] if read_only else [])
            env = {**os.environ, "CROUSGO_CONFIG_DIR": temp}
            async with stdio_client(StdioServerParameters(command=sys.executable, args=args, env=env)) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    return (await session.list_tools()).tools

    async def test_read_only_hides_the_write_tools(self):
        self.assertEqual({t.name for t in await self.tools(True)}, READ)

    async def test_write_tools_default_to_dry_run(self):
        listing = await self.tools(False)
        self.assertEqual({t.name for t in listing}, READ | {"clear_cart", "order_menu"})
        order = next(t for t in listing if t.name == "order_menu")
        self.assertTrue(order.inputSchema["properties"]["dry_run"]["default"])
        self.assertIn("reason", order.inputSchema["required"])


if __name__ == "__main__":
    unittest.main()
