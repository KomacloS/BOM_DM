"""Local AI agents configuration loader.

This module reads API keys and related settings for external AI/search
providers from a project-local TOML file, with environment variables able
to override any value. The secrets file is intended to live at the project
root as `agents.local.toml` and should not be committed to git.
"""

from __future__ import annotations

from pathlib import Path
import os
from typing import Dict, Optional, TypedDict
import logging

# Support both stdlib tomllib (Python 3.11+) and third-party toml
try:  # Python 3.11+
    import tomllib as _toml_mod  # type: ignore

    def _load_toml_file(path: Path) -> Dict:
        with open(path, "rb") as f:
            return _toml_mod.load(f)  # type: ignore

except Exception:
    try:
        import toml as _toml_mod  # type: ignore

        def _load_toml_file(path: Path) -> Dict:
            return _toml_mod.load(path)  # type: ignore

    except Exception:  # no TOML support available
        _toml_mod = None  # type: ignore

        def _load_toml_file(path: Path) -> Dict:
            return {}


APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent

# Project-local secrets file (ignored by git)
AGENTS_LOCAL_PATH = REPO_ROOT / "agents.local.toml"


class GoogleConfig(TypedDict, total=False):
    search_api_key: str
    cse_id: str


class OpenAIConfig(TypedDict, total=False):
    api_key: str
    base_url: str
    model: str


