"""
GitHub Copilot CLI subprocess wrapper.

Manages invocation of the Copilot CLI binary in programmatic mode,
handling session creation, resumption, and response parsing.
"""

import subprocess
import uuid
import time
from dataclasses import dataclass, field
from typing import Optional

from k8s_agent.config import settings
from k8s_agent.utils.logging import get_logger

logger = get_logger("copilot_client")


@dataclass
class CopilotResponse:
    """Structured response from a Copilot CLI invocation."""

    content: str
    session_name: str
    exit_code: int
    duration_ms: float
    error: Optional[str] = None
    raw_stderr: str = ""

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and self.error is None


@dataclass
class SessionInfo:
    """Tracks metadata about a Copilot CLI session."""

    name: str
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    turn_count: int = 0

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    @property
    def is_expired(self) -> bool:
        return self.age_seconds > settings.api_session_ttl_seconds

    def touch(self) -> None:
        self.last_used_at = time.time()
        self.turn_count += 1


class CopilotClient:
    """
    Wraps the GitHub Copilot CLI binary for programmatic invocation.

    Handles session lifecycle, subprocess management, and error recovery.
    Each call spawns a new subprocess using the -p (prompt) flag, which
    executes the prompt and exits cleanly.
    """

    def __init__(self, session_name: Optional[str] = None):
        """
        Initialize the Copilot client.

        Args:
            session_name: Optional explicit session name. If not provided,
                          a unique name is generated automatically.
        """
        self._session: Optional[SessionInfo] = None
        if session_name:
            self._session = SessionInfo(name=session_name)

    @property
    def session(self) -> Optional[SessionInfo]:
        return self._session

    @property
    def session_name(self) -> Optional[str]:
        return self._session.name if self._session else None

    def generate_session_name(self, prefix: str = "") -> str:
        """Generate a unique, predictable session name."""
        prefix = prefix or settings.session_prefix
        unique_id = uuid.uuid4().hex[:8]
        timestamp = int(time.time())
        return f"{prefix}-{timestamp}-{unique_id}"

    def generate_api_session_name(self) -> str:
        """
        Generate a time-bucketed session name for API mode.

        Sessions are bucketed by hour so that requests within the same
        hour share context, and a new hour creates a fresh session.
        """
        hour_bucket = int(time.time() // settings.api_session_ttl_seconds)
        return f"{settings.session_prefix}-api-{hour_bucket}"

    def _build_command(self, prompt: str, is_new_session: bool) -> list[str]:
        """
        Build the copilot CLI command arguments.

        Args:
            prompt: The user/system prompt to send.
            is_new_session: Whether this is the first turn (use --name)
                            or a continuation (use --resume).
        """
        cmd = [settings.copilot_binary]

        # Session management
        if self._session:
            if is_new_session:
                cmd.extend(["--name", self._session.name])
            else:
                cmd.extend(["--resume", self._session.name])

        # Prompt (programmatic mode)
        cmd.extend(["-p", prompt])

        # Autonomy flags
        cmd.append("--no-ask-user")
        cmd.append("--silent")

        # Model selection
        if settings.copilot_model != "auto":
            cmd.extend(["--model", settings.copilot_model])

        return cmd

    def invoke(
        self,
        prompt: str,
        timeout: Optional[int] = None,
    ) -> CopilotResponse:
        """
        Invoke the Copilot CLI with a prompt and return the response.

        Automatically handles session creation on the first call and
        session resumption on subsequent calls.

        Args:
            prompt: The prompt to send to Copilot.
            timeout: Override the default timeout in seconds.

        Returns:
            CopilotResponse with the agent's output.
        """
        timeout = timeout or settings.copilot_timeout

        # Determine if this is a new session or a continuation
        is_new_session = self._session is None or self._session.turn_count == 0

        # Create session if needed
        if self._session is None:
            self._session = SessionInfo(name=self.generate_session_name())

        cmd = self._build_command(prompt, is_new_session)

        logger.debug(
            f"Invoking Copilot CLI: session={self._session.name}, "
            f"turn={self._session.turn_count + 1}, new={is_new_session}"
        )

        start_time = time.time()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=None,  # inherit parent environment (for COPILOT_GITHUB_TOKEN)
            )

            duration_ms = (time.time() - start_time) * 1000
            self._session.touch()

            response = CopilotResponse(
                content=result.stdout.strip(),
                session_name=self._session.name,
                exit_code=result.returncode,
                duration_ms=duration_ms,
                raw_stderr=result.stderr.strip(),
            )

            if result.returncode != 0:
                response.error = (
                    f"Copilot CLI exited with code {result.returncode}: "
                    f"{result.stderr.strip()}"
                )
                logger.error(
                    f"Copilot CLI error: exit_code={result.returncode}, "
                    f"stderr={result.stderr.strip()[:200]}"
                )

            logger.debug(
                f"Copilot response received: {duration_ms:.0f}ms, "
                f"length={len(response.content)} chars"
            )

            return response

        except subprocess.TimeoutExpired:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                f"Copilot CLI timed out after {timeout}s "
                f"(session={self._session.name})"
            )
            return CopilotResponse(
                content="",
                session_name=self._session.name,
                exit_code=-1,
                duration_ms=duration_ms,
                error=f"Copilot CLI timed out after {timeout} seconds",
            )

        except FileNotFoundError:
            logger.critical(
                f"Copilot CLI binary not found at: {settings.copilot_binary}"
            )
            return CopilotResponse(
                content="",
                session_name=self._session.name if self._session else "unknown",
                exit_code=-2,
                duration_ms=0,
                error=(
                    f"Copilot CLI binary '{settings.copilot_binary}' not found. "
                    "Ensure GitHub Copilot CLI is installed and in PATH."
                ),
            )

        except OSError as e:
            logger.error(f"OS error invoking Copilot CLI: {e}")
            return CopilotResponse(
                content="",
                session_name=self._session.name if self._session else "unknown",
                exit_code=-3,
                duration_ms=0,
                error=f"OS error: {e}",
            )

    def invoke_with_retry(
        self,
        prompt: str,
        max_retries: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> CopilotResponse:
        """
        Invoke Copilot CLI with automatic retry on transient failures.

        Args:
            prompt: The prompt to send.
            max_retries: Number of retry attempts (default from settings).
            timeout: Timeout per attempt.

        Returns:
            CopilotResponse from the successful attempt or last failure.
        """
        retries = max_retries if max_retries is not None else settings.copilot_max_retries
        last_response: Optional[CopilotResponse] = None

        for attempt in range(1, retries + 1):
            response = self.invoke(prompt, timeout=timeout)

            if response.success:
                return response

            last_response = response
            logger.warning(
                f"Copilot invocation failed (attempt {attempt}/{retries}): "
                f"{response.error}"
            )

            if attempt < retries:
                # Exponential backoff: 1s, 2s, 4s...
                backoff = 2 ** (attempt - 1)
                time.sleep(backoff)

        return last_response  # type: ignore[return-value]

    def reset_session(self) -> None:
        """Discard the current session and start fresh."""
        old_name = self._session.name if self._session else None
        self._session = None
        logger.info(f"Session reset (previous: {old_name})")

    def start_new_session(self, name: Optional[str] = None) -> str:
        """
        Explicitly start a new named session.

        Args:
            name: Optional session name. Auto-generated if not provided.

        Returns:
            The session name.
        """
        session_name = name or self.generate_session_name()
        self._session = SessionInfo(name=session_name)
        logger.info(f"New session started: {session_name}")
        return session_name
