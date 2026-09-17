# Threat Model — Multi-Agent Platform

**Status:** Active
**Methodology:** STRIDE

---

## 1. System Overview

The Multi-Agent Platform is a web application that enables users to register, manage, and execute AI agents powered by Amazon Bedrock Agent Core. Users create jobs (one-time or scheduled), chat with agents directly in standalone conversation threads, view conversation history, and configure ambient signals that trigger agent execution based on S3 file uploads — optionally without human review (`autoExecute`).

### Architecture Summary

| Component   | Technology                                    | Purpose                                                           |
| ----------- | ---------------------------------------------- | ------------------------------------------------------------------ |
| Frontend    | React SPA on S3 + CloudFront                  | User interface                                                    |
| API Layer   | API Gateway (Regional) + Cognito Authorizer   | Request routing and authentication                                 |
| Compute     | 9 Lambda functions (Python 3.13)               | Business logic                                                    |
| Data Store  | 6 DynamoDB tables                              | Agent registry, job registry, conversation store, ambient signals, chat threads, idempotency records |
| AI Backend  | Bedrock Agent Core, Bedrock Foundation Models, Bedrock Guardrails | Agent execution and prompt-injection/content filtering |
| CDN/Routing | CloudFront                                    | Static assets, API proxy, TLS termination                         |
| Auth        | Cognito auth (user pool + identity pool)      | User authentication and federation                                |
| Scheduling  | CloudWatch Events (1-min interval)            | Periodic job execution                                             |
| Signals     | S3 bucket notifications → Lambda              | Event-driven agent triggers, optionally auto-executed              |
| Chat        | API Gateway → Lambda (async) → AgentCore      | Standalone conversational agent access, decoupled from jobs        |

