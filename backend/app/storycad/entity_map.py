from app.storycad.models import Act, Chapter, Scene, ChapterEdge, Character, CharacterRelation

ENTITY_MAP = {
    "acts": Act,
    "chapters": Chapter,
    "scenes": Scene,
    "edges": ChapterEdge,
    "characters": Character,
    "character_relations": CharacterRelation,
}
