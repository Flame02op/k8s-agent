"""
Tests for the Copilot CLI client.

Uses mocking to test subprocess interactions without
requiring an actual Copilot CLI installation.
"""

import time
from unittest.mock import patch, MagicMock

import pytest

from k8s_agent.core.copilot_client import CopilotClient, SessionInfo


class TestSessionInfo:
    """Tests for SessionInfo dataclass."""

    def test_session_creation(self):
        session = SessionInfo(name="test-session")
        assert session.name == "test-session"
        assert session.turn_count == 0
        assert not session.is_expired

    def test_session_touch(self):
        session = SessionInfo(name="test-session")
        session.touch()
        assert session.turn_count == 1

    def test_session_expiry(self):
        session = SessionInfo(name="test-session")
        # Manually set created_at to the past
        session.created_at = time.time() - 7200  # 2 hours ago
        assert session.is_expired


class TestCopilotClient:
    """Tests for the CopilotClient class."""

    def test_generate_session_name(self):
        client = CopilotClient()
        name = client.generate_session_name()
        assert name.startswith("k8s-agent-")
        assert len(name) > 15

    def test_generate_api_session_name(self):
        client = CopilotClient()
        name = client.generate_api_session_name()
        assert name.startswith("k8s-agent-api-")

    def test_explicit_session_name(self):
        client = CopilotClient(session_name="my-session")
        assert client.session_name == "my-session"

    @patch("subprocess.run")
    def test_invoke_success(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="Test response",
            stderr="",
            returncode=0,
        )

        client = CopilotClient(session_name="test")
        response = client.invoke("Hello")

        assert response.success
        assert response.content == "Test response"
        assert response.exit_code == 0
        assert client.session.turn_count == 1

    @patch("subprocess.run")
    def test_invoke_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="Error occurred",
            returncode=1,
        )

        client = CopilotClient(session_name="test")
        response = client.invoke("Hello")

        assert not response.success
        assert response.exit_code == 1
        assert "Error occurred" in response.error

    @patch("subprocess.run")
    def test_invoke_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="copilot", timeout=120)

        client = CopilotClient(session_name="test")
        response = client.invoke("Hello")

        assert not response.success
        assert "timed out" in response.error

    @patch("subprocess.run")
    def test_invoke_binary_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()

        client = CopilotClient(session_name="test")
        response = client.invoke("Hello")

        assert not response.success
        assert "not found" in response.error

    @patch("subprocess.run")
    def test_session_resume_on_second_call(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="Response",
            stderr="",
            returncode=0,
        )

        client = CopilotClient(session_name="test-session")

        # First call should use --name
        client.invoke("First")
        first_call_args = mock_run.call_args[0][0]
        assert "--name" in first_call_args
        assert "test-session" in first_call_args

        # Second call should use --resume
        client.invoke("Second")
        second_call_args = mock_run.call_args[0][0]
        assert "--resume" in second_call_args
        assert "test-session" in second_call_args

    def test_reset_session(self):
        client = CopilotClient(session_name="test")
        assert client.session_name == "test"
        client.reset_session()
        assert client.session is None

    def test_start_new_session(self):
        client = CopilotClient()
        name = client.start_new_session("custom-name")
        assert name == "custom-name"
        assert client.session_name == "custom-name"
