#!/bin/bash

# Script to attach IAM policies to Bedrock Agent Core execution role
# This script reads the agent_policies.json file and attaches it to the agent's role

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

# Function to extract role name from ARN or name
get_role_name() {
    local role_input="$1"

    # If it's an ARN, extract the role name
    if [[ $role_input == arn:aws:iam::* ]]; then
        echo "$role_input" | awk -F'/' '{print $NF}'
    else
        echo "$role_input"
    fi
}

# Function to check if policy exists
policy_exists() {
    local role_name="$1"
    local policy_name="$2"

    aws iam get-role-policy \
        --role-name "$role_name" \
        --policy-name "$policy_name" \
        >/dev/null 2>&1
}

# Function to attach inline policy to role
attach_policy() {
    local role_name="$1"
    local policy_name="$2"
    local policy_document="$3"

    log "Attaching policy '$policy_name' to role '$role_name'..."

    if policy_exists "$role_name" "$policy_name"; then
        log_warning "Policy '$policy_name' already exists. Updating..."
    fi

    aws iam put-role-policy \
        --role-name "$role_name" \
        --policy-name "$policy_name" \
        --policy-document "file://$policy_document"

    if [ $? -eq 0 ]; then
        log_success "Policy '$policy_name' attached successfully"
        return 0
    else
        log_error "Failed to attach policy '$policy_name'"
        return 1
    fi
}

# Function to list current policies on role
list_role_policies() {
    local role_name="$1"

    log "Current inline policies on role '$role_name':"
    aws iam list-role-policies --role-name "$role_name" --query 'PolicyNames' --output table

    log "Current attached managed policies on role '$role_name':"
    aws iam list-attached-role-policies --role-name "$role_name" --query 'AttachedPolicies[*].[PolicyName,PolicyArn]' --output table
}

# Function to get role from bedrock agentcore config
get_role_from_config() {
    if [ -f ".bedrock_agentcore.yaml" ]; then
        # Extract execution role ARN from config (under aws: section)
        local role_arn=$(grep "execution_role:" .bedrock_agentcore.yaml | head -1 | sed 's/.*execution_role: *//' | tr -d ' ' | tr -d '"')
        if [ -n "$role_arn" ]; then
            echo "$role_arn"
            return 0
        fi
    fi
    return 1
}

# Function to merge policy files into a single IAM policy document.

merge_policies() {
    local policies_dir="$1"
    local output_file="$2"

    python - "$policies_dir" "$output_file" <<'PY'
import glob
import json
import os
import sys

policies_dir, output_file = sys.argv[1], sys.argv[2]

statements = []
for path in sorted(glob.glob(os.path.join(policies_dir, "*.json"))):
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    for stmt in doc.get("Statement", []):
        statements.append(stmt)

merged = {"Version": "2012-10-17", "Statement": statements}
with open(output_file, "w", encoding="utf-8") as fh:
    json.dump(merged, fh, indent=2)
PY

    if [ ! -s "$output_file" ]; then
        log_error "Merged policy file is empty - aborting policy attach"
        return 1
    fi
}

