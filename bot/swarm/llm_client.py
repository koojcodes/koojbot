"""
OpenAI-format LLM client for swarm simulation agents.

Separate from Max's Anthropic client — this powers the OASIS simulation
agents and ontology generation using any OpenAI-compatible API (GPT-4o-mini,
Qwen, local models, etc.).

Adapted from MiroFish backend/app/utils/llm_client.py.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

from bot.config import settings


class SwarmLLMClient:
    """OpenAI-format LLM client for swarm operations."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or settings.SWARM_LLM_API_KEY
        self.base_url = base_url or settings.SWARM_LLM_BASE_URL
        self.model = model or settings.SWARM_LLM_MODEL_NAME

        if not self.api_key:
            raise ValueError(
                "SWARM_LLM_API_KEY not configured. "
                "Set it in .env to use swarm features."
            )

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
    ) -> str:
        """Send a chat completion request and return the text response."""
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format

        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        # Strip <think> blocks some models emit
        content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
        return content

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """Send a chat request and parse the response as JSON."""
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        cleaned = response.strip()
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            raise ValueError(f"LLM returned invalid JSON: {cleaned[:200]}")
