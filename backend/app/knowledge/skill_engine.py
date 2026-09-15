"""File-driven skill engine — no database dependency.

Skills are loaded from YAML files in the skills/ directory.
The engine provides:
  - Listing all available skills
  - Loading individual skill YAML (with caching)
  - Matching skills against file paths (conditional activation)
  - Aggregating prompt_overrides / rag_tags from active skills
  - Hot-reload via file-system watching

Skills only provide prompt guidance (``prompt_overrides``) and knowledge
indexing tags (``rag_tags``).  They NEVER gate tool availability — the
mode (chat / cowriter) controls that.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import aiofiles
import yaml

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent / "skills"
_SKILL_CACHE_TTL = 300  # 5 minutes
_SKILL_CACHE_MAX = 50


class SkillEngine:
    """File-driven skill engine.

    All skill definitions come from YAML files in ``skills/``.
    No database queries.  Skills are "available" (loaded from YAML)
    and can be "activated" (invoked by AI, user, or file-path trigger).
    """

    def __init__(self) -> None:
        self._yaml_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._last_mtime: dict[str, float] = {}
        self._watcher_task: asyncio.Task | None = None

    # ── YAML loading with LRU cache ─────────────────────────────────

    async def _load_yaml(self, name: str) -> dict | None:
        now = time.monotonic()
        if name in self._yaml_cache:
            ts, data = self._yaml_cache[name]
            if now - ts < _SKILL_CACHE_TTL:
                self._yaml_cache.move_to_end(name)
                return data

        path = _SKILLS_DIR / f"{name}.yaml"
        if not path.exists():
            return None

        # Check mtime for hot-reload
        try:
            stat = path.stat()
            current_mtime = stat.st_mtime
            cached_mtime = self._last_mtime.get(name)
            if cached_mtime is not None and current_mtime == cached_mtime:
                # File unchanged — keep cache
                self._yaml_cache[name] = (now, self._yaml_cache.get(name, (0, {}))[1])
                self._yaml_cache.move_to_end(name)
                return self._yaml_cache[name][1]
        except OSError:
            pass

        async with aiofiles.open(path, encoding="utf-8") as f:
            content = await f.read()
        data = await asyncio.to_thread(yaml.safe_load, content)
        if not isinstance(data, dict):
            logger.warning("Skill %s is not a valid YAML dict", name)
            return None

        self._yaml_cache[name] = (now, data)
        self._yaml_cache.move_to_end(name)
        try:
            self._last_mtime[name] = path.stat().st_mtime
        except OSError:
            pass

        # Evict oldest if over limit
        while len(self._yaml_cache) > _SKILL_CACHE_MAX:
            self._yaml_cache.popitem(last=False)

        return data

    def _invalidate_cache(self, name: str | None = None) -> None:
        if name:
            self._yaml_cache.pop(name, None)
            self._last_mtime.pop(name, None)
        else:
            self._yaml_cache.clear()
            self._last_mtime.clear()

    # ── List all available skills ───────────────────────────────────

    def list_all_skills(self) -> list[str]:
        """Return sorted list of all skill names from YAML files."""
        if not _SKILLS_DIR.exists():
            return []
        return sorted(
            p.stem for p in _SKILLS_DIR.glob("*.yaml") if p.stem != "__init__"
        )

    async def get_all_skills_meta(self) -> list[dict[str, Any]]:
        """Return metadata for ALL available skills (name, description, when_to_use, etc.)."""
        result: list[dict[str, Any]] = []
        for name in self.list_all_skills():
            data = await self._load_yaml(name)
            if data is None:
                continue
            result.append({
                "name": data.get("name", name),
                "genre": data.get("genre", ""),
                "description": data.get("description", ""),
                "when_to_use": data.get("when_to_use", ""),
                "user_invocable": data.get("user_invocable", True),
                "paths": data.get("paths", []),
                "aliases": data.get("aliases", []),
                "rag_tags": data.get("rag_tags", []),
            })
        return result

    # ── Get individual skill ────────────────────────────────────────

    async def get_skill(self, skill_name: str) -> dict | None:
        """Load a single skill's full YAML data by file stem, display name, or alias."""
        # Try direct file name first
        data = await self._load_yaml(skill_name)
        if data is not None:
            return data
        # Fall back: search by display name or alias
        for name in self.list_all_skills():
            data = await self._load_yaml(name)
            if data is None:
                continue
            if data.get("name") == skill_name:
                return data
            aliases = data.get("aliases", []) or []
            if skill_name in aliases:
                return data
        return None

    async def get_skill_by_name(self, name_or_stem: str) -> dict | None:
        """Convenience: look up by stem first, then display name."""
        return await self.get_skill(name_or_stem)

    # ── Merged data from active skills ──────────────────────────────

    async def get_merged_prompts(
        self, skill_names: list[str]
    ) -> dict[str, str]:
        """Merge prompt_overrides from a list of active skill names."""
        merged: dict[str, str] = {}
        for sname in skill_names:
            data = await self.get_skill(sname)
            if data is None:
                continue
            overrides = data.get("prompt_overrides", {}) or {}
            merged.update(overrides)
        return merged

    async def get_merged_tags(self, skill_names: list[str]) -> list[str]:
        """Aggregate rag_tags from a list of active skill names."""
        tags: set[str] = set()
        for sname in skill_names:
            data = await self.get_skill(sname)
            if data is None:
                continue
            skill_tags = data.get("rag_tags", []) or []
            tags.update(skill_tags)
        return sorted(tags)

    # ── File-path conditional activation ────────────────────────────

    async def match_skill_paths_async(
        self, file_paths: list[str]
    ) -> list[str]:
        """Async version of match_skill_paths."""
        matched: set[str] = set()
        for name in self.list_all_skills():
            data = await self._load_yaml(name)
            if data is None:
                continue
            patterns = data.get("paths", []) or []
            if not patterns:
                continue
            for fpath in file_paths:
                norm_path = fpath.replace("\\", "/")
                for pat in patterns:
                    if fnmatch.fnmatch(norm_path, pat):
                        matched.add(name)
                        break
                if name in matched:
                    break
        return sorted(matched)

    # ── Hot-reload support ──────────────────────────────────────────

    async def reload_skill(self, name: str) -> bool:
        """Force-reload a single skill YAML. Returns True if reloaded."""
        self._invalidate_cache(name)
        data = await self._load_yaml(name)
        return data is not None

    async def reload_all(self) -> int:
        """Force-reload all skill YAMLs. Returns count loaded."""
        self._invalidate_cache()
        count = 0
        for name in self.list_all_skills():
            if await self._load_yaml(name) is not None:
                count += 1
        return count


_shared_engine = SkillEngine()
