"""
Agent Orchestration Engine.

Coordinates the interaction loop between the user, GitHub Copilot CLI,
and kubectl to perform Kubernetes debugging, RCA, and remediation.
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from k8s_agent.config import settings
from k8s_agent.core.copilot_client import CopilotClient, CopilotResponse
from k8s_agent.core.kubectl import KubectlExecutor, KubectlResult
from k8s_agent.utils.logging import get_logger

logger = get_logger("agent")


SYSTEM_PROMPT = """You are a specialized Kubernetes debugging agent. Your role is to diagnose issues in Kubernetes clusters, provide detailed Root Cause Analysis (RCA), and suggest remediation steps.

## Your Capabilities
- Analyze failing workloads (Pods, Deployments, StatefulSets, DaemonSets, Jobs)
- Debug networking issues (Services, Endpoints, Ingress, NetworkPolicies, DNS)
- Trace connectivity failures between workloads
- Provide detailed RCA with evidence
- Suggest and generate remediation commands

## Response Format
You MUST respond in the following JSON format:

```json
{
  "thinking": "Your internal reasoning about the problem (brief)",
  "commands": [
    {
      "command": "kubectl command to execute (without 'kubectl' prefix)",
      "purpose": "Why this command is needed"
    }
  ],
  "analysis": "Your current analysis based on available information (markdown formatted)",
  "rca": {
    "root_cause": "The identified root cause (null if still investigating)",
    "evidence": ["List of evidence supporting the conclusion"],
    "severity": "critical|high|medium|low (null if still investigating)"
  },
  "remediation": {
    "steps": [
      {
        "description": "What this step does",
        "command": "The exact kubectl command to execute (without 'kubectl' prefix)",
        "risk": "low|medium|high"
      }
    ],
    "explanation": "Why these steps will fix the issue"
  },
  "status": "investigating|diagnosed|resolved",
  "follow_up_question": "Optional question to ask the user for more context (null if not needed)"
}
```

## Rules
1. When you need more information, include kubectl commands in the "commands" array.
2. Only include "rca" and "remediation" when you have enough evidence.
3. Always explain your reasoning in the "analysis" field.
4. For networking issues, systematically check: DNS resolution, Service endpoints, NetworkPolicies, and pod-to-pod connectivity.
5. For workload issues, check: Pod status, events, logs (current and previous), resource limits, and node conditions.
6. Remediation commands should be safe and reversible where possible.
7. NEVER include destructive commands (like deleting namespaces or PVCs) without explicit warnings.
8. If the cluster output is provided as context, analyze it directly without requesting the same command again.
"""


class AgentStatus(str, Enum):
    """Current status of the agent's diagnostic process."""
    IDLE = "idle"
    INVESTIGATING = "investigating"
    DIAGNOSED = "diagnosed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXECUTING_REMEDIATION = "executing_remediation"
    RESOLVED = "resolved"
    ERROR = "error"


@dataclass
class AgentTurn:
    """Represents a single turn in the agent conversation."""
    user_input: str
    copilot_response: Optional[str] = None
    kubectl_results: list[KubectlResult] = field(default_factory=list)
    parsed_response: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class DiagnosticResult:
    """Final diagnostic result from the agent."""
    status: AgentStatus
    analysis: str
    rca: Optional[dict] = None
    remediation: Optional[dict] = None
    follow_up_question: Optional[str] = None
    commands_executed: list[str] = field(default_factory=list)
    error: Optional[str] = None
    raw_response: str = ""


