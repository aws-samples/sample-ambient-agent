// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
/**
 * Multi-Agent Platform API client.
 *
 * Every endpoint is a thin wrapper over the shared `apiFetch` helper so that
 * auth, error handling, and JSON parsing are defined exactly once. New
 * endpoints should call `apiFetch<T>(path, init, operation)` rather than
 * hand-rolling `fetch()` + `response.json()`.
 *
 * Caching is controlled server-side via the Lambda `Cache-Control` header.
 */


import { fetchAuthSession } from "aws-amplify/auth";
import {
  Agent,
  Job,
  Signal,
  ChatThread,
  CreateAgentRequest,
  UpdateAgentRequest,
  CreateTaskRequest,
  UpdateTaskRequest,
  ExecuteTaskRequest,
  CreateSignalRequest,
  UpdateSignalRequest,
  CreateChatThreadRequest,
  SendChatMessageRequest,
  SendChatMessageResponse,
  ListAgentsResponse,
  ListTasksResponse,
  ListSignalsResponse,
  ListChatThreadsResponse,
  ListAgentsParams,
  ListTasksParams,
  ListSignalsParams,
  JobExecution,
  DashboardStats,
} from "../../types/multi-agent";

const API_BASE_URL = "/prod";

/**
 * Build request headers, injecting the Cognito id token. Throws if no token
 * is available so callers never silently issue anonymous requests.
 */
const getAuthHeaders = async (): Promise<Record<string, string>> => {
  try {
    const session = await fetchAuthSession();
    const token = session.tokens?.idToken?.toString();
    if (!token) {
      throw new Error("No id token in auth session");
    }
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    };
  } catch (error) {
    console.error("Error getting auth headers:", error);
    throw new Error("Authentication required");
  }
};

/**
 * Input to `apiFetch`. Mirrors the parts of `RequestInit` we actually use;
 * keeping the surface narrow avoids callers sneaking in options that would
 * bypass the shared guarantees (e.g. turning caching back on).
 */
interface ApiFetchInit {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  /** Serialised as JSON; set `Content-Type: application/json` automatically. */
  body?: unknown;
  /** Query-string parameters appended to the path. Falsy values are skipped. */
  query?: Record<string, string | number | undefined | null>;
}

/**
 * Build a `${API_BASE_URL}${path}?${query}` URL, skipping undefined/null/empty
 * values so callers don't have to guard each parameter individually.
 */
const buildUrl = (path: string, query?: ApiFetchInit["query"]): string => {
  if (!query) {
    return `${API_BASE_URL}${path}`;
  }
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    search.append(key, String(value));
  }
  const qs = search.toString();
  return qs ? `${API_BASE_URL}${path}?${qs}` : `${API_BASE_URL}${path}`;
};

/**
 * Issue an authenticated request to the platform API.
 *
 * `operation` is a short human-readable verb phrase ("get agent", "send chat
 * message"); it appears in thrown error messages so the UI can present a
 * useful diagnostic without the caller needing to catch-and-rethrow.
 */
const apiFetch = async <T>(
  path: string,
  init: ApiFetchInit,
  operation: string,
): Promise<T> => {
  const headers = await getAuthHeaders();
  const method = init.method ?? "GET";
  const url = buildUrl(path, init.query);

  const response = await fetch(url, {
    method,
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });


  if (!response.ok) {
    const errText = await response.text().catch(() => "");
    throw new Error(
      `Failed to ${operation} (status ${response.status})${errText ? `: ${errText}` : ""}`,
    );
  }

  // Some responses (e.g. a DELETE returning 204) legitimately have no body.
  // Fall back to an empty object cast so callers with a `{ message: string }`
  // return type don't explode on whitespace-only responses.
  const text = await response.text();
  if (!text) {
    return {} as T;
  }

  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error(
      `Failed to ${operation}: response was not valid JSON (status ${response.status})`,
    );
  }
};

export class MultiAgentApiClient {
  // Agent Management -----------------------------------------------------

  async listAgents(params?: ListAgentsParams): Promise<ListAgentsResponse> {
    return apiFetch<ListAgentsResponse>(
      "/agents",
      {
        query: {
          status: params?.status,
          type: params?.type,
          page: params?.page,
          pageSize: params?.pageSize,
        },
      },
      "list agents",
    );
  }

  async getAgent(agentId: string): Promise<Agent> {
    return apiFetch<Agent>(`/agents/${agentId}`, {}, "get agent");
  }

  async createAgent(agentData: CreateAgentRequest): Promise<Agent> {
    return apiFetch<Agent>(
      "/agents",
      {
        method: "POST",
        body: {
          agentName: agentData.agentName,
          agentArn: agentData.agentArn,
          agentType: agentData.agentType,
          description: agentData.description,
          capabilities: agentData.capabilities,
          metadata: agentData.metadata,
        },
      },
      "create agent",
    );
  }

