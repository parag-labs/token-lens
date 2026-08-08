"""Turn OpenTelemetry GenAI spans into the JSONL usage format TokenLens reads.

TokenLens works on a JSONL usage log. In a real system those records come from
your app's tracing. This adapter maps spans that follow the OpenTelemetry GenAI
semantic conventions (the ``gen_ai.*`` attributes) into TokenLens usage records,
so you can pipe real traces in instead of hand-writing the log.

It deliberately does NOT import the opentelemetry SDK: it accepts plain span-like
objects (anything exposing ``.attributes`` as a dict plus optional ``.start_time``
/ ``.end_time`` in nanoseconds), which keeps it dependency-free and trivially
testable. A ready-made SpanExporter subclass is provided for the common case where
the SDK is installed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict
from typing import Any, Protocol, TextIO

from tracer import UsageRecord

# OpenTelemetry GenAI semantic-convention attribute keys.
_MODEL_KEYS = ("gen_ai.request.model", "gen_ai.response.model")
_INPUT_KEYS = ("gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens")
_OUTPUT_KEYS = ("gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens")


class SpanLike(Protocol):
    attributes: dict[str, Any]


def _first(attrs: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for k in keys:
        if k in attrs and attrs[k] is not None:
            return attrs[k]
    return default


def span_to_record(span: SpanLike) -> UsageRecord | None:
    """Map one GenAI span to a UsageRecord, or None if it isn't an LLM call."""
    attrs = dict(getattr(span, "attributes", {}) or {})
    model = _first(attrs, _MODEL_KEYS)
    if not model:
        return None  # not a GenAI span

    input_tokens = int(_first(attrs, _INPUT_KEYS, 0) or 0)
    output_tokens = int(_first(attrs, _OUTPUT_KEYS, 0) or 0)

    start = getattr(span, "start_time", None)
    end = getattr(span, "end_time", None)
    latency_ms = 0.0
    timestamp = 0.0
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        latency_ms = (end - start) / 1_000_000  # ns -> ms
        timestamp = start / 1_000_000_000       # ns -> epoch seconds

    return UsageRecord(
        model=str(model),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=round(latency_ms, 3),
        feature=str(attrs.get("tokenlens.feature", attrs.get("feature", "unknown"))),
        tenant=str(attrs.get("tokenlens.tenant", attrs.get("tenant", "unknown"))),
        timestamp=round(timestamp, 6),
    )


def spans_to_records(spans: Iterable[SpanLike]) -> list[UsageRecord]:
    out = []
    for s in spans:
        rec = span_to_record(s)
        if rec is not None:
            out.append(rec)
    return out


def write_jsonl(records: Iterable[UsageRecord], sink: TextIO) -> int:
    """Write usage records as one JSON object per line. Returns the count written."""
    n = 0
    for r in records:
        sink.write(json.dumps(asdict(r)) + "\n")
        n += 1
    return n


def export_spans_to_jsonl(spans: Iterable[SpanLike], sink: TextIO) -> int:
    """Convenience: map GenAI spans and append them to a JSONL sink."""
    return write_jsonl(spans_to_records(spans), sink)


class TokenLensSpanExporter:
    """An OpenTelemetry SpanExporter that appends GenAI spans to a JSONL file.

    Only imported/instantiated when you actually wire it into an SDK pipeline, so
    the opentelemetry dependency stays optional:

        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        provider.add_span_processor(BatchSpanProcessor(TokenLensSpanExporter("usage.jsonl")))
    """

    def __init__(self, path: str):
        self._path = path

    def export(self, spans):  # matches opentelemetry SpanExporter.export signature
        with open(self._path, "a", encoding="utf-8") as f:
            export_spans_to_jsonl(spans, f)
        try:
            from opentelemetry.sdk.trace.export import SpanExportResult

            return SpanExportResult.SUCCESS
        except Exception:  # noqa: BLE001 - SDK not installed; export still succeeded
            return True

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
