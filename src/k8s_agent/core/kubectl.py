"""
Kubectl subprocess wrapper.

Provides a safe, structured interface for executing kubectl commands
against the target Kubernetes cluster. Handles timeouts, error parsing,
and output formatting.
"""

import subprocess
import shlex
import json
from dataclasses import dataclass
from typing import Optional, Any

from k8s_agent.config import settings
from k8s_agent.utils.logging import get_logger

logger = get_logger("kubectl")


@dataclass
class KubectlResult:
    """Structured result from a kubectl command execution."""

    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    parsed_json: Optional[Any] = None

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        """Return stdout if successful, stderr otherwise."""
        return self.stdout if self.success else self.stderr

    def as_context(self) -> str:
        """Format the result as context for the LLM prompt."""
        status = "SUCCESS" if self.success else "FAILED"
        output = self.stdout if self.success else self.stderr
        # Truncate very long outputs to avoid overwhelming the LLM
        if len(output) > 8000:
            output = output[:7500] + "\n\n... [OUTPUT TRUNCATED - showing first 7500 chars] ..."
        return (
            f"--- kubectl command: {self.command} ---\n"
            f"--- status: {status} (exit code: {self.exit_code}) ---\n"
            f"{output}\n"
            f"--- end of output ---"
        )


class KubectlExecutor:
    """
    Executes kubectl commands safely via subprocess.

    Supports namespace/context overrides, JSON output parsing,
    and command validation for write operations.
    """

    # Commands that are considered read-only (safe to execute without confirmation)
    READ_COMMANDS = frozenset({
        "get", "describe", "logs", "top", "explain",
        "api-resources", "api-versions", "cluster-info",
        "config view", "config get-contexts", "config current-context",
        "auth can-i", "version",
    })

    # Commands that modify cluster state (require confirmation)
    WRITE_COMMANDS = frozenset({
        "apply", "delete", "patch", "scale", "rollout",
        "cordon", "uncordon", "drain", "taint", "label",
        "annotate", "set", "create", "replace", "edit",
        "expose", "autoscale",
    })

    # Commands that are never allowed for safety
    BLOCKED_COMMANDS = frozenset({
        "proxy",
    })

    def __init__(
        self,
        context: Optional[str] = None,
        namespace: Optional[str] = None,
    ):
        """
        Initialize the kubectl executor.

        Args:
            context: Kubernetes context to use. Defaults to current context.
            namespace: Default namespace. Defaults to current namespace.
        """
        self.context = context or settings.kubectl_context
        self.namespace = namespace or settings.kubectl_namespace

    def _build_base_args(self) -> list[str]:
        """Build the base kubectl command with context/namespace flags."""
        args = [settings.kubectl_binary]
        if self.context:
            args.extend(["--context", self.context])
        if self.namespace:
            args.extend(["-n", self.namespace])
        return args

    def _classify_command(self, cmd_parts: list[str]) -> str:
        """
        Classify a kubectl command as 'read', 'write', or 'blocked'.

        Args:
            cmd_parts: The kubectl subcommand parts (e.g., ['get', 'pods']).

        Returns:
            Classification string: 'read', 'write', or 'blocked'.
        """
        if not cmd_parts:
            return "blocked"

        subcommand = cmd_parts[0].lower()

        if subcommand in self.BLOCKED_COMMANDS:
            return "blocked"
        if subcommand in self.WRITE_COMMANDS:
            return "write"
        if subcommand in self.READ_COMMANDS:
            return "read"

        # Check two-word commands (e.g., "config view")
        if len(cmd_parts) >= 2:
            two_word = f"{cmd_parts[0]} {cmd_parts[1]}".lower()
            if two_word in self.READ_COMMANDS:
                return "read"

        # Default to write (safer assumption)
        return "write"

    def execute(
        self,
        command: str,
        namespace: Optional[str] = None,
        timeout: Optional[int] = None,
        json_output: bool = False,
    ) -> KubectlResult:
        """
        Execute a kubectl command and return the structured result.

        Args:
            command: The kubectl command (without the 'kubectl' prefix).
                     Example: "get pods -l app=nginx"
            namespace: Override namespace for this specific command.
            timeout: Override timeout for this command.
            json_output: Whether to append -o json and parse the output.

        Returns:
            KubectlResult with stdout, stderr, and parsed data.
        """
        import time

        timeout = timeout or settings.kubectl_timeout

        # Parse the command string into parts
        try:
            cmd_parts = shlex.split(command)
        except ValueError as e:
            return KubectlResult(
                command=command,
                stdout="",
                stderr=f"Invalid command syntax: {e}",
                exit_code=-1,
                duration_ms=0,
            )

        # Build the full command
        full_cmd = self._build_base_args()

        # Override namespace if provided for this specific call
        if namespace and "-n" not in command and "--namespace" not in command:
            full_cmd.extend(["-n", namespace])

        full_cmd.extend(cmd_parts)

        # Append JSON output flag if requested
        if json_output and "-o" not in command:
            full_cmd.extend(["-o", "json"])

        command_str = " ".join(full_cmd)
        logger.debug(f"Executing: {command_str}")

        start_time = time.time()

        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            duration_ms = (time.time() - start_time) * 1000

            kubectl_result = KubectlResult(
                command=command_str,
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                exit_code=result.returncode,
                duration_ms=duration_ms,
            )

            # Parse JSON if requested and command succeeded
            if json_output and result.returncode == 0 and result.stdout.strip():
                try:
                    kubectl_result.parsed_json = json.loads(result.stdout)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse JSON output for: {command}")

            if result.returncode != 0:
                logger.warning(
                    f"kubectl command failed: {command} "
                    f"(exit={result.returncode}, stderr={result.stderr.strip()[:100]})"
                )
            else:
                logger.debug(f"kubectl success: {command} ({duration_ms:.0f}ms)")

            return kubectl_result

        except subprocess.TimeoutExpired:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"kubectl timed out after {timeout}s: {command}")
            return KubectlResult(
                command=command_str,
                stdout="",
                stderr=f"Command timed out after {timeout} seconds",
                exit_code=-1,
                duration_ms=duration_ms,
            )

        except FileNotFoundError:
            logger.critical(f"kubectl binary not found: {settings.kubectl_binary}")
            return KubectlResult(
                command=command_str,
                stdout="",
                stderr=f"kubectl binary not found at '{settings.kubectl_binary}'",
                exit_code=-2,
                duration_ms=0,
            )

    def execute_read(self, command: str, **kwargs) -> KubectlResult:
        """Execute a read-only kubectl command (no confirmation needed)."""
        cmd_parts = shlex.split(command)
        classification = self._classify_command(cmd_parts)

        if classification == "blocked":
            return KubectlResult(
                command=command,
                stdout="",
                stderr=f"Command '{cmd_parts[0]}' is blocked for safety.",
                exit_code=-3,
                duration_ms=0,
            )

        if classification == "write":
            logger.warning(
                f"execute_read called with write command: {command}. "
                "Use execute_write() instead."
            )
            return KubectlResult(
                command=command,
                stdout="",
                stderr="Write commands must use execute_write() with confirmation.",
                exit_code=-4,
                duration_ms=0,
            )

        return self.execute(command, **kwargs)

    def execute_write(self, command: str, **kwargs) -> KubectlResult:
        """
        Execute a write (mutating) kubectl command.

        This method does NOT ask for confirmation itself; the caller
        (CLI or API layer) is responsible for confirming with the user
        before calling this method.
        """
        cmd_parts = shlex.split(command)
        classification = self._classify_command(cmd_parts)

        if classification == "blocked":
            return KubectlResult(
                command=command,
                stdout="",
                stderr=f"Command '{cmd_parts[0]}' is blocked for safety.",
                exit_code=-3,
                duration_ms=0,
            )

        logger.info(f"Executing write command: {command}")
        return self.execute(command, **kwargs)

    def is_write_command(self, command: str) -> bool:
        """Check if a command would modify cluster state."""
        try:
            cmd_parts = shlex.split(command)
        except ValueError:
            return True  # Assume write if we can't parse
        return self._classify_command(cmd_parts) == "write"

    def check_connectivity(self) -> KubectlResult:
        """Verify that kubectl can connect to the cluster."""
        return self.execute("cluster-info", timeout=10)

    def get_current_context(self) -> str:
        """Get the current kubectl context name."""
        result = self.execute("config current-context", timeout=5)
        return result.stdout.strip() if result.success else "unknown"

    def get_namespaces(self) -> list[str]:
        """Get all namespace names in the cluster."""
        result = self.execute("get namespaces -o jsonpath={.items[*].metadata.name}", timeout=10)
        if result.success:
            return result.stdout.split()
        return []
