#!/usr/bin/env python3
"""Tests for the DashScope (Wan 2.7) image generation provider."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    """Ensure DASHSCOPE_API_KEY is set for all tests."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-sp-test-key-12345")


@pytest.fixture(autouse=True)
def _no_config(monkeypatch):
    """Neutralise config.yaml so resolution tests aren't perturbed by the
    host machine's real image_gen config. Tests that need a config value
    patch the same symbol themselves after this fixture runs."""
    import plugins.image_gen.dashscope as mod

    monkeypatch.setattr(mod, "_load_image_gen_config", lambda: {})
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    monkeypatch.delenv("DASHSCOPE_IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("DASHSCOPE_IMAGE_MODEL", raising=False)


def _ok_body(url: str = "https://dashscope-oss.aliyuncs.com/img.png?Expires=1") -> dict:
    return {
        "output": {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": [{"image": url, "type": "image"}],
                    },
                }
            ],
            "finished": True,
        },
        "usage": {"image_count": 1, "input_tokens": 10, "output_tokens": 2},
        "request_id": "req-123-abc",
    }


def _resp(body: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.content = b'{"x":1}'
    r.text = json.dumps(body)
    r.json.return_value = body
    return r


# ---------------------------------------------------------------------------
# Provider class tests
# ---------------------------------------------------------------------------


class TestDashScopeImageProvider:
    def test_name(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        assert DashScopeImageProvider().name == "dashscope"

    def test_display_name(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        assert DashScopeImageProvider().display_name == "DashScope (Wan)"

    def test_is_available_with_key(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        assert DashScopeImageProvider().is_available() is True

    def test_is_available_without_key(self, monkeypatch):
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        from plugins.image_gen.dashscope import DashScopeImageProvider

        assert DashScopeImageProvider().is_available() is False

    def test_list_models(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        models = DashScopeImageProvider().list_models()
        ids = {m["id"] for m in models}
        assert {"wan2.7-image-pro", "wan2.7-image"} <= ids
        for m in models:
            assert m["display"]
            assert m["speed"]
            assert m["strengths"]
            assert m["price"]

    def test_default_model(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        assert DashScopeImageProvider().default_model() == "wan2.7-image-pro"

    def test_get_setup_schema(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        schema = DashScopeImageProvider().get_setup_schema()
        assert schema["name"] == "DashScope (Wan)"
        assert schema["badge"] == "paid"
        env_vars = schema["env_vars"]
        assert len(env_vars) == 1
        assert env_vars[0]["key"] == "DASHSCOPE_API_KEY"

    def test_capabilities_advertises_text_and_image(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        caps = DashScopeImageProvider().capabilities()
        assert caps["modalities"] == ["text", "image"]
        assert caps["max_reference_images"] == 9


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


class TestModelResolution:
    def test_default(self):
        from plugins.image_gen.dashscope import _resolve_model

        assert _resolve_model() == "wan2.7-image-pro"

    def test_explicit_kwarg_wins(self):
        from plugins.image_gen.dashscope import _resolve_model

        assert _resolve_model("wan2.7-image") == "wan2.7-image"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_IMAGE_MODEL", "wan2.7-image")
        from plugins.image_gen.dashscope import _resolve_model

        assert _resolve_model() == "wan2.7-image"

    def test_config_scoped_model(self, monkeypatch):
        import plugins.image_gen.dashscope as mod

        monkeypatch.setattr(
            mod,
            "_load_image_gen_config",
            lambda: {"dashscope": {"model": "wan2.7-image"}},
        )
        from plugins.image_gen.dashscope import _resolve_model

        assert _resolve_model() == "wan2.7-image"


class TestEndpointResolution:
    def test_derived_from_text_base(self, monkeypatch):
        monkeypatch.setenv(
            "DASHSCOPE_BASE_URL",
            "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        )
        from plugins.image_gen.dashscope import _resolve_endpoint

        assert _resolve_endpoint() == (
            "https://token-plan.cn-beijing.maas.aliyuncs.com"
            "/api/v1/services/aigc/multimodal-generation/generation"
        )

    def test_explicit_env_override(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_IMAGE_BASE_URL", "https://example.com")
        from plugins.image_gen.dashscope import _resolve_endpoint

        assert _resolve_endpoint() == (
            "https://example.com/api/v1/services/aigc/multimodal-generation/generation"
        )

    def test_explicit_env_with_full_path_passes_through(self, monkeypatch):
        monkeypatch.setenv(
            "DASHSCOPE_IMAGE_BASE_URL",
            "https://host.example/api/v1/services/aigc/multimodal-generation/generation",
        )
        from plugins.image_gen.dashscope import _resolve_endpoint

        assert _resolve_endpoint() == (
            "https://host.example/api/v1/services/aigc/multimodal-generation/generation"
        )

    def test_default_when_nothing_set(self):
        from plugins.image_gen.dashscope import _resolve_endpoint

        assert _resolve_endpoint() == (
            "https://dashscope-intl.aliyuncs.com"
            "/api/v1/services/aigc/multimodal-generation/generation"
        )


class TestSizeResolution:
    @pytest.mark.parametrize(
        "aspect,expected",
        [
            ("landscape", "2048*1152"),
            ("portrait", "1152*2048"),
            ("square", "2K"),
        ],
    )
    def test_semantic_map(self, aspect, expected):
        from plugins.image_gen.dashscope import _resolve_size

        assert _resolve_size(aspect, "wan2.7-image-pro") == expected

    def test_config_size_override(self, monkeypatch):
        import plugins.image_gen.dashscope as mod

        monkeypatch.setattr(
            mod,
            "_load_image_gen_config",
            lambda: {"dashscope": {"size": "4K"}},
        )
        from plugins.image_gen.dashscope import _resolve_size

        assert _resolve_size("landscape", "wan2.7-image-pro") == "4K"


# ---------------------------------------------------------------------------
# Generate — main flow
# ---------------------------------------------------------------------------


class TestGenerate:
    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        from plugins.image_gen.dashscope import DashScopeImageProvider

        result = DashScopeImageProvider().generate(prompt="test")
        assert result["success"] is False
        assert "DASHSCOPE_API_KEY" in result["error"]
        assert result["error_type"] == "missing_api_key"

    def test_empty_prompt(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        result = DashScopeImageProvider().generate(prompt="   ")
        assert result["success"] is False
        assert result["error_type"] == "invalid_argument"

    def test_successful_generation(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        ok = _resp(_ok_body("https://oss.aliyuncs.com/result.png"))
        with patch(
            "plugins.image_gen.dashscope.requests.post", return_value=ok
        ) as mock_post, patch(
            "plugins.image_gen.dashscope.save_url_image",
            return_value=Path("/tmp/dashscope_wan2.7-image-pro_test.png"),
        ):
            result = DashScopeImageProvider().generate(prompt="A cinematic lamp")

        assert result["success"] is True
        assert result["image"] == "/tmp/dashscope_wan2.7-image-pro_test.png"
        assert result["provider"] == "dashscope"
        assert result["model"] == "wan2.7-image-pro"
        assert result["aspect_ratio"] == "landscape"
        assert result["modality"] == "text"
        assert result["size"] == "2048*1152"
        assert result["request_id"] == "req-123-abc"
        assert result["image_count"] == 1

        call = mock_post.call_args
        url = call.args[0]
        assert url.endswith("/api/v1/services/aigc/multimodal-generation/generation")
        payload = call.kwargs["json"]
        assert payload["model"] == "wan2.7-image-pro"
        message = payload["input"]["messages"][0]
        assert message["role"] == "user"
        assert message["content"][-1]["text"] == "A cinematic lamp"
        assert payload["parameters"]["thinking_mode"] is True
        assert payload["parameters"]["n"] == 1
        assert payload["parameters"]["watermark"] is False
        headers = call.kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-sp-test-key-12345"

    def test_image_edit_routes_to_image_modality(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        ok = _resp(_ok_body("https://oss.aliyuncs.com/edited.png"))
        with patch(
            "plugins.image_gen.dashscope.requests.post", return_value=ok
        ) as mock_post, patch(
            "plugins.image_gen.dashscope.save_url_image",
            return_value=Path("/tmp/dashscope_edit.png"),
        ):
            result = DashScopeImageProvider().generate(
                prompt="Spray-paint the graffiti onto the car",
                image_url="https://cdn/car.webp",
            )

        assert result["success"] is True
        assert result["modality"] == "image"
        payload = mock_post.call_args.kwargs["json"]
        content = payload["input"]["messages"][0]["content"]
        # Image comes first, text last.
        assert content[0]["image"] == "https://cdn/car.webp"
        assert content[-1]["text"] == "Spray-paint the graffiti onto the car"
        # thinking_mode is omitted for edits.
        assert "thinking_mode" not in payload["parameters"]

    def test_api_error_response(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        err = _resp(
            {"code": "InvalidParameter", "message": "num_images_per_prompt must be 1"},
            status=400,
        )
        with patch("plugins.image_gen.dashscope.requests.post", return_value=err):
            result = DashScopeImageProvider().generate(prompt="x")

        assert result["success"] is False
        assert result["error_type"] == "api_error"
        assert "InvalidParameter" in result["error"]
        assert result["provider"] == "dashscope"

    def test_auth_error_classified(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        err = _resp(
            {"code": "InvalidApiKey", "message": "invalid api key"},
            status=401,
        )
        with patch("plugins.image_gen.dashscope.requests.post", return_value=err):
            result = DashScopeImageProvider().generate(prompt="x")

        assert result["success"] is False
        assert result["error_type"] == "auth_error"

    def test_rate_limited_classified(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        err = _resp({"code": "Throttled", "message": "too many"}, status=429)
        with patch("plugins.image_gen.dashscope.requests.post", return_value=err):
            result = DashScopeImageProvider().generate(prompt="x")

        assert result["success"] is False
        assert result["error_type"] == "rate_limited"

    def test_empty_response_when_no_choices(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        ok = _resp({"output": {"choices": []}, "request_id": "r"})
        with patch("plugins.image_gen.dashscope.requests.post", return_value=ok):
            result = DashScopeImageProvider().generate(prompt="x")

        assert result["success"] is False
        assert result["error_type"] == "empty_response"

    def test_empty_response_when_no_image_url(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider

        ok = _resp(
            {
                "output": {
                    "choices": [
                        {
                            "message": {"content": [{"text": "no image here"}]},
                        }
                    ]
                },
                "request_id": "r",
            }
        )
        with patch("plugins.image_gen.dashscope.requests.post", return_value=ok):
            result = DashScopeImageProvider().generate(prompt="x")

        assert result["success"] is False
        assert result["error_type"] == "empty_response"

    def test_timeout(self):
        import requests as _requests

        from plugins.image_gen.dashscope import DashScopeImageProvider

        with patch(
            "plugins.image_gen.dashscope.requests.post",
            side_effect=_requests.Timeout("timed out"),
        ):
            result = DashScopeImageProvider().generate(prompt="x")

        assert result["success"] is False
        assert result["error_type"] == "timeout"

    def test_connection_error(self):
        import requests as _requests

        from plugins.image_gen.dashscope import DashScopeImageProvider

        with patch(
            "plugins.image_gen.dashscope.requests.post",
            side_effect=_requests.ConnectionError("dns fail"),
        ):
            result = DashScopeImageProvider().generate(prompt="x")

        assert result["success"] is False
        assert result["error_type"] == "connection_error"


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_wires_provider(self):
        from plugins.image_gen.dashscope import DashScopeImageProvider, register

        ctx = MagicMock()
        register(ctx)
        ctx.register_image_gen_provider.assert_called_once()
        provider = ctx.register_image_gen_provider.call_args.args[0]
        assert isinstance(provider, DashScopeImageProvider)
        assert provider.name == "dashscope"
