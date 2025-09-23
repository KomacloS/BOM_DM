from __future__ import annotations
from typing import List, Optional
import os, json, requests, re, logging


def choose_best_datasheet_url(
    pn: str,
    mfg: str,
    desc: str,
    candidates: List[dict],
    model: str = "gpt-4o-mini",
) -> Optional[str]:
    # Resolve provider configuration from environment
    base_url = (
        os.environ.get("AI_CHAT_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or "https://api.openai.com/v1/chat/completions"
    )
    api_key = (
        os.environ.get("AI_CHAT_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("AZURE_OPENAI_API_KEY")
        or None
    )
    # Allow overriding the model from env
    model = os.environ.get("AI_CHAT_MODEL", model)
    # Header name and scheme are configurable to support non-OpenAI providers
    auth_header = os.environ.get("AI_CHAT_AUTH_HEADER", "Authorization")
    auth_scheme = os.environ.get("AI_CHAT_AUTH_SCHEME", "Bearer")
    system = (
        "You are a precision assistant. Given a part number, manufacturer, and web search results, "
        "pick the single best URL that is the OFFICIAL datasheet PDF for that exact part. "
        "Prefer manufacturer domains and URLs ending with .pdf. If uncertain, return NONE. "
        "Respond ONLY with a JSON object like {\"best_url\": \"https://...\"}. This must be valid JSON."
    )
    user = {
        "pn": pn,
        "manufacturer": mfg,
        "description": desc,
        "candidates": candidates[:10],
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        # Some providers (e.g. Azure) expect 'api-key' header instead of Authorization
        if auth_header.lower() == "authorization":
            headers[auth_header] = f"{auth_scheme} {api_key}".strip()
        else:
            headers[auth_header] = api_key
    else:
        logging.warning("gpt_rerank: no API key configured; skipping rerank")
        return None
    try:
        logging.info(
            "gpt_rerank: model=%s base_url=%s candidates=%d pn=%s mfg=%s",
            model,
            base_url,
            len(candidates),
            pn,
            mfg,
        )
    except Exception:
        pass
    try:
        payload = {
            "model": model,
            # omit temperature by default for maximum provider compatibility
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
            "response_format": {"type": "json_object"},
        }
        if os.environ.get("AI_CHAT_TEMPERATURE"):
            try:
                payload["temperature"] = float(os.environ["AI_CHAT_TEMPERATURE"])  # type: ignore
            except Exception:
                pass
        r = requests.post(
            base_url,
            headers=headers,
            json=payload,
            timeout=30,
        )
        if not r.ok:
            try:
                err = r.json()
            except Exception:
                err = {"text": r.text[:500]}
            logging.warning(
                "gpt_rerank: HTTP %s error from provider: %s", r.status_code, err
            )
            return None
        data = r.json()
    except requests.RequestException as e:
        logging.warning("gpt_rerank: request failed: %s", e)
        return None
    text = data["choices"][0]["message"]["content"]
    try:
        obj = json.loads(text)
        best = obj.get("best_url") or obj.get("url")
        if isinstance(best, str):
            best = best.strip()
            # Treat placeholders like NONE as no result; require http/https URL
            if best and best.lower() not in ("none", "null", "n/a") and re.match(r"^https?://", best, re.I):
                logging.info("gpt_rerank: selected %s", best)
                return best
    except Exception:
        pass
    m = re.search(r"https?://\S+?\.pdf\b", text, re.I)
    if m:
        logging.info("gpt_rerank: extracted %s via regex", m.group(0))
        return m.group(0)
    return None
