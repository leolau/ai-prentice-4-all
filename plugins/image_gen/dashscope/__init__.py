"""
DashScope (Alibaba Wan) Image Generation Provider
=================================================

Exposes Alibaba Cloud Model Studio's **Wan 2.7** image generation family
(``wan2.7-image-pro``, ``wan2.7-image``) as an :class:`ImageGenProvider`.

Unlike the OpenAI-compatible image providers (OpenRouter / xAI) which speak
``/chat/completions`` with ``modalities:["image"]``, Wan uses DashScope's
native **multimodal-generation** endpoint:

    POST https://{WorkspaceId}.<region>.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation

with an OpenAI-flavoured ``input.messages`` body. The HTTP **synchronous**
variant (no ``X-DashScope-Async`` header) returns the generated image URL
inline in ``output.choices[0].message.content[].image`` — "recommended for
most use cases" per the API reference — so this provider is a single
request + download, with no task polling.

Credentials reuse the same ``DASHSCOPE_API_KEY`` the ``alibaba`` text/vision
provider reads, so a Token Plan workspace that already serves the text
models serves image generation from the same key. The generation host is
derived from ``DASHSCOPE_BASE_URL`` (the text endpoint) by stripping the
``/compatible-mode/v1`` suffix, which lands on the same ``maas.aliyuncs.com``
host the Wan generation path lives under — e.g. a Token Plan workspace
``token-plan`` resolves to
``https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation``.

Selection precedence (first hit wins):

1. explicit ``model`` kwarg (the agent passes the configured ``image_gen.model``)
2. ``DASHSCOPE_IMAGE_MODEL`` env var (escape hatch for scripts / tests)
3. ``image_gen.dashscope.model`` in ``config.yaml``
4. ``image_gen.model`` in ``config.yaml`` (written by ``hermes tools``)
5. :data:`DEFAULT_MODEL` — ``wan2.7-image-pro``

Docs: https://www.alibabacloud.com/help/en/model-studio/wan-image-generation-and-editing-api-reference
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    normalize_reference_images,
    resolve_aspect_ratio,
    save_url_image,
    success_response,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Wan 2.7 generation models. ``pro`` supports up to 4K for text-to-image.
DEFAULT_MODEL = "wan2.7-image-pro"

_MODELS: Dict[str, Dict[str, Any]] = {
    "wan2.7-image-pro": {
        "display": "Wan 2.7 Image Pro",
        "speed": "~20-90s",
        "strengths": "Highest-fidelity Wan; supports 4K text-to-image, editing, image sets.",
        "price": "see https://help.aliyun.com/zh/model-studio/billing-for-model-studio",
    },
    "wan2.7-image": {
        "display": "Wan 2.7 Image",
        "speed": "~10-40s",
        "strengths": "Faster Wan generation; up to 2K.",
        "price": "see https://help.aliyun.com/zh/model-studio/billing-for-model-studio",
    },
}

# Synchronous multimodal-generation path (returns the image URL inline — no
# task polling). Appended to the resolved host root.
GENERATION_PATH = "/api/v1/services/aigc/multimodal-generation/generation"

# Fallback host when no DASHSCOPE_BASE_URL / explicit override is configured.
DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com"

# Per-request timeout. Wan generation (especially ``pro`` with thinking_mode)
# can run well past a minute, so give a single sync call real headroom before
# treating it as hung. Mirrors the OpenRouter image-gen timeout.
_REQUEST_TIMEOUT = 300.0

# Wan accepts 0-9 input images for reference/editing.
_MAX_REFERENCE_IMAGES = 9

# Suffixes stripped from DASHSCOPE_BASE_URL (the text endpoint) to recover the
# maas host root the generation path is appended to. Longest first so a full
# ``/compatible-mode/v1`` is preferred over a bare ``/api/v1``.
_TEXT_BASE_SUFFIXES = (
    "/compatible-mode/v1",
    "/compatible-mode",
    "/api/v1",
)

# image_gen contract (semantic) -> Wan pixel dimensions at ~2K total.
# Text-to-image pixel range is 768x768..4096x4096, aspect 1:8..8:1; these
# stay safely inside both bounds. Square uses the "2K" spec (2048x2048).
_ASPECT_SIZES: Dict[str, str] = {
    "square": "2K",
    "landscape": "2048*1152",
    "portrait": "1152*2048",
}


# ---------------------------------------------------------------------------
# Config / credential resolution
# ---------------------------------------------------------------------------


def _load_image_gen_config() -> Dict[str, Any]:
    """Read the ``image_gen`` section from config.yaml (``{}`` on failure)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _resolve_api_key() -> str:
    """Return the DashScope API key from ``DASHSCOPE_API_KEY``."""
    return os.environ.get("DASHSCOPE_API_KEY", "").strip()


