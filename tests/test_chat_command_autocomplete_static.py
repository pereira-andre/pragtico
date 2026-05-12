from __future__ import annotations

import re
from pathlib import Path


def test_it_command_is_visible_in_initial_autocomplete_batch() -> None:
    script = Path("static/js/chat-command-autocomplete.js").read_text(encoding="utf-8")
    commands = re.findall(r'command:\s*"([^"]+)"', script)

    assert "/it 015" in commands[:8]
    assert "/regras" in commands[:8]
    assert "/regra 015" in commands[:8]
