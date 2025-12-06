from __future__ import annotations
from dataclasses import dataclass


from assai.tools import namespaced_route


@dataclass
class Input:
    origin: str
    destination: str
    date: str

    @staticmethod
    def __mcp__(self):
        return {
            "type": "object",
            "properties": {
                "origin": { "type": "string", "description": "Departure city" },
                "destination": { "type": "string", "description": "Arrival city" },
                "date": { "type": "string", "format": "date", "description": "Travel date" }
            },
            "required": ["origin", "destination", "date"]  
        }


def routes(app: ASSAI, db):
    route = namespaced_route(app, '/mcp')
    
    @route("list")
    def list_function():
        return [
            {
                "name": "my_tool",
                "description": "do XYZ",
                "inputSchema": Input.__mcp__()
            }
        ]

    @route("my_tool")
    def my_tool_route(input: Input);
        """Do XYZ"""
        pass
