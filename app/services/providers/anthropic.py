"""Claude as an `LLMProvider`.

Deliberately speaks the Messages API over urllib rather than pulling in an SDK,
so the dependency list stays short and this file is a complete, readable
example of what a new model vendor has to implement: one method, returning
text.

The key is read from the environment and never logged, echoed in an error, or
sent anywhere but api.anthropic.com.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import ProviderUnavailable

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
TIMEOUT_SECONDS = 90


class AnthropicProvider:
    """Text generation backed by Claude."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-sonnet-5") -> None:
        self._api_key = api_key
        self.model = model

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    @classmethod
    def from_env(cls) -> "AnthropicProvider | None":
        """The provider if a key is configured, otherwise None.

        Callers that can work without Claude use this and branch on None,
        rather than constructing one and catching ProviderUnavailable.
        """
        import os

        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            return None
        return cls(key, os.environ.get("ALOUD_ANTHROPIC_MODEL", "claude-sonnet-5").strip()
                   or "claude-sonnet-5")

    def complete(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 2000,
        temperature: float = 0.4,
    ) -> str:
        return self._send({
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        })

    def read_image(self, data: str, *, media_type: str, prompt: str) -> str:
        """Transcribe a base64 image. Used for photographed notes and working."""
        return self._send({
            "max_tokens": 3000,
            "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": media_type, "data": data,
                }},
                {"type": "text", "text": prompt},
            ]}],
        })

    def _send(self, fields: dict) -> str:
        if not self.available:
            raise ProviderUnavailable("No ANTHROPIC_API_KEY is configured.")

        payload = json.dumps({"model": self.model, **fields}).encode("utf-8")

        request = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "content-type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": API_VERSION,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            # Surface the status, never the key or the full request.
            detail = ""
            try:
                detail = json.loads(error.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:  # noqa: BLE001 - error parsing must not mask the error
                detail = ""
            raise ProviderUnavailable(
                f"Claude returned {error.code}." + (f" {detail}" if detail else "")
            ) from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise ProviderUnavailable(f"Could not reach Claude: {error.reason}") from None
        except json.JSONDecodeError:
            raise ProviderUnavailable("Claude returned a response that could not be read.") from None

        blocks = body.get("content", [])
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        if not text.strip():
            raise ProviderUnavailable("Claude returned an empty response.")
        return text.strip()
