# The auditor's image for Kubernetes sandboxes, where images must be pullable rather
# than built in place. Mirrors the generated docker sandbox, minus the audited task's
# pinned packages (those vary per task; the auditor reads them from the staged files).
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir inspect-ai pandas pyarrow pillow
# Fail the image build if the standard log-analysis stack is incomplete.
RUN python -c "import pandas, pyarrow, PIL; from inspect_ai.analysis import samples_df"

WORKDIR /audit
CMD ["sleep", "infinity"]
