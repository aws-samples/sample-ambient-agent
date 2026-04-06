// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { fetchAuthSession } from "aws-amplify/auth";
import {
  Agent,
  Job,
  Signal,
  CreateAgentRequest,
  UpdateAgentRequest,
  CreateTaskRequest,
  UpdateTaskRequest,
  ExecuteTaskRequest,
  CreateSignalRequest,
  UpdateSignalRequest,
  ListAgentsResponse,
  ListTasksResponse,
  ListSignalsResponse,
  ListAgentsParams,
  ListTasksParams,
  ListSignalsParams,
  JobExecution,
  DashboardStats,
} from "../../types/multi-agent";

const API_BASE_URL = "/prod";

/**
 * Get authenticated headers for API requests
 */
const getAuthHeaders = async (): Promise<HeadersInit> => {
  try {
    const session = await fetchAuthSession();
    const token = session.tokens?.idToken?.toString();

    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    };
  } catch (error) {
    console.error("Error getting auth headers:", error);
    throw new Error("Authentication required");
  }
};

export class MultiAgentApiClient {
  // Agent Management Methods
  async listAgents(params?: ListAgentsParams): Promise<ListAgentsResponse> {
    const headers = await getAuthHeaders();
    const searchParams = new URLSearchParams();

    if (params?.status) searchParams.append("status", params.status);
    if (params?.type) searchParams.append("type", params.type);
    if (params?.page) searchParams.append("page", params.page.toString());
    if (params?.pageSize)
      searchParams.append("pageSize", params.pageSize.toString());

    const url = `${API_BASE_URL}/agents${searchParams.toString() ? `?${searchParams.toString()}` : ""}`;

    const response = await fetch(url, {
      method: "GET",
      headers,
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to get agents: ${error}`);
    }

    return response.json();
  }

  async getAgent(agentId: string): Promise<Agent> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/agents/${agentId}`;

    const response = await fetch(url, {
      method: "GET",
      headers,
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to get agent: ${error}`);
    }

    return response.json();
  }

  async createAgent(agentData: CreateAgentRequest): Promise<Agent> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/agents`;

    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({
        agentName: agentData.agentName,
        agentArn: agentData.agentArn,
        agentType: agentData.agentType,
        description: agentData.description,
        capabilities: agentData.capabilities,
        metadata: agentData.metadata,
      }),
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to create agent: ${error}`);
    }

    return response.json();
  }

  async updateAgent(
    agentId: string,
    agentData: UpdateAgentRequest,
  ): Promise<Agent> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/agents/${agentId}`;

    const response = await fetch(url, {
      method: "PUT",
      headers,
      body: JSON.stringify({
        agentName: agentData.agentName,
        description: agentData.description,
        status: agentData.status,
        capabilities: agentData.capabilities,
        metadata: agentData.metadata,
      }),
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to update agent: ${error}`);
    }

    return response.json();
  }

  async deleteAgent(agentId: string): Promise<{ message: string }> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/agents/${agentId}`;

    const response = await fetch(url, {
      method: "DELETE",
      headers,
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to delete agent: ${error}`);
    }

    return response.json();
  }

  // Job Management Methods
  async listTasks(params?: ListTasksParams): Promise<ListTasksResponse> {
    const headers = await getAuthHeaders();
    const searchParams = new URLSearchParams();

    if (params?.status) searchParams.append("status", params.status);
    if (params?.agentId) searchParams.append("agentId", params.agentId);
    if (params?.jobType) searchParams.append("jobType", params.jobType);
    if (params?.page) searchParams.append("page", params.page.toString());
    if (params?.pageSize)
      searchParams.append("pageSize", params.pageSize.toString());

    const url = `${API_BASE_URL}/jobs${searchParams.toString() ? `?${searchParams.toString()}` : ""}`;

    const response = await fetch(url, {
      method: "GET",
      headers,
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to get jobs: ${error}`);
    }

    return response.json();
  }

  async getTask(jobId: string): Promise<Job> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/jobs/${jobId}`;

    const response = await fetch(url, {
      method: "GET",
      headers,
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to get job: ${error}`);
    }

    return response.json();
  }

  async createTask(taskData: CreateTaskRequest): Promise<Job> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/jobs`;

    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({
        agentId: taskData.agentId,
        jobName: taskData.jobName,
        jobType: taskData.jobType,
        prompt: taskData.prompt,
        schedule: taskData.schedule,
      }),
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to create job: ${error}`);
    }

    return response.json();
  }

