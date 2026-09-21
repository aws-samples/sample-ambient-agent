#!/bin/bash

# Agent Core Deployment Script
#
# Deploys the agent via the `@aws/agentcore` CLI (npm package) against
# the sibling AgentCore project at `../AmbientAgent` (relative to this
# script's own directory, i.e. `agent/../AmbientAgent`). On first run,
# this script creates that project itself (a `byo` runtime pointed at
# this `agent/` directory's `agent.py`) and creates a scoped IAM
# execution role, pinning it into the project's `agentcore.json`
# rather than letting the CLI create its own, broader default role.
# Both are reused on subsequent runs; `attach_agent_policies.sh` keeps
# the role's permissions in sync with `policies/*.json` on every run.
#
set -e  # Exit on any error

# Directory of this script (works regardless of the caller's cwd).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The AgentCore CLI project lives as a sibling of this `agent/`
# directory, not inside it - creating/importing an AgentCore project
# nested inside the directory it points its `codeLocation` at triggers
# a real bug in the CLI (infinite recursive directory copy). Override
# with AGENTCORE_PROJECT_DIR if you've placed the project elsewhere.
PROJECT_DIR="${AGENTCORE_PROJECT_DIR:-$SCRIPT_DIR/../AmbientAgent}"
AGENT_NAME="${AGENTCORE_AGENT_NAME:-ambient}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] ✓${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] ⚠${NC} $1"
}

log_error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ✗${NC} $1"
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Guard against a Python virtualenv's own `agentcore` console-script
# (from the old bedrock-agentcore-starter-toolkit package) shadowing
# the real npm-distributed `@aws/agentcore` CLI on PATH. Their CLIs
# are entirely different (this script's flags, like `--no-agent`,
# don't exist on the old one) - if a venv is active ahead of the npm
# CLI, every subsequent `agentcore` call in this script would run the
# wrong tool with confusing "no such option" errors.
check_agentcore_cli() {
    local resolved
    resolved="$(command -v agentcore)"
    case "$resolved" in
        */.venv/bin/agentcore | */venv/bin/agentcore)
            log_error "'agentcore' on PATH resolves to $resolved,"
            log_error "which is the old Python toolkit's CLI, not the npm-distributed"
            log_error "'@aws/agentcore' CLI this script requires. Deactivate the active"
            log_error "virtualenv ('deactivate') before running this script, or open a new shell."
            exit 1
            ;;
    esac
}

# Function to check prerequisites for agent deployment
check_prerequisites() {
    log "Checking prerequisites for agent deployment..."

    # Check for required commands
    local required_commands=("aws" "agentcore")
    for cmd in "${required_commands[@]}"; do
        if ! command_exists "$cmd"; then
            log_error "Required command '$cmd' not found. Please install it first."
            if [ "$cmd" = "agentcore" ]; then
                log "Install with: npm install -g @aws/agentcore@latest"
            fi
            exit 1
        fi
    done

    check_agentcore_cli

    # Check AWS credentials
    if ! aws sts get-caller-identity >/dev/null 2>&1; then
        log_error "AWS credentials not configured or invalid. Please run 'aws configure' first."
        exit 1
    fi

    # Check for .env file
    if [ ! -f "$SCRIPT_DIR/.env" ]; then
        log_error ".env file not found in agent directory. Please create it from .env.example."
        exit 1
    fi

    log_success "Prerequisites check passed"
}

