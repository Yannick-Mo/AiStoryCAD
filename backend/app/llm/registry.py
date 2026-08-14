from .types import ModelDef

_registry: dict[str, ModelDef] = {}
_order: list[str] = []


def register(name: str, model: ModelDef) -> None:
    if name not in _registry:
        _registry[name] = model
        _order.append(name)


def get(name: str) -> ModelDef:
    if name not in _registry:
        raise KeyError(f"Model '{name}' is not registered")
    return _registry[name]


def get_ordered() -> list[str]:
    return list(_order)


def get_default() -> ModelDef:
    for name in _order:
        return _registry[name]
    raise KeyError("No models registered")


def list_models() -> dict[str, ModelDef]:
    return dict(_registry)


def configure_from_settings(settings) -> None:
    api_key = settings.llm_api_key
    base_url = settings.llm_base_url.rstrip("/")
    raw = settings.llm_models

    if raw:
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = [p.strip() for p in entry.split("|")]
            name = parts[0]
            key = parts[1] if len(parts) > 1 else api_key
            url = parts[2] if len(parts) > 2 else base_url
            register(name, ModelDef(api_key=key, base_url=url.rstrip("/")))
        # The raw branch bypassed the fallback registration below, so
        # SWITCH_MODEL lookups for fallback models raised KeyError and the
        # chain silently fell through.  Register them here too.
        fallback_raw = settings.llm_fallback_models
        if fallback_raw:
            for name in fallback_raw.split(","):
                name = name.strip()
                if name:
                    register(name, ModelDef(api_key=api_key, base_url=base_url))
        return

    name = settings.llm_model
    register(name, ModelDef(api_key=api_key, base_url=base_url))
    register("deepseek-chat", ModelDef(api_key=api_key, base_url=base_url))

    fallback_raw = settings.llm_fallback_models
    if fallback_raw:
        for name in fallback_raw.split(","):
            name = name.strip()
            if name:
                register(name, ModelDef(api_key=api_key, base_url=base_url))
