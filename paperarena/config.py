"""Explicit provider profiles and per-role routing; no implicit fallback."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
import math
import os
from typing import Any
from urllib.parse import urlsplit


ROLES = ("heavy", "light", "expert")


@dataclass(frozen=True)
class APIProfile:
    provider: str
    model: str
    api_key: str = field(default="", repr=False)
    base_url: str | None = None
    api_key_env: str | None = None

    def __post_init__(self):
        if self.provider not in ("openai", "gemini", "custom"):
            raise ValueError("provider must be openai, gemini, or custom")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("A model ID is required for each API profile")
        if not isinstance(self.api_key, str):
            raise ValueError("api_key must be a string")
        if self.api_key_env is not None and (not isinstance(self.api_key_env, str) or not self.api_key_env.strip()):
            raise ValueError("api_key_env must be a nonempty environment variable name")
        if self.base_url:
            parsed = urlsplit(self.base_url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("base_url must be an HTTP(S) URL without embedded credentials, query, or fragment")
        if self.provider == "custom" and not self.base_url:
            raise ValueError("custom profiles require base_url")
        if self.provider == "gemini" and self.base_url:
            raise ValueError("Native Gemini uses its official endpoint; use custom for a compatible endpoint")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "APIProfile":
        return cls(**value)

    def resolved(self, *, require_key: bool = True) -> "APIProfile":
        key = self.api_key or (os.getenv(self.api_key_env, "") if self.api_key_env else "")
        if require_key and not key:
            raise ValueError("Missing API key for a routed API profile")
        return APIProfile(self.provider, self.model, key, self.base_url, self.api_key_env)

    def to_public_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model,
                "base_url": self.base_url, "api_key_env": self.api_key_env,
                "has_api_key": bool(self.api_key or (self.api_key_env and os.getenv(self.api_key_env)))}

    def worker_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model, "api_key": self.api_key,
                "base_url": self.base_url, "api_key_env": self.api_key_env}


@dataclass(frozen=True)
class RunConfig:
    profiles: dict[str, APIProfile]
    roles: dict[str, str] = field(default_factory=dict)
    mode: str = "retrieval_eval"
    years: tuple[int, int] | None = (2015, 2025)
    max_rounds: int = 1
    use_citation_graph: bool = True
    request_timeout_seconds: float = 120.0
    max_output_tokens: int = 8192
    expert_max_output_tokens: int = 16384
    search: dict[str, Any] = field(default_factory=dict, repr=False)
    pipeline: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.profiles or any(not isinstance(p, APIProfile) for p in self.profiles.values()):
            raise ValueError("profiles must contain at least one named APIProfile")
        if any(not isinstance(k, str) or not k.strip() for k in self.profiles):
            raise ValueError("API profile names must be nonempty strings")
        roles = dict(self.roles) or {role: next(iter(self.profiles)) for role in ROLES}
        if set(roles) != set(ROLES) or any(name not in self.profiles for name in roles.values()):
            raise ValueError("roles must route heavy, light, and expert to existing API profiles")
        object.__setattr__(self, "roles", roles)
        if self.mode not in ("retrieval_eval", "full_report"):
            raise ValueError("mode must be retrieval_eval or full_report")
        if self.years is not None:
            if len(self.years) != 2 or any(type(n) is not int for n in self.years) or self.years[0] > self.years[1]:
                raise ValueError("years must be [start, end] or null")
            object.__setattr__(self, "years", tuple(self.years))
        if type(self.max_rounds) is not int or self.max_rounds < 1:
            raise ValueError("max_rounds must be a positive integer")
        if not isinstance(self.request_timeout_seconds, (float, int)) or isinstance(self.request_timeout_seconds, bool) or not math.isfinite(self.request_timeout_seconds) or self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive and finite")
        for count in (self.max_output_tokens, self.expert_max_output_tokens):
            if type(count) is not int or not 1024 <= count <= 65536:
                raise ValueError("Output token limits must be between 1024 and 65536")
        allowed = {"serpapi_api_key", "serpapi_api_key_env", "semantic_scholar_api_key", "semantic_scholar_api_key_env", "mailto", "enable_serpapi", "enable_duckduckgo"}
        if set(self.search) - allowed:
            raise ValueError("Unknown search configuration field")
        if any(not isinstance(v, str) for k, v in self.search.items() if k not in ("enable_serpapi", "enable_duckduckgo")):
            raise ValueError("Search keys and environment references must be strings")
        if any(type(self.search[k]) is not bool for k in ("enable_serpapi", "enable_duckduckgo") if k in self.search):
            raise ValueError("Search feature flags must be booleans")
        # This shared types module is dependency-light and performs no IO.
        from pipelines.tasks.prior_art_types import PriorArtConfig
        if set(self.pipeline) - {row.name for row in fields(PriorArtConfig)}:
            raise ValueError("Unknown pipeline configuration field")
        if any(key in self.pipeline for key in ("execution_mode", "excluded_papers", "shared_pdf_cache_dir", "resume_same_task")):
            raise ValueError("Pipeline mode, gold exclusions, external caches, and resume are controlled by the public API")
        if type(self.pipeline.get("retrieval_eval_fulltext_limit", 5)) is not int or self.pipeline.get("retrieval_eval_fulltext_limit", 5) != 5:
            raise ValueError("retrieval_eval always uses five fixed PDF attempts")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunConfig":
        value = dict(value)
        value["profiles"] = {name: p if isinstance(p, APIProfile) else APIProfile.from_dict(p) for name, p in value.get("profiles", {}).items()}
        return cls(**value)

    def resolved(self, *, require_key: bool = True) -> "RunConfig":
        value = self.worker_dict()
        routed = set(self.roles.values())
        value["profiles"] = {name: p.resolved(require_key=require_key and name in routed).worker_dict() for name, p in self.profiles.items()}
        search = dict(self.search)
        for key in ("serpapi_api_key", "semantic_scholar_api_key"):
            env_name = search.get(key + "_env")
            search[key] = search.get(key) or (os.getenv(env_name, "") if env_name else "")
        value["search"] = search
        return RunConfig.from_dict(value)

    def worker_dict(self) -> dict[str, Any]:
        return {"profiles": {name: p.worker_dict() for name, p in self.profiles.items()},
                "roles": dict(self.roles), "mode": self.mode, "years": self.years,
                "max_rounds": self.max_rounds, "use_citation_graph": self.use_citation_graph,
                "request_timeout_seconds": self.request_timeout_seconds,
                "max_output_tokens": self.max_output_tokens, "expert_max_output_tokens": self.expert_max_output_tokens,
                "search": dict(self.search), "pipeline": dict(self.pipeline)}

    def to_public_dict(self) -> dict[str, Any]:
        result = self.worker_dict()
        result["profiles"] = {name: p.to_public_dict() for name, p in self.profiles.items()}
        result["search"] = {k: v for k, v in self.search.items() if "api_key" not in k or k.endswith("_env")}
        for key in ("serpapi_api_key", "semantic_scholar_api_key"):
            result["search"]["has_" + key] = bool(self.search.get(key) or (self.search.get(key + "_env") and os.getenv(self.search[key + "_env"])))
        return result


def worker_environment(config: RunConfig) -> dict[str, str]:
    """Build an isolated child environment, replacing every legacy API route."""
    prefixes = ("LLM_", "CUSTOM_LLM_", "OPENAI_", "GOOGLE_", "GEMINI_", "ANTHROPIC_", "AZURE_", "LITELLM_",
                "SEMANTIC_SCHOLAR_", "S2_", "SERPAPI_", "OPENALEX_", "CROSSREF_")
    exact = {"SERPAPI_API_KEY", "SEMANTIC_SCHOLAR_API_KEY", "OPENALEX_MAILTO", "CROSSREF_MAILTO"}
    exact.update(p.api_key_env for p in config.profiles.values() if p.api_key_env)
    exact.update(config.search[k] for k in ("serpapi_api_key_env", "semantic_scholar_api_key_env") if config.search.get(k))
    exact = {key.upper() for key in exact}
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(prefixes) and key.upper() not in exact}
    for role, profile_name in config.roles.items():
        profile = config.profiles[profile_name]
        prefix = "LLM_" + role.upper()
        env[prefix + "_PROVIDER"] = "google" if profile.provider == "gemini" else "custom"
        env[prefix + "_MODEL"] = profile.model
        env[prefix + "_API_KEY"] = profile.api_key
        if profile.provider != "gemini":
            env[prefix + "_API_BASE"] = (profile.base_url or "https://api.openai.com/v1").rstrip("/")
    for field, name in (("serpapi_api_key", "SERPAPI_API_KEY"), ("semantic_scholar_api_key", "SEMANTIC_SCHOLAR_API_KEY"), ("mailto", "OPENALEX_MAILTO"), ("mailto", "CROSSREF_MAILTO")):
        if config.search.get(field):
            env[name] = str(config.search[field])
    # Older core clients use mixed-case names on case-sensitive platforms.
    # Publish that compatibility alias only from this explicitly selected key.
    if config.search.get("semantic_scholar_api_key"):
        env["Semantic_Scholar_API_key"] = str(config.search["semantic_scholar_api_key"])
    env["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    env["PYTHONUTF8"] = "1"
    return env