### Trust Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│  TB-1: Internet / Untrusted                                 │
│  ┌───────────────┐                                          │
│  │ Browser/User  │                                          │
│  └──────┬────────┘                                          │
│─────────┼───────────────────────────────────────────────────│
│  TB-2: CloudFront Edge                                      │
│  ┌──────┴────────┐                                          │
│  │  CloudFront   │──── S3 (static assets)                   │
│  └──────┬────────┘                                          │
│─────────┼───────────────────────────────────────────────────│
│  TB-3: AWS Account / VPC                                    │
│  ┌──────┴────────┐     ┌──────────┐     ┌───────────────┐  │
│  │ API Gateway   │────▶│ Lambda   │────▶│  DynamoDB     │  │
│  │ + Cognito     │     │ Functions│     │  Tables       │  │
│  └───────────────┘     └────┬─────┘     └───────────────┘  │
│                             │                               │
│  ┌────────────────────────┐│                                │
│  │ Signal Uploads Bucket  ││  (single, stack-owned)         │
│  │ (S3, stack-owned)      │◀┘                                │
│  └────────────────────────┘                                 │
│─────────────────────────────┼───────────────────────────────│
│  TB-4: External AWS Services (may be cross-region)          │
│                     ┌───────┴──────────┐                    │
│                     │ Bedrock Agent    │                    │
│                     │ Core / Models /  │                    │
│                     │ Guardrails       │                    │
│                     └──────────────────┘                    │
└─────────────────────────────────────────────────────────────┘
```

Note on TB-4: the agent execution role (deployed outside this CDK stack,
via the AgentCore CLI) is a *separate* trust boundary from the platform
Lambdas' roles. It is provisioned and permission-scoped independently
(`agent/policies/*.json`, substituted and attached by
`agent/attach_agent_policies.sh`), and its AgentCore runtime may live in a
different AWS region than the platform stack (see §4.4).

---

## 2. Data Classification

| Data Type             | Classification           | Storage                       | Encryption                      |
| --------------------- | ------------------------ | ------------------------------ | -------------------------------- |
| Cognito credentials   | Sensitive                | Cognito (AWS-managed)          | AWS-managed                      |
| JWT tokens            | Sensitive                | Browser memory                 | TLS in transit                   |
| Agent configurations  | Non-public               | DynamoDB (agent-registry)      | AWS-managed at rest              |
| Job prompts & results | Sensitive                | DynamoDB (job-registry)        | AWS-managed at rest              |
| Conversation history  | Sensitive                | DynamoDB (conversation-store)  | AWS-managed at rest, 30-day TTL  |
| Signal configurations | Non-public               | DynamoDB (ambient-signals)     | AWS-managed at rest              |
| Chat thread metadata  | Non-public               | DynamoDB (chat-threads)        | AWS-managed at rest, 30-day TTL  |
| Idempotency records   | Internal                 | DynamoDB (idempotency-records) | AWS-managed at rest, short TTL   |
| S3 file content       | Varies (user-controlled) | S3 (single stack-owned signal-uploads bucket) | AWS-managed, access-logged |
| Frontend assets       | Public                   | S3 + CloudFront                | S3-managed, TLS in transit       |

---

## 3. STRIDE Threat Analysis

### 3.1 Spoofing (S)

| ID  | Threat                                    | Component   | Risk   | Mitigation                                                                  | Status    |
| --- | ------------------------------------------ | ----------- | ------ | ----------------------------------------------------------------------------- | --------- |
| S-1 | Attacker forges authentication tokens     | API Gateway | High   | Cognito JWT validation via API Gateway authorizer; tokens signed by Cognito | Mitigated |
| S-2 | Attacker reuses expired/stolen JWT        | API Gateway | Medium | Token expiration enforced by Cognito; advanced security mode ENFORCED (Plus feature plan) | Mitigated |
| S-3 | Self-signup allows unauthorized users     | Cognito     | Medium | `self_sign_up_enabled: false` — admin-only user creation                    | Mitigated |
| S-4 | Weak passwords enable credential stuffing | Cognito     | Medium | Password policy: 12+ chars, upper/lower/digit/symbol required               | Mitigated |
| S-5 | Account takeover via credential compromise (no second factor) | Cognito | Medium | TOTP MFA available (`Mfa.OPTIONAL`); not enforced — see AR-7 | Partially mitigated |
| S-6 | Malicious user forges an "ai" turn into their own conversation history via `POST /conversations/{sessionId}` | conversation_management.py | Low | Endpoint rejects any `type` other than `"human"`; `ai` turns can only be written by the job_execution/chat_execution Lambdas via their own DynamoDB writes | Mitigated |

### 3.2 Tampering (T)

| ID  | Threat                                               | Component           | Risk   | Mitigation                                                                                                             | Status                                      |
| --- | ------------------------------------------------------ | -------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| T-1 | Attacker modifies API requests in transit            | CloudFront → API GW | High   | HTTPS-only (CloudFront redirect, API GW HTTPS origin)                                                                  | Mitigated                                    |
| T-2 | Attacker modifies another user's agent/job/signal/chat thread | Lambda functions    | High   | All mutations verify `userId` from JWT matches resource owner. Signal/chat-thread creation also verifies ownership of the *referenced agent*, not just its existence | Mitigated |
| T-3 | DynamoDB UpdateExpression injection                  | job_execution.py    | High   | Field allowlist + `ExpressionAttributeNames` parameterization                                                          | Mitigated                                    |
| T-4 | Prompt injection via agent input (uploaded file content, signal name/description, chat message) | Bedrock Agent Core, signal_processor.py, agent_core.py, s3_reader.py | High | Bedrock Guardrail with a PROMPT_ATTACK content filter applied to every model invocation; all untrusted input (signal metadata, S3 trigger payload, file content) is wrapped in explicit `<untrusted_*>` tags with an accompanying system-prompt instruction to treat tagged content as data, never as instructions; bounded `max_iterations` (10, i.e. `recursion_limit=20`) and loop detection; a per-session `CircuitBreaker` caps identical back-to-back queries | Mitigated (defense-in-depth; residual risk accepted, see AR-3) |
| T-5 | Malicious S3 file triggers unintended *unreviewed* agent behavior via `autoExecute` | Signal processor, signal_management.py | High | `autoExecute` can only be set on a signal whose `bucketName` is the single platform-managed signal-uploads bucket (enforced server-side, not just IAM); auto-fired jobs are rate-limited per signal (`AUTO_EXECUTE_COOLDOWN_SECONDS`, default 60s) to bound denial-of-wallet exposure from a burst of uploads | Mitigated |
| T-6 | Signal management/agent-run-time IAM roles modify or read from arbitrary S3 buckets | signal_management.py, signal_processor.py, agent/policies/s3_access.json | High | Signal-management and signal-processor Lambda roles are scoped to the single stack-owned signal-uploads bucket ARN, not `arn:aws:s3:::*`. Agent execution role's S3 policy is scoped to `${AGENT_S3_BUCKET_NAME}` (substituted at attach time) instead of the account wildcard, and carries no write permissions (the shipped tool is read-only). The `s3_reader` tool additionally enforces its own bucket allowlist in code as defense-in-depth even if the IAM policy were ever re-widened. | Mitigated |
| T-7 | Agent-registration ARN validation bypass | agent_management.py | Medium | ARN is validated with a full regex anchored to the AgentCore runtime ARN shape (`arn:aws[partition-suffix]:bedrock-agentcore:<region>:<12-digit-account>:runtime/<name>`) | Mitigated |

### 3.3 Repudiation (R)

| ID  | Threat                             | Component            | Risk | Mitigation                                                                                         | Status    |
| --- | ------------------------------------ | ---------------------- | ---- | ------------------------------------------------------------------------------------------------- | --------- |
| R-1 | User denies performing an action   | All Lambda functions | Low  | CloudWatch Logs with user ID; API Gateway access logs with caller identity; CloudFront access logs | Mitigated |
| R-2 | Agent actions cannot be attributed | Job execution, chat execution | Low  | Execution logs stored per-job in DynamoDB with timestamps; conversation history preserved (jobs and chat threads share the conversation-store table) | Mitigated |
| R-3 | CloudWatch log groups retain data indefinitely, growing cost/exposure unbounded | All 9 Lambda functions | Low | `log_retention` set to 2 weeks on every function | Mitigated |

### 3.4 Information Disclosure (I)

| ID  | Threat                                                  | Component                  | Risk     | Mitigation                                                                                                 | Status                                                |
| --- | ---------------------------------------------------------- | ----------------------------- | -------- | ------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------- |
| I-1 | Cross-user conversation data leakage                    | conversation_management.py | Critical | `verify_user_access_to_session()` validates session ownership via a direct get_item (GSI-backed, not a scan — see D-3); fail-closed on error; GET endpoints return 404 (not 403) for a session that exists but isn't owned by the caller, removing a session-id existence oracle | Mitigated |
| I-2 | Agent response contains sensitive data from other users | Bedrock Agent Core         | Medium   | Agents are stateless per-invocation; session IDs are user-scoped; conversation context loaded per-session  | Mitigated                                             |
| I-3 | Error messages leak internal details                    | All Lambda functions       | Low      | Generic error messages returned to client; detailed errors logged to CloudWatch only                       | Mitigated                                             |
| I-4 | S3 bucket contents exposed via signal processor         | Signal processor           | Medium | Signal processor Lambda's S3 read grant is scoped to the single stack-owned signal-uploads bucket, not every bucket a user could name (see T-6) | Mitigated |
| I-5 | CloudFront serves cached API responses to wrong user    | CloudFront                 | Medium   | API behavior uses `CACHING_DISABLED` policy; Lambda responses include `Cache-Control: no-store`            | Mitigated                                             |
| I-6 | Full inbound S3 event (bucket/key = per-user upload activity) logged verbatim at INFO on every signal invocation | signal_processor.py | Low | Handler logs only record count and event key shape, not the event body | Mitigated |
| I-7 | CloudWatch Logs policy on the agent execution role (`arn:aws:logs:*:*:*`) is broader than necessary | agent/policies/cloudwatch_logs.json | Low | Scoped to `arn:aws:logs:*:${AWS_ACCOUNT_ID}:*` (account-scoped; region left wildcard since AgentCore runtimes are region-flexible by design) | Mitigated |

### 3.5 Denial of Service (D)

| ID  | Threat                                   | Component                  | Risk   | Mitigation                                                                                          | Status                                                     |
| --- | ------------------------------------------- | ------------------------------- | ------ | ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| D-1 | API request flooding                     | API Gateway                | Medium | Per-stage usage plan with rate/burst/quota limits (50 rps / 100 burst / 100k per day) added to the `prod` stage; optional AWS WAFv2 web ACL (managed rule groups + per-IP rate limiting + optional IP allowlist) gated behind `enable_waf: true`, wired from `config.yml` into the stack | Mitigated |
| D-2 | Expensive Bedrock model invocations      | Job execution, chat execution, signal auto-execute | Medium | Bedrock model scoped to Claude models only (foundation models + inference profiles prefixed `us.anthropic.claude-*`); agent `max_iterations: 10` → `recursion_limit: 20` bounds per-invocation cost; `autoExecute` firings are rate-limited per signal (see T-5); a per-session circuit breaker blocks identical back-to-back queries within a cooldown window | Mitigated |
| D-3 | Unbounded DynamoDB scans                 | conversation_management.py | Low    | `verify_user_access_to_session` uses a direct `get_item` against the conversation-store record (which already carries `userId`), not a scan; conversation table has 30-day TTL | Mitigated |
| D-4 | Scheduler creates runaway job executions | Scheduler Lambda           | Low    | Scheduler checks job status before re-triggering; jobs transition to terminal states                  | Mitigated                                                       |
| D-5 | Large conversation payloads              | Conversation store, chat_management.py | Low    | Pagination (default limit 50); DynamoDB 400KB item limit provides natural bound; chat messages are capped at 8000 characters before being accepted | Mitigated |
| D-6 | Burst of S3 uploads to the shared signal-uploads bucket drives repeated auto-executed Bedrock invocations | signal_processor.py | Medium | Per-signal auto-execute cooldown (default 60s); jobs that hit the cooldown are still created, just left `idle` for manual review rather than silently dropped (see T-5) | Mitigated |

### 3.6 Elevation of Privilege (E)

| ID  | Threat                                                 | Component                  | Risk   | Mitigation                                                                                                               | Status                                              |
| --- | ---------------------------------------------------------- | -------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| E-1 | Compromised Lambda accesses unrelated AWS resources    | Lambda IAM roles           | High   | Per-function IAM roles with least-privilege; DynamoDB grants scoped to specific tables; Bedrock scoped to account/region; S3 signal grants scoped to the single stack-owned bucket | Mitigated |
| E-2 | Agent tool executes arbitrary code                     | Calculator tool            | High   | `eval()` replaced with `simpleeval` library (sandboxed AST evaluation); no shell/exec tools                              | Mitigated                                           |
| E-3 | Signal management attaches a signal to an arbitrary S3 bucket owned by another AWS customer/application in the account | signal_management.py       | Critical | Signal bucket is validated server-side against a single platform-managed allowlisted bucket; the underlying IAM grants are scoped to that bucket's ARN, not `arn:aws:s3:::*` | Mitigated |
| E-4 | Cognito Identity Pool grants excessive AWS permissions | Cognito authenticated role | Low    | Authenticated role has no inline policies beyond federation                                                              | Mitigated                                           |
| E-5 | Signal/chat-thread creation lets a caller attach to another user's registered agent | signal_management.py, chat_management.py | Medium | Both endpoints verify the referenced agent's `userId` matches the caller, not just that the agent exists, via `authz.is_owner()` | Mitigated |
| E-6 | Identity-extraction failure fails open onto a shared `"unknown"` user id, letting unrelated callers pass each other's ownership checks | signal_management.py | High | `get_user_id_from_event` raises instead of returning `"unknown"` on missing claims, matching the fail-closed pattern used by every other handler in this platform | Mitigated |
| E-7 | Agent runtime role granted `s3:PutObject`/`s3:DeleteObject` on every bucket in the account despite shipping only a read-only tool | agent/policies/s3_access.json | High | No write statement is granted; `S3ReadAccess` is scoped to a single bucket (see T-6) | Mitigated |

---

## 4. Data Flow Threats

### 4.1 User → CloudFront → API Gateway → Lambda

| Step                     | Threat              | Mitigation                                          |
| -------------------------- | ---------------------- | ------------------------------------------------------ |
| Browser → CloudFront     | MitM, eavesdropping | TLS 1.2+ enforced, HTTPS redirect                   |
| CloudFront → API Gateway | Request tampering   | HTTPS-only origin protocol                          |
| API Gateway → Lambda     | Unauthorized access | Cognito authorizer validates JWT on every request; per-stage usage plan bounds request volume (see D-1) |
| Lambda → DynamoDB        | Data tampering      | IAM role-based access; no public DynamoDB endpoints |

### 4.2 Lambda → Bedrock Agent Core (jobs and chat)

| Step               | Threat                   | Mitigation                                                         |
| --------------------- | --------------------------- | ---------------------------------------------------------------------- |
| Lambda → Bedrock   | Prompt injection         | Bounded iterations, loop detection, per-session circuit breaker, Bedrock Guardrail PROMPT_ATTACK filter, and explicit untrusted-content tagging (see T-4) |
| Bedrock → Lambda   | Malicious response       | Response parsed as JSON against a fixed `{status, result|question|error}` contract; no code execution from response |
| Agent → S3 (tools) | Unauthorized file access | Agent S3 policy scoped to a single bucket (`${AGENT_S3_BUCKET_NAME}`), with an additional in-code allowlist check in `s3_reader.py` as defense-in-depth |

### 4.3 S3 → Signal Processor → Job Execution (ambient signals, including `autoExecute`)

| Step                            | Threat                    | Mitigation                                                                      |
| ---------------------------------- | ------------------------------ | -------------------------------------------------------------------------------------- |
| S3 notification → Lambda        | Spoofed S3 event          | Lambda permission scoped to S3 service principal with source ARN and account ID; source bucket is the single stack-owned signal-uploads bucket |
| Signal processor → Job creation | Unauthorized job creation | Signal processor validates signal exists and is enabled before creating job; signal creation itself requires ownership of the target agent |
| Signal processor → immediate execution (`autoExecute: true`) | Unreviewed agent execution on attacker-influenced input | Restricted to the platform-owned bucket only (see T-5); rate-limited per signal (see D-6); prompt sent to the agent wraps all signal/file metadata in `<untrusted_signal_metadata>` tags (see T-4) |

### 4.4 Chat threads (standalone conversational agent access)

| Step                                   | Threat                                          | Mitigation |
| ----------------------------------------- | ---------------------------------------------------- | -------------- |
| `POST /chats` (create thread)          | Attaching a chat thread to another user's agent | Ownership of the referenced agent is verified, not just existence (see E-5) |
| `POST /chats/{threadId}/messages`      | Message flooding / oversized messages driving unmetered Bedrock cost | 8000-character cap per message (see D-5); thread must be `idle` (not `busy`) to accept a new message, preventing turn interleaving |
| chat_management → chat_execution (async invoke) | Forged "ai" turns injected by a client calling the conversation API directly | `conversation_management.py`'s `POST /conversations/{sessionId}` rejects any `type` other than `"human"` — only `chat_execution.py`/`job_execution` (internal Lambda-to-DynamoDB writes, not exposed to the client) can write `"ai"` turns (see S-6) |
| chat_execution → AgentCore              | Cross-region invocation (see §4.5)              | Same client-construction and IAM scoping as job execution |

### 4.5 Cross-region AgentCore invocation

Both `job_execution/clients.py`'s `agentcore_client_for_arn()` and
`chat_execution.py`'s `_agentcore_client_for_arn()` parse the target
region directly out of the registered agent's ARN
(`agent_arn.split(":")[3]`) and construct a region-pinned
`bedrock-agentcore` client, because a registered AgentCore runtime may
live in a different AWS region than this platform stack.

| Step                                        | Threat                                                                 | Mitigation / Status |
| ---------------------------------------------- | --------------------------------------------------------------------------- | ------------------------ |
| Agent registration (`POST /agents`)         | Malformed/malicious `agentArn` accepted, feeding an arbitrary region/service into the boto3 client constructor | ARN is validated against a full regex anchored to the AgentCore runtime ARN shape before storage (see T-7) |
| job_execution/chat_execution → AgentCore (any region) | `bedrock-agentcore:InvokeAgentRuntime` granted with a region wildcard (`arn:aws:bedrock-agentcore:*:<account>:runtime/*`) across both the job-execution and chat-execution Lambda roles | **Accepted risk** — see AR-8. A region wildcard is inherent to supporting agents registered in a region different from the stack's own; the account id itself is still scoped, and the ARN validation above (T-7) prevents the wildcard from being combined with an unvalidated ARN. |

---

## 5. Accepted Risks

| ID   | Risk                                                       | Justification                                                                                                                                                                                                                                                                      | Owner         |
| ---- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------- |
| AR-3 | Prompt injection via crafted S3 file content, signal metadata, or chat messages | Inherent to LLM-based processing and cannot be eliminated outright. Mitigated in depth by: a Bedrock Guardrail PROMPT_ATTACK filter on every model invocation; explicit `<untrusted_*>` tagging of all user/uploader-controlled content plus a system-prompt instruction to treat tagged content as data; bounded iterations (`recursion_limit=20`); loop detection; and a per-session circuit breaker. None of this guarantees injected instructions can never influence model behavior; it raises the bar and adds detection, it does not close the class of risk. | Platform team |
| AR-4 | No WAF configured on CloudFront/API Gateway by default      | WAF (AWS-managed rule groups + per-IP rate limiting + optional IP allowlist) is implemented and wired to `enable_waf: true` in `config.yml`, but remains **off by default** to avoid imposing WAF's ongoing cost on a sample deployment. Enable it for any deployment handling real user traffic. | Platform team |
| AR-6 | Signal processor cross-tenant triggering on a shared bucket | Signals are matched by bucket name (always the single platform-managed bucket) without further per-user scoping in the processor's match filter. Any two users who each create an `s3_file_upload` signal with no prefix filter (or with overlapping prefixes) will both be triggered by the same upload, including an `autoExecute` firing. This is accepted for a sample/educational project on the basis that: (a) all users of a given deployment are admin-provisioned (no self-signup) and implicitly a trusted cohort, and (b) the UI's bucket field is fixed and read-only, but the prefix field is still a free-text field the user controls, so a deployment operator can mitigate this today by having each user adopt a disjoint prefix convention (e.g. `users/{email}/`) — the platform does not currently enforce or assign this. Production deployments should not accept this risk without adding real per-user prefix scoping or a fully bucket-per-user model. | Platform team |
| AR-7 | Cognito MFA is optional (`Mfa.OPTIONAL`), not required     | Enforcing required MFA cannot currently coexist with the admin-only user-provisioning flow (`CfnUserPoolUser`) without also building a mandatory first-login TOTP-enrollment UI, which the sample frontend does not have. TOTP is available and users may opt in; SMS MFA is deliberately not offered (adds an SMS-sending IAM role dependency and does not add meaningful security over TOTP for this admin-provisioned user base). | Platform team |
| AR-8 | `bedrock-agentcore:InvokeAgentRuntime` is granted with a region wildcard (`arn:aws:bedrock-agentcore:*:<account>:runtime/*`) on both the job-execution and chat-execution Lambda roles | Required to support agents registered in a region different from the stack's own — this is a documented platform feature (cross-region AgentCore invocation), not an oversight. The account id is still scoped (not also wildcarded), and the ARN accepted at registration time is validated against the AgentCore runtime ARN shape (see T-7), so the wildcard cannot be paired with an arbitrary/malformed ARN to reach an unintended resource type. | Platform team |
| AR-9 | `inference-profile` grant scoped to `us.anthropic.claude-*` prefix, not per-profile-ID | The "Claude models only" intent (see D-2) is enforced by prefix match on the inference-profile *name*, not by an allowlist of specific profile ARNs, since profile IDs are created dynamically and not known at deploy time. A cross-region inference profile whose name happens to start with `us.anthropic.claude-` but is configured (by an account admin, out of band) to route to a non-Claude model would still be reachable. Considered low-likelihood since profile creation itself requires separate IAM permissions this stack doesn't grant. | Platform team |

---

## 6. Recommendations

| Priority | Recommendation                                                                                         | Threat Addressed | Status          |
| ---------- | ----------------------------------------------------------------------------------------------------- | ------------------- | ------------------ |
| Medium   | Implement per-user Bedrock invocation quotas to prevent cost abuse                                     | D-2              | Open            |
| High     | Add real per-user scoping (prefix assignment or bucket-per-user) for ambient signals sharing the platform bucket | AR-6             | Open            |
| Low      | Add CloudTrail logging for Bedrock API calls                                                           | R-1              | Open            |
| Low      | Rotate Cognito user pool client secret periodically                                                    | S-2              | N/A — app client has `generate_secret=False` (public client, SRP-only) |
| Medium   | Require MFA (not just offer it) once the frontend supports mandatory first-login enrollment            | S-5, AR-7        | Open            |
| Medium   | Add per-user Bedrock invocation rate limiting distinct from the per-signal autoExecute cooldown        | D-2, D-6         | Open            |

---

## 7. Architecture Diagram Reference

Components and their interactions:

```
[Browser] ──HTTPS──▶ [CloudFront] ──optional──▶ [WAFv2 Web ACL] (enable_waf: true)
                        ├── /assets/* ──▶ [S3 Website Bucket]
                        └── /prod/*  ──▶ [API Gateway + Cognito Auth + Usage Plan]
                                            ├── /agents/*        ──▶ [agent-management λ]     ──▶ [agent-registry DDB]
                                            ├── /jobs/*          ──▶ [job-management λ]       ──▶ [job-registry DDB]
                                            ├── /jobs/*/execute  ──▶ [job-execution λ]        ──▶ [Bedrock Agent Core] (region from ARN)
                                            │                                                  ──▶ [conversation-store DDB]
                                            ├── /conversations/* ──▶ [conversation-mgmt λ]    ──▶ [conversation-store DDB]
                                            ├── /signals/*       ──▶ [signal-management λ]    ──▶ [ambient-signals DDB]
                                            └── /chats/*         ──▶ [chat-management λ] ──async──▶ [chat-execution λ] ──▶ [Bedrock Agent Core]
                                                                                                                       ──▶ [conversation-store DDB]
                                                                                          [chat-threads DDB]

[CloudWatch Events] ──1min──▶ [scheduler λ] ──▶ [job-execution λ]

[Signal Uploads Bucket (single, stack-owned)] ──notification──▶ [signal-processor λ] ──▶ [job-registry DDB]
                                                                          │
                                                                          └──autoExecute (rate-limited)──▶ [job-execution queue] ──▶ [job-execution λ]

[Agent Runtime (AgentCore, deployed separately via the AgentCore CLI, any region)]
   ── model calls ──▶ [Bedrock Guardrail: PROMPT_ATTACK + content filters] ──▶ [Bedrock Foundation Model]
   ── read-only ──▶ [Signal Uploads Bucket, scoped to ${AGENT_S3_BUCKET_NAME}]
```
