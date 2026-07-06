"""
Interactive CLI Mode.

Provides a rich, ChatGPT-like terminal experience for interacting
with the K8s debugging agent. Uses Rich for markdown rendering,
panels, and formatted output.
"""

import asyncio
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich.live import Live
from rich.spinner import Spinner
from rich.theme import Theme

from k8s_agent.core.agent import K8sAgent, AgentStatus, DiagnosticResult
from k8s_agent.core.kubectl import KubectlExecutor
from k8s_agent.diagnostics.workload import WorkloadDiagnostics
from k8s_agent.diagnostics.network import NetworkDiagnostics
from k8s_agent.config import settings
from k8s_agent.utils.logging import get_logger

logger = get_logger("cli.interactive")

# Custom theme for the agent
AGENT_THEME = Theme({
    "agent.name": "bold cyan",
    "agent.thinking": "dim italic",
    "agent.error": "bold red",
    "agent.success": "bold green",
    "agent.warning": "bold yellow",
    "rca.critical": "bold red",
    "rca.high": "red",
    "rca.medium": "yellow",
    "rca.low": "green",
})


class InteractiveCLI:
    """
    Interactive terminal interface for the K8s debugging agent.

    Provides a REPL loop with rich formatting, command history,
    and confirmation prompts for remediation actions.
    """

    BANNER = r"""
 _  _____ ____       _                    _
| |/ ( _ ) ___| __ _| |__   __ _  ___ _ __ | |_
| ' // _ \___ \/ _` | '_ \ / _` |/ _ \ '_ \| __|
| . \ (_) |__) | (_| | | | | (_| |  __/ | | | |_
|_|\_\___/____/ \__,_|_| |_|\__, |\___|_| |_|\__|
                             |___/
    """

    HELP_TEXT = """
**Available Commands:**

| Command | Description |
|---------|-------------|
| `/help` | Show this help message |
| `/scan [namespace]` | Scan for failing workloads |
| `/pod <name> [ns]` | Diagnose a specific pod |
| `/deploy <name> [ns]` | Diagnose a deployment |
| `/svc <name> [ns]` | Diagnose a service |
| `/dns <target> [pod] [ns]` | Diagnose DNS resolution |
| `/trace <src> <dst> [port] [ns]` | Trace network connectivity |
| `/netpol <pod> [ns]` | Diagnose NetworkPolicy issues |
| `/context` | Show current kubectl context |
| `/session` | Show current Copilot session info |
| `/reset` | Reset the agent session |
| `/exit` or `/quit` | Exit the agent |

**Or simply type your question in natural language.**
"""

    def __init__(
        self,
        namespace: Optional[str] = None,
        context: Optional[str] = None,
    ):
        self.console = Console(theme=AGENT_THEME)
        self.agent = K8sAgent(namespace=namespace, context=context)
        self.kubectl = KubectlExecutor(context=context, namespace=namespace)
        self.workload_diag = WorkloadDiagnostics(self.kubectl)
        self.network_diag = NetworkDiagnostics(self.kubectl)
        self._namespace = namespace or "default"

    def _print_banner(self) -> None:
        """Display the agent banner and connection info."""
        self.console.print(
            Panel(
                Text(self.BANNER, style="bold cyan"),
                title="[bold]Kubernetes Debugging Agent[/bold]",
                subtitle="Powered by GitHub Copilot CLI",
                border_style="cyan",
            )
        )

        # Show cluster connection info
        context = self.kubectl.get_current_context()
        connectivity = self.kubectl.check_connectivity()

        info_table = Table(show_header=False, box=None, padding=(0, 2))
        info_table.add_column("Key", style="bold")
        info_table.add_column("Value")
        info_table.add_row("Context:", context)
        info_table.add_row("Namespace:", self._namespace)
        info_table.add_row(
            "Cluster:",
            "[green]Connected[/green]" if connectivity.success
            else "[red]Disconnected[/red]",
        )
        info_table.add_row(
            "Session:",
            self.agent.session_name or "Will be created on first query",
        )

        self.console.print(Panel(info_table, title="Connection Info", border_style="dim"))

        if not connectivity.success:
            self.console.print(
                "[agent.error]WARNING: Cannot connect to the Kubernetes cluster. "
                "Please check your kubeconfig.[/agent.error]"
            )

        self.console.print(
            "\nType [bold]/help[/bold] for available commands or ask a question.\n"
        )

    def _render_diagnostic_result(self, result: DiagnosticResult) -> None:
        """Render a diagnostic result with rich formatting."""
        # Analysis section
        if result.analysis:
            self.console.print(Rule("Analysis", style="cyan"))
            self.console.print(Markdown(result.analysis))
            self.console.print()

        # RCA section
        if result.rca and result.rca.get("root_cause"):
            severity = result.rca.get("severity", "unknown")
            severity_style = f"rca.{severity}" if severity in ("critical", "high", "medium", "low") else "bold"

            rca_panel_content = []
            rca_panel_content.append(f"**Root Cause:** {result.rca['root_cause']}\n")
            rca_panel_content.append(f"**Severity:** [{severity_style}]{severity.upper()}[/{severity_style}]\n")

            if result.rca.get("evidence"):
                rca_panel_content.append("**Evidence:**")
                for ev in result.rca["evidence"]:
                    rca_panel_content.append(f"  - {ev}")

            self.console.print(
                Panel(
                    Markdown("\n".join(rca_panel_content)),
                    title="[bold red]Root Cause Analysis[/bold red]",
                    border_style="red",
                )
            )

        # Remediation section
        if result.remediation and result.remediation.get("steps"):
            self.console.print(Rule("Remediation Steps", style="green"))

            if result.remediation.get("explanation"):
                self.console.print(
                    Markdown(f"*{result.remediation['explanation']}*")
                )
                self.console.print()

            rem_table = Table(title="Proposed Actions", show_lines=True)
            rem_table.add_column("#", style="bold", width=3)
            rem_table.add_column("Description", style="white")
            rem_table.add_column("Command", style="cyan")
            rem_table.add_column("Risk", style="bold")

            for i, step in enumerate(result.remediation["steps"], 1):
                risk = step.get("risk", "unknown")
                risk_style = {
                    "low": "green",
                    "medium": "yellow",
                    "high": "red",
                }.get(risk, "white")

                rem_table.add_row(
                    str(i),
                    step.get("description", ""),
                    f"kubectl {step.get('command', '')}",
                    f"[{risk_style}]{risk.upper()}[/{risk_style}]",
                )

            self.console.print(rem_table)
            self.console.print()

        # Follow-up question
        if result.follow_up_question:
            self.console.print(
                Panel(
                    result.follow_up_question,
                    title="[bold yellow]Agent Question[/bold yellow]",
                    border_style="yellow",
                )
            )

        # Error
        if result.error:
            self.console.print(
                f"[agent.error]Error: {result.error}[/agent.error]"
            )

        # Commands executed summary
        if result.commands_executed:
            self.console.print(
                f"\n[dim]Commands executed: {len(result.commands_executed)}[/dim]"
            )

    def _handle_remediation_confirmation(self, result: DiagnosticResult) -> None:
        """Ask the user to confirm and execute remediation steps."""
        remediation = self.agent.get_pending_remediation()
        if not remediation or not remediation.get("steps"):
            return

        self.console.print()
        execute = Confirm.ask(
            "[bold yellow]Would you like to execute the remediation steps?[/bold yellow]"
        )

        if not execute:
            self.console.print("[dim]Remediation skipped.[/dim]")
            return

        # Ask which steps to execute
        steps = remediation["steps"]
        if len(steps) > 1:
            self.console.print(
                "Enter step numbers to execute (comma-separated, or 'all'):"
            )
            selection = Prompt.ask("Steps", default="all")

            if selection.lower() == "all":
                step_indices = list(range(len(steps)))
            else:
                try:
                    step_indices = [int(x.strip()) - 1 for x in selection.split(",")]
                except ValueError:
                    self.console.print("[agent.error]Invalid selection.[/agent.error]")
                    return
        else:
            step_indices = [0]

        # Final confirmation for high-risk steps
        high_risk = any(
            steps[i].get("risk") == "high"
            for i in step_indices
            if 0 <= i < len(steps)
        )
        if high_risk:
            confirm = Confirm.ask(
                "[bold red]WARNING: This includes HIGH RISK steps. Proceed?[/bold red]"
            )
            if not confirm:
                self.console.print("[dim]Remediation cancelled.[/dim]")
                return

        # Execute
        self.console.print()
        with self.console.status("[bold green]Executing remediation...[/bold green]"):
            results = self.agent.execute_remediation(step_indices)

        # Display results
        for i, result in enumerate(results):
            status_icon = "[green]✓[/green]" if result.success else "[red]✗[/red]"
            self.console.print(f"  {status_icon} {result.command}")
            if not result.success:
                self.console.print(f"    [red]{result.stderr}[/red]")

        if all(r.success for r in results):
            self.console.print(
                "\n[agent.success]All remediation steps executed successfully![/agent.success]"
            )
        else:
            self.console.print(
                "\n[agent.warning]Some steps failed. Review the output above.[/agent.warning]"
            )

    async def _handle_slash_command(self, command: str) -> bool:
        """
        Handle slash commands. Returns True if the REPL should continue.
        """
        parts = command.strip().split()
        cmd = parts[0].lower()

        if cmd in ("/exit", "/quit"):
            self.console.print("[dim]Goodbye![/dim]")
            return False

        elif cmd == "/help":
            self.console.print(Markdown(self.HELP_TEXT))

        elif cmd == "/context":
            ctx = self.kubectl.get_current_context()
            self.console.print(f"Current context: [bold]{ctx}[/bold]")

        elif cmd == "/session":
            session = self.agent.session_name
            turns = len(self.agent.history)
            self.console.print(f"Session: [bold]{session or 'None'}[/bold]")
            self.console.print(f"Turns: {turns}")
            self.console.print(f"Status: {self.agent.status.value}")

        elif cmd == "/reset":
            self.agent.copilot.reset_session()
            self.agent.history.clear()
            self.agent.status = AgentStatus.IDLE
            self.console.print("[agent.success]Session reset.[/agent.success]")

        elif cmd == "/scan":
            ns = parts[1] if len(parts) > 1 else None
            with self.console.status("[bold]Scanning for failing workloads...[/bold]"):
                snapshot = self.workload_diag.scan_failing_workloads(ns)
            result = await self.agent.analyze(
                f"Analyze these cluster diagnostics and identify any issues:\n\n"
                f"{snapshot.as_context()}"
            )
            self._render_diagnostic_result(result)
            self._handle_remediation_confirmation(result)

        elif cmd == "/pod":
            if len(parts) < 2:
                self.console.print("[agent.error]Usage: /pod <name> [namespace][/agent.error]")
                return True
            name = parts[1]
            ns = parts[2] if len(parts) > 2 else self._namespace
            with self.console.status(f"[bold]Diagnosing pod/{name}...[/bold]"):
                snapshot = self.workload_diag.diagnose_pod(name, ns)
            result = await self.agent.analyze(
                f"Diagnose why this pod is failing and provide RCA:\n\n"
                f"{snapshot.as_context()}"
            )
            self._render_diagnostic_result(result)
            self._handle_remediation_confirmation(result)

        elif cmd == "/deploy":
            if len(parts) < 2:
                self.console.print("[agent.error]Usage: /deploy <name> [namespace][/agent.error]")
                return True
            name = parts[1]
            ns = parts[2] if len(parts) > 2 else self._namespace
            with self.console.status(f"[bold]Diagnosing deployment/{name}...[/bold]"):
                snapshot = self.workload_diag.diagnose_deployment(name, ns)
            result = await self.agent.analyze(
                f"Diagnose why this deployment is failing and provide RCA:\n\n"
                f"{snapshot.as_context()}"
            )
            self._render_diagnostic_result(result)
            self._handle_remediation_confirmation(result)

        elif cmd == "/svc":
            if len(parts) < 2:
                self.console.print("[agent.error]Usage: /svc <name> [namespace][/agent.error]")
                return True
            name = parts[1]
            ns = parts[2] if len(parts) > 2 else self._namespace
            with self.console.status(f"[bold]Diagnosing service/{name}...[/bold]"):
                snapshot = self.network_diag.diagnose_service(name, ns)
            result = await self.agent.analyze(
                f"Diagnose connectivity issues with this service:\n\n"
                f"{snapshot.as_context()}"
            )
            self._render_diagnostic_result(result)
            self._handle_remediation_confirmation(result)

        elif cmd == "/dns":
            if len(parts) < 2:
                self.console.print("[agent.error]Usage: /dns <target> [source_pod] [namespace][/agent.error]")
                return True
            target = parts[1]
            pod = parts[2] if len(parts) > 2 else None
            ns = parts[3] if len(parts) > 3 else self._namespace
            with self.console.status(f"[bold]Diagnosing DNS for {target}...[/bold]"):
                snapshot = self.network_diag.diagnose_dns(target, pod, ns)
            result = await self.agent.analyze(
                f"Diagnose DNS resolution issues for '{target}':\n\n"
                f"{snapshot.as_context()}"
            )
            self._render_diagnostic_result(result)
            self._handle_remediation_confirmation(result)

        elif cmd == "/trace":
            if len(parts) < 3:
                self.console.print(
                    "[agent.error]Usage: /trace <source_pod> <target> [port] [namespace][/agent.error]"
                )
                return True
            src = parts[1]
            dst = parts[2]
            port = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
            ns = parts[4] if len(parts) > 4 else self._namespace
            with self.console.status(f"[bold]Tracing {src} -> {dst}...[/bold]"):
                snapshot = self.network_diag.trace_connectivity(src, dst, port, ns)
            result = await self.agent.analyze(
                f"Trace and diagnose the network connectivity issue:\n\n"
                f"{snapshot.as_context()}"
            )
            self._render_diagnostic_result(result)
            self._handle_remediation_confirmation(result)

        elif cmd == "/netpol":
            if len(parts) < 2:
                self.console.print("[agent.error]Usage: /netpol <pod> [namespace][/agent.error]")
                return True
            pod = parts[1]
            ns = parts[2] if len(parts) > 2 else self._namespace
            with self.console.status(f"[bold]Analyzing NetworkPolicies for {pod}...[/bold]"):
                snapshot = self.network_diag.diagnose_network_policy(pod, ns)
            result = await self.agent.analyze(
                f"Analyze NetworkPolicy issues affecting pod '{pod}':\n\n"
                f"{snapshot.as_context()}"
            )
            self._render_diagnostic_result(result)
            self._handle_remediation_confirmation(result)

        else:
            self.console.print(
                f"[agent.warning]Unknown command: {cmd}. Type /help for available commands.[/agent.warning]"
            )

        return True

    async def run(self) -> None:
        """Run the interactive REPL loop."""
        self._print_banner()

        while True:
            try:
                # Get user input
                user_input = Prompt.ask("\n[bold cyan]k8s-agent[/bold cyan]")

                if not user_input.strip():
                    continue

                # Handle slash commands
                if user_input.strip().startswith("/"):
                    should_continue = await self._handle_slash_command(user_input)
                    if not should_continue:
                        break
                    continue

                # Natural language query - send to agent
                with self.console.status(
                    "[bold cyan]Analyzing...[/bold cyan]",
                    spinner="dots",
                ):
                    result = await self.agent.analyze(user_input)

                self._render_diagnostic_result(result)
                self._handle_remediation_confirmation(result)

            except KeyboardInterrupt:
                self.console.print("\n[dim]Use /exit to quit.[/dim]")
                continue
            except EOFError:
                break
            except Exception as e:
                logger.exception("Unexpected error in interactive loop")
                self.console.print(f"[agent.error]Unexpected error: {e}[/agent.error]")
                continue


def run_interactive(
    namespace: Optional[str] = None,
    context: Optional[str] = None,
) -> None:
    """Entry point for the interactive CLI mode."""
    cli = InteractiveCLI(namespace=namespace, context=context)
    asyncio.run(cli.run())
