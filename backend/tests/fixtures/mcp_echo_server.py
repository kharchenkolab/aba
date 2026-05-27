"""Tiny demo MCP server for P3 #1 gateway tests. Single tool 'echo'."""
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

app = Server("echo-demo")


@app.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="echo",
            description="Echo the input text back.",
            inputSchema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        ),
        types.Tool(
            name="add",
            description="Add two integers.",
            inputSchema={"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "echo":
        return [types.TextContent(type="text", text=str(arguments.get("text", "")))]
    if name == "add":
        a = arguments.get("a", 0)
        b = arguments.get("b", 0)
        return [types.TextContent(type="text", text=str(a + b))]
    raise ValueError(f"unknown tool: {name}")


async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
