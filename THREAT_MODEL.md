# Threat Model — Multi-Agent Platform

**Document Version:** 1.0
**Date:** 2026-03-26
**Status:** Active
**Methodology:** STRIDE

---

## 1. System Overview

The Multi-Agent Platform is a web application that enables users to register, manage, and execute AI agents powered by Amazon Bedrock Agent Core. Users create jobs (one-time or scheduled), view conversation history, and configure ambient signals that trigger agent execution based on S3 file uploads.

### Architecture Summary

| Component   | Technology                                    | Purpose                                                           |
| ----------- | --------------------------------------------- | ----------------------------------------------------------------- |
| Frontend    | React SPA on S3 + CloudFront                  | User interface                                                    |
| API Layer   | API Gateway (Regional) + Cognito Authorizer   | Request routing and authentication                                |
| Compute     | 7 Lambda functions (Python 3.13)              | Business logic                                                    |
| Data Store  | 4 DynamoDB tables                             | Agent registry, job registry, conversation store, ambient signals |
| AI Backend  | Bedrock Agent Core, Bedrock Foundation Models | Agent execution                                                   |
| CDN/Routing | CloudFront                                    | Static assets, API proxy, TLS termination                         |
| Auth        | Cognito auth (user pool + identity pool)      | User authentication and federation                                |
| Scheduling  | CloudWatch Events (1-min interval)            | Periodic job execution                                            |
| Signals     | S3 bucket notifications → Lambda              | Event-driven agent triggers                                       |

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
│─────────────────────────────┼───────────────────────────────│
│  TB-4: External AWS Services                                │
│                     ┌───────┴──────────┐                    │
│                     │ Bedrock Agent    │                    │
│                     │ Core / Models    │                    │
│                     └──────────────────┘                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Data Classification

| Data Type             | Classification           | Storage                       | Encryption                      |
| --------------------- | ------------------------ | ----------------------------- | ------------------------------- |
| Cognito credentials   | Sensitive                | Cognito (AWS-managed)         | AWS-managed                     |
| JWT tokens            | Sensitive                | Browser memory                | TLS in transit                  |
| Agent configurations  | Non-public               | DynamoDB (agent-registry)     | AWS-managed at rest             |
| Job prompts & results | Sensitive                | DynamoDB (job-registry)       | AWS-managed at rest             |
| Conversation history  | Sensitive                | DynamoDB (conversation-store) | AWS-managed at rest, 30-day TTL |
| Signal configurations | Non-public               | DynamoDB (ambient-signals)    | AWS-managed at rest             |
| S3 file content       | Varies (user-controlled) | S3 (user buckets)             | Depends on bucket config        |
| Frontend assets       | Public                   | S3 + CloudFront               | S3-managed, TLS in transit      |

---

## 3. STRIDE Threat Analysis

### 3.1 Spoofing (S)

| ID  | Threat                                    | Component   | Risk   | Mitigation                                                                  | Status    |
| --- | ----------------------------------------- | ----------- | ------ | --------------------------------------------------------------------------- | --------- |
| S-1 | Attacker forges authentication tokens     | API Gateway | High   | Cognito JWT validation via API Gateway authorizer; tokens signed by Cognito | Mitigated |
| S-2 | Attacker reuses expired/stolen JWT        | API Gateway | Medium | Token expiration enforced by Cognito; advanced security mode ENFORCED       | Mitigated |
| S-3 | Self-signup allows unauthorized users     | Cognito     | Medium | `self_sign_up_enabled: false` — admin-only user creation                    | Mitigated |
| S-4 | Weak passwords enable credential stuffing | Cognito     | Medium | Password policy: 8+ chars, upper/lower/digit/symbol required                | Mitigated |

### 3.2 Tampering (T)

| ID  | Threat                                               | Component           | Risk   | Mitigation                                                                                                             | Status                                      |
| --- | ---------------------------------------------------- | ------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| T-1 | Attacker modifies API requests in transit            | CloudFront → API GW | High   | HTTPS-only (CloudFront redirect, API GW HTTPS origin)                                                                  | Mitigated                                   |
| T-2 | Attacker modifies another user's agent/job           | Lambda functions    | High   | All mutations verify `userId` from JWT matches resource owner                                                          | Mitigated                                   |
| T-3 | DynamoDB UpdateExpression injection                  | job_execution.py    | High   | Field allowlist + `ExpressionAttributeNames` parameterization                                                          | Mitigated                                   |
| T-4 | Prompt injection via agent input                     | Bedrock Agent Core  | Medium | Agent has bounded `max_iterations: 5` and loop detection; tools are read-only (calculator, S3 read) except human_input | Partially mitigated                         |
| T-5 | Malicious S3 file triggers unintended agent behavior | Signal processor    | Medium | Signal processor only passes file metadata to agent; agent reads file content via S3 reader tool                       | Accepted (inherent to LLM-based processing) |

