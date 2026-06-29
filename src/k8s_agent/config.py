"""
Configuration management for the K8s Agent.

Uses pydantic-settings for environment-variable-based configuration
with sensible defaults for production use.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Global settings for the K8s debugging agent."""

    model_config = SettingsConfigDict(
        env_prefix="K8S_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Copilot CLI Configuration ---
    copilot_binary: str = "copilot"
    copilot_model: str = "auto"
    copilot_timeout: int = 120  # seconds per invocation
    copilot_max_retries: int = 3

    # --- Session Management ---
    session_prefix: str = "k8s-agent"
    api_session_ttl_seconds: int = 3600  # 1 hour

    # --- Kubectl Configuration ---
    kubectl_binary: str = "kubectl"
    kubectl_timeout: int = 60  # seconds per command
    kubectl_context: str | None = None  # use current context if None
    kubectl_namespace: str | None = None  # use current namespace if None

    # --- API Server ---
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    api_workers: int = 4

    # --- Logging ---
    log_level: str = "INFO"
    log_format: str = "json"
    log_file: Path | None = None

    # --- Agent Behavior ---
    max_diagnostic_iterations: int = 10
    require_confirmation_for_writes: bool = True
    allowed_remediation_commands: list[str] = [
        "kubectl apply",
        "kubectl patch",
        "kubectl scale",
        "kubectl rollout restart",
        "kubectl delete pod",
        "kubectl cordon",
        "kubectl uncordon",
        "kubectl drain",
        "kubectl label",
        "kubectl annotate",
        "kubectl set",
    ]


# Singleton instance
settings = AgentSettings()
