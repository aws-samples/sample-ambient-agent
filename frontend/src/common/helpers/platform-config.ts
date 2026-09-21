// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
/**
 * Platform-level deployment config that isn't part of the Amplify
 * `ResourcesConfig` shape (Auth/API), but is still emitted into
 * `aws-exports.json` by the CDK deployment (see
 * `MultiAgentStack._deploy_frontend`).
 *
 * Currently this is just the signal-uploads bucket name: ambient S3
 * signals may only watch this single, stack-owned bucket (the backend
 * enforces this too - see `signal_management.py`'s bucket allowlist
 * check). Surfacing it here lets the Signals UI show/prefill the
 * bucket instead of accepting an arbitrary bucket name from the user.
 */

let cachedBucketName: string | null | undefined;

export async function fetchSignalUploadsBucket(): Promise<string | null> {
  if (cachedBucketName !== undefined) {
    return cachedBucketName;
  }

  try {
    const response = await fetch("/aws-exports.json");
    const data = await response.json();
    cachedBucketName = (data?.signalUploadsBucket as string | undefined) ?? null;
  } catch {
    cachedBucketName = null;
  }

  return cachedBucketName;
}
