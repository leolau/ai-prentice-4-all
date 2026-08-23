"""The pages of agent-home, for the agent's orientation.

Static on purpose: the registry of screens lives in the app
(`agent-home/src/components/coral/coral-apps.ts`), and this list mirrors it
so ``app_pages`` can answer without a browser round-trip. Dynamic element
knowledge comes from the live snapshot instead — this is only the map.
"""
from __future__ import annotations

PAGES: list[dict[str, str]] = [
    {"path": "/", "name": "Home", "purpose": "Daily overview and shortcuts"},
    {"path": "/chat", "name": "Chats", "purpose": "Full conversations with the agent"},
    {"path": "/todos", "name": "Tasks", "purpose": "To-dos: open, staged, done"},
    {"path": "/todos/[id]", "name": "Task detail", "purpose": "One to-do's detail and actions"},
    {"path": "/inbox", "name": "Inbox", "purpose": "Incoming items to triage"},
    {"path": "/inbox/[id]", "name": "Inbox item", "purpose": "One incoming item"},
    {"path": "/memory", "name": "Memory", "purpose": "The agent's long-term memory map"},
    {"path": "/projects", "name": "Projects", "purpose": "Project list"},
    {"path": "/projects/[slug]", "name": "Project", "purpose": "One project: board, runs, directives"},
    {"path": "/projects/[slug]/runs/[runNo]", "name": "Project run", "purpose": "One project run's detail"},
    {"path": "/projects/[slug]/cards/[id]", "name": "Board card", "purpose": "One board card"},
    {"path": "/projects/new", "name": "New project", "purpose": "Create a project"},
    {"path": "/files", "name": "Files", "purpose": "Shared file registry"},
    {"path": "/activity", "name": "Activity", "purpose": "Agent activity traces"},
    {"path": "/activity/[traceId]", "name": "Trace", "purpose": "One activity trace"},
    {"path": "/graph", "name": "Graph", "purpose": "Communication graph"},
    {"path": "/capacity", "name": "Capacity", "purpose": "Capacity overview"},
    {"path": "/users", "name": "Users", "purpose": "Members and roles"},
    {"path": "/members", "name": "Members", "purpose": "Comms members"},
    {"path": "/profiles/suggestions", "name": "Suggestions", "purpose": "Profile suggestions"},
    {"path": "/tools", "name": "Tools", "purpose": "Agent toolsets"},
    {"path": "/core", "name": "Core", "purpose": "Core status"},
    {"path": "/webview", "name": "Webview", "purpose": "Agent-driven webview console"},
    {"path": "/settings", "name": "Settings", "purpose": "App settings"},
    {"path": "/onboarding", "name": "Onboarding", "purpose": "First-run onboarding"},
]