  async updateAgent(
    agentId: string,
    agentData: UpdateAgentRequest,
  ): Promise<Agent> {
    return apiFetch<Agent>(
      `/agents/${agentId}`,
      {
        method: "PUT",
        body: {
          agentName: agentData.agentName,
          description: agentData.description,
          status: agentData.status,
          capabilities: agentData.capabilities,
          metadata: agentData.metadata,
        },
      },
      "update agent",
    );
  }

  async deleteAgent(agentId: string): Promise<{ message: string }> {
    return apiFetch<{ message: string }>(
      `/agents/${agentId}`,
      { method: "DELETE" },
      "delete agent",
    );
  }

  // Job Management -------------------------------------------------------

  async listTasks(params?: ListTasksParams): Promise<ListTasksResponse> {
    return apiFetch<ListTasksResponse>(
      "/jobs",
      {
        query: {
          status: params?.status,
          agentId: params?.agentId,
          jobType: params?.jobType,
          page: params?.page,
          pageSize: params?.pageSize,
        },
      },
      "list jobs",
    );
  }

  async getTask(jobId: string): Promise<Job> {
    return apiFetch<Job>(`/jobs/${jobId}`, {}, "get job");
  }

  async createTask(taskData: CreateTaskRequest): Promise<Job> {
    return apiFetch<Job>(
      "/jobs",
      {
        method: "POST",
        body: {
          agentId: taskData.agentId,
          jobName: taskData.jobName,
          jobType: taskData.jobType,
          prompt: taskData.prompt,
          schedule: taskData.schedule,
        },
      },
      "create job",
    );
  }

  async updateTask(jobId: string, taskData: UpdateTaskRequest): Promise<Job> {
    return apiFetch<Job>(
      `/jobs/${jobId}`,
      {
        method: "PUT",
        body: {
          jobName: taskData.jobName,
          status: taskData.status,
          requiresAction: taskData.requiresAction,
          schedule: taskData.schedule,
        },
      },
      "update job",
    );
  }

  async deleteTask(jobId: string): Promise<{ message: string }> {
    return apiFetch<{ message: string }>(
      `/jobs/${jobId}`,
      { method: "DELETE" },
      "delete job",
    );
  }

  async executeTask(
    jobId: string,
    executionData?: ExecuteTaskRequest,
  ): Promise<JobExecution> {
    return apiFetch<JobExecution>(
      `/jobs/${jobId}/execute`,
      {
        method: "POST",
        body: { humanResponse: executionData?.humanResponse },
      },
      "execute job",
    );
  }

  // Conversation Management ---------------------------------------------

  async getConversation(
    sessionId: string,
    params?: { limit?: number; offset?: number },
  ): Promise<{
    sessionId: string;
    agentId: string;
    agentName: string;
    messages: Array<{
      type: "human" | "ai";
      content: string;
      timestamp: string;
    }>;
    totalMessages: number;
    hasMore: boolean;
    createdAt: string;
    updatedAt: string;
  }> {
    return apiFetch(
      `/conversations/${sessionId}`,
      {
        query: { limit: params?.limit, offset: params?.offset },
      },
      "get conversation",
    );
  }

  async getConversationContext(sessionId: string): Promise<{
    sessionId: string;
    formattedContext: string;
    messageCount: number;
    lastMessageTime: string | null;
  }> {
    return apiFetch(
      `/conversations/${sessionId}/context`,
      {},
      "get conversation context",
    );
  }

  async addConversationMessage(
    sessionId: string,
    messageData: {
      type: "human" | "ai";
      content: string;
      agentId?: string;
    },
  ): Promise<{ message: string; messageCount: number; sessionId: string }> {
    return apiFetch(
      `/conversations/${sessionId}`,
      {
        method: "POST",
        body: {
          type: messageData.type,
          content: messageData.content,
          agentId: messageData.agentId,
        },
      },
      "add conversation message",
    );
  }

  // Signal Management ----------------------------------------------------

  async listSignals(params?: ListSignalsParams): Promise<ListSignalsResponse> {
    return apiFetch<ListSignalsResponse>(
      "/signals",
      {
        query: {
          status: params?.status,
          signalType: params?.signalType,
          agentId: params?.agentId,
          page: params?.page,
          pageSize: params?.pageSize,
        },
      },
      "list signals",
    );
  }

  async getSignal(signalId: string): Promise<Signal> {
    return apiFetch<Signal>(`/signals/${signalId}`, {}, "get signal");
  }

  async createSignal(signalData: CreateSignalRequest): Promise<Signal> {
    return apiFetch<Signal>(
      "/signals",
      {
        method: "POST",
        body: {
          signalName: signalData.signalName,
          signalType: signalData.signalType,
          agentId: signalData.agentId,
          description: signalData.description,
          configuration: signalData.configuration,
          enabled: signalData.enabled,
          autoExecute: signalData.autoExecute,
        },
      },
      "create signal",
    );
  }