### 3.3 Repudiation (R)

| ID  | Threat                             | Component            | Risk | Mitigation                                                                                         | Status    |
| --- | ---------------------------------- | -------------------- | ---- | -------------------------------------------------------------------------------------------------- | --------- |
| R-1 | User denies performing an action   | All Lambda functions | Low  | CloudWatch Logs with user ID; API Gateway access logs with caller identity; CloudFront access logs | Mitigated |
| R-2 | Agent actions cannot be attributed | Job execution        | Low  | Execution logs stored per-job in DynamoDB with timestamps; conversation history preserved          | Mitigated |

### 3.4 Information Disclosure (I)

| ID  | Threat                                                  | Component                  | Risk     | Mitigation                                                                                                 | Status                                                |
| --- | ------------------------------------------------------- | -------------------------- | -------- | ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| I-1 | Cross-user conversation data leakage                    | conversation_management.py | Critical | `verify_user_access_to_session()` validates session ownership via task registry scan; fail-closed on error | Mitigated                                             |
| I-2 | Agent response contains sensitive data from other users | Bedrock Agent Core         | Medium   | Agents are stateless per-invocation; session IDs are user-scoped; conversation context loaded per-session  | Mitigated                                             |
| I-3 | Error messages leak internal details                    | All Lambda functions       | Low      | Generic error messages returned to client; detailed errors logged to CloudWatch only                       | Mitigated                                             |
| I-4 | S3 bucket contents exposed via signal processor         | Signal processor           | Medium   | Signal processor Lambda has read access to user-specified S3 buckets; bucket names are user-configured     | Accepted — requires scoping S3 permissions per-signal |
| I-5 | CloudFront serves cached API responses to wrong user    | CloudFront                 | Medium   | API behavior uses `CACHING_DISABLED` policy; Lambda responses include `Cache-Control: no-store`            | Mitigated                                             |

### 3.5 Denial of Service (D)

| ID  | Threat                                   | Component                  | Risk   | Mitigation                                                                                          | Status                                                     |
| --- | ---------------------------------------- | -------------------------- | ------ | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| D-1 | API request flooding                     | API Gateway                | Medium | API Gateway throttling (default limits); Cognito auth required for all endpoints                    | Partially mitigated — no explicit rate limiting configured |
| D-2 | Expensive Bedrock model invocations      | Job execution              | Medium | Bedrock model scoped to Claude models only; agent `max_iterations: 5` limits per-invocation cost    | Partially mitigated                                        |
| D-3 | Unbounded DynamoDB scans                 | conversation_management.py | Low    | Scan in `verify_user_access_to_session` is bounded by table size; conversation table has 30-day TTL | Accepted                                                   |
| D-4 | Scheduler creates runaway job executions | Scheduler Lambda           | Low    | Scheduler checks job status before re-triggering; jobs transition to terminal states                | Mitigated                                                  |
| D-5 | Large conversation payloads              | Conversation store         | Low    | Pagination (default limit 50); DynamoDB 400KB item limit provides natural bound                     | Mitigated                                                  |

### 3.6 Elevation of Privilege (E)

| ID  | Threat                                                 | Component                  | Risk   | Mitigation                                                                                                               | Status                                              |
| --- | ------------------------------------------------------ | -------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------- |
| E-1 | Compromised Lambda accesses unrelated AWS resources    | Lambda IAM roles           | High   | Per-function IAM roles with least-privilege; DynamoDB grants scoped to specific tables; Bedrock scoped to account/region | Mitigated                                           |
| E-2 | Agent tool executes arbitrary code                     | Calculator tool            | High   | `eval()` replaced with `simpleeval` library (sandboxed AST evaluation); no shell/exec tools                              | Mitigated                                           |
| E-3 | Signal management modifies arbitrary S3 buckets        | signal_management.py       | Medium | S3 notification permissions use wildcard resource — required for user-specified buckets at runtime                       | Accepted — inherent to dynamic bucket configuration |
| E-4 | Cognito Identity Pool grants excessive AWS permissions | Cognito authenticated role | Low    | Authenticated role has no inline policies beyond federation                                                              | Mitigated                                           |

---

## 4. Data Flow Threats

### 4.1 User → CloudFront → API Gateway → Lambda

| Step                     | Threat              | Mitigation                                          |
| ------------------------ | ------------------- | --------------------------------------------------- |
| Browser → CloudFront     | MitM, eavesdropping | TLS 1.2+ enforced, HTTPS redirect                   |
| CloudFront → API Gateway | Request tampering   | HTTPS-only origin protocol                          |
| API Gateway → Lambda     | Unauthorized access | Cognito authorizer validates JWT on every request   |
| Lambda → DynamoDB        | Data tampering      | IAM role-based access; no public DynamoDB endpoints |

