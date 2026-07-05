"""
Workload Diagnostics Module.

Provides pre-built diagnostic routines for common Kubernetes workload
failures. These routines gather relevant information before sending
it to the LLM for analysis, reducing the number of back-and-forth
iterations needed.
"""

from dataclasses import dataclass, field
from typing import Optional

from k8s_agent.core.kubectl import KubectlExecutor, KubectlResult
from k8s_agent.utils.logging import get_logger

logger = get_logger("diagnostics.workload")


@dataclass
class WorkloadSnapshot:
    """A comprehensive snapshot of a workload's state for diagnosis."""

    resource_type: str
    resource_name: str
    namespace: str
    results: dict[str, KubectlResult] = field(default_factory=dict)

    def as_context(self) -> str:
        """Format all gathered data as LLM context."""
        parts = [
            f"## Workload Diagnostic Snapshot",
            f"**Resource:** {self.resource_type}/{self.resource_name}",
            f"**Namespace:** {self.namespace}",
            "",
        ]
        for label, result in self.results.items():
            parts.append(f"### {label}")
            parts.append(result.as_context())
            parts.append("")
        return "\n".join(parts)


class WorkloadDiagnostics:
    """
    Pre-built diagnostic routines for Kubernetes workloads.

    Gathers comprehensive information about failing workloads to minimize
    the number of LLM iterations needed for diagnosis.
    """

    def __init__(self, kubectl: KubectlExecutor):
        self.kubectl = kubectl

    def diagnose_pod(
        self, name: str, namespace: str = "default"
    ) -> WorkloadSnapshot:
        """
        Gather comprehensive diagnostic information about a pod.

        Collects: pod spec, status, events, logs, previous logs,
        and related resource information.
        """
        snapshot = WorkloadSnapshot(
            resource_type="pod",
            resource_name=name,
            namespace=namespace,
        )

        # Pod description (includes events, conditions, container statuses)
        snapshot.results["Pod Description"] = self.kubectl.execute(
            f"describe pod {name}", namespace=namespace
        )

        # Pod YAML for detailed spec analysis
        snapshot.results["Pod Spec (YAML)"] = self.kubectl.execute(
            f"get pod {name} -o yaml", namespace=namespace
        )

        # Current logs (last 100 lines)
        snapshot.results["Current Logs (last 100 lines)"] = self.kubectl.execute(
            f"logs {name} --tail=100 --all-containers=true",
            namespace=namespace,
        )

        # Previous container logs (if container restarted)
        snapshot.results["Previous Logs"] = self.kubectl.execute(
            f"logs {name} --previous --tail=50 --all-containers=true",
            namespace=namespace,
        )

        # Events related to this pod
        snapshot.results["Related Events"] = self.kubectl.execute(
            f"get events --field-selector involvedObject.name={name} "
            f"--sort-by=.lastTimestamp",
            namespace=namespace,
        )

        # Node information (if pod is scheduled)
        pod_result = self.kubectl.execute(
            f"get pod {name} -o jsonpath={{.spec.nodeName}}",
            namespace=namespace,
        )
        if pod_result.success and pod_result.stdout.strip():
            node_name = pod_result.stdout.strip()
            snapshot.results["Node Conditions"] = self.kubectl.execute(
                f"describe node {node_name} | grep -A 20 'Conditions:'"
            )

        logger.info(f"Workload snapshot gathered for pod/{name} in {namespace}")
        return snapshot

    def diagnose_deployment(
        self, name: str, namespace: str = "default"
    ) -> WorkloadSnapshot:
        """
        Gather diagnostic information about a deployment and its pods.
        """
        snapshot = WorkloadSnapshot(
            resource_type="deployment",
            resource_name=name,
            namespace=namespace,
        )

        # Deployment description
        snapshot.results["Deployment Description"] = self.kubectl.execute(
            f"describe deployment {name}", namespace=namespace
        )

        # Deployment status
        snapshot.results["Deployment Status"] = self.kubectl.execute(
            f"get deployment {name} -o wide", namespace=namespace
        )

        # ReplicaSets owned by this deployment
        snapshot.results["ReplicaSets"] = self.kubectl.execute(
            f"get replicaset -l app={name} -o wide", namespace=namespace
        )

        # Pods owned by this deployment
        snapshot.results["Pods"] = self.kubectl.execute(
            f"get pods -l app={name} -o wide", namespace=namespace
        )

        # Events for the deployment
        snapshot.results["Deployment Events"] = self.kubectl.execute(
            f"get events --field-selector involvedObject.name={name} "
            f"--sort-by=.lastTimestamp",
            namespace=namespace,
        )

        # Get failing pods and their logs
        pods_result = self.kubectl.execute(
            f"get pods -l app={name} --field-selector=status.phase!=Running "
            f"-o jsonpath={{.items[*].metadata.name}}",
            namespace=namespace,
        )
        if pods_result.success and pods_result.stdout.strip():
            failing_pods = pods_result.stdout.strip().split()
            for pod_name in failing_pods[:3]:  # Limit to first 3 failing pods
                snapshot.results[f"Failing Pod Logs ({pod_name})"] = (
                    self.kubectl.execute(
                        f"logs {pod_name} --tail=50 --all-containers=true",
                        namespace=namespace,
                    )
                )
                snapshot.results[f"Failing Pod Events ({pod_name})"] = (
                    self.kubectl.execute(
                        f"get events --field-selector involvedObject.name={pod_name} "
                        f"--sort-by=.lastTimestamp",
                        namespace=namespace,
                    )
                )

        # Rollout status
        snapshot.results["Rollout Status"] = self.kubectl.execute(
            f"rollout status deployment/{name} --timeout=5s",
            namespace=namespace,
        )

        # Rollout history
        snapshot.results["Rollout History"] = self.kubectl.execute(
            f"rollout history deployment/{name}", namespace=namespace
        )

        logger.info(f"Workload snapshot gathered for deployment/{name} in {namespace}")
        return snapshot

    def diagnose_statefulset(
        self, name: str, namespace: str = "default"
    ) -> WorkloadSnapshot:
        """Gather diagnostic information about a StatefulSet."""
        snapshot = WorkloadSnapshot(
            resource_type="statefulset",
            resource_name=name,
            namespace=namespace,
        )

        snapshot.results["StatefulSet Description"] = self.kubectl.execute(
            f"describe statefulset {name}", namespace=namespace
        )

        snapshot.results["StatefulSet Status"] = self.kubectl.execute(
            f"get statefulset {name} -o wide", namespace=namespace
        )

        snapshot.results["Pods"] = self.kubectl.execute(
            f"get pods -l app={name} -o wide", namespace=namespace
        )

        snapshot.results["PVCs"] = self.kubectl.execute(
            f"get pvc -l app={name}", namespace=namespace
        )

        snapshot.results["Events"] = self.kubectl.execute(
            f"get events --field-selector involvedObject.name={name} "
            f"--sort-by=.lastTimestamp",
            namespace=namespace,
        )

        logger.info(f"Workload snapshot gathered for statefulset/{name} in {namespace}")
        return snapshot

    def diagnose_daemonset(
        self, name: str, namespace: str = "default"
    ) -> WorkloadSnapshot:
        """Gather diagnostic information about a DaemonSet."""
        snapshot = WorkloadSnapshot(
            resource_type="daemonset",
            resource_name=name,
            namespace=namespace,
        )

        snapshot.results["DaemonSet Description"] = self.kubectl.execute(
            f"describe daemonset {name}", namespace=namespace
        )

        snapshot.results["DaemonSet Status"] = self.kubectl.execute(
            f"get daemonset {name} -o wide", namespace=namespace
        )

        snapshot.results["Pods"] = self.kubectl.execute(
            f"get pods -l app={name} -o wide", namespace=namespace
        )

        snapshot.results["Events"] = self.kubectl.execute(
            f"get events --field-selector involvedObject.name={name} "
            f"--sort-by=.lastTimestamp",
            namespace=namespace,
        )

        logger.info(f"Workload snapshot gathered for daemonset/{name} in {namespace}")
        return snapshot

    def diagnose_job(
        self, name: str, namespace: str = "default"
    ) -> WorkloadSnapshot:
        """Gather diagnostic information about a Job."""
        snapshot = WorkloadSnapshot(
            resource_type="job",
            resource_name=name,
            namespace=namespace,
        )

        snapshot.results["Job Description"] = self.kubectl.execute(
            f"describe job {name}", namespace=namespace
        )

        snapshot.results["Job Status"] = self.kubectl.execute(
            f"get job {name} -o wide", namespace=namespace
        )

        snapshot.results["Pods"] = self.kubectl.execute(
            f"get pods -l job-name={name} -o wide", namespace=namespace
        )

        # Get logs from job pods
        pods_result = self.kubectl.execute(
            f"get pods -l job-name={name} "
            f"-o jsonpath={{.items[*].metadata.name}}",
            namespace=namespace,
        )
        if pods_result.success and pods_result.stdout.strip():
            pod_names = pods_result.stdout.strip().split()
            for pod_name in pod_names[:3]:
                snapshot.results[f"Pod Logs ({pod_name})"] = (
                    self.kubectl.execute(
                        f"logs {pod_name} --tail=100 --all-containers=true",
                        namespace=namespace,
                    )
                )

        snapshot.results["Events"] = self.kubectl.execute(
            f"get events --field-selector involvedObject.name={name} "
            f"--sort-by=.lastTimestamp",
            namespace=namespace,
        )

        logger.info(f"Workload snapshot gathered for job/{name} in {namespace}")
        return snapshot

    def scan_failing_workloads(
        self, namespace: Optional[str] = None
    ) -> WorkloadSnapshot:
        """
        Scan for all failing workloads across the cluster or a namespace.
        """
        ns = namespace or "default"
        snapshot = WorkloadSnapshot(
            resource_type="cluster-scan",
            resource_name="failing-workloads",
            namespace=ns,
        )

        ns_flag = f"-n {ns}" if namespace else "--all-namespaces"

        # Pods not in Running/Succeeded state
        snapshot.results["Non-Running Pods"] = self.kubectl.execute(
            f"get pods {ns_flag} --field-selector=status.phase!=Running,status.phase!=Succeeded -o wide"
        )

        # Pods with restart counts > 0
        snapshot.results["Restarting Pods"] = self.kubectl.execute(
            f"get pods {ns_flag} -o wide --sort-by=.status.containerStatuses[0].restartCount"
        )

        # Deployments not fully available
        snapshot.results["Deployment Status"] = self.kubectl.execute(
            f"get deployments {ns_flag} -o wide"
        )

        # Recent warning events
        snapshot.results["Warning Events (last 1h)"] = self.kubectl.execute(
            f"get events {ns_flag} --field-selector type=Warning "
            f"--sort-by=.lastTimestamp"
        )

        # Node conditions
        snapshot.results["Node Status"] = self.kubectl.execute(
            "get nodes -o wide"
        )

        logger.info(f"Cluster scan completed for namespace={ns or 'all'}")
        return snapshot
