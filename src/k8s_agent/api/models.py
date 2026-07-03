"""
API Request/Response Models.

Pydantic models for the REST API endpoints, providing
validation, serialization, and OpenAPI documentation.
"""

from datetime import datetime
from typing import Optional, Any

from pydantic import BaseModel, Field


# --- Request Models ---


class AnalyzeRequest(BaseModel):
    """Request body for the /analyze endpoint."""

    prompt: str = Field(
        ...,
        description="The debugging query or issue description.",
        min_length=1,
        max_length=10000,
        examples=["Why is my nginx deployment failing in namespace production?"],
    )
    namespace: Optional[str] = Field(
        default=None,
        description="Kubernetes namespace to focus on.",
        examples=["default", "production"],
    )
    context: Optional[str] = Field(
        default=None,
        description="Kubernetes context to use.",
    )
    session_id: Optional[str] = Field(
        default=None,
        description=(
            "Explicit session ID for conversation continuity. "
            "If not provided, the agent uses time-bucketed sessions (1-hour TTL)."
        ),
    )


class RemediationRequest(BaseModel):
    """Request body for the /remediate endpoint."""

    session_id: str = Field(
        ...,
        description="The session ID from a previous /analyze response.",
    )
    step_indices: Optional[list[int]] = Field(
        default=None,
        description=(
            "Indices of remediation steps to execute (0-based). "
            "If null, all steps are executed."
        ),
    )
    confirm: bool = Field(
        default=False,
        description="Must be set to true to confirm execution.",
    )


class DiagnoseResourceRequest(BaseModel):
    """Request body for targeted resource diagnosis."""

    resource_type: str = Field(
        ...,
        description="Kubernetes resource type.",
        examples=["pod", "deployment", "service", "statefulset", "daemonset", "job"],
    )
    resource_name: str = Field(
        ...,
        description="Name of the resource to diagnose.",
    )
    namespace: str = Field(
        default="default",
        description="Namespace of the resource.",
    )
    context: Optional[str] = Field(
        default=None,
        description="Kubernetes context to use.",
    )


class NetworkTraceRequest(BaseModel):
    """Request body for network connectivity tracing."""

    source_pod: str = Field(
        ...,
        description="Name of the source pod.",
    )
    target: str = Field(
        ...,
        description="Target (service name, pod name, or IP).",
    )
    port: Optional[int] = Field(
        default=None,
        description="Target port to test connectivity.",
    )
    namespace: str = Field(
        default="default",
        description="Namespace of the source pod.",
    )
    target_namespace: Optional[str] = Field(
        default=None,
        description="Namespace of the target (if different from source).",
    )
    context: Optional[str] = Field(
        default=None,
        description="Kubernetes context to use.",
    )


# --- Response Models ---


class RCADetail(BaseModel):
    """Root Cause Analysis details."""

    root_cause: Optional[str] = None
    evidence: list[str] = Field(default_factory=list)
    severity: Optional[str] = None


class RemediationStep(BaseModel):
    """A single remediation step."""

    description: str
    command: str
    risk: str = "medium"


class RemediationDetail(BaseModel):
    """Remediation plan details."""

    steps: list[RemediationStep] = Field(default_factory=list)
    explanation: Optional[str] = None


class AnalyzeResponse(BaseModel):
    """Response from the /analyze endpoint."""

    session_id: str = Field(
        description="Session ID for follow-up queries.",
    )
    status: str = Field(
        description="Agent status: investigating, diagnosed, resolved, error.",
    )
    analysis: str = Field(
        description="The agent's analysis in markdown format.",
    )
    rca: Optional[RCADetail] = Field(
        default=None,
        description="Root Cause Analysis (if diagnosis is complete).",
    )
    remediation: Optional[RemediationDetail] = Field(
        default=None,
        description="Suggested remediation steps.",
    )
    follow_up_question: Optional[str] = Field(
        default=None,
        description="Question from the agent if more context is needed.",
    )
    commands_executed: list[str] = Field(
        default_factory=list,
        description="kubectl commands that were executed during analysis.",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if something went wrong.",
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Response timestamp.",
    )


class RemediationResponse(BaseModel):
    """Response from the /remediate endpoint."""

    session_id: str
    executed_steps: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Results of each executed step.",
    )
    all_successful: bool
    error: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class HealthResponse(BaseModel):
    """Response from the /health endpoint."""

    status: str = "healthy"
    cluster_connected: bool
    current_context: str
    copilot_available: bool
    active_sessions: int
    version: str


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
