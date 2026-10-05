     1	# AI2 Reasoning Challenge (ARC)
     2	
     3	[ARC](https://arxiv.org/pdf/1803.05457) is a benchmark using natural science questions to evaluate a model's knowledge and reasoning capabilities. The dataset ships with `Easy` and `Challenge` sets.
     4	
     5	<!-- Contributors: Automatically Generated -->
     6	
     7	Contributed by [@jjallaire](https://github.com/jjallaire)
     8	
     9	<!-- /Contributors: Automatically Generated -->
    10	
    11	<!-- Usage: Automatically Generated -->
    12	
    13	## Usage
    14	
    15	### Installation
    16	
    17	Install with `pip install inspect-evals`, or `uv sync` from a checkout of this repository.
    18	
    19	### Running evaluations
    20	
    21	```bash
    22	uv run inspect eval inspect_evals/arc_easy --model openai/gpt-5-nano
    23	uv run inspect eval inspect_evals/arc_challenge --model openai/gpt-5-nano
    24	```
    25	
    26	To run multiple tasks simultaneously use `inspect eval-set`:
    27	
    28	```bash
    29	uv run inspect eval-set inspect_evals/arc_easy inspect_evals/arc_challenge
    30	```
    31	
    32	You can also import tasks as normal Python objects and run them from python:
    33	
    34	```python
    35	from inspect_ai import eval, eval_set
    36	from inspect_evals.arc import arc_easy, arc_challenge
    37	eval(arc_easy)
    38	eval_set([arc_easy, arc_challenge], log_dir='logs-run-42')
    39	```
    40	
    41	Drop `uv run` if you manage dependencies yourself. Log viewing (`inspect view`) and default-model setup are documented in the [Inspect Evals README](../../../README.md#getting-started).
    42	
    43	<!-- /Usage: Automatically Generated -->
    44	
    45	<!-- Options: Automatically Generated -->
    46	
    47	## Options
    48	
    49	You can control a variety of options from the command line. For example:
    50	
    51	```bash
    52	uv run inspect eval inspect_evals/arc_easy --limit 10 --sample-shuffle
    53	uv run inspect eval inspect_evals/arc_challenge --max-connections 10
    54	uv run inspect eval inspect_evals/arc_easy --temperature 0.5
    55	```
    56	
    57	See `uv run inspect eval --help` for all available options.
    58	
    59	<!-- /Options: Automatically Generated -->
    60	
    61	<!-- Parameters: Automatically Generated -->
    62	
    63	## Parameters
    64	
    65	### `arc_easy`, `arc_challenge`
    66	
    67	No task parameters.
    68	
    69	<!-- /Parameters: Automatically Generated -->
    70	
    71	## Dataset
    72	
    73	The ARC dataset is a dataset of 7,787 genuine grade-school level, multiple-choice science questions. Here is an example prompt (after being further process by Inspect):
    74	
    75	> Answer the following multiple choice question. The entire content of your response should be of the following format: 'ANSWER: $LETTER (without quotes) where LETTER is one of A,B,C,D.
    76	
    77	```text
    78	An astronomer observes that a planet rotates faster after a meteorite impact. Which is the most likely effect of this increase in rotation?
    79	
    80	A) Planetary density will decrease.  
    81	B) Planetary years will become longer.  
    82	C) Planetary days will become shorter.  
    83	D) Planetary gravity will become stronger.  
    84	```
    85	
    86	The model is then tasked to pick the correct choice.
    87	
    88	## Scoring
    89	
    90	A simple accuracy is calculated over the datapoints.
    91	
    92	## Changelog
    93	
    94	### [2-A] - 2026-02-16
    95	
    96	- Migrate version to new scheme. See [#907](https://github.com/UKGovernmentBEIS/inspect_evals/pull/907).
    97	
    98	### [1.0.1] - 2025-12-18
    99	
   100	- Adds backoff policy for functions that connect to huggingface servers.
