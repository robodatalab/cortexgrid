FROM ghcr.io/actions/actions-runner:2.337.0

USER root

ARG YQ_VERSION=v4.45.1
ARG KUBECTL_VERSION=v1.31.0
ARG GH_VERSION=2.62.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends jq unzip \
 && curl -fsSL https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64 -o /usr/local/bin/yq \
 && chmod +x /usr/local/bin/yq \
 && curl -fsSL https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl -o /usr/local/bin/kubectl \
 && chmod +x /usr/local/bin/kubectl \
 && curl -fsSL https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz -o /tmp/gh.tgz \
 && tar -xzf /tmp/gh.tgz -C /tmp \
 && mv /tmp/gh_${GH_VERSION}_linux_amd64/bin/gh /usr/local/bin/gh \
 && curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip \
 && unzip -q /tmp/awscliv2.zip -d /tmp \
 && /tmp/aws/install \
 && rm -rf /tmp/awscliv2.zip /tmp/aws /tmp/gh.tgz /tmp/gh_${GH_VERSION}_linux_amd64 \
 && rm -rf /var/lib/apt/lists/*

USER runner

# trigger: 315-trigger-tests-2026-05-16
