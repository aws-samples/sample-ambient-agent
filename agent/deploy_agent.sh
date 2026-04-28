#!/bin/bash

# Agent Core Deployment Script
# Extracted from master deployment script
# Deploys only the Bedrock Agent Core component

set -e  # Exit on any error

# When the caller passes --agent-arn, we redeploy in place against the
# existing AgentCore runtime with that ARN. No-arg = fresh deployment
# using whatever name is in .bedrock_agentcore.yaml (or a prompt if the
# file is absent). The ARN's runtime name is derived from the last
# slash-segment of the ARN: e.g. `agent_1-4mH5Mr5ndW` from
# `arn:aws:bedrock-agentcore:us-west-2:123:runtime/agent_1-4mH5Mr5ndW`.
AGENT_ARN=""

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

# Function to check prerequisites for agent deployment
check_prerequisites() {
    log "Checking prerequisites for agent deployment..."

    # Check for required commands
    local required_commands=("aws" "agentcore")
    for cmd in "${required_commands[@]}"; do
        if ! command_exists "$cmd"; then
            log_error "Required command '$cmd' not found. Please install it first."
            exit 1
        fi
    done

    # Check AWS credentials
    if ! aws sts get-caller-identity >/dev/null 2>&1; then
        log_error "AWS credentials not configured or invalid. Please run 'aws configure' first."
        exit 1
    fi

    # Check for .env file
    if [ ! -f ".env" ]; then
        log_error ".env file not found in project root. Please create it from env.example."
        exit 1
    fi

    log_success "Prerequisites check passed"
}

