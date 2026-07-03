"""
API Session Manager.

Manages Copilot CLI sessions for the API mode with time-based TTL.
Sessions are bucketed by hour so that requests within the same
hour share context, and a new hour creates a fresh session.
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Optional

from k8s_agent.core.agent import K8sAgent
from k8s_agent.config import settings
from k8s_agent.utils.logging import get_logger

logger = get_logger("api.session_manager")


@dataclass
class ManagedSession:
    """A managed API session with TTL tracking."""

    session_id: str
    agent: K8sAgent
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    request_count: int = 0

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    @property
    def is_expired(self) -> bool:
        return self.age_seconds > settings.api_session_ttl_seconds

    def touch(self) -> None:
        self.last_accessed_at = time.time()
        self.request_count += 1


class SessionManager:
    """
    Thread-safe session manager for the API server.

    Handles session creation, retrieval, expiration, and cleanup.
    Uses a background cleanup thread to remove expired sessions.
    """

    def __init__(self):
        self._sessions: dict[str, ManagedSession] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = 300  # 5 minutes
        self._cleanup_thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        """Start the background cleanup thread."""
        self._running = True
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="session-cleanup",
        )
        self._cleanup_thread.start()
        logger.info("Session manager started")

    def stop(self) -> None:
        """Stop the background cleanup thread."""
        self._running = False
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)
        logger.info("Session manager stopped")

    def _cleanup_loop(self) -> None:
        """Background loop that removes expired sessions."""
        while self._running:
            time.sleep(self._cleanup_interval)
            self._cleanup_expired()

    def _cleanup_expired(self) -> None:
        """Remove all expired sessions."""
        with self._lock:
            expired = [
                sid for sid, session in self._sessions.items()
                if session.is_expired
            ]
            for sid in expired:
                del self._sessions[sid]
                logger.info(f"Expired session removed: {sid}")

            if expired:
                logger.info(
                    f"Cleanup: removed {len(expired)} expired sessions, "
                    f"{len(self._sessions)} active"
                )

    def _generate_time_bucketed_id(self) -> str:
        """Generate a session ID based on the current time bucket."""
        bucket = int(time.time() // settings.api_session_ttl_seconds)
        return f"{settings.session_prefix}-api-{bucket}"

    def get_or_create_session(
        self,
        session_id: Optional[str] = None,
        namespace: Optional[str] = None,
        context: Optional[str] = None,
    ) -> ManagedSession:
        """
        Get an existing session or create a new one.

        If session_id is provided, attempts to retrieve that session.
        If not provided, uses time-bucketed session naming.

        Args:
            session_id: Explicit session ID (optional).
            namespace: Kubernetes namespace for new sessions.
            context: Kubernetes context for new sessions.

        Returns:
            The managed session (existing or newly created).
        """
        with self._lock:
            # Use provided session_id or generate time-bucketed one
            sid = session_id or self._generate_time_bucketed_id()

            # Check for existing session
            if sid in self._sessions:
                session = self._sessions[sid]
                if not session.is_expired:
                    session.touch()
                    logger.debug(f"Reusing session: {sid} (requests: {session.request_count})")
                    return session
                else:
                    # Session expired, remove it
                    del self._sessions[sid]
                    logger.info(f"Session expired, creating new: {sid}")

            # Create new session
            agent = K8sAgent(
                session_name=sid,
                namespace=namespace,
                context=context,
            )
            session = ManagedSession(session_id=sid, agent=agent)
            self._sessions[sid] = session
            logger.info(f"New session created: {sid}")

            return session

    def get_session(self, session_id: str) -> Optional[ManagedSession]:
        """Get an existing session by ID (returns None if not found or expired)."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session and not session.is_expired:
                session.touch()
                return session
            elif session and session.is_expired:
                del self._sessions[session_id]
            return None

    def remove_session(self, session_id: str) -> bool:
        """Explicitly remove a session."""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.info(f"Session removed: {session_id}")
                return True
            return False

    @property
    def active_count(self) -> int:
        """Number of active (non-expired) sessions."""
        with self._lock:
            return sum(
                1 for s in self._sessions.values() if not s.is_expired
            )

    @property
    def total_count(self) -> int:
        """Total number of sessions (including expired)."""
        with self._lock:
            return len(self._sessions)


# Singleton instance for the API server
session_manager = SessionManager()
