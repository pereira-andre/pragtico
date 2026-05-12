from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

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


def test_command_token_matches_rank_before_keyword_matches() -> None:
    if not shutil.which("node"):
        pytest.skip("node is required to exercise the browser autocomplete script")

    script = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");

        const context = {
          console,
          window: { setTimeout },
          document: {
            createElement() {
              return {
                dataset: {},
                className: "",
                type: "",
                innerHTML: "",
                scrollIntoView() {},
              };
            },
          },
        };
        vm.runInNewContext(fs.readFileSync("static/js/chat-command-autocomplete.js", "utf8"), context);

        function matchesFor(value) {
          const textarea = {
            value,
            addEventListener() {},
            dispatchEvent() {},
            focus() {},
            setSelectionRange() {},
          };
          const panel = {
            children: [],
            innerHTML: "",
            classList: {
              values: new Set(["hidden"]),
              add(value) { this.values.add(value); },
              remove(value) { this.values.delete(value); },
              contains(value) { return this.values.has(value); },
            },
            appendChild(child) { this.children.push(child); },
            querySelector() { return this.children.find((child) => child.className.includes("active")) || null; },
            addEventListener() {},
          };
          const autocomplete = context.window.PRAGticoChatCommandAutocomplete.attach({ textarea, panel, role: "admin" });
          autocomplete.refresh();
          return panel.children.map((child) => (child.innerHTML.match(/<strong>([^<]+)<\\/strong>/) || [])[1]);
        }

        const cases = {
          "/it": "/it 015",
          "/regra": "/regra 015",
          "/rieam": "/rieam",
          "/verificar": "/verificar",
          "/nova-escala": "/nova-escala",
        };
        for (const [query, expected] of Object.entries(cases)) {
          const first = matchesFor(query)[0];
          if (first !== expected) {
            throw new Error(`${query} matched ${first}, expected ${expected}`);
          }
        }
        """
    )

    subprocess.run(["node", "-e", script], check=True, cwd=Path.cwd())