def _load_file_config(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = _load_toml_file(path)
        # Ensure we always return dicts for expected sections
        return {
            "google": dict(data.get("google", {})),
            "openai": dict(data.get("openai", {})),
            "mouser": dict(data.get("mouser", {})),
            "digikey": dict(data.get("digikey", {})),
            "nexar": dict(data.get("nexar", {})),
        }
    except Exception as e:
        # If the file exists but cannot be parsed, log and fall back to empty values
        logging.getLogger(__name__).warning(
            "Agents: failed to parse %s (%s). Check for duplicate table headers or TOML syntax.",
            str(path),
            e.__class__.__name__,
        )
        return {}


def load_agents_config() -> Dict[str, dict]:
    """Load agents configuration, with env vars overriding file values.

    File format (agents.local.toml):

        [google]
        search_api_key = "..."
        cse_id = "..."  # Custom Search Engine ID

        [openai]
        api_key = "..."
        base_url = "https://api.openai.com/v1"  # optional
        model = "gpt-4o-mini"                  # optional
    """

    file_cfg = _load_file_config(AGENTS_LOCAL_PATH)

    google: GoogleConfig = {
        "search_api_key": file_cfg.get("google", {}).get("search_api_key", ""),
        "cse_id": file_cfg.get("google", {}).get("cse_id", ""),
    }
    # Environment overrides
    # Standard env var names (if provided) override file values
    google_env_key = os.getenv("GOOGLE_SEARCH_API_KEY")
    google_env_cse = os.getenv("GOOGLE_CSE_ID")
    if google_env_key:
        google["search_api_key"] = google_env_key
    if google_env_cse:
        google["cse_id"] = google_env_cse

    openai: OpenAIConfig = {
        "api_key": file_cfg.get("openai", {}).get("api_key", ""),
        "base_url": file_cfg.get("openai", {}).get("base_url", ""),
        "model": file_cfg.get("openai", {}).get("model", ""),
    }
    # Environment overrides
    oai_env_key = os.getenv("OPENAI_API_KEY")
    oai_env_base = os.getenv("OPENAI_BASE_URL")
    oai_env_model = os.getenv("OPENAI_MODEL")
    if oai_env_key:
        openai["api_key"] = oai_env_key
    if oai_env_base:
        openai["base_url"] = oai_env_base
    if oai_env_model:
        openai["model"] = oai_env_model

    # Distributor/aggregator APIs
    mouser = {
        "api_key": file_cfg.get("mouser", {}).get("api_key", ""),
    }
    digikey = {
        "access_token": file_cfg.get("digikey", {}).get("access_token", ""),
        "client_id": file_cfg.get("digikey", {}).get("client_id", ""),
        "client_secret": file_cfg.get("digikey", {}).get("client_secret", ""),
    }
    nexar = {
        "client_id": file_cfg.get("nexar", {}).get("client_id", ""),
        "client_secret": file_cfg.get("nexar", {}).get("client_secret", ""),
        "access_token": file_cfg.get("nexar", {}).get("access_token", ""),
    }

    # Environment overrides (if set)
    if os.getenv("MOUSER_API_KEY"):
        mouser["api_key"] = os.getenv("MOUSER_API_KEY")  # type: ignore[assignment]
    if os.getenv("PROVIDER_MOUSER_KEY"):
        mouser["api_key"] = os.getenv("PROVIDER_MOUSER_KEY")  # type: ignore[assignment]

    if os.getenv("DIGIKEY_ACCESS_TOKEN"):
        digikey["access_token"] = os.getenv("DIGIKEY_ACCESS_TOKEN")  # type: ignore[assignment]
    if os.getenv("DIGIKEY_CLIENT_ID"):
        digikey["client_id"] = os.getenv("DIGIKEY_CLIENT_ID")  # type: ignore[assignment]
    if os.getenv("DIGIKEY_CLIENT_SECRET"):
        digikey["client_secret"] = os.getenv("DIGIKEY_CLIENT_SECRET")  # type: ignore[assignment]

    if os.getenv("NEXAR_CLIENT_ID"):
        nexar["client_id"] = os.getenv("NEXAR_CLIENT_ID")  # type: ignore[assignment]
    if os.getenv("NEXAR_CLIENT_SECRET"):
        nexar["client_secret"] = os.getenv("NEXAR_CLIENT_SECRET")  # type: ignore[assignment]
    if os.getenv("NEXAR_ACCESS_TOKEN") or os.getenv("PROVIDER_OCTOPART_ACCESS_TOKEN"):
        nexar["access_token"] = os.getenv("NEXAR_ACCESS_TOKEN") or os.getenv("PROVIDER_OCTOPART_ACCESS_TOKEN")  # type: ignore[assignment]

    return {"google": google, "openai": openai, "mouser": mouser, "digikey": digikey, "nexar": nexar}


def get_google_search_credentials() -> tuple[str, str]:
    """Return (api_key, cse_id) for Google Custom Search.

    Values may be empty strings if not configured.
    """
    cfg = load_agents_config()["google"]
    return cfg.get("search_api_key", ""), cfg.get("cse_id", "")


def get_openai_credentials() -> tuple[str, str, str]:
    """Return (api_key, base_url, model) for OpenAI/ChatGPT usage.

    `base_url` and `model` may be empty if not configured.
    """
    cfg = load_agents_config()["openai"]
    return cfg.get("api_key", ""), cfg.get("base_url", ""), cfg.get("model", "")


def apply_env_from_agents() -> None:
    """Populate well-known environment variables from agents config.

    Bridge our local `agents.local.toml` into the env names used by
    existing services:
      - datasheet_search expects: GOOGLE_API_KEY, GOOGLE_CSE_ID
      - gpt_rerank expects: AI_CHAT_URL (full endpoint) and OPENAI_API_KEY
        and optionally AI_CHAT_MODEL
    """
    cfg = load_agents_config()

    log = logging.getLogger(__name__)

    # Google Custom Search
    g_key = cfg.get("google", {}).get("search_api_key") or ""
    g_cse = cfg.get("google", {}).get("cse_id") or ""
    if g_key and not os.environ.get("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = g_key
    if g_cse and not os.environ.get("GOOGLE_CSE_ID"):
        os.environ["GOOGLE_CSE_ID"] = g_cse

    # OpenAI/Chat reranker
    o_key = cfg.get("openai", {}).get("api_key") or ""
    o_base = cfg.get("openai", {}).get("base_url") or ""
    o_model = cfg.get("openai", {}).get("model") or ""

    if o_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = o_key

    # gpt_rerank posts directly to a chat completions URL. If a base_url
    # like https://api.openai.com/v1 is provided, derive the full endpoint.
    if not os.environ.get("AI_CHAT_URL") and o_base:
        base = o_base.rstrip("/")
        if base.endswith("/chat/completions"):
            os.environ["AI_CHAT_URL"] = base
        else:
            os.environ["AI_CHAT_URL"] = base + "/chat/completions"

    if o_model and not os.environ.get("AI_CHAT_MODEL"):
        os.environ["AI_CHAT_MODEL"] = o_model

    # Mouser API key
    m_key = cfg.get("mouser", {}).get("api_key", "")
    if m_key and not os.environ.get("MOUSER_API_KEY"):
        os.environ["MOUSER_API_KEY"] = m_key

    # Digi-Key tokens/credentials
    dk = cfg.get("digikey", {})
    if dk.get("access_token") and not os.environ.get("DIGIKEY_ACCESS_TOKEN"):
        os.environ["DIGIKEY_ACCESS_TOKEN"] = dk.get("access_token")
    if dk.get("client_id") and not os.environ.get("DIGIKEY_CLIENT_ID"):
        os.environ["DIGIKEY_CLIENT_ID"] = dk.get("client_id")
    if dk.get("client_secret") and not os.environ.get("DIGIKEY_CLIENT_SECRET"):
        os.environ["DIGIKEY_CLIENT_SECRET"] = dk.get("client_secret")

    # Nexar (Octopart) credentials
    nx = cfg.get("nexar", {})
    if nx.get("client_id") and not os.environ.get("NEXAR_CLIENT_ID"):
        os.environ["NEXAR_CLIENT_ID"] = nx.get("client_id")
    if nx.get("client_secret") and not os.environ.get("NEXAR_CLIENT_SECRET"):
        os.environ["NEXAR_CLIENT_SECRET"] = nx.get("client_secret")
    if nx.get("access_token") and not os.environ.get("NEXAR_ACCESS_TOKEN"):
        os.environ["NEXAR_ACCESS_TOKEN"] = nx.get("access_token")

    # Summary of configured providers (post-apply)
    log.info(
        "Agents: keys present -> google=%s openai=%s mouser=%s digikey=%s nexar=%s",
        bool(os.getenv("GOOGLE_API_KEY") and os.getenv("GOOGLE_CSE_ID")),
        bool(os.getenv("OPENAI_API_KEY")),
        bool(os.getenv("MOUSER_API_KEY")),
        bool(os.getenv("DIGIKEY_ACCESS_TOKEN")),
        bool(os.getenv("NEXAR_ACCESS_TOKEN")),
    )
