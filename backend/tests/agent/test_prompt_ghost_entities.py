"""Guard: no prompt may teach a **deleted entity** as if it still exists.

``test_prompt_ghost_tools.py`` already guards tool *names*. This guards entity
*nouns* — the other half of the same drift, and the half that bit us hardest.

What happened
-------------
The scene layer and the chapter-edge layer were removed (migrations 0020, 0018).
The tool registry changed with them, so the ghost-tool guard went quiet. But the
**entity nouns stayed in the prompts** — three summary/compression prompts still
listed 场景 and 连线 as things to preserve, teaching the model to refer to
entities that no longer exist. Nothing failed: those prompts are prose, not code,
so no schema test and no tool test could see them.

  - ``context_compressor`` 两份摘要提示词
  - ``middle_compress`` 的压缩规则（还在教"保留幕/章/场景的名称、排序号、ID"）
  - ``memory/history_manager`` 的历史摘要提示词

How this checks
---------------
Only **string literals that can reach a prompt** — via ``ast``, so comments and
docstrings (which legitimately discuss the removal) are never flagged. YAML
prompts are read raw, since every line in them is prompt text.

Legitimate uses that are NOT entity lists
-----------------------------------------
场景 and 主题 have ordinary-language uses unrelated to the deleted layers:

  - ``_is_meaningful_query`` 的关键词表——用户问"如何写打斗场景"必须命中
  - ``knowledge_tools`` 的例句 "如何写打斗场景"
  - ``response_builder`` 的 "适用场景："（UI 文案）
  - ``inspiration`` 的 ``opening_scene``（开篇场景，灵感字段）
  - ``writer_agent`` 的 "complete scene body"（叙事情节段落）

None of those are enumerations, which is why the check is narrow on purpose.
"""
import ast
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parents[2] / "app" / "agent"
_PROMPTS_DIR = _AGENT_DIR / "prompts"

DEAD_NOUNS = {
    "场景": "章（scene 层已随迁移 0020 删除）",
    "连线": "伏笔 Setup（章间连线层已随迁移 0020 删除）",
}

# 枚举式清单才算违规：场景/章节/角色、幕/章/场景 这类并列写法
_ENUM_RE = r"[（(][^）)\n]{0,80}场景[^）)\n]{0,80}[）)]"
_SLASH_RE = r"[^，。；：\n]{0,30}场景/[^，。；：\n]{0,60}"

# 豁免的是**具体那一句**，不是整个文件。要写清为什么它是普通汉语。
LITERAL_ALLOW: list[tuple[str, str]] = [
    ("tools/knowledge_tools.py",
     "'如何写打斗场景' 是用户提问的例句——普通汉语，不是实体清单"),
]


def _allowed(rel: str, lit: str) -> str | None:
    for suffix, reason in LITERAL_ALLOW:
        if rel.endswith(suffix) and any(
            token in lit for token in ("如何写打斗场景", "适用场景")
        ):
            return reason
    return None


def _prompt_literals(path: Path) -> list[str]:
    """Return string literals in a .py file, excluding docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in docstrings
    ]


def test_no_prompt_enumerates_a_deleted_entity():
    """枚举式实体清单里不许出现已删实体（只看会进提示词的字符串）。"""
    import re

    enum_re = re.compile(_ENUM_RE)
    slash_re = re.compile(_SLASH_RE)
    offenders: list[str] = []

    for p in sorted(_AGENT_DIR.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(_AGENT_DIR.parent).as_posix()
        for i, lit in enumerate(_prompt_literals(p)):
            for noun in DEAD_NOUNS:
                if noun not in lit:
                    continue
                if not (enum_re.search(lit) or slash_re.search(lit)):
                    continue
                if _allowed(rel, lit):
                    continue
                offenders.append(f"{rel} 字面量#{i}: {lit.strip()[:110]}")

    for y in sorted(_PROMPTS_DIR.glob("*.yaml")):
        text = y.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(n in line for n in DEAD_NOUNS) and (
                enum_re.search(line) or slash_re.search(line)
            ):
                offenders.append(f"{y.name}:{lineno}: {line.strip()[:110]}")

    assert not offenders, (
        "这些提示词还在把已删实体当现存实体教给模型：\n  "
        + "\n  ".join(offenders)
        + f"\n\n已删概念与现在的叫法：{DEAD_NOUNS}"
    )


def test_compression_prompts_name_only_live_entities():
    """压缩/摘要提示词里不许再出现已删实体 —— 这一条不挑写法。"""
    for p in (
        _AGENT_DIR / "context_compressor.py",
        _AGENT_DIR / "middle_compress.py",
        _AGENT_DIR / "memory" / "history_manager.py",
    ):
        assert p.exists(), p
        joined = "\n".join(_prompt_literals(p))
        for noun in DEAD_NOUNS:
            assert noun not in joined, (
                f"{p.name} 的提示词字面量里还有 {noun!r}"
                f"（{DEAD_NOUNS[noun]}）——它在教模型引用不存在的东西"
            )


def test_response_builder_does_not_read_a_key_nobody_writes():
    """``project_ctx["edges"]`` 的连线统计是死分支。

    连线层删除后全仓再没有一处写这个键，``len([])==0`` 永远为假。
    死分支比没有更糟：它让人以为连线还在。伏笔条数改由 setups 承担。
    """
    joined = "\n".join(_prompt_literals(_AGENT_DIR / "response_builder.py"))
    assert '"edges"' not in joined, "response_builder 还在读 project_ctx['edges']"
    assert "条连线" not in joined
    assert "setups" in joined, "伏笔条数应该由 setups 承担"


def test_middle_compress_no_longer_teaches_scene_ids():
    """它还在教"保留幕/章/场景的名称、排序号、ID" —— 场景没有 ID 可保了。"""
    joined = "\n".join(_prompt_literals(_AGENT_DIR / "middle_compress.py"))
    assert "场景" not in joined
    assert "幕/章节" in joined or "幕/章" in joined