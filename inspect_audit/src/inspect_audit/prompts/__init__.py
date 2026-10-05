"""Every prompt in the package, as markdown next to this file.

Prose belongs in prose files. A system prompt is the most-read and most-edited text
in an agent system, and it is reviewed by people who are not reading the code around
it, so it lives in markdown that renders and diffs like prose rather than in a Python
string that has to be escaped, indented and scrolled past.

Each constant is one file. `{name}` placeholders are filled with `str.format`, so a
literal brace in a prompt must be doubled; there are none today.
"""

from pathlib import Path

HERE = Path(__file__).parent


def _read(name: str) -> str:
    return (HERE / name).read_text()


# The sample auditor. Filled with `root` (where the item is staged), `items` (the audit
# items this auditor was granted, rendered from their skills), and the two optional
# sections below, which are empty strings when they do not apply.
AUDIT = _read("audit.md")

# Rendered into the audit prompt only when the benchmark is unpublished. The auditor
# keeps its shell and its internet -- an auditor that cannot check anything invents
# citations -- but it must not hand the item to a third party to do the checking. The
# boundary is what leaves in a request, not whether the network is reachable.
AUDIT_CONFIDENTIAL = _read("audit_confidential.md")

# Rendered into the audit prompt only when the operator sets `notes`: a free-form steer,
# kept separate from the skills so a skill stays general and the steer stays a per-run
# knob.
AUDIT_NOTES = _read("audit_notes.md")

# The outer investigator: one benchmark, its logs, its paper, a container and a budget.
INVESTIGATE = _read("investigate.md")

# The layer-2 synthesis agent, which works over finished audit logs with an operator on
# the other end of an ACP connection. Filled with `root`.
REPORT = _read("report.md")

# The same agent when no logs were staged and it has no tools: a plumbing test.
REPORT_CHAT_ONLY = _read("report_chat_only.md")

__all__ = [
    "AUDIT",
    "AUDIT_CONFIDENTIAL",
    "AUDIT_NOTES",
    "INVESTIGATE",
    "REPORT",
    "REPORT_CHAT_ONLY",
]
