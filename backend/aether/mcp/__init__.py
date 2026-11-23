"""MCP (Model Context Protocol) client support: external tool servers next to the built-in tools."""
from .client import MCPError, MCPServer
from .manager import MCPManager, tool_name

__all__ = ["MCPError", "MCPManager", "MCPServer", "tool_name"]
