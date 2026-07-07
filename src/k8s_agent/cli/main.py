"""
CLI Entry Point.

Defines the Typer application with all commands and options
for the K8s debugging agent.
"""

import asyncio
from typing import Optional

import typer
from rich.console import Console
from typing_extensions import Annotated

from k8s_agent.config import settings
from k8s_agent.utils.logging import setup_logging, get_logger

app = typer.Typer(
    name="k8s-agent",
    help="Specialized Kubernetes debugging agent powered by GitHub Copilot CLI.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()


@app.command()
def interactive(
    namespace: Annotated[
        Optional[str],
        typer.Option("--namespace", "-n", help="Default Kubernetes namespace."),
    ] = None,
    context: Annotated[
        Optional[str],
        typer.Option("--context", "-c", help="Kubernetes context to use."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", "-l", help="Log level (DEBUG, INFO, WARNING, ERROR)."),
    ] = "WARNING",
) -> None:
    """
    Launch the interactive debugging session.

    Provides a REPL interface with rich formatting, slash commands,
    and natural language queries for Kubernetes debugging.
    """
    setup_logging(mode="human", level=log_level)

    from k8s_agent.cli.interactive import run_interactive

    run_interactive(namespace=namespace, context=context)


@app.command()
def analyze(
    query: Annotated[
        str,
        typer.Argument(help="The debugging query or issue description."),
    ],
    namespace: Annotated[
        Optional[str],
        typer.Option("--namespace", "-n", help="Kubernetes namespace."),
    ] = None,
    context: Annotated[
        Optional[str],
        typer.Option("--context", "-c", help="Kubernetes context."),
    ] = None,
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Output format: text, json, markdown."),
    ] = "text",
    log_level: Annotated[
        str,
        typer.Option("--log-level", "-l", help="Log level."),
    ] = "WARNING",
) -> None:
    """
    Run a single-shot analysis query (non-interactive).

    Executes the query, performs diagnosis, and outputs the result.
    """
    import json as json_module

    setup_logging(mode="human", level=log_level)
    logger = get_logger("cli")

    from k8s_agent.core.agent import K8sAgent

    agent = K8sAgent(namespace=namespace, context=context)

    with console.status("[bold cyan]Analyzing...[/bold cyan]", spinner="dots"):
        result = asyncio.run(agent.analyze(query))

    if output == "json":
        output_data = {
            "status": result.status.value,
            "analysis": result.analysis,
            "rca": result.rca,
            "remediation": result.remediation,
            "commands_executed": result.commands_executed,
            "error": result.error,
        }
        console.print_json(json_module.dumps(output_data, indent=2))
    elif output == "markdown":
        from rich.markdown import Markdown

        md_parts = [f"# Kubernetes Diagnostic Report\n"]
        md_parts.append(f"**Status:** {result.status.value}\n")
        if result.analysis:
            md_parts.append(f"## Analysis\n{result.analysis}\n")
        if result.rca:
            md_parts.append(f"## Root Cause Analysis\n")
            md_parts.append(f"**Root Cause:** {result.rca.get('root_cause', 'N/A')}\n")
            md_parts.append(f"**Severity:** {result.rca.get('severity', 'N/A')}\n")
        if result.remediation:
            md_parts.append(f"## Remediation\n")
            for i, step in enumerate(result.remediation.get("steps", []), 1):
                md_parts.append(
                    f"{i}. {step.get('description', '')} "
                    f"(`kubectl {step.get('command', '')}`)\n"
                )
        console.print(Markdown("\n".join(md_parts)))
    else:
        # Text output
        from k8s_agent.cli.interactive import InteractiveCLI

        cli = InteractiveCLI(namespace=namespace, context=context)
        cli._render_diagnostic_result(result)


@app.command()
def scan(
    namespace: Annotated[
        Optional[str],
        typer.Option("--namespace", "-n", help="Namespace to scan (all if omitted)."),
    ] = None,
    context: Annotated[
        Optional[str],
        typer.Option("--context", "-c", help="Kubernetes context."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", "-l", help="Log level."),
    ] = "WARNING",
) -> None:
    """
    Scan for failing workloads and provide a health summary.
    """
    setup_logging(mode="human", level=log_level)

    from k8s_agent.core.agent import K8sAgent
    from k8s_agent.core.kubectl import KubectlExecutor
    from k8s_agent.diagnostics.workload import WorkloadDiagnostics

    kubectl = KubectlExecutor(context=context, namespace=namespace)
    diag = WorkloadDiagnostics(kubectl)
    agent = K8sAgent(namespace=namespace, context=context)

    with console.status("[bold]Scanning cluster...[/bold]", spinner="dots"):
        snapshot = diag.scan_failing_workloads(namespace)
        result = asyncio.run(agent.analyze(
            f"Analyze these cluster diagnostics and provide a health summary "
            f"with any issues found:\n\n{snapshot.as_context()}"
        ))

    from k8s_agent.cli.interactive import InteractiveCLI

    cli = InteractiveCLI(namespace=namespace, context=context)
    cli._render_diagnostic_result(result)


@app.command()
def serve(
    host: Annotated[
        str,
        typer.Option("--host", "-H", help="API server host."),
    ] = "0.0.0.0",
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="API server port."),
    ] = 8080,
    workers: Annotated[
        int,
        typer.Option("--workers", "-w", help="Number of worker processes."),
    ] = 4,
    log_level: Annotated[
        str,
        typer.Option("--log-level", "-l", help="Log level."),
    ] = "INFO",
) -> None:
    """
    Start the REST API server for programmatic interaction.
    """
    import uvicorn

    setup_logging(mode="json", level=log_level)
    logger = get_logger("cli")

    logger.info(f"Starting API server on {host}:{port} with {workers} workers")
    console.print(
        f"[bold green]Starting K8s Agent API server on {host}:{port}[/bold green]"
    )

    uvicorn.run(
        "k8s_agent.api.server:app",
        host=host,
        port=port,
        workers=workers,
        log_level=log_level.lower(),
        access_log=True,
    )


@app.command()
def version() -> None:
    """Show the agent version and configuration."""
    from importlib.metadata import version as pkg_version

    try:
        ver = pkg_version("k8s-agent")
    except Exception:
        ver = "1.0.0 (development)"

    console.print(f"[bold]k8s-agent[/bold] v{ver}")
    console.print(f"  Copilot binary: {settings.copilot_binary}")
    console.print(f"  Kubectl binary: {settings.kubectl_binary}")
    console.print(f"  Model: {settings.copilot_model}")
    console.print(f"  Session prefix: {settings.session_prefix}")
    console.print(f"  API session TTL: {settings.api_session_ttl_seconds}s")


if __name__ == "__main__":
    app()
