// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Multi-Agent Platform Types

export interface Agent {
  agentId: string;
  userId: string;
  agentName: string;
  agentArn: string;
  agentType: "user_initiated" | "scheduled" | "ambient";
  description: string;
  capabilities: string; // Free-form text for custom capabilities
  status: "active" | "inactive" | "error";
  platformCompatibility: "full" | "limited"; // Platform feature compatibility flag
  createdAt: string;
  updatedAt: string;
  metadata?: Record<string, any>;
}

// Deprecated - keeping for backward compatibility, will be replaced with free-form capabilities
export type AgentCapability =
  | "human_interruption"
  | "conversation_continuity"
  | "scheduled_execution";

export interface Job {
  jobId: string;
  userId: string;
  agentId: string;
  jobName: string;
  jobType: "user_initiated" | "scheduled" | "signal_triggered";
  status: TaskStatus;

  sessionId: string;
  prompt: string;
  requiresAction: boolean;
  createdAt: string;
  updatedAt: string;
  schedule?: TaskSchedule;
  nextRun?: string;
  finalResult?: string;
  errorMessage?: string;
  executionLogs?: TaskExecutionLog[];
}

export type TaskStatus =
  | "idle"
  | "busy"
  | "completed"
  | "interrupted"
  | "error"
  | "scheduled_for_execution";

export interface TaskSchedule {
  type: "one_time" | "recurring";
  pattern: string;
  enabled: boolean;
  nextRun?: string;
}

export interface JobExecution {
  status: "completed" | "interrupted" | "error";
  result: string;
  requiresAction?: boolean;
  sessionId: string;
  executionTime?: number;
  error?: string;
}

export interface TaskExecutionLog {
  jobId: string;
  level: "info" | "warning" | "error" | "success";
  message: string;
  timestamp: string;
}

// API Request/Response Types
export interface CreateAgentRequest {
  agentName: string;
  agentArn: string;
  agentType: Agent["agentType"];
  description?: string;
  capabilities?: string; // Changed to string to match Agent interface
  metadata?: Record<string, any>;
}

export interface UpdateAgentRequest {
  agentName?: string;
  description?: string;
  status?: Agent["status"];
  capabilities?: AgentCapability[];
  metadata?: Record<string, any>;
}

export interface CreateTaskRequest {
  agentId: string;
  jobName: string;
  jobType: Job["jobType"];
  prompt: string;
  schedule?: TaskSchedule;
}

export interface UpdateTaskRequest {
  jobName?: string;
  status?: TaskStatus;
  requiresAction?: boolean;
  schedule?: TaskSchedule;
}

export interface ExecuteTaskRequest {
  humanResponse?: string;
}

// List Response Types
export interface ListAgentsResponse {
  agents: Agent[];
  total: number;
  page: number;
  pageSize: number;
}

export interface ListTasksResponse {
  jobs: Job[];
  total: number;
  page: number;
  pageSize: number;
}

// Query Parameters
export interface ListAgentsParams {
  status?: string;
  type?: string;
  page?: number;
  pageSize?: number;
}

export interface ListTasksParams {
  status?: string;
  agentId?: string;
  jobType?: string;
  page?: number;
  pageSize?: number;
}

// UI State Types
export interface AgentFormData {
  agentName: string;
  agentArn: string;
  agentType: Agent["agentType"];
  description: string;
  capabilities: string[]; // Array of capability strings for tag-based input
}

export interface TaskFormData {
  agentId: string;
  jobName: string;
  jobType: Job["jobType"];
  prompt: string;
  schedule?: {
    type: TaskSchedule["type"];
    pattern: string;
    enabled: boolean;
  };
}

// Dashboard Statistics
export interface DashboardStats {
  totalAgents: number;
  activeAgents: number;
  totalTasks: number;
  runningTasks: number;
  completedTasks: number;
  interruptedTasks: number;
  recentActivity: RecentActivity[];
}

export interface RecentActivity {
  id: string;
  type:
    | "agent_registered"
    | "task_created"
    | "task_completed"
    | "task_interrupted"
    | "task_error";
  message: string;
  timestamp: string;
  agentName?: string;
  jobName?: string;
}

// Signal Management Types
export interface Signal {
  signalId: string;
  userId: string;
  signalName: string;
  signalType: "s3_file_upload";
  agentId: string;
  description: string;
  configuration: SignalConfiguration;
  enabled: boolean;
  // When true, the signal processor enqueues the created job onto the
  // worker queue immediately, so the agent fires without any user click.
  // When false (default), the job lands in "idle" and the user runs it
  // from the Jobs page - useful for review-first workflows.
  autoExecute?: boolean;
  triggerCount?: number;
  lastTriggered?: string;
  createdAt: string;
  updatedAt: string;
  recentActivity?: SignalActivity[];
}

export interface SignalConfiguration {
  bucketName?: string;
  prefix?: string;
  suffix?: string;
  fileTypes?: string[];
  eventPattern?: Record<string, any>;
}

export interface SignalActivity {
  id: string;
  timestamp: string;
  event: string;
  payload: Record<string, any>;
  status: "success" | "error";
  error?: string;
}

export interface CreateSignalRequest {
  signalName: string;
  signalType: Signal["signalType"];
  agentId: string;
  description?: string;
  configuration: SignalConfiguration;
  enabled?: boolean;
  autoExecute?: boolean;
}

export interface UpdateSignalRequest {
  signalName?: string;
  description?: string;
  configuration?: SignalConfiguration;
  enabled?: boolean;
  autoExecute?: boolean;
}

export interface ListSignalsResponse {
  signals: Signal[];
  total: number;
  page: number;
  pageSize: number;
}

export interface ListSignalsParams {
  status?: string;
  signalType?: string;
  agentId?: string;
  page?: number;
  pageSize?: number;
}

export interface SignalFormData {
  signalName: string;
  signalType: Signal["signalType"];
  agentId: string;
  description: string;
  configuration: SignalConfiguration;
  enabled: boolean;
  autoExecute: boolean;
}

// -----------------------------------------------------------------------
// Chat Types
//
// A "chat thread" is a free-form conversation with a registered agent,
// independent from the Jobs workflow. Threads own metadata only; messages
// live in the shared conversation-store table keyed by sessionId, so the
// existing /conversations/{sessionId} GET endpoint serves chat history too.
// -----------------------------------------------------------------------

export type ChatThreadStatus = "idle" | "busy" | "awaiting_human";

export interface ChatThread {
  threadId: string;
  userId: string;
  agentId: string;
  agentName: string;
  title: string;
  sessionId: string;
  status: ChatThreadStatus;
  lastMessagePreview?: string;
  createdAt: string;
  updatedAt: string;
}

export interface CreateChatThreadRequest {
  agentId: string;
  title?: string;
}

export interface SendChatMessageRequest {
  message: string;
}

export interface ListChatThreadsResponse {
  threads: ChatThread[];
  total: number;
}

export interface SendChatMessageResponse {
  threadId: string;
  sessionId: string;
  status: ChatThreadStatus;
  message: string;
}
