"""
Async parse dispatch.

Local (default): the upload route runs the parse in a FastAPI BackgroundTask.
Production (PARSE_BACKEND=pubsub): the upload route publishes a small job message
to Pub/Sub; a Cloud Run **push** subscription delivers it to POST /internal/parse,
which decodes it and runs the same parse. This decouples the fast upload from the
slow parse, gives retries + a dead-letter queue, and scales the worker to zero.

The google-cloud-pubsub SDK is imported lazily, so the app runs locally without it.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Optional

from app.config import Settings, get_settings

log = logging.getLogger("collateral.messaging")


def publish_parse(payload: dict, settings: Optional[Settings] = None) -> str:
    """Publish a parse-job message to Pub/Sub; returns the published message id."""
    settings = settings or get_settings()
    if not (settings.google_cloud_project and settings.pubsub_parse_topic):
        raise RuntimeError("PARSE_BACKEND=pubsub requires GOOGLE_CLOUD_PROJECT + PUBSUB_PARSE_TOPIC")
    try:
        from google.cloud import pubsub_v1
    except Exception as e:  # pragma: no cover - only when pubsub backend is used
        raise RuntimeError("pip install '.[cloud]' for google-cloud-pubsub") from e

    publisher = pubsub_v1.PublisherClient()
    topic = publisher.topic_path(settings.google_cloud_project, settings.pubsub_parse_topic)
    # ordering/dedup key: one logical parse per (tenant, pair, role)
    future = publisher.publish(topic, json.dumps(payload).encode("utf-8"))
    msg_id = future.result(timeout=30)
    log.info("published parse job %s -> %s", payload.get("job_id"), topic)
    return msg_id


def decode_push(envelope: dict) -> dict:
    """Decode a Pub/Sub *push* envelope into the original job payload.

    Push delivers: {"message": {"data": "<base64(json)>", ...}, "subscription": ...}
    """
    message = (envelope or {}).get("message")
    if not isinstance(message, dict) or not message.get("data"):
        raise ValueError("not a Pub/Sub push envelope (missing message.data)")
    return json.loads(base64.b64decode(message["data"]).decode("utf-8"))
