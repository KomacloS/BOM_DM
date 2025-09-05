from __future__ import annotations
from typing import List, Optional
import os, json, requests, re


def choose_best_datasheet_url(pn: str, mfg: str, desc: str, candidates: List[dict], model: str = "gpt-4o-mini") -> Optional[str]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    system = (
        "You are a precision assistant. Given a part number, manufacturer, and web search results, "
        "pick the single best URL that is the OFFICIAL datasheet PDF for that exact part. "
        "Prefer manufacturer domains and URLs ending with .pdf. If uncertain, return NONE."
    )
    user = {
        "pn": pn,
        "manufacturer": mfg,
        "description": desc,
        "candidates": candidates[:10],
    }
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"]["content"]
    try:
        obj = json.loads(text)
        best = obj.get("best_url") or obj.get("url")
        if isinstance(best, str) and best.strip():
            return best.strip()
    except Exception:
        pass
    m = re.search(r"https?://\S+?\.pdf\b", text, re.I)
    return m.group(0) if m else None