# Main function
main() {
    log "========================================="
    log "AGENT POLICY ATTACHMENT SCRIPT"
    log "========================================="

    # Check if policies directory exists
    POLICIES_DIR="policies"
    if [ ! -d "$POLICIES_DIR" ]; then
        # Fallback to old single file approach
        POLICY_FILE="agent_policies.json"
        if [ ! -f "$POLICY_FILE" ]; then
            log_error "Neither 'policies/' directory nor 'agent_policies.json' file found"
            log "Please ensure policies directory exists with policy JSON files"
            exit 1
        fi
        log_warning "Using legacy single policy file: $POLICY_FILE"
        MERGED_POLICY_FILE="$POLICY_FILE"
    else
        # Check if there are any policy files
        if ! ls "$POLICIES_DIR"/*.json >/dev/null 2>&1; then
            log_error "No policy files found in $POLICIES_DIR/"
            log "Please add policy JSON files to the policies directory"
            exit 1
        fi

        log_success "Found policies directory: $POLICIES_DIR"

        # List policy files
        log "Policy files to be attached:"
        for policy_file in "$POLICIES_DIR"/*.json; do
            if [ -f "$policy_file" ]; then
                log "  - $(basename "$policy_file")"
            fi
        done

        # Merge all policy files into one. Use a path in the current
        # working directory rather than /tmp because on Git Bash /
        # MINGW64 the POSIX `/tmp` path and Windows-native `%TEMP%`
        # do not line up, and the AWS CLI (Windows binary) cannot
        # read files the bash-side Python wrote under `/tmp`.
        MERGED_POLICY_FILE="./.merged_agent_policies_$$.json"
        log "Merging policy files..."
        if ! merge_policies "$POLICIES_DIR" "$MERGED_POLICY_FILE"; then
            log_error "merge_policies failed; aborting"
            exit 1
        fi
        if [ ! -s "$MERGED_POLICY_FILE" ]; then
            log_error "Merged policy file missing or empty at $MERGED_POLICY_FILE"
            exit 1
        fi
        log_success "Policies merged successfully"
    fi

    # Get role from command line argument or config file
    ROLE_INPUT=""
    if [ -n "$1" ]; then
        ROLE_INPUT="$1"
        log "Using role from command line: $ROLE_INPUT"
    else
        log "No role specified, checking .bedrock_agentcore.yaml..."
        ROLE_INPUT=$(get_role_from_config)
        if [ $? -eq 0 ]; then
            log_success "Found role in config: $ROLE_INPUT"
        else
            log_error "Could not find role in .bedrock_agentcore.yaml"
            log "Usage: $0 [ROLE_ARN_OR_NAME]"
            log "Example: $0 arn:aws:iam::123456789012:role/MyAgentRole"
            log "Example: $0 MyAgentRole"
            exit 1
        fi
    fi

    # Extract role name
    ROLE_NAME=$(get_role_name "$ROLE_INPUT")
    log "Role name: $ROLE_NAME"

    # Verify role exists
    log "Verifying role exists..."
    if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
        log_error "Role '$ROLE_NAME' not found"
        exit 1
    fi
    log_success "Role verified"

    # Show current policies
    echo ""
    list_role_policies "$ROLE_NAME"
    echo ""

    # Attach the policy
    POLICY_NAME="BedrockAgentCoreCustomPolicy"
    attach_policy "$ROLE_NAME" "$POLICY_NAME" "$MERGED_POLICY_FILE"

    # Clean up temporary file if it was created
    if [ "$MERGED_POLICY_FILE" != "agent_policies.json" ] \
        && [ "$MERGED_POLICY_FILE" != "./.merged_agent_policies_$$.json" ] \
        || [ -f "$MERGED_POLICY_FILE" ]; then
        rm -f "$MERGED_POLICY_FILE"
        log "Cleaned up temporary merged policy file"
    fi

    # Show updated policies
    echo ""
    log "Updated policies:"
    list_role_policies "$ROLE_NAME"
    echo ""

    log_success "Policy attachment completed successfully"
    log "The agent now has permissions for all policies in the policies/ directory"
}

# Parse command line arguments
case "${1:-}" in
    --help|-h)
        echo "Usage: $0 [ROLE_ARN_OR_NAME]"
        echo ""
        echo "Attach IAM policies to Bedrock Agent Core execution role"
        echo ""
        echo "Arguments:"
        echo "  ROLE_ARN_OR_NAME    IAM role ARN or name (optional if .bedrock_agentcore.yaml exists)"
        echo ""
        echo "Options:"
        echo "  --help, -h          Show this help message"
        echo ""
        echo "Examples:"
        echo "  $0 arn:aws:iam::123456789012:role/MyAgentRole"
        echo "  $0 MyAgentRole"
        echo "  $0  # Uses role from .bedrock_agentcore.yaml"
        echo ""
        echo "The script will attach policies defined in agent_policies.json to the specified role."
        exit 0
        ;;
    --*)
        log_error "Unknown option: $1"
        echo "Use --help for usage information"
        exit 1
        ;;
esac

# Run main function
main "$@"
