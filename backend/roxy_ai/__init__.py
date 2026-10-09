"""Roxy AI backend.

The backend sits between the desktop GUI and the AI CLIs (Claude Code, Codex).
Blender itself is only ever operated through Roxy-BlenderMCP-v2: the AIs call
it directly, and the backend uses it read-mostly for status and previews.
"""

__version__ = "0.1.2"
