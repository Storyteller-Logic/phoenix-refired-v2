"""Phase 0: Connectivity and baseline tests."""

import os
import pytest
import requests

# Endpoints to test
GEMMA_ENDPOINT = "http://127.0.0.1:5810"
AGENTS_ENDPOINT = "http://127.0.0.1:8000/v1"

# Brain path - to be discovered
BRAIN_PATH = "/mnt/hdd/phoenix-refire/brain"

def test_brain_exists():
    """Test that brain directory exists."""
    assert os.path.exists(BRAIN_PATH), f"Brain not found at {BRAIN_PATH}"

def test_gemma_endpoint():
    """Test Gemma Heretic endpoint."""
    try:
        r = requests.get(f"{GEMMA_ENDPOINT}/", timeout=5)
        assert r.status_code in [200, 404, 405], f"Got {r.status_code}"
    except Exception as e:
        pytest.fail(f"Gemma Heretic unreachable: {e}")

def test_agents_endpoint():
    """Test Agents-a1 endpoint."""
    try:
        r = requests.get(f"{AGENTS_ENDPOINT}/models", timeout=5)
        assert r.status_code == 200, f"Got status {r.status_code}"
        data = r.json()
        assert any(m.get("id") == "agents-a1" for m in data.get("data", [])), "agents-a1 not in list"
    except Exception as e:
        pytest.fail(f"Agents-a1 unreachable: {e}")

if __name__ == "__main__":
    pytest.main([__file__, "-v"])

