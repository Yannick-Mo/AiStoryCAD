from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jinja2
import yaml
from loguru import logger

_PROMPT_DIR = Path(__file__).parent


class _ReportingUndefined(jinja2.Undefined):
    """Jinja2 undefined that warns on stringification and errors on comparison.

    Unlike the previous _PreservingUndefined (which silently returned
    ``{{variable_name}}`` on ``__str__`` and always-``False`` on ``__eq__``),
    this class:
    - Logs a warning when an undefined variable is coerced to string.
    - Raises ``TypeError`` when compared (``==`` / ``!=``), catching bugs.
    - Returns ``False`` on ``__bool__`` so ``{% if var %}`` skips cleanly.
    """

    def __str__(self) -> str:
        logger.warning("Undefined template variable: {}", self._undefined_name)
        return "{{" + self._undefined_name + "}}"

    def __eq__(self, other: object) -> bool:
        logger.warning(
            "Undefined template variable '{}' compared via == — returning False. "
            "Add |default(...) to the template to suppress this warning.",
            self._undefined_name,
        )
        return False

    def __ne__(self, other: object) -> bool:
        logger.warning(
            "Undefined template variable '{}' compared via != — returning True. "
            "Add |default(...) to the template to suppress this warning.",
            self._undefined_name,
        )
        return True

    __bool__ = __nonzero__ = lambda self: False  # type: ignore[assignment]


class PromptTemplate:
    """Jinja2-based template renderer for prompt files."""

    def __init__(self, template: str):
        env = jinja2.Environment(
            undefined=_ReportingUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        # Custom filter for slice syntax not natively supported by Jinja2 loops.
        # Usage: {% for item in items|take(5) %} instead of items[:5]
        env.filters["take"] = (
            lambda value, n: value[:n] if isinstance(value, (list, tuple)) else value
        )
        self._template = env.from_string(template)

    def render(self, **kwargs: Any) -> str:
        result = self._template.render(**kwargs)

        dangling = re.findall(r"\{\{.+?\}\}", result)
        if dangling:
            for d in dangling[:5]:
                logger.warning("Unresolved template placeholder: {}", d)

        return result


class PromptLoader:
    """Loads and caches all prompt YAML files at construction time."""

    def __init__(self):
        self._cache: dict[str, PromptTemplate] = {}
        self._preload_all()

    def _preload_all(self) -> None:
        for yaml_file in _PROMPT_DIR.glob("*.yaml"):
            name = yaml_file.stem
            # Skip non-template YAML files (e.g. system.yaml used by builder.py)
            if name == "system":
                continue
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and "system" in data:
                    self._cache[name] = PromptTemplate(data["system"])
                else:
                    logger.warning("Prompt file {} has no 'system' key", yaml_file)
            except Exception:
                logger.exception("Failed to pre-load prompt: {}", yaml_file)

    def load(self, name: str) -> PromptTemplate | None:
        return self._cache.get(name)

    def render(self, name: str, **kwargs: Any) -> str | None:
        tpl = self.load(name)
        if tpl is None:
            return None
        return tpl.render(**kwargs)


_global_loader = PromptLoader()


def get_prompt_loader() -> PromptLoader:
    return _global_loader


def render_prompt(name: str, **kwargs: Any) -> str | None:
    return _global_loader.render(name, **kwargs)
