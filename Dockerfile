FROM python:3.11-slim

LABEL maintainer="K8s Agent Team"
LABEL description="Specialized Kubernetes Debugging Agent"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install kubectl
RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && chmod +x kubectl \
    && mv kubectl /usr/local/bin/

# Create non-root user
RUN useradd -m -s /bin/bash agent-user
WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy application code
COPY src/ src/

# Switch to non-root user
USER agent-user

# Expose API port
EXPOSE 8080

# Default command: start the API server
ENTRYPOINT ["python", "-m", "k8s_agent.cli.main"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