def _resolve_model(explicit: Optional[str] = None) -> str:
    """Pick the Wan model id.

    Precedence: explicit kwarg → ``DASHSCOPE_IMAGE_MODEL`` env →
    ``image_gen.dashscope.model`` → ``image_gen.model`` → default.
    """
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    env_override = os.environ.get("DASHSCOPE_IMAGE_MODEL", "").strip()
    if env_override:
        return env_override
    cfg = _load_image_gen_config()
    scoped = cfg.get("dashscope")
    if isinstance(scoped, dict):
        value = scoped.get("model")
        if isinstance(value, str) and value.strip():
            return value.strip()
    top = cfg.get("model")
    if isinstance(top, str) and top.strip():
        return top.strip()
    return DEFAULT_MODEL


def _resolve_size(aspect: str, model_id: str) -> str:
    """Map the semantic aspect ratio to a Wan ``size`` value.

    ``image_gen.dashscope.size`` overrides everything (power users); otherwise
    ``pro`` text-to-image defaults to ``4K`` and everything else uses the
    semantic→pixel map. Unknown aspect falls back to square.
    """
    cfg = _load_image_gen_config().get("dashscope")
    if isinstance(cfg, dict):
        override = cfg.get("size")
        if isinstance(override, str) and override.strip():
            return override.strip()
    return _ASPECT_SIZES.get(aspect, _ASPECT_SIZES["square"])


def _resolve_endpoint() -> str:
    """Resolve the full generation endpoint URL.

    Order:
    1. ``image_gen.dashscope.base_url`` (config) — full endpoint or host root.
    2. ``DASHSCOPE_IMAGE_BASE_URL`` env — full endpoint or host root.
    3. Derive from ``DASHSCOPE_BASE_URL`` (Token Plan text endpoint) by
       stripping the ``/compatible-mode/v1`` suffix.
    4. :data:`DEFAULT_BASE_URL`.
    """
    cfg = _load_image_gen_config().get("dashscope")
    explicit = ""
    if isinstance(cfg, dict):
        raw = cfg.get("base_url")
        if isinstance(raw, str) and raw.strip():
            explicit = raw.strip()
    if not explicit:
        explicit = os.environ.get("DASHSCOPE_IMAGE_BASE_URL", "").strip()

    if explicit:
        explicit = explicit.rstrip("/")
        if explicit.endswith(GENERATION_PATH):
            return explicit
        return explicit + GENERATION_PATH

    text_base = os.environ.get("DASHSCOPE_BASE_URL", "").strip()
    if text_base:
        host = text_base.rstrip("/")
        for suffix in _TEXT_BASE_SUFFIXES:
            if host.endswith(suffix):
                host = host[: -len(suffix)]
                break
        return host + GENERATION_PATH

    return DEFAULT_BASE_URL + GENERATION_PATH


def _looks_like_url(value: str) -> bool:
    v = value.strip().lower()
    return v.startswith("http://") or v.startswith("https://")


