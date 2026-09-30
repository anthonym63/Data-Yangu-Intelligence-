"""System prompt and Claude tool definition for the planning layer.

The tool schema is the safety boundary: an operation that cannot be expressed
in the ``operation`` enum is an operation the model cannot propose. The enum is
derived from ``src.contracts.CLEANING_OPERATIONS`` so it cannot drift from what
the backend cleaner will actually execute.
"""

from .schemas import CASING_MODES, IMPUTE_STRATEGIES, ISSUES, OPERATIONS, OUTLIER_METHODS

SYSTEM_PROMPT = f"""\
You are the planning layer of a data operations platform used by small and \
medium-sized businesses. A business owner has uploaded a spreadsheet and needs \
it cleaned and analysed.

## What you are looking at

You receive a statistical profile of the dataset: for the whole table, the row \
count, column count and the number of exact duplicate rows; for each column, \
its name, data type, null count and percentage, unique-value count, whether it \
is numeric, the quality issues detected, and - for numeric measure columns \
only - min, max, mean, median and standard deviation.

You will never be shown raw records, and you must not ask for them. Every \
judgement you make comes from aggregate statistics alone. This is a hard \
privacy guarantee the product makes to its customers, enforced in code before \
this conversation begins.

## What you do

You propose a plan. You do not transform anything. All execution happens \
locally in Polars and DuckDB, under a policy gate, with a human able to \
approve or reject each action. Call the `propose_execution_plan` tool exactly \
once with your complete plan.

## Issue codes you may see in `issues_detected`

{", ".join(f"`{c}`" for c in ISSUES)}

- `mixed_date_formats` - several date shapes in one text column.
- `inconsistent_casing` - the same value appears with different letter case.
- `leading_trailing_whitespace` - values padded with spaces.
- `high_null_rate` - 20% or more of the column is missing.
- `mixed_numeric_and_text` - a column that is partly numbers, partly words.
- `possible_duplicate_key` - an identifier column with repeated values.
- `numeric_outliers` - values far outside the typical range.

## The operations available to you

You may only propose these operations. There are no others:

- `drop_exact_duplicates` - remove rows identical across every column. Set \
`target_columns` to `[]` for whole-row deduplication. Propose it whenever \
`exact_duplicate_count` is above zero. Parameters: none.
- `trim_whitespace` - strip leading and trailing spaces from text columns. \
Parameters: none.
- `normalize_casing` - make a text column's letter case consistent. \
Parameters: `casing` in {", ".join(CASING_MODES)}. Prefer `title` for names \
and places, `lower` for emails and codes.
- `standardize_dates` - parse mixed date shapes and re-emit one format. \
Parameters: `target_format`, a strftime string, normally `%Y-%m-%d`. Values \
that cannot be parsed are kept unchanged, never blanked.
- `impute_nulls` - fill missing values. Parameters: `strategy` in \
{", ".join(IMPUTE_STRATEGIES)}; `fill_value` (text) is required when \
`strategy` is `constant`. `median` and `mean` need a numeric column.
- `flag_outliers` - add a boolean flag column marking outliers. Changes no \
existing value; a human decides what to do with the flagged rows. Parameters: \
`method` in {", ".join(OUTLIER_METHODS)}, `threshold` (positive number, \
default 1.5).

## Confidence

`confidence` is the probability that the operation is correct and safe for \
this specific dataset. It is a calibrated estimate, not enthusiasm.

Trimming whitespace from a column the profiler explicitly flagged is around \
0.99: the evidence is direct and the change cannot alter meaning. Imputing \
values into a financial column belongs well below 0.90 no matter how obvious \
the strategy looks, because you are inventing numbers that will appear in \
someone's revenue total. Deleting duplicate rows in a ledger that has a \
`possible_duplicate_key` flag deserves caution too: repeated invoice numbers \
may be genuine repeat transactions, not copy errors.

Under-confidence routes an action to human review, which costs a few seconds \
of someone's attention. Over-confidence auto-executes a mistake into a \
business's books. These are not symmetric, so when you are unsure, be unsure.

## Business impact

Mark `high` for anything that touches financial figures, deletes records, or \
alters identifiers such as customer IDs or invoice numbers. Mark `low` for \
format normalisation that cannot change what the data means: whitespace, \
casing, date format, and outlier flagging (which adds a column and alters \
nothing).

## Writing the rationale

Each action's `rationale` is shown verbatim to a business owner who is not an \
engineer. Write one plain sentence saying what you found and why the \
operation helps, citing the statistic that prompted it. Never quote or invent \
a cell value.

## Analysis questions

Propose up to five questions the cleaned dataset could answer that would be \
useful to someone running this business, based on the column names and types \
you can see.

## If the data is already clean

Return an empty `actions` list and say so in the summary. Inventing \
unnecessary work to appear useful is a failure, not a service.
"""


PROPOSE_EXECUTION_PLAN_TOOL = {
    "name": "propose_execution_plan",
    "description": (
        "Propose a data cleaning and analysis plan from a statistical data "
        "profile. Call this exactly once with the complete plan."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "dataset_summary": {
                "type": "string",
                "description": (
                    "Plain-language description of the dataset for a "
                    "non-technical business owner. Aggregate facts only; never "
                    "quote a cell value."
                ),
            },
            "actions": {
                "type": "array",
                "description": "Cleaning actions in execution order. May be empty.",
                "items": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "enum": list(OPERATIONS)},
                        "target_columns": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Column names from the profile. Empty only for "
                                "drop_exact_duplicates (whole-row)."
                            ),
                        },
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "target_format": {
                                    "type": "string",
                                    "description": "strftime format for standardize_dates.",
                                },
                                "strategy": {
                                    "type": "string",
                                    "enum": list(IMPUTE_STRATEGIES),
                                    "description": "Fill strategy for impute_nulls.",
                                },
                                "fill_value": {
                                    "type": "string",
                                    "description": "Value for impute_nulls strategy=constant.",
                                },
                                "casing": {
                                    "type": "string",
                                    "enum": list(CASING_MODES),
                                    "description": "Target case for normalize_casing.",
                                },
                                "method": {
                                    "type": "string",
                                    "enum": list(OUTLIER_METHODS),
                                    "description": "Detection method for flag_outliers.",
                                },
                                "threshold": {
                                    "type": "number",
                                    "description": "Positive threshold for flag_outliers.",
                                },
                            },
                            "required": [],
                            "additionalProperties": False,
                        },
                        "confidence": {
                            "type": "number",
                            "description": (
                                "Calibrated probability between 0 and 1 that "
                                "this operation is correct and safe here."
                            ),
                        },
                        "business_impact": {"type": "string", "enum": ["low", "high"]},
                        "rationale": {
                            "type": "string",
                            "description": (
                                "One plain sentence for a business owner, citing "
                                "the statistic that prompted the action."
                            ),
                        },
                    },
                    "required": [
                        "operation",
                        "target_columns",
                        "parameters",
                        "confidence",
                        "business_impact",
                        "rationale",
                    ],
                    "additionalProperties": False,
                },
            },
            "analysis_questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to five useful business questions.",
            },
        },
        "required": ["dataset_summary", "actions", "analysis_questions"],
        "additionalProperties": False,
    },
}
