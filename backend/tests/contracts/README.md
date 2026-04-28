# Agent <-> platform contract tests

These tests pin the wire contract between the agent container
(`agent/core/agent_core.py`) and the job-execution worker
(`backend/functions/multi_agent/job_execution/`).

## Contract

The agent returns exactly one of:

| status        | required fields | worker behaviour                      |
|---------------|-----------------|---------------------------------------|
| `completed`   | `result`        | persist result, mark job completed    |
| `interrupted` | `question`      | persist question, mark job interrupted, set `requiresAction=true` |
| `error`       | `error`         | persist error, mark job error         |

Anything else (unknown status, malformed JSON, non-object JSON) is
treated as an error. There is no string-matching fallback.

## Running

From the repository root:

```powershell
cd backend
pip install -r requirements.txt
pip install pytest
python -m pytest tests/contracts -v
```

The tests use in-process stubs for boto3 and do not touch AWS. They
also do not require `aws-lambda-powertools` to be installed on the
test runner because the parser under test lives in
`job_execution.agent_client`, which is pure Python.

If `aws-lambda-powertools` is on the test runner (e.g. CI), the
broader `job_execution` package can be imported end-to-end for
integration-style tests; today we scope to the parser for speed.

## When a test fails

A failure means the agent/platform contract has drifted. Decide
whether the change was intentional:

- If yes, update both the agent code and this test in the same PR.
- If no, fix the regression in the agent before merging.