def _normalize_image_input(value: str) -> Optional[str]:
    """Coerce an image argument into a Wan-compatible input (URL or data URI).

    DashScope accepts a publicly-accessible URL (http/https) **or** a
    ``data:{mime};base64,...`` string. When the agent hands us a local file
    path (common after a prior tool saved an image), read + base64-encode it
    so editing works without a publicly-hosted asset.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    # Already a data URI or a public URL — pass through verbatim.
    if value.startswith("data:") or _looks_like_url(value):
        return value
    # Local file path — read + encode.
    path = Path(value)
    try:
        if path.is_file():
            raw = path.read_bytes()
            mime = (mimetypes.guess_type(str(path))[0] or "image/png").split(";")[0]
            b64 = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{b64}"
    except OSError:
        pass
    # Unknown shape (already-encoded base64 without the data: prefix, etc.) —
    # hand it back and let the API surface a precise error if it's invalid.
    return value


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class DashScopeImageProvider(ImageGenProvider):
    """Wan 2.7 image generation via the DashScope multimodal-generation API."""

    @property
    def name(self) -> str:
        return "dashscope"

    @property
    def display_name(self) -> str:
        return "DashScope (Wan)"

    def is_available(self) -> bool:
        return bool(_resolve_api_key())

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": mid,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": meta["price"],
            }
            for mid, meta in _MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "DashScope (Wan)",
            "badge": "paid",
            "tag": "Alibaba Wan 2.7 image generation via DashScope; uses DASHSCOPE_API_KEY",
            "env_vars": [
                {
                    "key": "DASHSCOPE_API_KEY",
                    "prompt": "DashScope (Alibaba Cloud Model Studio) API key",
                    "url": "https://bailian.console.aliyun.com/?tab=model#/api-key",
                }
            ],
        }

    def capabilities(self) -> Dict[str, Any]:
        return {
            "modalities": ["text", "image"],
            "max_reference_images": _MAX_REFERENCE_IMAGES,
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        api_key = _resolve_api_key()
        if not api_key:
            return error_response(
                error="DASHSCOPE_API_KEY is not set. Configure it in .env or `hermes tools` → Image Generation.",
                error_type="missing_api_key",
                provider=self.name,
            )

        if not isinstance(prompt, str) or not prompt.strip():
            return error_response(
                error="prompt is required for image generation",
                error_type="invalid_argument",
                provider=self.name,
            )

        model_id = _resolve_model(kwargs.get("model"))
        aspect = resolve_aspect_ratio(aspect_ratio)
        size = _resolve_size(aspect, model_id)
        endpoint = _resolve_endpoint()

        # Build the OpenAI-flavoured messages payload Wan expects. Image
        # inputs come first (order = reference order), then the text prompt.
        content: List[Dict[str, str]] = []
        is_edit = False

        refs = normalize_reference_images(reference_image_urls)
        source_images: List[str] = []
        if isinstance(image_url, str) and image_url.strip():
            source_images.append(image_url.strip())
        if refs:
            source_images.extend(refs[:_MAX_REFERENCE_IMAGES])

        for img in source_images:
            normalized = _normalize_image_input(img)
            if normalized:
                content.append({"image": normalized})
                is_edit = True
        content.append({"text": prompt.strip()})

        parameters: Dict[str, Any] = {
            "size": size,
            "n": 1,
            "watermark": False,
        }
        # thinking_mode only applies to text-to-image with no image input.
        if not is_edit:
            parameters["thinking_mode"] = True

        payload: Dict[str, Any] = {
            "model": model_id,
            "input": {
                "messages": [{"role": "user", "content": content}],
            },
            "parameters": parameters,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Hermes-Agent/1.0 (dashscope-image-gen)",
        }

        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.Timeout:
            return error_response(
                error=f"DashScope image generation timed out ({int(_REQUEST_TIMEOUT)}s)",
                error_type="timeout",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        except requests.ConnectionError as exc:
            return error_response(
                error=f"DashScope connection error: {exc}",
                error_type="connection_error",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        status = response.status_code
        try:
            body = response.json() if response.content else {}
        except Exception as exc:  # noqa: BLE001
            return error_response(
                error=f"DashScope returned invalid JSON ({status}): {exc}",
                error_type="invalid_response",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        # Error envelope: {"code": "...", "message": "...", "request_id": "..."}
        err_code = body.get("code") if isinstance(body, dict) else None
        if isinstance(err_code, str) and err_code:
            err_msg = body.get("message") or body.get("msg") or err_code
            etype = "api_error"
            if status == 401 or "auth" in err_code.lower() or "key" in err_code.lower():
                etype = "auth_error"
            elif status == 429:
                etype = "rate_limited"
            return error_response(
                error=f"DashScope rejected request ({status}/{err_code}): {err_msg}",
                error_type=etype,
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        if status >= 400:
            err_msg = (
                (body.get("message") if isinstance(body, dict) else None)
                or (body.get("detail") if isinstance(body, dict) else None)
                or response.text[:300]
            )
            return error_response(
                error=f"DashScope image generation failed ({status}): {err_msg}",
                error_type="api_error",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        # Success envelope: output.choices[].message.content[].image (type=="image")
        output = body.get("output") if isinstance(body, dict) else None
        if not isinstance(output, dict):
            return error_response(
                error="DashScope response missing 'output' object",
                error_type="invalid_response",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        choices = output.get("choices")
        if not isinstance(choices, list) or not choices:
            return error_response(
                error="DashScope response had no choices",
                error_type="empty_response",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        image_url_out: Optional[str] = None
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            msg_content = message.get("content")
            if not isinstance(msg_content, list):
                continue
            for entry in msg_content:
                if not isinstance(entry, dict):
                    continue
                # Wan marks image parts with "type": "image"; also tolerate a
                # bare {"image": "..."} entry without the type tag.
                if entry.get("type") and entry.get("type") != "image":
                    continue
                url = entry.get("image")
                if isinstance(url, str) and url.strip():
                    image_url_out = url.strip()
                    break
            if image_url_out:
                break

        if image_url_out is None:
            return error_response(
                error="DashScope completed but returned no image URL",
                error_type="empty_response",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        # Materialise locally — the OSS URL carries an Expires signature and
        # is purged after 24h, so a downstream consumer (Telegram send_photo,
        # browser fetch) can't rely on it staying live.
        try:
            saved_path = save_url_image(image_url_out, prefix=f"dashscope_{model_id}")
            image_ref = str(saved_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "DashScope image URL %s could not be cached (%s); falling back to bare URL.",
                image_url_out,
                exc,
            )
            image_ref = image_url_out

        extra: Dict[str, Any] = {
            "size": size,
            "request_id": body.get("request_id") or "",
        }
        usage = body.get("usage")
        if isinstance(usage, dict):
            if isinstance(usage.get("image_count"), int):
                extra["image_count"] = usage["image_count"]

        return success_response(
            image=image_ref,
            model=model_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider=self.name,
            modality="image" if is_edit else "text",
            extra=extra,
        )


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx: Any) -> None:
    """Plugin entry point — wire ``DashScopeImageProvider`` into the registry."""
    ctx.register_image_gen_provider(DashScopeImageProvider())
