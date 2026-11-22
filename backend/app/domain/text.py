from __future__ import annotations

import re

_WS = re.compile(r"\s+")
TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


def normalize(s: str) -> str:
    return _WS.sub(" ", s.strip()).casefold()


def match_option(options: list[str], user_input: str) -> str | None:
    norm = normalize(user_input)
    for opt in options:
        if normalize(opt) == norm:
            return opt
    if norm.isdigit():
        idx = int(norm)
        if 1 <= idx <= len(options):
            return options[idx - 1]
    return None


def template_vars(text: str) -> list[str]:
    return TEMPLATE_RE.findall(text)


def var_root_candidates(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(len(parts), 0, -1)]
