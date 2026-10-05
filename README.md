# Understanding Inspect AI

Notes explaining what Inspect does, how evaluations work, and what the results can—and cannot—tell you.

**Status:** Documentation only. This repository does not contain a runnable evaluation, and no model testing has been performed for these notes. This is not official Inspect documentation.

## What is Inspect?

Inspect is an open-source Python framework for evaluating AI models and agents. It provides reusable software for running tests, scoring outputs, and examining the results. It is not a chatbot, a single fixed safety test, or a certificate that a model is safe.

Think of it as laboratory equipment: it supports experiments, but someone must still choose what to test and interpret the evidence.

It supports evaluations involving knowledge, reasoning, coding, behaviour, multimodal understanding, and agents that use tools. You can write your own evaluations or use existing ones.

Source: [Inspect introduction](https://inspect.aisi.org.uk/).

## The three main building blocks

An Inspect evaluation is called a **task**. A typical task combines:

| Component | Purpose |
| --- | --- |
| Dataset | Provides test inputs and expected answers or grading guidance. |
| Solver | Specifies how the model attempts the task, from one response to a multi-step interaction. |
| Scorer | Judges the output using answer matching, custom rules, model-based grading, or another defined method. |

These components can be changed independently. For example, you might use the same questions and marking rules to compare two models, or keep the model fixed while changing its instructions.

Meaningful comparisons require attention to the configuration. A score belongs to a particular evaluation setup—not just to a model name.

Source: [Inspect tasks](https://inspect.aisi.org.uk/tasks.html).

## Example: testing an email assistant

Imagine an assistant that summarises emails. Some messages contain text that tries to override the user's request. An illustrative evaluation could use a fake inbox and ask whether the assistant both completes its legitimate task and ignores those misleading instructions.

This is a proposed example, not a test implemented or run in this repository.

Suppose an assistant passed 97 out of 100 such cases. That would mean 97% success on that particular collection of cases under those conditions. It would not establish that the assistant is “97% safe” in general.

## Looking beyond a headline score

Inspect's log viewer helps evaluators inspect individual samples, messages, tool activity, and scoring details. This makes it possible to investigate failures instead of relying only on averages.

Scorers also need checking. An evaluation may be misleading when the marking process misreads an answer or rejects an equivalent response.

After running an evaluation, the viewer can be launched with:

```bash
inspect view
```

Source: [Inspect log viewer](https://inspect.aisi.org.uk/log-viewer.html).

## Testing actions, not just answers

Tool-using agents can take several steps: inspect information, execute a tool, observe the output, and continue working. Inspect supports this kind of evaluation and sandbox integrations for executing untrusted model-generated code.

Providing tools changes what is being measured. Describing how to complete a task is different from actually completing it in a test environment.

Source: [Inspect introduction and agent example](https://inspect.aisi.org.uk/).

Evaluators can also set limits on messages, tokens, execution time, or model cost. Keep those limits consistent when comparing runs, and report them with the results. Cost limits should not be treated as a guarantee against every possible external computing charge.

Source: [Inspect evaluation limits](https://inspect.aisi.org.uk/setting-limits.html).

## Getting started with the software

Install the framework in a suitable Python environment:

```bash
pip install inspect-ai
```

A real evaluation also needs a task and access to the chosen model. Depending on the provider, this can require an additional Python package and credentials. Installation alone does not run a test, and this documentation-only repository does not include one.

The framework is open source, but hosted model calls and computing resources may still cost money.

Source: [Inspect getting started](https://inspect.aisi.org.uk/#getting-started).

## Keeping a future evaluation project safe to share

Do not commit API keys, passwords, or other credentials. A `.gitignore` can exclude local files from new Git commits, but it does not remove files or secrets already tracked in Git history. Review everything before uploading, including files submitted through the GitHub website.

For a future Python evaluation project, sensible starting exclusions include:

```gitignore
.env
.env.*
!.env.example
.venv/
venv/
__pycache__/
*.py[cod]
logs/
*.eval
```

These patterns are a starting point, not a guarantee that every sensitive file will be excluded. Review datasets and model transcripts before sharing them, and add ignore rules for any custom output locations.

Sources: [GitHub: ignoring files](https://docs.github.com/en/get-started/git-basics/ignoring-files) and [GitHub: adding files safely](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository).

## Key takeaway

Inspect helps turn an impression about an AI system into a defined experiment with results that can be examined. The quality and scope of that evidence still depend on the test design, scoring, model configuration, and situations covered.

## Further reading

- [Official Inspect documentation](https://inspect.aisi.org.uk/)
- [Inspect source repository](https://github.com/UKGovernmentBEIS/inspect_ai)
- [Original release announcement supplied in the discussion, dated 10 May 2024](https://www.gov.uk/government/news/ai-safety-institute-releases-new-ai-safety-evaluations-platform)
