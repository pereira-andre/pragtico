from __future__ import annotations

import re
from pathlib import Path

from domain.chat_action_config import SLASH_COMMAND_ALIASES


def _autocomplete_command_tokens() -> set[str]:
    script = Path("static/js/chat-command-autocomplete.js").read_text(encoding="utf-8")
    direct_commands = re.findall(r'command:\s*"/([^"\s]+)', script)
    alias_commands = re.findall(r'aliasFrom\("[^"]+",\s*"/([^"\s]+)"', script)
    return set(direct_commands + alias_commands)


def test_it_command_is_visible_in_initial_autocomplete_batch() -> None:
    script = Path("static/js/chat-command-autocomplete.js").read_text(encoding="utf-8")
    commands = re.findall(r'command:\s*"([^"]+)"', script)

    assert "/it 015" in commands[:8]
    assert "/regras" in commands[:8]
    assert "/regra 015" in commands[:8]


def test_every_registered_slash_alias_has_autocomplete_entry() -> None:
    missing = sorted(set(SLASH_COMMAND_ALIASES) - _autocomplete_command_tokens())

    assert missing == []
