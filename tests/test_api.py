"""
Tests for the REST API server.
"""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

from k8s_agent.api.server import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    @patch("k8s_agent.api.server.subprocess.run")
    @patch("k8s_agent.api.server.KubectlExecutor")
    def test_health_check_healthy(self, mock_kubectl_cls, mock_subprocess, client):
        # Mock kubectl
        mock_kubectl = MagicMock()
        mock_kubectl.check_connectivity.return_value = MagicMock(success=True)
        mock_kubectl.get_current_context.return_value = "test-context"
        mock_kubectl_cls.return_value = mock_kubectl

        # Mock copilot version check
        mock_subprocess.return_value = MagicMock(returncode=0)

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["cluster_connected"] is True
        assert data["copilot_available"] is True

    @patch("k8s_agent.api.server.subprocess.run")
    @patch("k8s_agent.api.server.KubectlExecutor")
    def test_health_check_degraded(self, mock_kubectl_cls, mock_subprocess, client):
        mock_kubectl = MagicMock()
        mock_kubectl.check_connectivity.return_value = MagicMock(success=False)
        mock_kubectl.get_current_context.return_value = "unknown"
        mock_kubectl_cls.return_value = mock_kubectl

        mock_subprocess.side_effect = FileNotFoundError()

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"


class TestAnalyzeEndpoint:
    """Tests for the /analyze endpoint."""

    def test_analyze_empty_prompt(self, client):
        response = client.post("/analyze", json={"prompt": ""})
        assert response.status_code == 422  # Validation error

    def test_analyze_missing_prompt(self, client):
        response = client.post("/analyze", json={})
        assert response.status_code == 422


class TestRemediateEndpoint:
    """Tests for the /remediate endpoint."""

    def test_remediate_without_confirm(self, client):
        response = client.post("/remediate", json={
            "session_id": "test-session",
            "confirm": False,
        })
        assert response.status_code == 400

    def test_remediate_nonexistent_session(self, client):
        response = client.post("/remediate", json={
            "session_id": "nonexistent-session",
            "confirm": True,
        })
        assert response.status_code == 404


class TestSessionEndpoints:
    """Tests for session management endpoints."""

    def test_list_sessions(self, client):
        response = client.get("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "active_sessions" in data
        assert "ttl_seconds" in data

    def test_delete_nonexistent_session(self, client):
        response = client.delete("/sessions/nonexistent")
        assert response.status_code == 404