  async updateSignal(
    signalId: string,
    signalData: UpdateSignalRequest,
  ): Promise<Signal> {
    // Only send fields the caller actually provided. `update_signal`
    // on the backend checks `if "<field>" in body` to decide whether to
    // write it, so sending `undefined` would still trip that check and
    // overwrite real values with `null`. `autoExecute` specifically is
    // a boolean: we cannot substitute `||` / default handling for it
    // because `false` is a valid, deliberate value the user toggled.
    const body: Record<string, unknown> = {};
    if (signalData.signalName !== undefined)
      body.signalName = signalData.signalName;
    if (signalData.description !== undefined)
      body.description = signalData.description;
    if (signalData.configuration !== undefined)
      body.configuration = signalData.configuration;
    if (signalData.enabled !== undefined) body.enabled = signalData.enabled;
    if (signalData.autoExecute !== undefined)
      body.autoExecute = signalData.autoExecute;
    return apiFetch<Signal>(
      `/signals/${signalId}`,
      {
        method: "PUT",
        body,
      },
      "update signal",
    );
  }

  async deleteSignal(signalId: string): Promise<{ message: string }> {
    return apiFetch<{ message: string }>(
      `/signals/${signalId}`,
      { method: "DELETE" },
      "delete signal",
    );
  }

  // Chat Thread Management ----------------------------------------------

  async listChatThreads(): Promise<ListChatThreadsResponse> {
    return apiFetch<ListChatThreadsResponse>(
      "/chats",
      {},
      "list chat threads",
    );
  }

  async createChatThread(
    request: CreateChatThreadRequest,
  ): Promise<ChatThread> {
    return apiFetch<ChatThread>(
      "/chats",
      {
        method: "POST",
        body: { agentId: request.agentId, title: request.title },
      },
      "create chat thread",
    );
  }

  async getChatThread(threadId: string): Promise<ChatThread> {
    return apiFetch<ChatThread>(`/chats/${threadId}`, {}, "get chat thread");
  }

  async deleteChatThread(threadId: string): Promise<{ message: string }> {
    return apiFetch<{ message: string }>(
      `/chats/${threadId}`,
      { method: "DELETE" },
      "delete chat thread",
    );
  }

  async sendChatMessage(
    threadId: string,
    request: SendChatMessageRequest,
  ): Promise<SendChatMessageResponse> {
    return apiFetch<SendChatMessageResponse>(
      `/chats/${threadId}/messages`,
      {
        method: "POST",
        body: { message: request.message },
      },
      "send chat message",
    );
  }

  // Dashboard ------------------------------------------------------------

  async getDashboardStats(): Promise<DashboardStats> {
    // Fan out to the two underlying lists in parallel. This stays in the
    // client (rather than a dedicated server endpoint) because the volumes
    // involved are small and the aggregation is purely presentational.
    const [agentsResponse, tasksResponse] = await Promise.all([
      this.listAgents({ pageSize: 1000 }),
      this.listTasks({ pageSize: 1000 }),
    ]);

    const agents = agentsResponse.agents;
    const jobs = tasksResponse.jobs;

    const totalAgents = agents.length;
    const activeAgents = agents.filter((a) => a.status === "active").length;
    const totalTasks = jobs.length;
    const runningTasks = jobs.filter(
      (t) => t.status === "busy" || t.status === "scheduled_for_execution",
    ).length;
    const completedTasks = jobs.filter((t) => t.status === "completed").length;
    const interruptedTasks = jobs.filter(
      (t) => t.status === "interrupted",
    ).length;

    const recentActivity = jobs
      .sort(
        (a, b) =>
          new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
      )
      .slice(0, 10)
      .map((job) => {
        const agent = agents.find((a) => a.agentId === job.agentId);
        let type: DashboardStats["recentActivity"][0]["type"] = "task_created";
        let message = `Job "${job.jobName}" was created`;

        switch (job.status) {
          case "completed":
            type = "task_completed";
            message = `Job "${job.jobName}" completed successfully`;
            break;
          case "interrupted":
            type = "task_interrupted";
            message = `Job "${job.jobName}" requires human input`;
            break;
          case "error":
            type = "task_error";
            message = `Job "${job.jobName}" encountered an error`;
            break;
        }

        return {
          id: job.jobId,
          type,
          message,
          timestamp: job.updatedAt,
          agentName: agent?.agentName,
          jobName: job.jobName,
        };
      });

    return {
      totalAgents,
      activeAgents,
      totalTasks,
      runningTasks,
      completedTasks,
      interruptedTasks,
      recentActivity,
    };
  }
}
