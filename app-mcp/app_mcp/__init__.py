"""app-mcp — runtime UI introspection and control bridge for agent-home.

A standalone service on the box: a WebSocket hub the app's browser bridge
connects to (ticket-authenticated, ticket minted by the agent-home BFF), and
an MCP server the Hermes agent talks to. The agent can ask what the user is
looking at, describe every UI element on the page, and operate elements —
destructive operations go through Hermes' tool-approval flow.
"""