# Function to source environment variables
source_env() {
    log "Loading environment variables from .env..."
    if [ -f ".env" ]; then
        set -a
        source .env
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
attach_agent_policies() {
    log "========================================="
    log "ATTACHING IAM POLICIES TO AGENT ROLE"
    log "========================================="

    # Save current directory
    local ORIGINAL_DIR="$(pwd)"

    # Navigate to agent directory if needed
    if [ ! -f "attach_agent_policies.sh" ]; then
        if [ -f "agent/attach_agent_policies.sh" ]; then
            cd agent
        else
            log_warning "Policy attachment script not found. Skipping policy attachment."
            log "To attach policies later, run: cd agent && ./attach_agent_policies.sh"
            return 0
        fi
    fi

    # Make script executable if not already
    chmod +x attach_agent_policies.sh 2>/dev/null || true

    # Run the policy attachment script
    log "Running policy attachment script..."
    if ./attach_agent_policies.sh; then
        log_success "Policies attached successfully"
    else
        log_warning "Policy attachment failed or was skipped"
        log "You can manually attach policies later by running: cd agent && ./attach_agent_policies.sh"
    fi

    # Return to original directory
    cd "$ORIGINAL_DIR"
}

# Function to purge stale agent entries from `.bedrock_agentcore.yaml`.
#
# `.bedrock_agentcore.yaml` accumulates every agent the user has ever
# configured locally. If the corresponding AgentCore runtime has since
# been deleted in AWS, `agentcore launch --auto-update-on-conflict`
# will try to update a non-existent runtime and bail out with
# ResourceNotFoundException. This function calls ListAgentRuntimes in
# the stack's region, compares the live set against what the yaml
# claims, and rewrites the yaml so only live entries remain. If the
# `default_agent` pointer becomes stale, it is reset to the first live
# entry (or removed, triggering agentcore's first-run flow).
purge_stale_agent_entries() {
    if [ ! -f ".bedrock_agentcore.yaml" ]; then
        return 0
    fi

    log "Checking .bedrock_agentcore.yaml for stale agent entries..."

    local region="${AWS_DEFAULT_REGION:-us-west-2}"
    local live_ids
    # Distinguish "list call failed" from "list call returned no
    # runtimes". Empty output with exit 0 means the account has zero
    # live agents, so ALL recorded yaml entries are stale. Only treat a
    # non-zero exit as "leave the yaml alone".
    if ! live_ids=$(aws bedrock-agentcore-control list-agent-runtimes \
            --region "$region" \
            --query 'agentRuntimes[].agentRuntimeId' \
            --output text 2>/dev/null); then
        log_warning "Could not list live AgentCore runtimes; leaving yaml untouched"
        return 0
    fi

    python - "$live_ids" <<'PY'
import sys

try:
    import yaml
except ImportError:
    # No PyYAML; leave the file alone rather than corrupt it.
    sys.exit(0)

live = set(sys.argv[1].split())
path = ".bedrock_agentcore.yaml"
with open(path, "r", encoding="utf-8") as fh:
    doc = yaml.safe_load(fh) or {}

agents = doc.get("agents", {}) or {}

# If the yaml is already in a "no agents" state (e.g., a prior run
# failed mid-way leaving `agents: {}`), delete the file so the
# agentcore CLI runs its first-time flow. `agents: {}` + missing
# default_agent is a fatal state for the CLI.
if not agents:
    import os as _os
    _os.remove(path)
    print("Removed empty .bedrock_agentcore.yaml (no agents recorded)")
    sys.exit(0)

kept, dropped = {}, []
for name, entry in agents.items():
    agent_id = (entry.get("bedrock_agentcore") or {}).get("agent_id") or ""
    if agent_id and agent_id in live:
        kept[name] = entry
    else:
        dropped.append((name, agent_id or "<no-id>"))

if not dropped:
    sys.exit(0)

print("Dropping stale agent entries:")
for name, agent_id in dropped:
    print(f"  - {name} (agent_id={agent_id})")

doc["agents"] = kept

# If default_agent points at something we just removed, pick a new
# default or remove the key entirely so agentcore's first-run flow
# triggers cleanly.
default_name = doc.get("default_agent")
if default_name and default_name not in kept:
    if kept:
        doc["default_agent"] = next(iter(kept))
        print(f"Reset default_agent -> {doc['default_agent']}")
    else:
        doc.pop("default_agent", None)
        print("Cleared default_agent (no live entries remain)")

# If the purge left the config with zero agents, the safest thing is
# to remove the config file entirely. AgentCore's CLI treats an
# `agents: {}` / missing `default_agent` combo as a fatal state
# (`ValueError: No agent specified and no default set`), whereas a
# missing config file triggers its normal first-run flow cleanly.
import os
if not kept:
    os.remove(path)
    print("Removed empty .bedrock_agentcore.yaml so agentcore can start fresh")
else:
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)
PY
}

