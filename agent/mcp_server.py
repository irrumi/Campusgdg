"""CampusPulse MCP server (FastMCP). Run: python -m agent.mcp_server  (stdio)"""
from mcp.server.fastmcp import FastMCP

from agent.tools import TOOLS

mcp = FastMCP("campuspulse")
for _fn in TOOLS:
    mcp.tool()(_fn)

if __name__ == "__main__":
    mcp.run()
