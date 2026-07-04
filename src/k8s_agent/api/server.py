"""
REST API Server.

FastAPI application providing programmatic access to the K8s
debugging agent. Supports session-based conversations with
automatic TTL management.
"""

import subprocess
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from k8s_agent.api.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    RemediationRequest,
    RemediationResponse,
    DiagnoseResourceRequest,
    NetworkTraceRequest,
    HealthResponse,
    ErrorResponse,
)
from k8s_agent.api.session_manager import session_manager
from k8s_agent.core.kubectl import KubectlExecutor
from k8s_agent.diagnostics.workload import WorkloadDiagnostics
from k8s_agent.diagnostics.network import NetworkDiagnostics
from k8s_agent.config import settings
from k8s_agent.utils.logging import get_logger, setup_logging

logger = get_logger("api.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown events."""
    setup_logging(mode="json")
    session_manager.start()
    logger.info("K8s Agent API server started")
    yield
    session_manager.stop()
    logger.info("K8s Agent API server stopped")


app = FastAPI(
    title="K8s Debugging Agent API",
    description=(
        "A specialized Kubernetes debugging agent that provides "
        "Root Cause Analysis and remediation for cluster issues. "
        "Powered by GitHub Copilot CLI."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware for flexibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Exception Handlers ---


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler for unhandled errors."""
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error="Internal server error",
            detail=str(exc),
        ).model_dump(mode="json"),
    )


# --- Health & Info Endpoints ---


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health check",
)
async def health_check():
    """
    Check the health of the agent, cluster connectivity,
    and Copilot CLI availability.
    """
    kubectl = KubectlExecutor()
    connectivity = kubectl.check_connectivity()
    current_context = kubectl.get_current_context()

    # Check copilot binary availability
    try:
        result = subprocess.run(
            [settings.copilot_binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        copilot_available = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        copilot_available = False

    return HealthResponse(
        status="healthy" if connectivity.success and copilot_available else "degraded",
        cluster_connected=connectivity.success,
        current_context=current_context,
        copilot_available=copilot_available,
        active_sessions=session_manager.active_count,
        version="1.0.0",
    )


# --- Core Analysis Endpoints ---


@app.post(
    "/analyze",
    response_model=AnalyzeResponse,
    tags=["Analysis"],
    summary="Analyze a Kubernetes issue",
    responses={
        500: {"model": ErrorResponse, "description": "Internal error"},
    },
)
async def analyze(request: AnalyzeRequest):
    """
    Submit a debugging query for analysis.

    The agent will:
    1. Interpret the query
    2. Execute relevant kubectl commands
    3. Analyze the output
    4. Provide RCA and remediation steps

    Sessions are automatically managed with a 1-hour TTL.
    Provide a `session_id` from a previous response to continue
    a conversation.
    """
    try:
        session = session_manager.get_or_create_session(
            session_id=request.session_id,
            namespace=request.namespace,
            context=request.context,
        )

        result = await session.agent.analyze(request.prompt)

        return AnalyzeResponse(
            session_id=session.session_id,
            status=result.status.value,
            analysis=result.analysis,
            rca=result.rca,
            remediation=result.remediation,
            follow_up_question=result.follow_up_question,
            commands_executed=result.commands_executed,
            error=result.error,
        )

    except Exception as e:
        logger.exception(f"Analysis failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {str(e)}",
        )


@app.post(
    "/diagnose",
    response_model=AnalyzeResponse,
    tags=["Analysis"],
    summary="Diagnose a specific Kubernetes resource",
)
async def diagnose_resource(request: DiagnoseResourceRequest):
    """
    Diagnose a specific Kubernetes resource by type and name.

    Gathers comprehensive diagnostic information about the resource
    and provides analysis with RCA.
    """
    kubectl = KubectlExecutor(context=request.context, namespace=request.namespace)
    workload_diag = WorkloadDiagnostics(kubectl)
    network_diag = NetworkDiagnostics(kubectl)

    # Route to the appropriate diagnostic routine
    resource_type = request.resource_type.lower()
    snapshot = None

    if resource_type == "pod":
        snapshot = workload_diag.diagnose_pod(request.resource_name, request.namespace)
    elif resource_type in ("deployment", "deploy"):
        snapshot = workload_diag.diagnose_deployment(request.resource_name, request.namespace)
    elif resource_type in ("statefulset", "sts"):
        snapshot = workload_diag.diagnose_statefulset(request.resource_name, request.namespace)
    elif resource_type in ("daemonset", "ds"):
        snapshot = workload_diag.diagnose_daemonset(request.resource_name, request.namespace)
    elif resource_type == "job":
        snapshot = workload_diag.diagnose_job(request.resource_name, request.namespace)
    elif resource_type in ("service", "svc"):
        snapshot = network_diag.diagnose_service(request.resource_name, request.namespace)
    elif resource_type in ("ingress", "ing"):
        snapshot = network_diag.diagnose_ingress(request.resource_name, request.namespace)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported resource type: {resource_type}. "
            f"Supported: pod, deployment, statefulset, daemonset, job, service, ingress.",
        )

    # Create a session and analyze
    session = session_manager.get_or_create_session(
        namespace=request.namespace,
        context=request.context,
    )

    result = await session.agent.analyze(
        f"Diagnose this {resource_type} and provide detailed RCA:\n\n"
        f"{snapshot.as_context()}"
    )

    return AnalyzeResponse(
        session_id=session.session_id,
        status=result.status.value,
        analysis=result.analysis,
        rca=result.rca,
        remediation=result.remediation,
        follow_up_question=result.follow_up_question,
        commands_executed=result.commands_executed,
        error=result.error,
    )


@app.post(
    "/trace",
    response_model=AnalyzeResponse,
    tags=["Networking"],
    summary="Trace network connectivity between pods",
)
async def trace_network(request: NetworkTraceRequest):
    """
    Trace network connectivity from a source pod to a target.

    Systematically checks DNS, endpoints, NetworkPolicies,
    and direct connectivity.
    """
    kubectl = KubectlExecutor(context=request.context, namespace=request.namespace)
    network_diag = NetworkDiagnostics(kubectl)

    snapshot = network_diag.trace_connectivity(
        source_pod=request.source_pod,
        target=request.target,
        port=request.port,
        namespace=request.namespace,
        target_namespace=request.target_namespace,
    )

    session = session_manager.get_or_create_session(
        namespace=request.namespace,
        context=request.context,
    )

    result = await session.agent.analyze(
        f"Trace and diagnose this network connectivity issue:\n\n"
        f"{snapshot.as_context()}"
    )

    return AnalyzeResponse(
        session_id=session.session_id,
        status=result.status.value,
        analysis=result.analysis,
        rca=result.rca,
        remediation=result.remediation,
        follow_up_question=result.follow_up_question,
        commands_executed=result.commands_executed,
        error=result.error,
    )


# --- Remediation Endpoint ---


@app.post(
    "/remediate",
    response_model=RemediationResponse,
    tags=["Remediation"],
    summary="Execute remediation steps",
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
)
async def execute_remediation(request: RemediationRequest):
    """
    Execute previously suggested remediation steps.

    Requires the session_id from a prior /analyze response that
    included remediation steps. The `confirm` field must be set
    to true as a safety measure.
    """
    if not request.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Remediation requires explicit confirmation. Set 'confirm' to true.",
        )

    session = session_manager.get_session(request.session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{request.session_id}' not found or expired.",
        )

    pending = session.agent.get_pending_remediation()
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending remediation steps in this session.",
        )

    # Execute the remediation
    results = session.agent.execute_remediation(request.step_indices)

    executed_steps = []
    for result in results:
        executed_steps.append({
            "command": result.command,
            "success": result.success,
            "output": result.stdout if result.success else result.stderr,
            "duration_ms": result.duration_ms,
        })

    return RemediationResponse(
        session_id=request.session_id,
        executed_steps=executed_steps,
        all_successful=all(r.success for r in results),
    )


# --- Scan Endpoint ---


@app.get(
    "/scan",
    response_model=AnalyzeResponse,
    tags=["Analysis"],
    summary="Scan for failing workloads",
)
async def scan_cluster(
    namespace: str | None = None,
    context: str | None = None,
):
    """
    Scan the cluster (or a specific namespace) for failing workloads
    and provide a health summary.
    """
    kubectl = KubectlExecutor(context=context, namespace=namespace)
    workload_diag = WorkloadDiagnostics(kubectl)

    snapshot = workload_diag.scan_failing_workloads(namespace)

    session = session_manager.get_or_create_session(
        namespace=namespace,
        context=context,
    )

    result = await session.agent.analyze(
        f"Analyze these cluster diagnostics and provide a health summary "
        f"with any issues found:\n\n{snapshot.as_context()}"
    )

    return AnalyzeResponse(
        session_id=session.session_id,
        status=result.status.value,
        analysis=result.analysis,
        rca=result.rca,
        remediation=result.remediation,
        follow_up_question=result.follow_up_question,
        commands_executed=result.commands_executed,
        error=result.error,
    )


# --- Session Management Endpoints ---


@app.delete(
    "/sessions/{session_id}",
    tags=["Sessions"],
    summary="Delete a session",
)
async def delete_session(session_id: str):
    """Explicitly delete a session and its context."""
    removed = session_manager.remove_session(session_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return {"message": f"Session '{session_id}' deleted.", "session_id": session_id}


@app.get(
    "/sessions",
    tags=["Sessions"],
    summary="List active sessions",
)
async def list_sessions():
    """List all active (non-expired) sessions."""
    return {
        "active_sessions": session_manager.active_count,
        "total_sessions": session_manager.total_count,
        "ttl_seconds": settings.api_session_ttl_seconds,
    }
