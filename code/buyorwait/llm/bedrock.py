"""Thin Bedrock Converse client with retries, a content-addressed cache, and usage accounting.

Every call is recorded (model, input/output tokens, latency, cache hit) so the final run can
produce evaluation/usage_report.md from measured numbers. Secrets come only from the standard
AWS credential chain; nothing is read from the repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import PATHS

DEFAULT_MODEL = os.environ.get("BOW_MODEL_ID", "us.amazon.nova-pro-v1:0")
FALLBACK_MODEL = os.environ.get("BOW_FALLBACK_MODEL_ID", "qwen.qwen3-vl-235b-a22b")
REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

# USD per 1M tokens, Bedrock on-demand list prices for us-east-1 as published in September 2026.
# Nova Pro is 0.80 / 3.20; Qwen3-VL is listed at 0.30 / 1.50 by some sources and 0.53 / 2.66 by others,
# so the higher figure is used. Override with BOW_PRICE_JSON='{"model": [in, out]}' when billed differently.
PRICES: Dict[str, List[float]] = {
    "us.amazon.nova-pro-v1:0": [0.80, 3.20],
    "us.amazon.nova-lite-v1:0": [0.06, 0.24],
    "global.amazon.nova-2-lite-v1:0": [0.06, 0.24],
    "qwen.qwen3-vl-235b-a22b": [0.53, 2.66],   # the higher of the two published figures; conservative
}
if os.environ.get("BOW_PRICE_JSON"):
    PRICES.update(json.loads(os.environ["BOW_PRICE_JSON"]))


@dataclass
class CallRecord:
    model: str
    purpose: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    cached: bool
    ok: bool
    error: str = ""


@dataclass
class Usage:
    calls: List[CallRecord] = field(default_factory=list)

    def add(self, rec: CallRecord) -> None:
        self.calls.append(rec)

    def summary(self) -> Dict[str, Any]:
        by_model: Dict[str, Dict[str, float]] = {}
        for c in self.calls:
            m = by_model.setdefault(c.model, {"calls": 0, "live_calls": 0, "cached_calls": 0, "input_tokens": 0,
                                              "output_tokens": 0, "errors": 0, "latency_s": 0.0})
            m["calls"] += 1
            m["cached_calls" if c.cached else "live_calls"] += 1
            m["input_tokens"] += c.input_tokens
            m["output_tokens"] += c.output_tokens
            m["errors"] += 0 if c.ok else 1
            m["latency_s"] += c.latency_s
        for model, m in by_model.items():
            pin, pout = PRICES.get(model, [0.0, 0.0])
            m["estimated_cost_usd"] = round(m["input_tokens"] / 1e6 * pin + m["output_tokens"] / 1e6 * pout, 6)
        total = {
            "calls": sum(m["calls"] for m in by_model.values()),
            "input_tokens": sum(m["input_tokens"] for m in by_model.values()),
            "output_tokens": sum(m["output_tokens"] for m in by_model.values()),
            "estimated_cost_usd": round(sum(m["estimated_cost_usd"] for m in by_model.values()), 6),
        }
        return {"by_model": by_model, "total": total}


class BedrockClient:
    def __init__(self, model_id: str = DEFAULT_MODEL, cache_dir: Optional[Path] = None, usage: Optional[Usage] = None,
                 enabled: bool = True, max_attempts: int = 3):
        self.model_id = model_id
        self.cache_dir = cache_dir or (PATHS.cache / "llm")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.usage = usage or Usage()
        self.enabled = enabled
        self.max_attempts = max_attempts
        self._client = None
        self.unavailable_reason: str = ""

    def _rt(self):
        if self._client is None:
            import boto3
            from botocore.config import Config
            self._client = boto3.client("bedrock-runtime", region_name=REGION,
                                        config=Config(read_timeout=120, retries={"max_attempts": 2}))
        return self._client

    @staticmethod
    def _key(model: str, messages: List[dict], system: Optional[str], tool_config: Optional[dict]) -> str:
        h = hashlib.sha256()
        h.update(model.encode())
        h.update(json.dumps({"s": system, "t": tool_config}, sort_keys=True, default=str).encode())
        for m in messages:
            for c in m.get("content", []):
                if "text" in c:
                    h.update(c["text"].encode())
                elif "image" in c:
                    h.update(hashlib.sha256(c["image"]["source"]["bytes"]).hexdigest().encode())
                else:
                    h.update(json.dumps(c, sort_keys=True, default=str).encode())
        return h.hexdigest()

    def converse(self, messages: List[dict], purpose: str, system: Optional[str] = None,
                 tool_config: Optional[dict] = None, max_tokens: int = 400, temperature: float = 0.0,
                 model_id: Optional[str] = None, use_cache: bool = True) -> Optional[dict]:
        """Returns the raw Converse response (dict) or None when the provider is unavailable."""
        model = model_id or self.model_id
        if not self.enabled:
            self.unavailable_reason = self.unavailable_reason or "model calls disabled"
            return None
        key = self._key(model, messages, system, tool_config)
        path = self.cache_dir / f"{key}.json"
        if use_cache and path.is_file():
            resp = json.loads(path.read_text())
            u = resp.get("usage", {})
            self.usage.add(CallRecord(model, purpose, u.get("inputTokens", 0), u.get("outputTokens", 0), 0.0, True, True))
            return resp
        kwargs: Dict[str, Any] = {"modelId": model, "messages": messages,
                                  "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature}}
        if system:
            kwargs["system"] = [{"text": system}]
        if tool_config:
            kwargs["toolConfig"] = tool_config
        last_err = ""
        for attempt in range(self.max_attempts):
            t = time.time()
            try:
                resp = self._rt().converse(**kwargs)
                resp = {"output": resp["output"], "usage": resp["usage"], "stopReason": resp.get("stopReason")}
                u = resp["usage"]
                self.usage.add(CallRecord(model, purpose, u.get("inputTokens", 0), u.get("outputTokens", 0),
                                          round(time.time() - t, 3), False, True))
                if use_cache:
                    path.write_text(json.dumps(resp, default=str))
                return resp
            except Exception as e:  # noqa: BLE001 - provider errors are reported, never raised into the pipeline
                last_err = f"{type(e).__name__}: {str(e)[:200]}"
                self.usage.add(CallRecord(model, purpose, 0, 0, round(time.time() - t, 3), False, False, last_err))
                if "AccessDenied" in last_err or "ExpiredToken" in last_err or "NoCredentials" in last_err \
                        or "MissingDependency" in last_err or "ResourceNotFound" in last_err:
                    break
                time.sleep(1.5 * (attempt + 1))
        self.unavailable_reason = last_err
        return None

    @staticmethod
    def text_of(resp: dict) -> str:
        for c in resp["output"]["message"]["content"]:
            if "text" in c:
                return c["text"]
        return ""
