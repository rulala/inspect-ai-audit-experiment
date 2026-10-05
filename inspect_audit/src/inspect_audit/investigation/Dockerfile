FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends librsvg2-bin ca-certificates curl git jq ripgrep poppler-utils graphviz \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir inspect-ai==INSPECT_VERSION pandas pyarrow matplotlib jupyter pyyaml girth==0.8.0
ARG QUARTO_VERSION=1.9.38
RUN arch="$(dpkg --print-architecture)" && curl -fsSL "https://github.com/quarto-dev/quarto-cli/releases/download/v${QUARTO_VERSION}/quarto-${QUARTO_VERSION}-linux-${arch}.deb" -o /tmp/quarto.deb \
    && dpkg -i /tmp/quarto.deb && rm /tmp/quarto.deb
WORKDIR /workspace
CMD ["sleep", "infinity"]