### 4.2 Lambda → Bedrock Agent Core

| Step               | Threat                   | Mitigation                                                         |
| ------------------ | ------------------------ | ------------------------------------------------------------------ |
| Lambda → Bedrock   | Prompt injection         | Agent has bounded iterations and loop detection                    |
| Bedrock → Lambda   | Malicious response       | Response parsed as JSON; no code execution from response           |
| Agent → S3 (tools) | Unauthorized file access | Agent S3 policy scoped to specific bucket (`${AGENT_BUCKET_NAME}`) |

### 4.3 S3 → Signal Processor → Job Execution

| Step                            | Threat                    | Mitigation                                                                      |
| ------------------------------- | ------------------------- | ------------------------------------------------------------------------------- |
| S3 notification → Lambda        | Spoofed S3 event          | Lambda permission scoped to S3 service principal with source ARN and account ID |
| Signal processor → Job creation | Unauthorized job creation | Signal processor validates signal exists and is enabled before creating job     |

---

## 5. Accepted Risks

| ID   | Risk                                                       | Justification                                                                                                                                                                                                                                                                      | Owner         |
| ---- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| AR-1 | Signal management has wildcard S3 notification permissions | Required for user-configured bucket names at runtime; cannot be scoped at deploy time                                                                                                                                                                                              | Platform team |
| AR-2 | Signal processor has wildcard S3 read permissions          | Same as AR-1; reads files from user-specified buckets                                                                                                                                                                                                                              | Platform team |
| AR-3 | Prompt injection via crafted S3 file content               | Inherent to LLM-based processing; mitigated by bounded iterations and read-only tools                                                                                                                                                                                              | Platform team |
| AR-4 | No WAF configured on CloudFront/API Gateway                | WAF is not required for this deployment; application-layer protections (Cognito auth, input validation, rate limiting via API Gateway defaults) provide sufficient defense for the current threat profile. WAF can be enabled via `enable_waf: true` if the risk profile changes.  | Platform team |
| AR-5 | No explicit API Gateway throttling/rate limiting           | Relies on default API Gateway limits; should add usage plans for production                                                                                                                                                                                                        | Platform team |
| AR-6 | Signal processor cross-tenant triggering on shared buckets | If two users configure signals on the same S3 bucket, uploads trigger both users' agents. Signals are matched by bucket name without user-scoping. Acceptable for a sample project; production deployments should add user-scoping validation in the signal processor scan filter. | Platform team |

---

## 6. Recommendations

| Priority | Recommendation                                                                                         | Threat Addressed | Status          |
| -------- | ------------------------------------------------------------------------------------------------------ | ---------------- | --------------- |
| ~~High~~ | ~~Enable WAF on CloudFront distribution (`enable_waf: true`)~~                                         | D-1, S-2         | Accepted (AR-4) |
| High     | Add API Gateway usage plans with rate limiting per user                                                | D-1, D-2         | Open            |
| High     | Add GSI on task registry for `sessionId` to replace full table scan in `verify_user_access_to_session` | D-3, I-1         | Open            |
| Medium   | Implement per-user Bedrock invocation quotas to prevent cost abuse                                     | D-2              | Open            |
| Medium   | Add input validation/sanitization on agent prompts before sending to Bedrock                           | T-4              | Open            |
| Medium   | Scope signal processor S3 read permissions to buckets registered in signals table (runtime validation) | E-3, I-4         | Open            |
| Low      | Enable DynamoDB point-in-time recovery on all tables                                                   | Data recovery    | Done            |
| Low      | Add CloudTrail logging for Bedrock API calls                                                           | R-1              | Open            |
| Low      | Rotate Cognito user pool client secret periodically                                                    | S-2              | Open            |

---

## 7. Architecture Diagram Reference

Components and their interactions:

```
[Browser] ──HTTPS──▶ [CloudFront]
                        ├── /assets/* ──▶ [S3 Website Bucket]
                        └── /prod/*  ──▶ [API Gateway + Cognito Auth]
                                            ├── /agents/*        ──▶ [agent-management λ]     ──▶ [agent-registry DDB]
                                            ├── /jobs/*          ──▶ [job-management λ]       ──▶ [job-registry DDB]
                                            ├── /jobs/*/execute  ──▶ [job-execution λ]        ──▶ [Bedrock Agent Core]
                                            │                                                  ──▶ [conversation-store DDB]
                                            ├── /conversations/* ──▶ [conversation-mgmt λ]    ──▶ [conversation-store DDB]
                                            └── /signals/*       ──▶ [signal-management λ]    ──▶ [ambient-signals DDB]

[CloudWatch Events] ──1min──▶ [scheduler λ] ──▶ [job-execution λ]

[S3 Bucket] ──notification──▶ [signal-processor λ] ──▶ [job-registry DDB]
```
