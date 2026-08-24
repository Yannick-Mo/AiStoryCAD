from .types import ModelDef

_registry: dict[str, ModelDef] = {}
_order: list[str] = []
_middle_model: str = ""
_embedding: dict[str, str] = {
    "base_url": "",
    "model": "",
    "api_key": "",
    "proxy": "",
}


def reset() -> None:
    _registry.clear()
    _order.clear()


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


def get_primary_name() -> str:
    if not _order:
        raise KeyError("No models registered")
    return _order[0]


def list_models() -> dict[str, ModelDef]:
    return dict(_registry)


def configure_from_settings(settings) -> None:
    reset()
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


def configure_from_config(cfg) -> None:
    """Rebuild the registry from a DB ModelConfig row.

    Fallback entries may be "name" (shares the main provider) or
    "name|api_key|base_url" (independent provider).
    """
    reset()
    main_key = cfg.main_api_key or ""
    main_url = (cfg.main_base_url or "https://api.deepseek.com/v1").rstrip("/")
    main_name = cfg.main_model or "deepseek-v4-flash"
    register(main_name, ModelDef(api_key=main_key, base_url=main_url))

    fallbacks = cfg.fallback_models or []
    if isinstance(fallbacks, str):
        fallbacks = [f.strip() for f in fallbacks.split(",") if f.strip()]
    for entry in fallbacks:
        parts = [p.strip() for p in str(entry).split("|")]
        name = parts[0]
        if not name or name in _registry:
            continue
        key = parts[1] if len(parts) > 1 else main_key
        url = parts[2] if len(parts) > 2 else main_url
        register(name, ModelDef(api_key=key, base_url=url.rstrip("/")))

    if cfg.middle_model:
        middle = cfg.middle_model.strip()
        if middle and middle not in _registry:
            register(middle, ModelDef(api_key=main_key, base_url=main_url))


def set_middle_model(name: str) -> None:
    global _middle_model
    _middle_model = name or ""


def get_middle_model() -> str:
    return _middle_model


def set_embedding(base_url: str = "", model: str = "", api_key: str = "", proxy: str = "") -> None:
    _embedding["base_url"] = base_url
    _embedding["model"] = model
    _embedding["api_key"] = api_key
    _embedding["proxy"] = proxy


def get_embedding() -> dict[str, str]:
    return dict(_embedding)