# Function to deploy agent core
deploy_agent() {
    log "========================================="
    log "DEPLOYING AGENT CORE"
    log "========================================="

    # Get the current directory
    CURRENT_DIR="$(pwd)"
    log "Current directory: $CURRENT_DIR"

    # Check if we're already in an agent directory (contains agent.py)
    if [ -f "agent.py" ]; then
        log "Found agent.py in current directory - proceeding with deployment"
    else
        # Look for agent directory
        if [ -d "agent" ]; then
            log "Found agent directory, changing to it"
            cd agent
        else
            log_error "Could not find agent directory or agent.py file"
            log "Please run this script from the project root or from within the agent directory"
            exit 1
        fi
    fi

    log "Agent directory: $(pwd)"

    # Verify agent.py exists
    if [ ! -f "agent.py" ]; then
        log_error "agent.py not found in $(pwd)"
        exit 1
    fi

    # Check if agent is already configured
    if [ -f ".bedrock_agentcore.yaml" ]; then
        log_warning "Agent appears to be already configured. Checking current status..."

        # Extract current agent ARN from config for agent specifically
        CURRENT_AGENT_ARN=$(awk '/agents:/,/agent:/ {next} /agent:/,/ambient_agent:/ { if (/agent_arn:/) print $2; exit }' .bedrock_agentcore.yaml)
        if [ -n "$CURRENT_AGENT_ARN" ]; then
            log "Current agent ARN: $CURRENT_AGENT_ARN"
        fi
    fi

    # Prune any recorded agents whose runtimes no longer exist in AWS.
    # This avoids `agentcore launch` failing with
    # ResourceNotFoundException when the user deleted an agent from the
    # AWS console but the yaml still references it.
    purge_stale_agent_entries

    # If the caller passed --agent-arn, derive the agent name from the
    # ARN and feed it to `agentcore configure` non-interactively so the
    # subsequent `agentcore launch` updates the existing runtime in
    # place instead of creating a new one.
    #
    # AgentCore ARNs have the shape
    # `.../runtime/<name>-<unique-suffix>`. The unique suffix is
    # AWS-assigned and alphanumeric; the <name> portion is whatever the
    # user chose (alphanumeric + underscore, no hyphens allowed by
    # `agentcore configure`'s validator). So we split on the last
    # hyphen to recover the original name rather than taking the whole
    # last slash-segment.
    if [ -n "$AGENT_ARN" ]; then
        RUNTIME_SEGMENT=$(echo "$AGENT_ARN" | awk -F'/' '{print $NF}')
        if [ -z "$RUNTIME_SEGMENT" ]; then
            log_error "Could not derive agent name from --agent-arn: $AGENT_ARN"
            exit 1
        fi
        # Strip the trailing `-<suffix>` to get the configurable name.
        TARGET_AGENT_NAME="${RUNTIME_SEGMENT%-*}"
        if [ -z "$TARGET_AGENT_NAME" ] || [ "$TARGET_AGENT_NAME" = "$RUNTIME_SEGMENT" ]; then
            log_error "ARN did not match expected AgentCore runtime shape: $AGENT_ARN"
            log_error "Expected `.../runtime/<name>-<suffix>`"
            exit 1
        fi
        log "Redeploying to existing runtime (name=$TARGET_AGENT_NAME, arn=$AGENT_ARN)"
        log "Configuring agent (non-interactive, name=$TARGET_AGENT_NAME)..."
        agentcore configure -e agent.py -n "$TARGET_AGENT_NAME"
    else
        log "Configuring agent..."
        agentcore configure -e agent.py
    fi

    log "Launching agent..."
    agentcore launch --auto-update-on-conflict

    # Extract the ARN for the agent we just deployed.
    #
    # `.bedrock_agentcore.yaml` lists every agent the user has ever
    # configured in this directory, not just the current one. Naively
    # grepping for `agent_arn:` returns all of them. Instead, read the
    # yaml, find the entry whose key matches `default_agent` (which
    # agentcore sets to the agent just configured) and pull its ARN.
    if [ ! -f ".bedrock_agentcore.yaml" ]; then
        log_error "Agent configuration file not found after deployment"
        exit 1
    fi

    NEW_AGENT_ARN=$(python - <<'PY'
import sys
try:
    import yaml  # PyYAML ships with most Python distributions used here
except ImportError:
    print("", end="")
    sys.exit(0)

with open(".bedrock_agentcore.yaml", "r", encoding="utf-8") as fh:
    doc = yaml.safe_load(fh) or {}

default_name = doc.get("default_agent")
agents = doc.get("agents", {}) or {}
entry = agents.get(default_name, {}) if default_name else {}
arn = (entry.get("bedrock_agentcore") or {}).get("agent_arn", "")
print(arn)
PY
    )

    # Fallback: if PyYAML is not available, pick the ARN associated with
    # the default_agent key via grep + awk. Handles the common case
    # where the yaml only has one agent too.
    if [ -z "$NEW_AGENT_ARN" ]; then
        DEFAULT_AGENT=$(grep '^default_agent:' .bedrock_agentcore.yaml | sed 's/default_agent: *//' | tr -d ' ')
        if [ -n "$DEFAULT_AGENT" ]; then
            NEW_AGENT_ARN=$(awk -v target="$DEFAULT_AGENT" '
                $0 ~ "^  " target ":$" { in_agent=1; next }
                in_agent && /^  [a-zA-Z_]+:$/ && $0 !~ "^  " target ":$" { in_agent=0 }
                in_agent && /agent_arn:/ {
                    sub(/.*agent_arn: */, "")
                    gsub(/[[:space:]]/, "")
                    print
                    exit
                }
            ' .bedrock_agentcore.yaml)
        fi
    fi

    if [ -n "$NEW_AGENT_ARN" ]; then
        log_success "Agent deployed successfully with ARN: $NEW_AGENT_ARN"
        export INVOKE_AGENT_ARN="$NEW_AGENT_ARN"
    else
        log_error "Failed to extract agent ARN from configuration"
        exit 1
    fi

    # Stay in agent directory for policy attachment
    log_success "Agent Core deployment completed"
}

# Function to verify agent deployment
verify_agent_deployment() {
    log "========================================="
    log "VERIFYING AGENT DEPLOYMENT"
    log "========================================="

    # Check agent status
    log "Checking agent status..."
    if [ -n "$INVOKE_AGENT_ARN" ]; then
        log_success "Agent ARN: $INVOKE_AGENT_ARN"

        # Test if agent is accessible
        log "Testing agent accessibility..."
        if aws bedrock-agent get-agent --agent-id $(echo $INVOKE_AGENT_ARN | cut -d'/' -f2) >/dev/null 2>&1; then
            log_success "Agent is accessible and ready"
        else
            log_warning "Agent may not be fully ready yet (this is normal for new deployments)"
        fi
    else
        log_error "Agent ARN not found in environment"
        exit 1
    fi
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

    echo ""
    log "Next steps:"
    echo "1. Copy the Agent Runtime ARN above to register in the platform UI"
    echo "2. Navigate to the Agents page in the web application"
    echo "3. Click 'Register New Agent' and paste the ARN"
    echo ""
    log "To test the agent directly:"
    echo "aws bedrock-agent-runtime invoke-agent --agent-id \$(echo $INVOKE_AGENT_ARN | cut -d'/' -f2) --agent-alias-id TSTALIASID --session-id test-session --input-text 'Hello, how are you?'"
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
    source_env
    deploy_agent
    attach_agent_policies
    verify_agent_deployment
    display_summary

    log_success "Agent deployment completed successfully at $(date)"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Agent Core Deployment Script"
            echo "Deploys the Bedrock Agent Core component."
            echo ""
            echo "Options:"
            echo "  --agent-arn <ARN>   Redeploy to an existing AgentCore runtime."
            echo "                      The runtime name is taken from the ARN's"
            echo "                      last slash-segment. The ARN is preserved;"
            echo "                      only the code/version is updated."
            echo "  --help, -h          Show this help message"
            echo ""
            echo "Prerequisites:"
            echo "  - AWS CLI configured with appropriate credentials"
            echo "  - agentcore CLI tool installed"
            echo "  - .env file present in project root"
            echo ""
            echo "Behavior:"
            echo "  Without --agent-arn, this script creates a new runtime (or"
            echo "  updates whatever is already pinned in .bedrock_agentcore.yaml)."
            echo "  With --agent-arn, it updates the referenced runtime in place."
            echo ""
            echo "Examples:"
            echo "  $0"
            echo "  $0 --agent-arn arn:aws:bedrock-agentcore:us-west-2:123:runtime/agent_1-4mH5Mr5ndW"
            echo ""
            exit 0
            ;;
        --agent-arn)
            if [ -z "${2:-}" ]; then
                log_error "--agent-arn requires a value"
                exit 1
            fi
            AGENT_ARN="$2"
            shift 2
            ;;
        --agent-arn=*)
            AGENT_ARN="${1#--agent-arn=}"
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
