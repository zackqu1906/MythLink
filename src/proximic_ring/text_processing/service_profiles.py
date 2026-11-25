"""Persist independent endpoint, model and credential settings for each text service."""
from dataclasses import replace
from urllib.parse import urlsplit

from .local_server import DEFAULT_LOCAL_BASE_URL, DEFAULT_LOCAL_MODEL
from .model import (
    ARK_DEEPSEEK_FLASH_MODEL, DEFAULT_ARK_API_KEY_ENV, DEFAULT_ARK_BASE_URL, DEFAULT_ARK_MODEL,
    DEFAULT_BAILIAN_API_KEY_ENV, DEFAULT_BAILIAN_BASE_URL, DEFAULT_QWEN_MODEL,
    LLM_PROVIDER_BAILIAN, LLM_PROVIDER_LOCAL, LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_VOLCENGINE, LLMSettings, normalize_llm_provider,
)


_DEFAULTS = {
    LLM_PROVIDER_BAILIAN: (DEFAULT_BAILIAN_BASE_URL, DEFAULT_QWEN_MODEL, DEFAULT_BAILIAN_API_KEY_ENV),
    LLM_PROVIDER_VOLCENGINE: (DEFAULT_ARK_BASE_URL, DEFAULT_ARK_MODEL, DEFAULT_ARK_API_KEY_ENV),
    LLM_PROVIDER_LOCAL: (DEFAULT_LOCAL_BASE_URL, DEFAULT_LOCAL_MODEL, ""),
    LLM_PROVIDER_OPENAI: ("https://api.openai.com/v1", "gpt-5.6-luna", "OPENAI_API_KEY"),
}
_FIELDS = {"baseUrl": "base_url", "model": "model", "apiKeyEnv": "api_key_env", "apiKey": "api_key"}


def _read(store, provider, prefix):
    url, model, key_env = _DEFAULTS[provider]
    default = LLMSettings(provider=provider, enabled=True, base_url=url, model=model, api_key_env=key_env)
    values = {attr: str(store.value(f"{prefix}/{key}", getattr(default, attr)) or "").strip()
              for key, attr in _FIELDS.items()}
    if provider == LLM_PROVIDER_VOLCENGINE and values["model"] == "deepseek-v4-flash-260425":
        values["model"] = DEFAULT_ARK_MODEL
    return replace(default, **values)


def _write(store, prefix, profile):
    for key, attr in _FIELDS.items():
        store.setValue(f"{prefix}/{key}", getattr(profile, attr))


def screen_context_service(store, *, timeout_s: float) -> LLMSettings:
    """Use the current Ark credentials for screen OCR, without switching the editor."""
    prefix = ("llm" if store.value("llm/provider") == LLM_PROVIDER_VOLCENGINE
              else f"llm/profiles/{LLM_PROVIDER_VOLCENGINE}")
    ark = _read(store, LLM_PROVIDER_VOLCENGINE, prefix)
    return replace(ark, model=ARK_DEEPSEEK_FLASH_MODEL, timeout_s=timeout_s)


def select_service(store, provider, previous):
    """Archive the outgoing profile before exposing the new active fields."""
    if previous.provider != LLM_PROVIDER_LOCAL:
        _write(store, f"llm/profiles/{previous.provider}", previous)
        store.setValue("llm/lastOnlineProvider", previous.provider)
    active = _read(store, provider, f"llm/profiles/{provider}")
    _write(store, "llm", active)
    store.setValue("llm/provider", provider)
    if provider != LLM_PROVIDER_LOCAL:
        store.setValue("llm/lastOnlineProvider", provider)
    return active


def load_service(store):
    provider = str(store.value("llm/provider", "") or "").strip()
    url = str(store.value("llm/baseUrl", "") or "").strip()
    # Older builds sometimes kept the previous online profile in the active
    # fields while local was selected. Recover it before replacing those fields.
    try:
        host = urlsplit(url).hostname
    except ValueError:
        # Keep malformed custom addresses editable in Settings.
        host = None
    inferred = (LLM_PROVIDER_LOCAL if host in {"127.0.0.1", "::1", "localhost"} else
                LLM_PROVIDER_VOLCENGINE if host == "ark.cn-beijing.volces.com" else
                LLM_PROVIDER_OPENAI if url else LLM_PROVIDER_BAILIAN)
    active_provider = normalize_llm_provider(provider or inferred)
    prefix = "llm" if provider or url else f"llm/profiles/{active_provider}"
    active = _read(store, active_provider, prefix)
    if not store.contains("llm/separateProfilesV1"):
        if active.provider == LLM_PROVIDER_LOCAL and url and inferred != LLM_PROVIDER_LOCAL:
            active = select_service(store, LLM_PROVIDER_LOCAL, _read(store, inferred, "llm"))
        elif active.provider == LLM_PROVIDER_VOLCENGINE and active.model.lower().startswith("deepseek"):
            active = select_service(store, LLM_PROVIDER_BAILIAN, active)
        else:
            _write(store, "llm", active)
            store.setValue("llm/provider", active.provider)
        store.setValue("llm/separateProfilesV1", True)
    return active
