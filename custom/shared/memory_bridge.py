#!/usr/bin/env python3
"""
Memory Bridge

Pushes facts from standalone triage agents (WhatsApp, email, calendar) into
Hermes's long-term memory file (MEMORY.md). The agent reads this file at
session start via tools/memory_tool.py, so facts pushed here appear in the
agent's system prompt snapshot for the next conversation.

Memory entries use the § (section sign) delimiter, matching the format that
tools/memory_tool.py expects. Each fact is prefixed with a provenance tag
showing the source channel, date, and sender.

Configuration (read from the triage agent's config.json under the "memory" key):
    enabled            — if false, no-op (default: true)
    memory_file        — override path to MEMORY.md (default: $HERMES_HOME/memories/MEMORY.md)
    max_facts_per_batch — cap to prevent flooding memory (default: 10)
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ENTRY_DELIMITER = "\n§\n"

# Cache the config so we don't re-read config.json on every batch.
_config_cache = None
_config_path_cache = None


def _get_memory_config(config_path=None):
    """Read memory config from config.json. Cached per path."""
    global _config_cache, _config_path_cache
    if config_path and config_path == _config_path_cache and _config_cache is not None:
        return _config_cache

    config = {}
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path) as f:
                full_config = json.load(f)
            config = full_config.get('memory', {})
        except Exception:
            pass

    _config_cache = config
    _config_path_cache = config_path
    return config


def _get_memory_file(config_path=None):
    """Resolve the MEMORY.md path."""
    mem_cfg = _get_memory_config(config_path)
    override = mem_cfg.get('memory_file')
    if override:
        return Path(override)

    hermes_home = os.environ.get('HERMES_HOME', '')
    if hermes_home:
        return Path(hermes_home) / 'memories' / 'MEMORY.md'

    # Fallback: ~/.hermes/memories/MEMORY.md
    return Path.home() / '.hermes' / 'memories' / 'MEMORY.md'


def _is_enabled(config_path=None):
    """Check if the memory bridge is enabled."""
    mem_cfg = _get_memory_config(config_path)
    return mem_cfg.get('enabled', True)


def _get_max_facts(config_path=None):
    """Get the max facts per batch cap."""
    mem_cfg = _get_memory_config(config_path)
    return mem_cfg.get('max_facts_per_batch', 10)


def remember_facts(facts, *, source, sender, config_path=None):
    """Append facts to MEMORY.md using the § delimiter.

    Each fact is a self-contained statement. The agent will see these in its
    system prompt snapshot at the next session start.

    Args:
        facts: list of {"fact": str, "category": str} dicts
        source: 'whatsapp' | 'email' | 'calendar'
        sender: phone number or email of the sender
        config_path: path to the triage agent's config.json (optional)

    Returns:
        Number of facts successfully written, or 0 if disabled/failed.
    """
    if not _is_enabled(config_path):
        return 0

    if not facts or not isinstance(facts, list):
        return 0

    max_facts = _get_max_facts(config_path)
    facts = facts[:max_facts]  # cap to prevent flooding

    memory_file = _get_memory_file(config_path)

    # Ensure the directory exists
    memory_file.parent.mkdir(parents=True, exist_ok=True)

    # Build the text to append
    now = datetime.now(timezone.utc)
    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M')

    entries = []
    for fact_entry in facts:
        if isinstance(fact_entry, dict):
            fact_text = fact_entry.get('fact', '').strip()
            category = fact_entry.get('category', '')
        elif isinstance(fact_entry, str):
            fact_text = fact_entry.strip()
            category = ''
        else:
            continue

        if not fact_text:
            continue

        # Build a provenance-prefixed entry
        provenance = f"[{source} {date_str} {time_str} from {sender}]"
        entry = f"{provenance} {fact_text}"
        entries.append(entry)

    if not entries:
        return 0

    # Read existing content
    try:
        existing = memory_file.read_text(encoding='utf-8') if memory_file.exists() else ''
    except Exception:
        existing = ''

    # Append with § delimiter
    # If the file is empty, start with the first entry (no leading delimiter).
    # If it has content, add a § delimiter before the new entries.
    new_block = ENTRY_DELIMITER.join(entries)

    if existing:
        # Ensure existing content ends with a newline before the delimiter
        if not existing.endswith('\n'):
            existing += '\n'
        content = existing + ENTRY_DELIMITER + new_block
    else:
        content = new_block

    # Atomic write via temp file + rename
    tmp_file = memory_file.with_suffix('.md.tmp')
    try:
        tmp_file.write_text(content, encoding='utf-8')
        tmp_file.replace(memory_file)
    except Exception as e:
        print(f"[memory_bridge] Failed to write {memory_file}: {e}")
        try:
            tmp_file.unlink(missing_ok=True)
        except Exception:
            pass
        return 0

    return len(entries)
