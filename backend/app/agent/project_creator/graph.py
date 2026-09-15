from app.agent.project_creator.state import MaterialState
from app.agent.project_creator.nodes.analyze import analyze_material
from app.agent.project_creator.nodes.settings import build_settings
from app.agent.project_creator.nodes.spine import plan_spine
from app.agent.project_creator.nodes.plan import plan_structure
from app.agent.project_creator.nodes.characters import design_characters
from app.agent.project_creator.nodes.blueprints import write_chapter_blueprints
from app.agent.project_creator.nodes.validate import validate

# 一个节点一件事，顺序就是它们互相依赖的顺序：
# 拆素材 → 建世界观 → 定这本书要走到哪 → 拆幕与章 → 设计人物 → 逐幕写章蓝图 → 校验。
# 场景层与章间连线已删除：这条流水线只产出 幕 / 章 / 人物 / 角色关系。
NODE_STEPS = [
    ("analyze_material", analyze_material),
    ("build_settings", build_settings),
    ("plan_spine", plan_spine),
    ("plan_structure", plan_structure),
    ("design_characters", design_characters),
    ("write_chapter_blueprints", write_chapter_blueprints),
    ("validate", validate),
]


async def run_pipeline(initial_state: MaterialState):
    """按顺序跑完建项目流水线，每步 yield (节点名, 该节点的输出)。

    initial_state 会被**原地**更新：跑完之后它本身就是最终状态，
    调用方（工具层 / SSE 路由）拿它去调 writer.write_project_from_state。

    典型用法::

        async for _ in run_pipeline(state):
            pass
    """
    for name, func in NODE_STEPS:
        output = await func(initial_state)
        initial_state.update(output)
        yield name, output