class K8sAgent:
    """
    The main Kubernetes debugging agent.

    Orchestrates the loop:
    1. Receive user query
    2. Send to Copilot CLI with system context
    3. Parse response for kubectl commands
    4. Execute kubectl commands
    5. Feed results back to Copilot
    6. Repeat until RCA is complete or max iterations reached
    """

    def __init__(
        self,
        session_name: Optional[str] = None,
        namespace: Optional[str] = None,
        context: Optional[str] = None,
    ):
        """
        Initialize the K8s debugging agent.

        Args:
            session_name: Explicit Copilot session name (auto-generated if None).
            namespace: Default Kubernetes namespace to operate in.
            context: Kubernetes context to use.
        """
        self.copilot = CopilotClient(session_name=session_name)
        self.kubectl = KubectlExecutor(context=context, namespace=namespace)
        self.status = AgentStatus.IDLE
        self.history: list[AgentTurn] = []
        self._pending_remediation: Optional[dict] = None

    @property
    def session_name(self) -> Optional[str]:
        return self.copilot.session_name

    def _build_prompt(self, user_input: str, kubectl_context: str = "") -> str:
        """
        Build the full prompt to send to Copilot CLI.

        On the first turn, includes the system prompt. On subsequent turns,
        includes kubectl output context.
        """
        parts = []

        # Include system prompt on first turn
        if not self.history:
            parts.append(SYSTEM_PROMPT)
            parts.append("\n---\n")

        # Include kubectl output context if available
        if kubectl_context:
            parts.append("## Kubectl Output (from previous commands):\n")
            parts.append(kubectl_context)
            parts.append("\n---\n")

        # User query
        parts.append(f"## User Query:\n{user_input}")
        parts.append("\n\nRespond with the JSON format specified in the system prompt.")

        return "\n".join(parts)

    def _build_followup_prompt(self, kubectl_context: str) -> str:
        """Build a follow-up prompt with kubectl results for continued analysis."""
        return (
            "Here are the results from the kubectl commands you requested. "
            "Continue your analysis and provide updated findings.\n\n"
            f"{kubectl_context}\n\n"
            "Respond with the JSON format specified in the system prompt."
        )

    def _parse_response(self, raw_response: str) -> Optional[dict]:
        """
        Parse the Copilot response, extracting JSON from potential markdown wrapping.

        Handles cases where the response is wrapped in ```json ... ``` blocks.
        """
        if not raw_response:
            return None

        # Try direct JSON parse first
        try:
            return json.loads(raw_response)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code blocks
        json_patterns = [
            r"```json\s*\n(.*?)\n\s*```",
            r"```\s*\n(.*?)\n\s*```",
            r"\{[\s\S]*\}",
        ]

        for pattern in json_patterns:
            matches = re.findall(pattern, raw_response, re.DOTALL)
            for match in matches:
                try:
                    parsed = json.loads(match)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue

        logger.warning("Failed to parse JSON from Copilot response")
        return None

    def _execute_diagnostic_commands(self, commands: list[dict]) -> str:
        """
        Execute a list of diagnostic kubectl commands and format the results.

        Args:
            commands: List of command dicts with 'command' and 'purpose' keys.

        Returns:
            Formatted string of all command outputs for context injection.
        """
        results = []

        for cmd_info in commands:
            command = cmd_info.get("command", "")
            purpose = cmd_info.get("purpose", "")

            if not command:
                continue

            # Strip 'kubectl' prefix if present
            command = command.strip()
            if command.startswith("kubectl "):
                command = command[8:]

            # Only execute read commands during investigation
            if self.kubectl.is_write_command(command):
                results.append(
                    f"--- SKIPPED (write command): kubectl {command} ---\n"
                    f"Purpose: {purpose}\n"
                    "Write commands require explicit user confirmation.\n"
                    "--- end ---"
                )
                continue

            result = self.kubectl.execute_read(command)
            results.append(result.as_context())

            # Track in current turn
            if self.history:
                self.history[-1].kubectl_results.append(result)

        return "\n\n".join(results)

    async def analyze(self, user_input: str) -> DiagnosticResult:
        """
        Run the full diagnostic loop for a user query.

        This is the main entry point. It will:
        1. Send the query to Copilot
        2. Execute any requested kubectl commands
        3. Feed results back to Copilot
        4. Repeat until diagnosis is complete or max iterations reached

        Args:
            user_input: The user's debugging query.

        Returns:
            DiagnosticResult with the full analysis.
        """
        self.status = AgentStatus.INVESTIGATING
        turn = AgentTurn(user_input=user_input)
        self.history.append(turn)

        # Build initial prompt
        prompt = self._build_prompt(user_input)
        all_commands_executed: list[str] = []

        for iteration in range(settings.max_diagnostic_iterations):
            logger.info(f"Diagnostic iteration {iteration + 1}")

            # Invoke Copilot
            response = self.copilot.invoke_with_retry(prompt)

            if not response.success:
                self.status = AgentStatus.ERROR
                return DiagnosticResult(
                    status=self.status,
                    analysis="",
                    error=f"Copilot CLI error: {response.error}",
                    raw_response=response.content,
                )

            turn.copilot_response = response.content

            # Parse the response
            parsed = self._parse_response(response.content)
            turn.parsed_response = parsed

            if parsed is None:
                # If we can't parse JSON, return the raw response as analysis
                self.status = AgentStatus.DIAGNOSED
                return DiagnosticResult(
                    status=self.status,
                    analysis=response.content,
                    commands_executed=all_commands_executed,
                    raw_response=response.content,
                )

            # Check if there are commands to execute
            commands = parsed.get("commands", [])
            status = parsed.get("status", "investigating")

            if commands and status == "investigating":
                # Execute the diagnostic commands
                kubectl_context = self._execute_diagnostic_commands(commands)
                all_commands_executed.extend(
                    [c.get("command", "") for c in commands]
                )

                # Build follow-up prompt with results
                prompt = self._build_followup_prompt(kubectl_context)
                continue

            # Diagnosis is complete (or no more commands needed)
            break

        # Build the final result
        if parsed:
            rca = parsed.get("rca")
            remediation = parsed.get("remediation")

            if remediation and remediation.get("steps"):
                self.status = AgentStatus.DIAGNOSED
                self._pending_remediation = remediation
            elif rca and rca.get("root_cause"):
                self.status = AgentStatus.DIAGNOSED
            else:
                self.status = AgentStatus.INVESTIGATING

            return DiagnosticResult(
                status=self.status,
                analysis=parsed.get("analysis", ""),
                rca=rca,
                remediation=remediation,
                follow_up_question=parsed.get("follow_up_question"),
                commands_executed=all_commands_executed,
                raw_response=response.content,
            )

        self.status = AgentStatus.ERROR
        return DiagnosticResult(
            status=self.status,
            analysis="Unable to complete diagnosis.",
            error="Max iterations reached without conclusion.",
            commands_executed=all_commands_executed,
            raw_response=response.content if response else "",
        )

    def get_pending_remediation(self) -> Optional[dict]:
        """Get the pending remediation steps awaiting user confirmation."""
        return self._pending_remediation

    def execute_remediation(self, step_indices: Optional[list[int]] = None) -> list[KubectlResult]:
        """
        Execute confirmed remediation steps.

        Args:
            step_indices: Indices of steps to execute. If None, execute all.

        Returns:
            List of KubectlResult for each executed step.
        """
        if not self._pending_remediation:
            logger.warning("No pending remediation to execute")
            return []

        steps = self._pending_remediation.get("steps", [])
        if step_indices is None:
            step_indices = list(range(len(steps)))

        results = []
        self.status = AgentStatus.EXECUTING_REMEDIATION

        for idx in step_indices:
            if idx < 0 or idx >= len(steps):
                continue

            step = steps[idx]
            command = step.get("command", "")

            if not command:
                continue

            # Strip 'kubectl' prefix if present
            if command.startswith("kubectl "):
                command = command[8:]

            logger.info(f"Executing remediation step {idx + 1}: {command}")
            result = self.kubectl.execute_write(command)
            results.append(result)

            if not result.success:
                logger.error(
                    f"Remediation step {idx + 1} failed: {result.stderr}"
                )
                break

        # Clear pending remediation after execution
        self._pending_remediation = None
        self.status = AgentStatus.RESOLVED if all(r.success for r in results) else AgentStatus.ERROR

        return results

    def follow_up(self, user_input: str) -> "DiagnosticResult":
        """
        Send a follow-up message in the same session.

        This is a synchronous wrapper around analyze() for simpler usage.
        """
        import asyncio
        return asyncio.run(self.analyze(user_input))
