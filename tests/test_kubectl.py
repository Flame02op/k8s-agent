"""
Tests for the kubectl executor.
"""

from unittest.mock import patch, MagicMock

import pytest

from k8s_agent.core.kubectl import KubectlExecutor


class TestKubectlExecutor:
    """Tests for KubectlExecutor."""

    def test_classify_read_commands(self):
        executor = KubectlExecutor()
        assert executor._classify_command(["get", "pods"]) == "read"
        assert executor._classify_command(["describe", "pod", "nginx"]) == "read"
        assert executor._classify_command(["logs", "nginx"]) == "read"
        assert executor._classify_command(["top", "nodes"]) == "read"

    def test_classify_write_commands(self):
        executor = KubectlExecutor()
        assert executor._classify_command(["apply", "-f", "file.yaml"]) == "write"
        assert executor._classify_command(["delete", "pod", "nginx"]) == "write"
        assert executor._classify_command(["scale", "--replicas=3"]) == "write"
        assert executor._classify_command(["patch", "deploy"]) == "write"

    def test_classify_blocked_commands(self):
        executor = KubectlExecutor()
        assert executor._classify_command(["proxy"]) == "blocked"

    def test_is_write_command(self):
        executor = KubectlExecutor()
        assert executor.is_write_command("apply -f deployment.yaml")
        assert executor.is_write_command("delete pod nginx")
        assert not executor.is_write_command("get pods")
        assert not executor.is_write_command("describe service nginx")

    @patch("subprocess.run")
    def test_execute_success(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="NAME   READY   STATUS\nnginx  1/1     Running",
            stderr="",
            returncode=0,
        )

        executor = KubectlExecutor()
        result = executor.execute("get pods")

        assert result.success
        assert "nginx" in result.stdout
        assert result.exit_code == 0

    @patch("subprocess.run")
    def test_execute_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="error: the server doesn't have a resource type \"podz\"",
            returncode=1,
        )

        executor = KubectlExecutor()
        result = executor.execute("get podz")

        assert not result.success
        assert result.exit_code == 1

    @patch("subprocess.run")
    def test_execute_read_blocks_write(self, mock_run):
        executor = KubectlExecutor()
        result = executor.execute_read("delete pod nginx")

        assert not result.success
        assert "execute_write()" in result.stderr
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_execute_read_blocks_blocked(self, mock_run):
        executor = KubectlExecutor()
        result = executor.execute_read("proxy")

        assert not result.success
        assert "blocked" in result.stderr
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_execute_with_namespace(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="output",
            stderr="",
            returncode=0,
        )

        executor = KubectlExecutor(namespace="production")
        executor.execute("get pods")

        call_args = mock_run.call_args[0][0]
        assert "-n" in call_args
        assert "production" in call_args

    @patch("subprocess.run")
    def test_execute_with_context(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="output",
            stderr="",
            returncode=0,
        )

        executor = KubectlExecutor(context="staging-cluster")
        executor.execute("get pods")

        call_args = mock_run.call_args[0][0]
        assert "--context" in call_args
        assert "staging-cluster" in call_args

    @patch("subprocess.run")
    def test_execute_json_output(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout='{"kind": "PodList", "items": []}',
            stderr="",
            returncode=0,
        )

        executor = KubectlExecutor()
        result = executor.execute("get pods", json_output=True)

        assert result.success
        assert result.parsed_json is not None
        assert result.parsed_json["kind"] == "PodList"

    def test_as_context_format(self):
        from k8s_agent.core.kubectl import KubectlResult

        result = KubectlResult(
            command="kubectl get pods",
            stdout="nginx  1/1  Running",
            stderr="",
            exit_code=0,
            duration_ms=150.0,
        )

        context = result.as_context()
        assert "kubectl get pods" in context
        assert "SUCCESS" in context
        assert "nginx" in context