  async updateTask(jobId: string, taskData: UpdateTaskRequest): Promise<Job> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/jobs/${jobId}`;

    const response = await fetch(url, {
      method: "PUT",
      headers,
      body: JSON.stringify({
        jobName: taskData.jobName,
        status: taskData.status,
        requiresAction: taskData.requiresAction,
        schedule: taskData.schedule,
      }),
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to update job: ${error}`);
    }

    return response.json();
  }

  async deleteTask(jobId: string): Promise<{ message: string }> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/jobs/${jobId}`;

    const response = await fetch(url, {
      method: "DELETE",
      headers,
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to delete job: ${error}`);
    }

    return response.json();
  }

  async executeTask(
    jobId: string,
    executionData?: ExecuteTaskRequest,
  ): Promise<JobExecution> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/jobs/${jobId}/execute`;

    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({
        humanResponse: executionData?.humanResponse,
      }),
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to execute job: ${error}`);
    }

    return response.json();
  }

  // Conversation Management Methods
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
    const headers = await getAuthHeaders();
    const searchParams = new URLSearchParams();

    if (params?.limit) searchParams.append("limit", params.limit.toString());
    if (params?.offset) searchParams.append("offset", params.offset.toString());

    const url = `${API_BASE_URL}/conversations/${sessionId}${searchParams.toString() ? `?${searchParams.toString()}` : ""}`;

    const response = await fetch(url, {
      method: "GET",
      headers,
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to get conversation: ${error}`);
    }

    return response.json();
  }

  async getConversationContext(sessionId: string): Promise<{
    sessionId: string;
    formattedContext: string;
    messageCount: number;
    lastMessageTime: string | null;
  }> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/conversations/${sessionId}/context`;

    const response = await fetch(url, {
      method: "GET",
      headers,
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to get conversation context: ${error}`);
    }

    return response.json();
  }

  async addConversationMessage(
    sessionId: string,
    messageData: {
      type: "human" | "ai";
      content: string;
      agentId?: string;
    },
  ): Promise<{
    message: string;
    messageCount: number;
    sessionId: string;
  }> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/conversations/${sessionId}`;

    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({
        type: messageData.type,
        content: messageData.content,
        agentId: messageData.agentId,
      }),
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to add conversation message: ${error}`);
    }

    return response.json();
  }

  // Signal Management Methods
  async listSignals(params?: ListSignalsParams): Promise<ListSignalsResponse> {
    const headers = await getAuthHeaders();
    const searchParams = new URLSearchParams();

    if (params?.status) searchParams.append("status", params.status);
    if (params?.signalType)
      searchParams.append("signalType", params.signalType);
    if (params?.agentId) searchParams.append("agentId", params.agentId);
    if (params?.page) searchParams.append("page", params.page.toString());
    if (params?.pageSize)
      searchParams.append("pageSize", params.pageSize.toString());

    const url = `${API_BASE_URL}/signals${searchParams.toString() ? `?${searchParams.toString()}` : ""}`;

    const response = await fetch(url, {
      method: "GET",
      headers,
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to get signals: ${error}`);
    }

    return response.json();
  }

  async getSignal(signalId: string): Promise<Signal> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/signals/${signalId}`;

    const response = await fetch(url, {
      method: "GET",
      headers,
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to get signal: ${error}`);
    }

    return response.json();
  }

  async createSignal(signalData: CreateSignalRequest): Promise<Signal> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/signals`;

    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({
        signalName: signalData.signalName,
        signalType: signalData.signalType,
        agentId: signalData.agentId,
        description: signalData.description,
        configuration: signalData.configuration,
        enabled: signalData.enabled,
      }),
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to create signal: ${error}`);
    }

    return response.json();
  }

  async updateSignal(
    signalId: string,
    signalData: UpdateSignalRequest,
  ): Promise<Signal> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/signals/${signalId}`;

    const response = await fetch(url, {
      method: "PUT",
      headers,
      body: JSON.stringify({
        signalName: signalData.signalName,
        description: signalData.description,
        configuration: signalData.configuration,
        enabled: signalData.enabled,
      }),
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to update signal: ${error}`);
    }

    return response.json();
  }

  async deleteSignal(signalId: string): Promise<{ message: string }> {
    const headers = await getAuthHeaders();
    const url = `${API_BASE_URL}/signals/${signalId}`;

    const response = await fetch(url, {
      method: "DELETE",
      headers,
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to delete signal: ${error}`);
    }

    return response.json();
  }

  // Dashboard Methods
  async getDashboardStats(): Promise<DashboardStats> {
    // Get agents and jobs in parallel
    const [agentsResponse, tasksResponse] = await Promise.all([
      this.listAgents({ pageSize: 1000 }), // Get all agents for stats
      this.listTasks({ pageSize: 1000 }), // Get all jobs for stats
    ]);

    const agents = agentsResponse.agents;
    const jobs = tasksResponse.jobs;

    // Calculate statistics
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

    // Generate recent activity (simplified - in real implementation this might come from a separate endpoint)
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
