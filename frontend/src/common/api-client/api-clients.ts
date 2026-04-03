// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Import your API clients here
import { MultiAgentApiClient } from "./multi-agent-api-client";

export class ApiClient {
  private _multiAgentClient: MultiAgentApiClient | undefined;

  public get multiAgentClient() {
    if (!this._multiAgentClient) {
      this._multiAgentClient = new MultiAgentApiClient();
    }
    return this._multiAgentClient;
  }
}

// Export a singleton instance
export const apiClient = new ApiClient();
