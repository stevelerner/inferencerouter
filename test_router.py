"""
Unit tests for InferenceRouter's routing logic.

Run with: python -m pytest test_router.py -v
"""

import os
import pytest
from unittest.mock import patch

# Mock environment before importing router
os.environ.setdefault("LOCAL_BASE", "http://localhost:11434/v1")
os.environ.setdefault("LOCAL_MODEL", "llama2")
os.environ.setdefault("LOCAL_EMBED", "nomic-embed-text")
os.environ.setdefault("LOCAL_FIM", "starcoder:1.3b")

import router


class TestClassify:
    """Test the routing classifier."""

    def test_api_key_pins_local(self):
        """Real API keys should always route to local."""
        messages = [{"role": "user", "content": "Help me with this API key: sk-ant-abc123def456"}]
        decision = router.classify(messages)
        assert decision["route"] == "local"
        assert decision["sensitive"] is True
        assert "secret" in decision["reason"]

    def test_ssn_pins_local(self):
        """SSN patterns should pin to local."""
        messages = [{"role": "user", "content": "My SSN is 123-45-6789"}]
        decision = router.classify(messages)
        assert decision["route"] == "local"
        assert decision["sensitive"] is True

    def test_private_key_pins_local(self):
        """Private key blocks should pin to local."""
        messages = [{
            "role": "user",
            "content": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBA..."
        }]
        decision = router.classify(messages)
        assert decision["route"] == "local"
        assert decision["sensitive"] is True

    def test_code_block_under_strict_residency(self):
        """Code blocks should pin to local under RESIDENCY=strict."""
        with patch.dict(os.environ, {"RESIDENCY": "strict"}):
            messages = [{
                "role": "user",
                "content": "```python\nprint('hello')\n```"
            }]
            decision = router.classify(messages)
            assert decision["route"] == "local"
            assert decision["sensitive"] is True

    def test_code_block_allowed_under_secrets_only(self):
        """Code blocks should be allowed to cloud under RESIDENCY=secrets-only."""
        with patch.dict(os.environ, {"RESIDENCY": "secrets-only"}):
            # Small, simple code
            messages = [{
                "role": "user",
                "content": "```python\nprint('hello')\n```"
            }]
            decision = router.classify(messages)
            assert decision["route"] == "local"  # simple task default
            assert decision["sensitive"] is False

    def test_oversized_payload_routes_to_cloud(self):
        """Oversized payloads without secrets should route to cloud."""
        # Create a payload that exceeds LOCAL_CTX_TOKENS
        huge_text = "word " * 100000  # ~500k chars = way over default 65536 tokens
        messages = [{"role": "user", "content": huge_text}]
        decision = router.classify(messages)
        assert decision["route"] == "cloud"
        assert decision["oversize"] is True

    def test_complex_task_routes_to_cloud(self):
        """Complex tasks should route to cloud."""
        messages = [{
            "role": "user",
            "content": "Help me architect a distributed system with consistency guarantees"
        }]
        decision = router.classify(messages)
        assert decision["route"] == "cloud"
        assert decision["complex"] is True

    def test_simple_task_routes_to_local(self):
        """Simple tasks should route to local."""
        messages = [{
            "role": "user",
            "content": "What is 2 + 2?"
        }]
        decision = router.classify(messages)
        assert decision["route"] == "local"
        assert decision["complex"] is False
        assert "no reason to pay" in decision["reason"]

    def test_vision_content_extracted(self):
        """Vision-style content blocks should be extracted."""
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Look at this: "},
                {"type": "image_url", "image_url": {"url": "https://..."}}
            ]
        }]
        # Should not crash; vision blocks are text-extracted
        decision = router.classify(messages)
        assert isinstance(decision["route"], str)

    def test_secret_oversize_warning(self):
        """Oversized secret payloads should warn about truncation."""
        with patch.dict(os.environ, {"LOCAL_CTX_TOKENS": "100"}):
            # Force validation again (normally only runs at import)
            huge_secret = "sk-ant-" + ("x" * 10000)
            messages = [{"role": "user", "content": huge_secret}]
            decision = router.classify(messages)
            assert decision["route"] == "local"
            assert decision["oversize"] is True
            assert "WARNING" in decision["reason"] or "truncate" in decision["reason"].lower()

    def test_multiple_messages(self):
        """Multiple messages should be combined for classification."""
        messages = [
            {"role": "user", "content": "Question 1"},
            {"role": "assistant", "content": "Answer 1"},
            {"role": "user", "content": "Help me refactor my code"}
        ]
        decision = router.classify(messages)
        assert decision["complex"] is True  # "refactor" is a complex cue


class TestTokenEstimation:
    """Test token estimation."""

    def test_est_tokens_positive(self):
        """est_tokens should always return at least 1."""
        assert router.est_tokens("") >= 1
        assert router.est_tokens("a") >= 1

    def test_est_tokens_reasonable(self):
        """est_tokens should be in a reasonable ballpark."""
        # "hello world" is roughly 2-3 tokens
        tokens = router.est_tokens("hello world")
        assert 1 <= tokens <= 10

        # 1000 chars should be roughly 250 tokens (4 chars per token rule of thumb)
        text = "a" * 1000
        tokens = router.est_tokens(text)
        assert 100 < tokens < 500  # loose bounds


class TestConfigValidation:
    """Test config validation."""

    def test_missing_local_base_fails(self):
        """Config validation should fail if LOCAL_BASE is missing."""
        with patch.dict(os.environ, {"LOCAL_BASE": ""}, clear=False):
            with pytest.raises(RuntimeError, match="LOCAL_BASE"):
                router._validate_config()

    def test_missing_local_model_fails(self):
        """Config validation should fail if LOCAL_MODEL is missing."""
        with patch.dict(os.environ, {"LOCAL_MODEL": ""}, clear=False):
            with pytest.raises(RuntimeError, match="LOCAL_MODEL"):
                router._validate_config()

    def test_invalid_residency_fails(self):
        """Config validation should reject invalid RESIDENCY."""
        with patch.dict(os.environ, {"RESIDENCY": "invalid"}):
            with pytest.raises(RuntimeError, match="residency"):
                router._validate_config()

    def test_valid_config_passes(self):
        """Valid config should not raise."""
        # This test is implicit in the module loading successfully
        assert router.RESIDENCY in ("strict", "secrets-only")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
