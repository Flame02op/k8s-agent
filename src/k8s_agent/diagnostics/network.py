"""
Network Diagnostics Module.

Provides pre-built diagnostic routines for Kubernetes networking issues
including Services, Ingress, DNS, NetworkPolicies, and pod-to-pod
connectivity tracing.
"""

from dataclasses import dataclass, field
from typing import Optional

from k8s_agent.core.kubectl import KubectlExecutor, KubectlResult
from k8s_agent.utils.logging import get_logger

logger = get_logger("diagnostics.network")


@dataclass
class NetworkSnapshot:
    """A comprehensive snapshot of networking state for diagnosis."""

    issue_type: str
    target: str
    namespace: str
    results: dict[str, KubectlResult] = field(default_factory=dict)

    def as_context(self) -> str:
        """Format all gathered data as LLM context."""
        parts = [
            f"## Network Diagnostic Snapshot",
            f"**Issue Type:** {self.issue_type}",
            f"**Target:** {self.target}",
            f"**Namespace:** {self.namespace}",
            "",
        ]
        for label, result in self.results.items():
            parts.append(f"### {label}")
            parts.append(result.as_context())
            parts.append("")
        return "\n".join(parts)


class NetworkDiagnostics:
    """
    Pre-built diagnostic routines for Kubernetes networking issues.

    Covers: Services, Endpoints, Ingress, DNS resolution,
    NetworkPolicies, and pod-to-pod connectivity.
    """

    def __init__(self, kubectl: KubectlExecutor):
        self.kubectl = kubectl

    def diagnose_service(
        self, name: str, namespace: str = "default"
    ) -> NetworkSnapshot:
        """
        Diagnose a Kubernetes Service connectivity issue.

        Checks: service spec, endpoints, backing pods, and selectors.
        """
        snapshot = NetworkSnapshot(
            issue_type="service",
            target=name,
            namespace=namespace,
        )

        # Service description
        snapshot.results["Service Description"] = self.kubectl.execute(
            f"describe service {name}", namespace=namespace
        )

        # Service YAML for detailed spec
        snapshot.results["Service Spec"] = self.kubectl.execute(
            f"get service {name} -o yaml", namespace=namespace
        )

        # Endpoints (critical for debugging)
        snapshot.results["Endpoints"] = self.kubectl.execute(
            f"get endpoints {name} -o yaml", namespace=namespace
        )

        # Get the service selector and find matching pods
        selector_result = self.kubectl.execute(
            f"get service {name} -o jsonpath={{.spec.selector}}",
            namespace=namespace,
        )
        if selector_result.success and selector_result.stdout.strip():
            # Parse selector and find pods
            snapshot.results["Matching Pods"] = self.kubectl.execute(
                f"get pods -o wide --show-labels", namespace=namespace
            )

        # EndpointSlices (newer API)
        snapshot.results["EndpointSlices"] = self.kubectl.execute(
            f"get endpointslices -l kubernetes.io/service-name={name} -o yaml",
            namespace=namespace,
        )

        # Events related to the service
        snapshot.results["Service Events"] = self.kubectl.execute(
            f"get events --field-selector involvedObject.name={name} "
            f"--sort-by=.lastTimestamp",
            namespace=namespace,
        )

        logger.info(f"Network snapshot gathered for service/{name} in {namespace}")
        return snapshot

    def diagnose_ingress(
        self, name: str, namespace: str = "default"
    ) -> NetworkSnapshot:
        """
        Diagnose an Ingress resource issue.

        Checks: ingress spec, backend services, TLS configuration,
        and ingress controller status.
        """
        snapshot = NetworkSnapshot(
            issue_type="ingress",
            target=name,
            namespace=namespace,
        )

        # Ingress description
        snapshot.results["Ingress Description"] = self.kubectl.execute(
            f"describe ingress {name}", namespace=namespace
        )

        # Ingress YAML
        snapshot.results["Ingress Spec"] = self.kubectl.execute(
            f"get ingress {name} -o yaml", namespace=namespace
        )

        # Ingress class
        snapshot.results["IngressClasses"] = self.kubectl.execute(
            "get ingressclass -o wide"
        )

        # Backend services referenced by this ingress
        snapshot.results["All Services in Namespace"] = self.kubectl.execute(
            "get services -o wide", namespace=namespace
        )

        # Ingress controller pods
        snapshot.results["Ingress Controller Pods"] = self.kubectl.execute(
            "get pods --all-namespaces -l "
            "app.kubernetes.io/component=controller -o wide"
        )

        # Events
        snapshot.results["Ingress Events"] = self.kubectl.execute(
            f"get events --field-selector involvedObject.name={name} "
            f"--sort-by=.lastTimestamp",
            namespace=namespace,
        )

        logger.info(f"Network snapshot gathered for ingress/{name} in {namespace}")
        return snapshot

    def diagnose_dns(
        self,
        target_dns: str,
        source_pod: Optional[str] = None,
        namespace: str = "default",
    ) -> NetworkSnapshot:
        """
        Diagnose DNS resolution issues from within the cluster.

        Checks: CoreDNS status, DNS resolution from a pod, DNS configuration.
        """
        snapshot = NetworkSnapshot(
            issue_type="dns",
            target=target_dns,
            namespace=namespace,
        )

        # CoreDNS pods status
        snapshot.results["CoreDNS Pods"] = self.kubectl.execute(
            "get pods -n kube-system -l k8s-app=kube-dns -o wide"
        )

        # CoreDNS service
        snapshot.results["DNS Service"] = self.kubectl.execute(
            "get service kube-dns -n kube-system -o yaml"
        )

        # CoreDNS configmap
        snapshot.results["CoreDNS ConfigMap"] = self.kubectl.execute(
            "get configmap coredns -n kube-system -o yaml"
        )

        # CoreDNS logs (last 50 lines)
        snapshot.results["CoreDNS Logs"] = self.kubectl.execute(
            "logs -n kube-system -l k8s-app=kube-dns --tail=50 "
            "--all-containers=true"
        )

        # If we have a source pod, test DNS resolution from it
        if source_pod:
            snapshot.results["DNS Lookup from Pod"] = self.kubectl.execute(
                f"exec {source_pod} -- nslookup {target_dns}",
                namespace=namespace,
            )
            snapshot.results["Resolv.conf from Pod"] = self.kubectl.execute(
                f"exec {source_pod} -- cat /etc/resolv.conf",
                namespace=namespace,
            )

        # DNS endpoints
        snapshot.results["DNS Endpoints"] = self.kubectl.execute(
            "get endpoints kube-dns -n kube-system -o yaml"
        )

        logger.info(f"DNS diagnostic snapshot gathered for target={target_dns}")
        return snapshot

    def diagnose_network_policy(
        self,
        pod_name: str,
        namespace: str = "default",
    ) -> NetworkSnapshot:
        """
        Diagnose NetworkPolicy issues affecting a specific pod.

        Checks: all NetworkPolicies in the namespace, pod labels,
        and policy selectors.
        """
        snapshot = NetworkSnapshot(
            issue_type="network-policy",
            target=pod_name,
            namespace=namespace,
        )

        # All NetworkPolicies in the namespace
        snapshot.results["NetworkPolicies"] = self.kubectl.execute(
            "get networkpolicy -o yaml", namespace=namespace
        )

        # Pod labels (to match against policies)
        snapshot.results["Pod Labels"] = self.kubectl.execute(
            f"get pod {pod_name} -o jsonpath={{.metadata.labels}}",
            namespace=namespace,
        )

        # Pod spec (for namespace selectors)
        snapshot.results["Pod Details"] = self.kubectl.execute(
            f"get pod {pod_name} -o yaml", namespace=namespace
        )

        # All pods in namespace with labels (for peer matching)
        snapshot.results["All Pods with Labels"] = self.kubectl.execute(
            "get pods --show-labels -o wide", namespace=namespace
        )

        # Namespace labels (for namespace selectors in policies)
        snapshot.results["Namespace Labels"] = self.kubectl.execute(
            f"get namespace {namespace} -o yaml"
        )

        logger.info(
            f"NetworkPolicy diagnostic gathered for pod/{pod_name} in {namespace}"
        )
        return snapshot

    def trace_connectivity(
        self,
        source_pod: str,
        target: str,
        port: Optional[int] = None,
        namespace: str = "default",
        target_namespace: Optional[str] = None,
    ) -> NetworkSnapshot:
        """
        Trace network connectivity between a source pod and a target.

        Performs a systematic check of the network path including:
        DNS, endpoints, network policies, and direct connectivity tests.

        Args:
            source_pod: Name of the source pod.
            target: Target (can be a service name, pod name, or IP).
            port: Target port to test.
            namespace: Namespace of the source pod.
            target_namespace: Namespace of the target (if different).
        """
        target_ns = target_namespace or namespace
        snapshot = NetworkSnapshot(
            issue_type="connectivity-trace",
            target=f"{source_pod} -> {target}:{port or 'any'}",
            namespace=namespace,
        )

        # Source pod status
        snapshot.results["Source Pod Status"] = self.kubectl.execute(
            f"get pod {source_pod} -o wide", namespace=namespace
        )

        # Source pod network info
        snapshot.results["Source Pod IP"] = self.kubectl.execute(
            f"get pod {source_pod} -o jsonpath={{.status.podIP}}",
            namespace=namespace,
        )

        # DNS resolution test from source pod
        snapshot.results["DNS Resolution"] = self.kubectl.execute(
            f"exec {source_pod} -- nslookup {target}",
            namespace=namespace,
        )

        # Connectivity test (if port specified)
        if port:
            # Try wget with timeout
            snapshot.results["Connectivity Test (wget)"] = self.kubectl.execute(
                f"exec {source_pod} -- wget -q --timeout=5 --spider "
                f"http://{target}:{port}/",
                namespace=namespace,
            )

            # Try nc (netcat) as fallback
            snapshot.results["Connectivity Test (nc)"] = self.kubectl.execute(
                f"exec {source_pod} -- nc -zv -w 5 {target} {port}",
                namespace=namespace,
            )

        # Check if target is a service
        snapshot.results["Target Service"] = self.kubectl.execute(
            f"get service {target} -o wide", namespace=target_ns
        )

        # Target endpoints
        snapshot.results["Target Endpoints"] = self.kubectl.execute(
            f"get endpoints {target}", namespace=target_ns
        )

        # NetworkPolicies affecting source
        snapshot.results["Source NetworkPolicies"] = self.kubectl.execute(
            "get networkpolicy -o yaml", namespace=namespace
        )

        # NetworkPolicies affecting target
        if target_ns != namespace:
            snapshot.results["Target NetworkPolicies"] = self.kubectl.execute(
                "get networkpolicy -o yaml", namespace=target_ns
            )

        # Check for any deny-all policies
        snapshot.results["All NetworkPolicies (source ns)"] = self.kubectl.execute(
            "get networkpolicy", namespace=namespace
        )

        logger.info(
            f"Connectivity trace gathered: {source_pod} -> {target}:{port} "
            f"({namespace} -> {target_ns})"
        )
        return snapshot

    def diagnose_cluster_networking(self) -> NetworkSnapshot:
        """
        Perform a broad cluster networking health check.

        Checks: CNI status, kube-proxy, CoreDNS, and node networking.
        """
        snapshot = NetworkSnapshot(
            issue_type="cluster-networking",
            target="cluster-wide",
            namespace="kube-system",
        )

        # CNI pods (common CNI plugins)
        for cni in ["calico", "cilium", "flannel", "weave"]:
            result = self.kubectl.execute(
                f"get pods -n kube-system -l app={cni} -o wide"
            )
            if result.success and result.stdout.strip() and "No resources" not in result.stdout:
                snapshot.results[f"CNI Pods ({cni})"] = result
                break

        # kube-proxy
        snapshot.results["kube-proxy Pods"] = self.kubectl.execute(
            "get pods -n kube-system -l k8s-app=kube-proxy -o wide"
        )

        # CoreDNS
        snapshot.results["CoreDNS Pods"] = self.kubectl.execute(
            "get pods -n kube-system -l k8s-app=kube-dns -o wide"
        )

        # Node network status
        snapshot.results["Nodes"] = self.kubectl.execute(
            "get nodes -o wide"
        )

        # Services in kube-system
        snapshot.results["System Services"] = self.kubectl.execute(
            "get services -n kube-system -o wide"
        )

        # Cluster CIDR info
        snapshot.results["Cluster Info"] = self.kubectl.execute(
            "cluster-info"
        )

        logger.info("Cluster networking diagnostic gathered")
        return snapshot
