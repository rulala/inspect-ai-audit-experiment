"""The container templates the package writes, as the files they will become.

A Dockerfile is a Dockerfile. Kept as text in a Python module it is escaped, indented
inside a string, and invisible to every tool that understands the format; kept here it
is a file you can lint, build and diff. The code that renders them fills the fields
named below and writes them into a run directory.
"""

from pathlib import Path

HERE = Path(__file__).parent

# The auditor's image. `requirements` is the audited task's own package specs, installed
# so the auditor can read and run the real grading code in place.
DOCKERFILE = (HERE / "auditor.Dockerfile").read_text()

# Our own compose rather than Inspect's auto-generated one, which sets
# `network_mode: none`. An auditor without egress either reports that it cannot verify
# or invents citations, and both have been observed.
#
# `network_mode: bridge` gives egress via the shared docker0 and allocates no
# per-sample network. A named network would take a /24 per sample from Docker's
# ~31-subnet default pool, exhaust it at concurrency, and leak on a hard kill.
# Isolation from the benchmark still holds: the benchmark box is on `network_mode:
# none` (no interface) or its own named network, neither of which docker0 can reach,
# and the auditor never talks to it over the network anyway.
COMPOSE = (HERE / "auditor.compose.yaml").read_text()

# Appended to the Helm values for a k8s run: Cilium egress for the auditor service,
# DNS plus world, so the same "can verify" property holds off Docker.
EGRESS_POLICY = (HERE / "egress.helm.yaml").read_text()

__all__ = ["COMPOSE", "DOCKERFILE", "EGRESS_POLICY"]
