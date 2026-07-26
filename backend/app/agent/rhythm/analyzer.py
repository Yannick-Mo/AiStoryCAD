"""Rhythm analyzer — auto-annotates chapters, compares against genre baselines."""

import statistics
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.llm.client import LLMError, get_shared_client
from app.llm.types import Message
from app.storycad.models import ChapterRhythm, Chapter, Scene, SceneContent
from app.project.models import Project


class RhythmAnalyzer:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._baselines = self._genres_baseline()

    async def analyze(self, project_id: str) -> dict:
        chapters_data = await self._load_chapters(project_id)
        if not chapters_data:
            return {
                "chapters": [],
                "genre_comparison": {},
                "overall_assessment": "No chapters found for rhythm analysis.",
                "emotion_curve": [],
                "info_density": [],
                "dialogue_ratio": [],
            }

        anomalies = self._detect_anomalies(chapters_data)
        chapters_out = []
        for cd in chapters_data:
            ch = {
                "chapter_id": str(cd["chapter_id"]),
                "title": cd["title"],
                "metrics": {
                    "action": cd["action"],
                    "suspense": cd["suspense"],
                    "emotion": cd["emotion"],
                    "humor": cd["humor"],
                    "intensity": cd["intensity"],
                },
                "word_count": cd["word_count"],
                "anomaly_score": 0.0,
                "anomaly_label": None,
                "ai_note": None,
            }
            for a in anomalies:
                if a["chapter_id"] == str(cd["chapter_id"]):
                    ch["anomaly_score"] = a["score"]
                    ch["anomaly_label"] = a["label"]
                    ch["ai_note"] = a["note"]
                    break
            chapters_out.append(ch)

        genre = await self._get_project_genre(project_id)
        genre_comparison = self._baselines.get(genre, {})
        assessment = await self._ai_assess(project_id, chapters_data)
        emotion_curve = [(i, cd["emotion"]) for i, cd in enumerate(chapters_data)]
        info_density = self._compute_info_density(chapters_data)
        dialogue_ratio = await self._compute_dialogue_ratio(chapters_data)

        return {
            "chapters": chapters_out,
            "genre_comparison": genre_comparison,
            "overall_assessment": assessment,
            "emotion_curve": emotion_curve,
            "info_density": info_density,
            "dialogue_ratio": dialogue_ratio,
        }

    async def _load_chapters(self, project_id: str) -> list[dict]:
        result = await self.db.execute(
            select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.sort_order)
        )
        chapters = result.scalars().all()

        rhythm_result = await self.db.execute(
            select(ChapterRhythm).where(ChapterRhythm.project_id == project_id)
        )
        rhythms = {str(r.chapter_id): r for r in rhythm_result.scalars().all()}

        out = []
        for ch in chapters:
            ch_id = str(ch.id)
            rhythm = rhythms.get(ch_id)
            out.append({
                "chapter_id": ch.id,
                "title": ch.title,
                "word_count": ch.total_words or 0,
                "action": rhythm.action if rhythm else 5,
                "suspense": rhythm.suspense if rhythm else 5,
                "emotion": rhythm.emotion if rhythm else 5,
                "humor": rhythm.humor if rhythm else 5,
                "intensity": rhythm.intensity if rhythm else 5,
            })
        return out

    async def _get_project_genre(self, project_id: str) -> str:
        result = await self.db.execute(select(Project).where(Project.id == project_id))
        proj = result.scalar_one_or_none()
        return proj.genre if proj and proj.genre else ""

    def _detect_anomalies(self, chapters_data: list[dict]) -> list[dict]:
        if len(chapters_data) < 2:
            return []

        metrics = ["action", "suspense", "emotion", "humor", "intensity"]
        values = {m: [cd[m] for cd in chapters_data] for m in metrics}
        means = {m: statistics.mean(v) for m, v in values.items()}
        stdevs = {
            m: statistics.stdev(v) if len(v) > 1 and statistics.stdev(v) > 0 else 1.0
            for m, v in values.items()
        }

        anomaly_labels = {
            "action": ("动作过强", "动作偏低"),
            "suspense": ("悬疑过强", "悬疑不足"),
            "emotion": ("情感转折点", "情感偏低"),
            "humor": ("幽默偏多", "幽默偏少"),
            "intensity": ("强度过高", "强度偏低"),
        }

        anomalies = []
        for cd in chapters_data:
            max_z = 0.0
            max_metric = None
            for m in metrics:
                z = (cd[m] - means[m]) / stdevs[m]
                if abs(z) > abs(max_z):
                    max_z = z
                    max_metric = m

            if abs(max_z) > 1.5 and max_metric:
                label_high, label_low = anomaly_labels[max_metric]
                label = label_high if max_z > 0 else label_low
                anomalies.append({
                    "chapter_id": str(cd["chapter_id"]),
                    "score": round(max_z, 2),
                    "label": label,
                    "metric": max_metric,
                    "note": f"{label}: z={max_z:.2f}, {max_metric}={cd[max_metric]} (均值={means[max_metric]:.1f})",
                })

        return anomalies

    def _compute_info_density(self, chapters_data: list[dict]) -> list[tuple[int, float]]:
        if not chapters_data:
            return []
        word_counts = [cd["word_count"] for cd in chapters_data]
        max_wc = max(word_counts) if max(word_counts) > 0 else 1
        return [(i, round(cd["word_count"] / max_wc, 4)) for i, cd in enumerate(chapters_data)]

    async def _compute_dialogue_ratio(self, chapters_data: list[dict]) -> list[tuple[int, float]]:
        if not chapters_data:
            return []

        chapter_ids = [cd["chapter_id"] for cd in chapters_data]
        stmt = (
            select(Scene.chapter_id, SceneContent.content)
            .join(SceneContent, SceneContent.scene_id == Scene.id)
            .where(Scene.chapter_id.in_(chapter_ids))
        )
        rows = (await self.db.execute(stmt)).all()

        chapter_scenes: dict[str, list[str]] = {}
        for chapter_id, content in rows:
            key = str(chapter_id)
            if key not in chapter_scenes:
                chapter_scenes[key] = []
            chapter_scenes[key].append(content or "")

        ratios = []
        for i, cd in enumerate(chapters_data):
            cid = str(cd["chapter_id"])
            contents = chapter_scenes.get(cid, [])
            if not contents:
                ratios.append((i, 0.5))
                continue

            total_chars = 0
            dialogue_chars = 0
            for text in contents:
                total_chars += len(text)
                in_quote = False
                for ch in text:
                    if ch in ('"', '\u201c', '\u300c'):
                        in_quote = True
                    elif ch in ('"', '\u201d', '\u300d'):
                        if in_quote:
                            dialogue_chars += 1
                        in_quote = False
                    elif in_quote:
                        dialogue_chars += 1

            ratio = round(dialogue_chars / total_chars, 4) if total_chars > 0 else 0.5
            ratios.append((i, max(0.0, min(1.0, ratio))))

        return ratios

    def _genres_baseline(self) -> dict:
        return {
            "网络爽文": {
                "action": {"min": 6, "max": 10, "typical": 8},
                "suspense": {"min": 3, "max": 7, "typical": 5},
                "emotion": {"min": 3, "max": 6, "typical": 4},
                "humor": {"min": 2, "max": 5, "typical": 3},
                "intensity": {"min": 6, "max": 10, "typical": 8},
            },
            "悬疑": {
                "action": {"min": 3, "max": 7, "typical": 5},
                "suspense": {"min": 7, "max": 10, "typical": 9},
                "emotion": {"min": 3, "max": 6, "typical": 4},
                "humor": {"min": 1, "max": 4, "typical": 2},
                "intensity": {"min": 5, "max": 9, "typical": 7},
            },
            "言情": {
                "action": {"min": 2, "max": 5, "typical": 3},
                "suspense": {"min": 2, "max": 5, "typical": 3},
                "emotion": {"min": 7, "max": 10, "typical": 9},
                "humor": {"min": 3, "max": 7, "typical": 5},
                "intensity": {"min": 3, "max": 7, "typical": 5},
            },
            "科幻": {
                "action": {"min": 4, "max": 8, "typical": 6},
                "suspense": {"min": 5, "max": 9, "typical": 7},
                "emotion": {"min": 3, "max": 6, "typical": 4},
                "humor": {"min": 2, "max": 5, "typical": 3},
                "intensity": {"min": 5, "max": 9, "typical": 7},
            },
            "奇幻": {
                "action": {"min": 5, "max": 9, "typical": 7},
                "suspense": {"min": 4, "max": 8, "typical": 6},
                "emotion": {"min": 3, "max": 7, "typical": 5},
                "humor": {"min": 3, "max": 6, "typical": 4},
                "intensity": {"min": 5, "max": 9, "typical": 7},
            },
            "历史": {
                "action": {"min": 4, "max": 8, "typical": 6},
                "suspense": {"min": 4, "max": 7, "typical": 5},
                "emotion": {"min": 4, "max": 7, "typical": 6},
                "humor": {"min": 2, "max": 5, "typical": 3},
                "intensity": {"min": 4, "max": 7, "typical": 5},
            },
            "恐怖": {
                "action": {"min": 3, "max": 7, "typical": 5},
                "suspense": {"min": 8, "max": 10, "typical": 9},
                "emotion": {"min": 4, "max": 8, "typical": 6},
                "humor": {"min": 0, "max": 3, "typical": 1},
                "intensity": {"min": 7, "max": 10, "typical": 9},
            },
        }

    async def _ai_assess(self, project_id: str, chapters_data: list[dict]) -> str:
        try:
            client = get_shared_client().fork()
            metrics_summary = "\n".join(
                f"章节{i+1}: 动作={cd['action']} 悬疑={cd['suspense']} 情感={cd['emotion']} 幽默={cd['humor']} 强度={cd['intensity']} (字数={cd['word_count']})"
                for i, cd in enumerate(chapters_data)
            )
            prompt = f"""分析以下小说章节的节奏数据，给出整体的节奏评估（50-100字）：
项目ID: {project_id}
各章节节奏数据：
{metrics_summary}"""
            result = await client.chat(
                messages=[
                    Message(role="system", content="你是一部小说写作助手的节奏分析专家。用中文回答，简洁专业。"),
                    Message(role="user", content=prompt),
                ],
                temperature=0.3,
                max_tokens=512,
            )
            return result.content or self._rule_assessment(chapters_data)
        except LLMError:
            return self._rule_assessment(chapters_data)

    def _rule_assessment(self, chapters_data: list[dict]) -> str:
        if not chapters_data:
            return "暂无章节数据"
        avg_intensity = statistics.mean(cd["intensity"] for cd in chapters_data)
        avg_emotion = statistics.mean(cd["emotion"] for cd in chapters_data)
        avg_action = statistics.mean(cd["action"] for cd in chapters_data)

        parts = []
        if avg_intensity > 7:
            parts.append("整体节奏紧张激烈")
        elif avg_intensity < 4:
            parts.append("整体节奏较为平缓")
        else:
            parts.append("整体节奏适中")

        if avg_emotion > 7:
            parts.append("情感渲染充分")
        elif avg_emotion < 4:
            parts.append("情感表达较为克制")

        if avg_action > 7:
            parts.append("动作场面丰富")
        elif avg_action < 4:
            parts.append("动作元素较少")

        return "，".join(parts) + "。"