# Function to create the sibling AgentCore CLI project if it doesn't
# exist yet, so a first-time run of this script needs nothing beyond
# `.env` set up. Safe to call on every run - it's a no-op once
# agentcore.json exists.
bootstrap_agentcore_project() {
    if [ -f "$PROJECT_DIR/agentcore/agentcore.json" ]; then
        return 0
    fi

    log "AgentCore project not found at $PROJECT_DIR - creating it now..."

    local parent_dir project_name rel_code_location
    parent_dir="$(dirname "$PROJECT_DIR")"
    project_name="$(basename "$PROJECT_DIR")"
    mkdir -p "$parent_dir"

    # Creating/importing an AgentCore project nested inside this
    # directory (the one its `codeLocation` points at) triggers a real
    # bug in the CLI (infinite recursive directory copy). Compare
    # resolved (`..`-free) absolute paths, not raw strings - PROJECT_DIR
    # defaults to "$SCRIPT_DIR/../AmbientAgent", which literally starts
    # with "$SCRIPT_DIR/" even though it resolves to a sibling.
    local resolved_parent_dir
    resolved_parent_dir="$(cd "$parent_dir" && pwd)"
    case "$resolved_parent_dir" in
        "$SCRIPT_DIR" | "$SCRIPT_DIR"/*)
            log_error "AGENTCORE_PROJECT_DIR/--project-dir ($PROJECT_DIR) resolves to"
            log_error "$resolved_parent_dir/$project_name, which is nested inside this agent/"
            log_error "directory. That triggers a CLI bug (infinite recursive directory copy)"
            log_error "because the project's codeLocation points at its own ancestor."
            log_error "Point --project-dir at a sibling directory instead."
            exit 1
            ;;
    esac

    # Path to this agent/ directory, relative to the new project
    # directory - agentcore.json stores codeLocation as a relative
    # path (e.g. "../agent"), and computing it rather than assuming
    # "../agent" keeps this working if --project-dir isn't a direct
    # sibling.
    rel_code_location=$(python3 -c "
import os, sys
print(os.path.relpath(sys.argv[1], sys.argv[2]))
" "$SCRIPT_DIR" "$PROJECT_DIR" 2>/dev/null)
    if [ -z "$rel_code_location" ]; then
        log_error "Could not compute a relative path from $PROJECT_DIR to $SCRIPT_DIR"
        exit 1
    fi

    if ! (
        cd "$parent_dir" \
        && agentcore create --project-name "$project_name" --no-agent --skip-git --output-dir . \
        && cd "$project_name" \
        && agentcore add agent --name "$AGENT_NAME" --type byo \
            --code-location "$rel_code_location" --entrypoint agent.py --language Python \
            --framework LangChain_LangGraph --model-provider Bedrock
    ); then
        log_error "Failed to bootstrap the AgentCore project at $PROJECT_DIR"
        log "You can retry this script, or run the steps manually - see agent/README.md."
        exit 1
    fi

    log_success "Created AgentCore project at $PROJECT_DIR"
}

# Function to source environment variables
source_env() {
    log "Loading environment variables from .env..."
    if [ -f "$SCRIPT_DIR/.env" ]; then
        set -a
        source "$SCRIPT_DIR/.env"
        set +a
        log_success "Environment variables loaded"
        log "Using AWS Account ID: $AWS_ACCOUNT_ID"
        log "Using AWS Region: $AWS_DEFAULT_REGION"
    else
        log_error ".env file not found"
        exit 1
    fi
}

# Function to attach IAM policies to agent role
#
# The runtime is deployed against a pre-existing role pinned via
# `executionRoleArn` in agentcore.json (not a CLI-managed default
# role), so this step always runs to keep that role's inline policy in
# sync with agent/policies/*.json - the CLI does not manage this
# role's permissions for us.
attach_agent_policies() {
    log "========================================="
    log "ATTACHING IAM POLICIES TO AGENT ROLE"
    log "========================================="

    local ORIGINAL_DIR="$(pwd)"
    cd "$SCRIPT_DIR"

    chmod +x attach_agent_policies.sh 2>/dev/null || true

    log "Running policy attachment script..."
    if ./attach_agent_policies.sh "$EXECUTION_ROLE_ARN"; then
        log_success "Policies attached successfully"
    else
        log_warning "Policy attachment failed"
        log "You can manually attach policies later by running: cd agent && ./attach_agent_policies.sh <role-arn-or-name>"
    fi

    cd "$ORIGINAL_DIR"
}

# Function to deploy agent core
deploy_agent() {
    log "========================================="
    log "DEPLOYING AGENT CORE"
    log "========================================="

    log "AgentCore project: $PROJECT_DIR"
    cd "$PROJECT_DIR"

    log "Validating project configuration..."
    if ! agentcore validate; then
        log_error "agentcore.json failed validation - fix it before deploying"
        exit 1
    fi

    log "Deploying via 'agentcore deploy'..."
    # -y: auto-confirm, read credentials from env (matches this
    # script's non-interactive usage everywhere else).
    agentcore deploy -y

    log "Fetching deployed resource status..."
    STATUS_JSON=$(agentcore status --runtime "$AGENT_NAME" --json 2>/dev/null || true)

    # Best-effort extraction: try a few plausible field names since the
    # CLI's `status --json` resource shape isn't guaranteed stable
    # across versions. If none match, fall back to printing the raw
    # JSON so the user can find the ARN by eye rather than the script
    # silently claiming success with no ARN.
    NEW_AGENT_ARN=""
    if [ -n "$STATUS_JSON" ]; then
        NEW_AGENT_ARN=$(echo "$STATUS_JSON" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
resources = data.get('resources', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
for r in resources:
    if not isinstance(r, dict):
        continue
    name_match = r.get('name') == '$AGENT_NAME' or r.get('resourceName') == '$AGENT_NAME'
    if name_match:
        arn = r.get('arn') or r.get('runtimeArn') or r.get('agentRuntimeArn') or ''
        if arn:
            print(arn)
            break
" 2>/dev/null || true)
    fi

    if [ -n "$NEW_AGENT_ARN" ]; then
        log_success "Agent deployed successfully with ARN: $NEW_AGENT_ARN"
        export INVOKE_AGENT_ARN="$NEW_AGENT_ARN"
    else
        log_warning "Could not automatically extract the runtime ARN."
        log "Run 'agentcore status --runtime $AGENT_NAME --json' from $PROJECT_DIR and look for the runtime's ARN, or run 'agentcore fetch access --name $AGENT_NAME --type agent'."
        if [ -n "$STATUS_JSON" ]; then
            echo "$STATUS_JSON"
        fi
    fi

    cd "$SCRIPT_DIR"
    log_success "Agent Core deployment completed"
}

# Function to read the pinned execution role ARN out of agentcore.json.
# Creates the role (and pins it into agentcore.json) if none is set yet,
# so a genuinely from-scratch checkout deploys without requiring the
# user to hand-create or hand-paste an IAM role first. Sets the global
# EXECUTION_ROLE_ARN for attach_agent_policies/deploy_agent to use.
resolve_execution_role() {
    EXECUTION_ROLE_ARN=$(python3 -c "
import json
with open('$PROJECT_DIR/agentcore/agentcore.json', encoding='utf-8') as fh:
    doc = json.load(fh)
for rt in doc.get('runtimes', []):
    if rt.get('name') == '$AGENT_NAME':
        print(rt.get('executionRoleArn', ''))
        break
" 2>/dev/null || true)

    if [ -n "$ROLE_ARN_OVERRIDE" ]; then
        EXECUTION_ROLE_ARN="$ROLE_ARN_OVERRIDE"
        log "Using --role-arn override: $EXECUTION_ROLE_ARN"
    elif [ -z "$EXECUTION_ROLE_ARN" ]; then
        log_warning "No executionRoleArn set for runtime '$AGENT_NAME' - creating one."
        EXECUTION_ROLE_ARN=$(create_execution_role)
        pin_execution_role "$EXECUTION_ROLE_ARN"
    fi

    log "Execution role: $EXECUTION_ROLE_ARN"
}

# Function to create the AgentCore Runtime execution role from
# scratch: a role trusted only by bedrock-agentcore.amazonaws.com for
# *this* account/region, with no permissions attached yet
# (attach_agent_policies fills those in from agent/policies/*.json,
# including the runtime baseline every AgentCore runtime needs to boot
# - ECR image pull, workload identity token, X-Ray/CloudWatch metrics,
# log group access). Prints the new role's ARN on stdout.
create_execution_role() {
    local role_name="AmbientAgentCoreExecutionRole-${AWS_DEFAULT_REGION}"
    local trust_policy
    trust_policy=$(mktemp)
    cat >"$trust_policy" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowBedrockAgentCoreAssumeRole",
      "Effect": "Allow",
      "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "${AWS_ACCOUNT_ID}" },
        "ArnLike": { "aws:SourceArn": "arn:aws:bedrock-agentcore:${AWS_DEFAULT_REGION}:${AWS_ACCOUNT_ID}:*" }
      }
    }
  ]
}
EOF

    local existing_arn
    existing_arn=$(aws iam get-role --role-name "$role_name" --query 'Role.Arn' --output text 2>/dev/null || true)
    if [ -n "$existing_arn" ] && [ "$existing_arn" != "None" ]; then
        log "Reusing existing role: $existing_arn" >&2
        rm -f "$trust_policy"
        echo "$existing_arn"
        return 0
    fi

    log "Creating IAM role '$role_name'..." >&2
    local role_arn
    role_arn=$(aws iam create-role \
        --role-name "$role_name" \
        --assume-role-policy-document "file://$trust_policy" \
        --description "Bedrock AgentCore Runtime execution role for the $AGENT_NAME agent" \
        --query 'Role.Arn' --output text)
    rm -f "$trust_policy"

    if [ -z "$role_arn" ]; then
        log_error "Failed to create IAM role '$role_name'" >&2
        exit 1
    fi

    log_success "Created role: $role_arn" >&2
    log "Waiting for IAM role propagation..." >&2
    sleep 10

    echo "$role_arn"
}

# Function to write executionRoleArn onto the runtime entry in
# agentcore.json so subsequent runs (and `agentcore deploy` itself)
# pick it up without re-creating a role every time.
pin_execution_role() {
    local role_arn="$1"
    python3 -c "
import json
path = '$PROJECT_DIR/agentcore/agentcore.json'
with open(path, encoding='utf-8') as fh:
    doc = json.load(fh)
for rt in doc.get('runtimes', []):
    if rt.get('name') == '$AGENT_NAME':
        rt['executionRoleArn'] = '$role_arn'
        break
else:
    raise SystemExit(f\"No runtime named '$AGENT_NAME' in {path}\")
with open(path, 'w', encoding='utf-8') as fh:
    json.dump(doc, fh, indent=2)
    fh.write('\n')
"
    log_success "Pinned executionRoleArn in $PROJECT_DIR/agentcore/agentcore.json"
}

# Function to verify agent deployment
verify_agent_deployment() {
    log "========================================="
    log "VERIFYING AGENT DEPLOYMENT"
    log "========================================="

    if [ -z "$INVOKE_AGENT_ARN" ]; then
        log_warning "Agent ARN was not captured automatically; skipping automated verification."
        log "Run 'agentcore status --runtime $AGENT_NAME' from $PROJECT_DIR to check deployment status."
        return 0
    fi

    log_success "Agent ARN: $INVOKE_AGENT_ARN"
    log "Checking agent status via the CLI..."
    (cd "$PROJECT_DIR" && agentcore status --runtime "$AGENT_NAME") || \
        log_warning "Could not confirm status automatically (this is not necessarily an error - new deployments can take a moment to report READY)."
}

# Function to display agent deployment summary
display_summary() {
    log "========================================="
    log "AGENT DEPLOYMENT SUMMARY"
    log "========================================="

    echo ""
    log_success "Agent Core deployed successfully!"
    echo ""

    if [ -n "$INVOKE_AGENT_ARN" ]; then
        echo -e "${GREEN}Agent ARN:${NC} $INVOKE_AGENT_ARN"
    fi
    echo -e "${GREEN}Execution role:${NC} $EXECUTION_ROLE_ARN"

    echo ""
    log "Next steps:"
    echo "1. Copy the Agent Runtime ARN above to register in the platform UI"
    echo "2. Navigate to the Agents page in the web application"
    echo "3. Click 'Register New Agent' and paste the ARN"
    echo ""
    log "To invoke the agent directly:"
    echo "  (cd $PROJECT_DIR && agentcore invoke \"Hello, how are you?\")"
    echo ""
}

# Function to handle cleanup on error
cleanup_on_error() {
    log_error "Agent deployment failed. Check the logs above for details."
    log "You may need to clean up partially deployed resources manually."
    exit 1
}

# Main deployment function
main() {
    log "========================================="
    log "AGENT CORE DEPLOYMENT SCRIPT"
    log "========================================="
    log "Starting agent deployment at $(date)"

    # Set up error handling
    trap cleanup_on_error ERR

    # Run deployment steps
    check_prerequisites
    bootstrap_agentcore_project
    source_env
    resolve_execution_role

    if [ "$POLICIES_ONLY" = "true" ]; then
        # Re-sync the execution role's IAM policy from agent/policies/*.json
        # without a full `agentcore deploy` - for when only permissions
        # changed and the runtime itself doesn't need re-provisioning.
        attach_agent_policies
        log_success "Policy sync completed successfully at $(date)"
        return 0
    fi

    deploy_agent
    attach_agent_policies
    verify_agent_deployment
    display_summary

    log_success "Agent deployment completed successfully at $(date)"
}

# Global set by resolve_execution_role / --role-arn.
EXECUTION_ROLE_ARN=""
ROLE_ARN_OVERRIDE=""
POLICIES_ONLY="false"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Agent Core Deployment Script"
            echo "Deploys the agent via the '@aws/agentcore' CLI (agentcore deploy)"
            echo "against the sibling AgentCore project at ../AmbientAgent."
            echo ""
            echo "Options:"
            echo "  --role-arn <ARN>    Execution role to use if agentcore.json's"
            echo "                      runtime entry has no executionRoleArn set."
            echo "  --project-dir <dir> AgentCore project directory (default: ../AmbientAgent)."
            echo "  --agent-name <name> Runtime name within the project (default: ambient)."
            echo "  --policies-only     Re-sync the execution role's IAM policy from"
            echo "                      agent/policies/*.json without a full 'agentcore deploy'."
            echo "                      Use this after editing a policies/*.json file when the"
            echo "                      runtime itself doesn't need re-provisioning."
            echo "  --help, -h          Show this help message"
            echo ""
            echo "Prerequisites:"
            echo "  - AWS CLI and the 'agentcore' npm CLI installed"
            echo "    (npm install -g @aws/agentcore@latest)"
            echo "  - .env file present in this (agent/) directory"
            echo ""
            echo "On first run, this script creates the AgentCore project at --project-dir"
            echo "(a 'byo' runtime pointed at this agent/ directory) and an IAM execution"
            echo "role scoped to agent/policies/*.json, if they don't already exist."
            echo ""
            echo "Examples:"
            echo "  $0"
            echo "  $0 --policies-only"
            echo "  $0 --role-arn arn:aws:iam::123456789012:role/MyAgentRole"
            echo ""
            exit 0
            ;;
        --policies-only)
            POLICIES_ONLY="true"
            shift
            ;;
        --role-arn)
            if [ -z "${2:-}" ]; then
                log_error "--role-arn requires a value"
                exit 1
            fi
            ROLE_ARN_OVERRIDE="$2"
            shift 2
            ;;
        --role-arn=*)
            ROLE_ARN_OVERRIDE="${1#--role-arn=}"
            shift
            ;;
        --project-dir)
            if [ -z "${2:-}" ]; then
                log_error "--project-dir requires a value"
                exit 1
            fi
            PROJECT_DIR="$2"
            shift 2
            ;;
        --project-dir=*)
            PROJECT_DIR="${1#--project-dir=}"
            shift
            ;;
        --agent-name)
            if [ -z "${2:-}" ]; then
                log_error "--agent-name requires a value"
                exit 1
            fi
            AGENT_NAME="$2"
            shift 2
            ;;
        --agent-name=*)
            AGENT_NAME="${1#--agent-name=}"
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Run the main function
main

# Ensure clean exit
exit 0
