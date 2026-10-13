"""Minimal OpenAI-compatible chat client (stdlib only) for running the judge outside an agent,
e.g. against a local Ollama endpoint during evaluation."""

from __future__ import annotations

import json
import urllib.request

from memguard.judge import LLM


def openai_compatible(base_url: str, model: str, *, api_key: str = "none",
                      timeout: float = 120.0, temperature: float = 0.0) -> LLM:
    url = base_url.rstrip("/") + "/chat/completions"

    def call(system: str, user: str) -> str:
        body = {
            "model": model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        request = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
        return data["choices"][0]["message"].get("content") or ""

    return call
