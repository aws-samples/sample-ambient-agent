// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import {
  Container,
  Header,
  SpaceBetween,
  Button,
  Table,
  Box,
  StatusIndicator,
  Badge,
  Modal,
  Form,
  FormField,
  Input,
  Textarea,
  Select,
  Alert,
  Pagination,
  TextFilter,
  Tabs,
  Grid,
  Toggle,
  DatePicker,
  TimeInput,
  Link,
  Icon,
} from "@cloudscape-design/components";
import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useSearchParams, useNavigate } from "react-router-dom";
import BaseAppLayout from "../components/base-app-layout";
import ConversationHistory, {
  InlineConversationHistory,
} from "../components/conversation-history";
import ChatPanel, { ChatPanelStatus } from "../components/chat-panel";
import { apiClient } from "../common/api-client/api-clients";
import {
  Job,
  CreateTaskRequest,
  TaskFormData,
  Agent,
} from "../types/multi-agent";
import { TaskExecutionLog } from "../types/multi-agent";

/**
 * Map a job's status + requiresAction flag onto the ChatPanel status states.
 * - `busy` / `scheduled_for_execution` -> "busy" (agent is working)
 * - interrupted or requiresAction=true -> "awaiting_human"
 * - otherwise                          -> "idle"
 */
function jobStatusToChatStatus(job: Job): ChatPanelStatus {
  if (job.status === "busy" || job.status === "scheduled_for_execution") {
    return "busy";
  }
  if (job.status === "interrupted" || job.requiresAction) {
    return "awaiting_human";
  }
  return "idle";
}

export default function TasksPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedItems, setSelectedItems] = useState<Job[]>([]);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showExecuteModal, setShowExecuteModal] = useState(false);
  const [executingTask, setExecutingTask] = useState<Job | null>(null);
  const [filteringText, setFilteringText] = useState("");
  const [currentPageIndex, setCurrentPageIndex] = useState(1);
  const [activeTabId, setActiveTabId] = useState("all");
  const [recentlyViewedTasks, setRecentlyViewedTasks] = useState<Job[]>([]);
  const [taskDetailTabId, setTaskDetailTabId] = useState("details");
  const pageSize = 10;

  // Get jobId from URL params if present
  const taskIdFromUrl = searchParams.get("jobId");

  // Form state for creating new job
  const [formData, setFormData] = useState<TaskFormData>({
    agentId: "",
    jobName: "",
    jobType: "user_initiated",
    prompt: "",
    schedule: undefined,
  });

  const [humanResponse, setHumanResponse] = useState("");

  // Handle job viewing and recently viewed functionality
  const handleViewTask = (job: Job) => {
    // Add to recently viewed (avoid duplicates and limit to 5)
    setRecentlyViewedTasks((prev) => {
      const filtered = prev.filter((t) => t.jobId !== job.jobId);
      return [job, ...filtered].slice(0, 5);
    });

    // Navigate to job details
    setSearchParams({ jobId: job.jobId });
  };

  const handleBackToTasks = () => {
    setSearchParams({});
  };

  // Fetch jobs
  const {
    data: tasksData,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["jobs", activeTabId, filteringText, currentPageIndex],
    queryFn: () => {
      // For specific status tabs, use the status filter
      let statusFilter = activeTabId === "all" ? undefined : activeTabId;

      // For the busy tab, we need to get all jobs since it includes multiple statuses
      if (activeTabId === "busy") {
        statusFilter = undefined;
      }

      console.log(
        "Fetching jobs with status filter:",
        statusFilter,
        "for tab:",
        activeTabId,
      );

      return apiClient.multiAgentClient.listTasks({
        status: statusFilter,
        page: currentPageIndex,
        pageSize: pageSize,
      });
    },
    staleTime: 5 * 60 * 1000, // 5 minutes - data is fresh for 5 minutes
    refetchOnWindowFocus: false, // Don't refetch on window focus
    refetchInterval: false, // Disable all polling - rely on invalidation only
  });

  // Fetch all jobs for tab counts (without pagination)
  const { data: allTasksData } = useQuery({
    queryKey: ["all-jobs-for-counts"],
    queryFn: () => apiClient.multiAgentClient.listTasks({ pageSize: 1000 }),
    staleTime: 5 * 60 * 1000, // 5 minutes - data is fresh for 5 minutes
    refetchOnWindowFocus: false, // Don't refetch on window focus
    refetchInterval: false, // Disable all polling - rely on invalidation only
  });

  // Fetch agents for dropdown
  const { data: agentsData } = useQuery({
    queryKey: ["agents-for-jobs"],
    queryFn: () => apiClient.multiAgentClient.listAgents({ pageSize: 100 }),
  });

  // Fetch specific job if jobId in URL
  const { data: specificTask } = useQuery({
    queryKey: ["job", taskIdFromUrl],
    queryFn: () =>
      taskIdFromUrl ? apiClient.multiAgentClient.getTask(taskIdFromUrl) : null,
    enabled: !!taskIdFromUrl,
  });

  // Get execution logs from the job data (stored in DynamoDB)
  const taskLogs = (specificTask?.executionLogs as TaskExecutionLog[]) || [];

  // Auto-scroll logs when new ones arrive
  useEffect(() => {
    if (taskLogs.length > 0 && taskDetailTabId === "logs") {
      const logsContainer = document.getElementById("job-logs-container");
      if (logsContainer) {
        logsContainer.scrollTop = logsContainer.scrollHeight;
      }
    }
  }, [taskLogs, taskDetailTabId]);

  // Poll for job updates when viewing a specific job to get fresh logs
  useEffect(() => {
    if (!taskIdFromUrl) return;

    // Stop polling if the job is in a terminal state
    const terminalStatuses = ["completed", "error", "idle"];
    if (
      specificTask &&
      !specificTask.requiresAction &&
      terminalStatuses.includes(specificTask.status)
    ) {
      return;
    }

    const interval = setInterval(() => {
      queryClient.invalidateQueries({ queryKey: ["job", taskIdFromUrl] });
    }, 2000); // Poll every 2 seconds

    return () => clearInterval(interval);
  }, [taskIdFromUrl, queryClient, specificTask]);

  // Create job mutation
  const createTaskMutation = useMutation({
    mutationFn: (taskData: CreateTaskRequest) =>
      apiClient.multiAgentClient.createTask(taskData),
    onSuccess: (newTask) => {
      // Add the new job to the main jobs query
      queryClient.setQueryData(
        ["jobs", activeTabId, filteringText, currentPageIndex],
        (old: any) => {
          if (!old) return old;
          return {
            ...old,
            jobs: [newTask, ...old.jobs],
            total: old.total + 1,
          };
        },
      );

      // Add the new job to the all-jobs-for-counts query
      queryClient.setQueryData(["all-jobs-for-counts"], (old: any) => {
        if (!old) return old;
        return {
          ...old,
          jobs: [newTask, ...old.jobs],
          total: old.total + 1,
        };
      });

      // Also invalidate to ensure fresh data on next fetch
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["all-jobs-for-counts"] });

      // CRITICAL: Invalidate workflows page queries for cross-page real-time updates
      queryClient.invalidateQueries({ queryKey: ["dashboard-stats"] });
      queryClient.invalidateQueries({ queryKey: ["recent-jobs"] });

      setShowCreateModal(false);
      resetForm();
    },
  });

  // Execute job mutation
  const executeTaskMutation = useMutation({
    mutationFn: ({
      jobId,
      humanResponse,
    }: {
      jobId: string;
      humanResponse?: string;
    }) => {
      return apiClient.multiAgentClient.executeTask(jobId, { humanResponse });
    },
    onMutate: async ({ jobId }) => {
      // Close modal immediately for better UX
      setShowExecuteModal(false);
      setHumanResponse("");
      setExecutingTask(null);

      // Cancel any outgoing refetches
      await queryClient.cancelQueries({ queryKey: ["jobs"] });
      await queryClient.cancelQueries({ queryKey: ["all-jobs-for-counts"] });
      if (taskIdFromUrl) {
        await queryClient.cancelQueries({ queryKey: ["job", taskIdFromUrl] });
      }

      // Snapshot the previous values for rollback
      const previousTasks = queryClient.getQueryData([
        "jobs",
        activeTabId,
        filteringText,
        currentPageIndex,
      ]);
      const previousAllTasks = queryClient.getQueryData([
        "all-jobs-for-counts",
      ]);
      const previousSpecificTask = taskIdFromUrl
        ? queryClient.getQueryData(["job", taskIdFromUrl])
        : null;

      // Optimistically update job status to 'busy'
      const updateTask = (job: any) =>
        job.jobId === jobId
          ? {
              ...job,
              status: "busy",
              requiresAction: false,
              updatedAt: new Date().toISOString(),
            }
          : job;

      queryClient.setQueryData(
        ["jobs", activeTabId, filteringText, currentPageIndex],
        (old: any) => {
          if (!old) return old;
          return {
            ...old,
            jobs: old.jobs.map(updateTask),
          };
        },
      );

      queryClient.setQueryData(["all-jobs-for-counts"], (old: any) => {
        if (!old) return old;
        return {
          ...old,
          jobs: old.jobs.map(updateTask),
        };
      });

      if (taskIdFromUrl && taskIdFromUrl === jobId) {
        queryClient.setQueryData(["job", taskIdFromUrl], (old: any) => {
          if (!old) return old;
          return {
            ...old,
            status: "busy",
            requiresAction: false,
            updatedAt: new Date().toISOString(),
          };
        });
      }

      return { previousTasks, previousAllTasks, previousSpecificTask };
    },
    onSuccess: (_, variables) => {
      // Invalidate queries to get fresh data from backend
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["all-jobs-for-counts"] });

      if (taskIdFromUrl) {
        queryClient.invalidateQueries({ queryKey: ["job", taskIdFromUrl] });
      }

      // CRITICAL: Invalidate conversation history when job execution completes
      // This ensures the conversation history updates when job status changes
      const job =
        selectedItems.find((t) => t.jobId === variables.jobId) || specificTask;
      if (job?.sessionId) {
        queryClient.invalidateQueries({
          queryKey: ["conversation", job.sessionId],
        });
        queryClient.invalidateQueries({
          queryKey: ["conversation-context", job.sessionId],
        });
      }

      // CRITICAL: Invalidate workflows page queries for cross-page real-time updates
      queryClient.invalidateQueries({ queryKey: ["dashboard-stats"] });
      queryClient.invalidateQueries({ queryKey: ["recent-jobs"] });
    },
    onError: (error, variables, context) => {
      // Rollback optimistic updates on error
      if (context?.previousTasks) {
        queryClient.setQueryData(
          ["jobs", activeTabId, filteringText, currentPageIndex],
          context.previousTasks,
        );
      }
      if (context?.previousAllTasks) {
        queryClient.setQueryData(
          ["all-jobs-for-counts"],
          context.previousAllTasks,
        );
      }
      if (context?.previousSpecificTask && taskIdFromUrl) {
        queryClient.setQueryData(
          ["job", taskIdFromUrl],
          context.previousSpecificTask,
        );
      }

      // Reopen modal on error so user can retry
      setShowExecuteModal(true);
      setExecutingTask(
        selectedItems.find((t) => t.jobId === variables.jobId) || null,
      );

      console.error("Job execution failed:", error);
    },
  });

  // Delete job mutation
  const deleteTaskMutation = useMutation({
    mutationFn: (jobId: string) => apiClient.multiAgentClient.deleteTask(jobId),
    onSuccess: (_, deletedTaskId) => {
      // Update the main jobs query by removing the deleted job
      queryClient.setQueryData(
        ["jobs", activeTabId, filteringText, currentPageIndex],
        (old: any) => {
          if (!old) return old;
          return {
            ...old,
            jobs: old.jobs.filter((job: any) => job.jobId !== deletedTaskId),
            total: old.total - 1,
          };
        },
      );

      // Update the all-jobs-for-counts query by removing the deleted job
      queryClient.setQueryData(["all-jobs-for-counts"], (old: any) => {
        if (!old) return old;
        return {
          ...old,
          jobs: old.jobs.filter((job: any) => job.jobId !== deletedTaskId),
          total: old.total - 1,
        };
      });

      // Remove deleted job from recently viewed jobs
      setRecentlyViewedTasks((prev) =>
        prev.filter((job) => job.jobId !== deletedTaskId),
      );

      // Also invalidate to ensure fresh data on next fetch
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["all-jobs-for-counts"] });

      // CRITICAL: Invalidate workflows page queries for cross-page real-time updates
      queryClient.invalidateQueries({ queryKey: ["dashboard-stats"] });
      queryClient.invalidateQueries({ queryKey: ["recent-jobs"] });

      setSelectedItems([]);
    },
  });

  const resetForm = () => {
    setFormData({
      agentId: "",
      jobName: "",
      jobType: "user_initiated",
      prompt: "",
      schedule: undefined,
    });
  };

  const handleCreateTask = () => {
    createTaskMutation.mutate({
      agentId: formData.agentId,
      jobName: formData.jobName,
      jobType: formData.jobType,
      prompt: formData.prompt,
      schedule: formData.schedule,
    });
  };

  const handleExecuteTask = (job: Job) => {
    // Clear all previous modal state first to prevent contamination
    setHumanResponse("");
    setExecutingTask(job);

    // Reset any previous execution results
    executeTaskMutation.reset();

    setShowExecuteModal(true);
  };

  const handleRunExecution = () => {
    if (executingTask) {
      executeTaskMutation.mutate({
        jobId: executingTask.jobId,
        humanResponse: humanResponse || undefined,
      });
    }
  };

  const handleDeleteSelected = () => {
    selectedItems.forEach((job) => {
      deleteTaskMutation.mutate(job.jobId);
    });
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "completed":
        return "success";
      case "busy":
      case "scheduled_for_execution":
        return "in-progress";
      case "interrupted":
        return "warning";
      case "error":
        return "error";
      default:
        return "stopped";
    }
  };

  const getAgentName = (agentId: string) => {
    return (
      agentsData?.agents.find((a: Agent) => a.agentId === agentId)?.agentName ||
      "Unknown Agent"
    );
  };

  const formatTimestamp = (timestamp: string) => {
    return new Date(timestamp).toLocaleString();
  };

  const agentOptions =
    agentsData?.agents.map((agent) => ({
      label: agent.agentName,
      value: agent.agentId,
    })) || [];

  const taskTypeOptions = [
    { label: "User Initiated", value: "user_initiated" },
    { label: "Scheduled", value: "scheduled" },
  ];

  // Apply filtering to the jobs
  const filteredTasks =
    tasksData?.jobs.filter((job) => {
      // Apply status filtering based on active tab
      if (activeTabId !== "all") {
        if (activeTabId === "busy") {
          // For busy tab, include both 'busy' and 'scheduled_for_execution'
          if (
            !(job.status === "busy" || job.status === "scheduled_for_execution")
          ) {
            return false;
          }
        } else {
          // For other tabs, exact status match
          if (job.status !== activeTabId) {
            return false;
          }
        }
      }

      // Apply text filtering
      if (filteringText) {
        return (
          job.jobName.toLowerCase().includes(filteringText.toLowerCase()) ||
          job.prompt.toLowerCase().includes(filteringText.toLowerCase()) ||
          getAgentName(job.agentId)
            .toLowerCase()
            .includes(filteringText.toLowerCase())
        );
      }

      return true;
    }) || [];

  const tabCounts = {
    all: allTasksData?.total || 0,
    idle: allTasksData?.jobs.filter((t) => t.status === "idle").length || 0,
    busy:
      allTasksData?.jobs.filter(
        (t) => t.status === "busy" || t.status === "scheduled_for_execution",
      ).length || 0,
    completed:
      allTasksData?.jobs.filter((t) => t.status === "completed").length || 0,
    interrupted:
      allTasksData?.jobs.filter((t) => t.status === "interrupted").length || 0,
    error: allTasksData?.jobs.filter((t) => t.status === "error").length || 0,
  };

  // If viewing a specific job, show full-page job details
  if (specificTask) {
    return (
      <BaseAppLayout
        content={
          <SpaceBetween size="l">
            <Header
              variant="h1"
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button iconName="arrow-left" onClick={handleBackToTasks}>
                    Back to Jobs
                  </Button>
                  {specificTask.requiresAction && (
                    <Button
                      variant="primary"
                      onClick={() => handleExecuteTask(specificTask)}
                    >
                      Provide Response
                    </Button>
                  )}
                  {(specificTask.status === "idle" ||
                    specificTask.status === "interrupted") && (
                    <Button
                      variant="normal"
                      onClick={() => handleExecuteTask(specificTask)}
                    >
                      Execute Job
                    </Button>
                  )}
                </SpaceBetween>
              }
            >
              {specificTask.jobName}
            </Header>

            <Container>
              <Grid gridDefinition={[{ colspan: 8 }, { colspan: 4 }]}>
                <SpaceBetween direction="vertical" size="l">
                  <div>
                    <Box variant="awsui-key-label">Job ID</Box>
                    <Box variant="code">{specificTask.jobId}</Box>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Agent</Box>
                    <Box>{getAgentName(specificTask.agentId)}</Box>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Session ID</Box>
                    <Box variant="code">{specificTask.sessionId}</Box>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Prompt</Box>
                    <Box variant="code">{specificTask.prompt}</Box>
                  </div>
                  {specificTask.finalResult && (
                    <div>
                      <Box variant="awsui-key-label">Final Result</Box>
                      <Box variant="code">{specificTask.finalResult}</Box>
                    </div>
                  )}
                  {specificTask.errorMessage && (
                    <div>
                      <Box variant="awsui-key-label">Error Message</Box>
                      <Box variant="code" color="text-status-error">
                        {specificTask.errorMessage}
                      </Box>
                    </div>
                  )}
                  {specificTask.schedule && (
                    <div>
                      <Box variant="awsui-key-label">Schedule</Box>
                      <SpaceBetween direction="vertical" size="xs">
                        <Box>Type: {specificTask.schedule.type}</Box>
                        <Box>Pattern: {specificTask.schedule.pattern}</Box>
                        <Box>
                          Enabled:{" "}
                          {specificTask.schedule.enabled ? "Yes" : "No"}
                        </Box>
                        {specificTask.schedule.nextRun && (
                          <Box>
                            Next Run:{" "}
                            {formatTimestamp(specificTask.schedule.nextRun)}
                          </Box>
                        )}
                      </SpaceBetween>
                    </div>
                  )}
                </SpaceBetween>
                <SpaceBetween direction="vertical" size="l">
                  <div>
                    <Box variant="awsui-key-label">Status</Box>
                    <StatusIndicator type={getStatusColor(specificTask.status)}>
                      {specificTask.status.replace("_", " ")}
                    </StatusIndicator>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Type</Box>
                    <Badge
                      color={
                        specificTask.jobType === "scheduled" ? "blue" : "grey"
                      }
                    >
                      {specificTask.jobType.replace("_", " ")}
                    </Badge>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Requires Action</Box>
                    <StatusIndicator
                      type={specificTask.requiresAction ? "warning" : "success"}
                    >
                      {specificTask.requiresAction
                        ? "Yes - Human input needed"
                        : "No"}
                    </StatusIndicator>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Created</Box>
                    <Box>{formatTimestamp(specificTask.createdAt)}</Box>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Last Updated</Box>
                    <Box>{formatTimestamp(specificTask.updatedAt)}</Box>
                  </div>
                  {specificTask.nextRun && (
                    <div>
                      <Box variant="awsui-key-label">Next Scheduled Run</Box>
                      <Box>{formatTimestamp(specificTask.nextRun)}</Box>
                    </div>
                  )}
                </SpaceBetween>
              </Grid>
            </Container>

            {/* Job Details Tabs */}
            <Container>
              <Tabs
                activeTabId={taskDetailTabId}
                onChange={({ detail }) =>
                  setTaskDetailTabId(detail.activeTabId)
                }
                tabs={[
                  {
                    label: "Chat",
                    id: "conversation",
                    content: specificTask.sessionId ? (
                      <ChatPanel
                        sessionId={specificTask.sessionId}
                        status={jobStatusToChatStatus(specificTask)}
                        onSendMessage={async (message) => {
                          // Sending on a job-detail chat calls the existing
                          // execute endpoint. For an interrupted job we
                          // forward the message as `humanResponse` so the
                          // agent's continuation flow picks it up; for any
                          // other status (idle/completed) we start a fresh
                          // execution with the message as the new prompt via
                          // a conversation-store append + execute, matching
                          // the previous behaviour.
                          await apiClient.multiAgentClient.executeTask(
                            specificTask.jobId,
                            { humanResponse: message },
                          );
                          // Optimistically mark the job busy so the panel
                          // locks its composer; the polling effect above
                          // will refresh the real status.
                          queryClient.setQueryData(
                            ["job", specificTask.jobId],
                            (old: any) =>
                              old
                                ? { ...old, status: "busy", requiresAction: false }
                                : old,
                          );
                          queryClient.invalidateQueries({
                            queryKey: ["conversation", specificTask.sessionId],
                          });
                        }}
                        title="Chat"
                        subtitle={`Job: ${specificTask.jobName}`}
                        maxHeight="600px"
                      />
                    ) : (
                      <Box textAlign="center" color="inherit" padding="l">
                        <Box variant="p" color="inherit">
                          No conversation history available for this job.
                        </Box>
                      </Box>
                    ),
                  },
                  {
                    label: `Execution Logs ${taskLogs.length > 0 ? `(${taskLogs.length})` : ""}`,
                    id: "logs",
                    content: (
                      <div
                        id="job-logs-container"
                        style={{
                          maxHeight: "400px",
                          overflowY: "auto",
                          fontFamily: "monospace",
                          fontSize: "12px",
                          backgroundColor: "#232f3e",
                          color: "#ffffff",
                          padding: "12px",
                          borderRadius: "4px",
                        }}
                      >
                        {taskLogs.length === 0 ? (
                          <Box textAlign="center" color="inherit" padding="l">
                            <Box variant="p" color="inherit">
                              No execution logs available yet. Logs will appear
                              here when the job is executed.
                            </Box>
                          </Box>
                        ) : (
                          <SpaceBetween size="xs">
                            {taskLogs.map((log, index) => (
                              <div
                                key={index}
                                style={{
                                  display: "flex",
                                  alignItems: "flex-start",
                                  gap: "8px",
                                  padding: "4px 0",
                                }}
                              >
                                <span
                                  style={{
                                    color: "#aab7b8",
                                    minWidth: "140px",
                                    flexShrink: 0,
                                  }}
                                >
                                  {new Date(log.timestamp).toLocaleTimeString()}
                                </span>
                                <span
                                  style={{
                                    minWidth: "60px",
                                    flexShrink: 0,
                                    fontWeight: "bold",
                                    color:
                                      log.level === "error"
                                        ? "#ff5252"
                                        : log.level === "warning"
                                          ? "#ff9800"
                                          : log.level === "success"
                                            ? "#4caf50"
                                            : "#2196f3",
                                  }}
                                >
                                  [{log.level.toUpperCase()}]
                                </span>
                                <span style={{ flex: 1 }}>{log.message}</span>
                              </div>
                            ))}
                          </SpaceBetween>
                        )}
                      </div>
                    ),
                  },
                ]}
              />
            </Container>

            {/* Execute Job Modal */}
            <Modal
              onDismiss={() => setShowExecuteModal(false)}
              visible={showExecuteModal}
              closeAriaLabel="Close modal"
              footer={
                <Box float="right">
                  <SpaceBetween direction="horizontal" size="xs">
                    <Button
                      variant="link"
                      onClick={() => setShowExecuteModal(false)}
                    >
                      Cancel
                    </Button>
                    <Button
                      variant="primary"
                      onClick={handleRunExecution}
                      loading={executeTaskMutation.isPending}
                    >
                      {executingTask?.requiresAction
                        ? "Send Response"
                        : "Execute Job"}
                    </Button>
                  </SpaceBetween>
                </Box>
              }
              header={`${executingTask?.requiresAction ? "Respond to" : "Execute"} Job: ${executingTask?.jobName}`}
            >
              <SpaceBetween direction="vertical" size="l">
                {executingTask?.requiresAction && (
                  <Alert type="info">
                    This job is waiting for human input to continue.
                  </Alert>
                )}

                <div>
                  <Box variant="awsui-key-label">Job Prompt</Box>
                  <Box variant="code">{executingTask?.prompt}</Box>
                </div>

                {/* Show conversation history for interrupted jobs */}
                {executingTask?.requiresAction && executingTask?.sessionId && (
                  <div>
                    <Box variant="awsui-key-label">Recent Conversation</Box>
                    <InlineConversationHistory
                      sessionId={executingTask.sessionId}
                      maxMessages={5}
                    />
                  </div>
                )}

                {executingTask?.requiresAction && (
                  <FormField
                    label="Your Response"
                    description="Provide the information the agent needs to continue"
                  >
                    <Textarea
                      value={humanResponse}
                      onChange={({ detail }) => setHumanResponse(detail.value)}
                      placeholder="Enter your response here..."
                      rows={4}
                    />
                  </FormField>
                )}

                {executeTaskMutation.data && (
                  <Alert
                    type={
                      executeTaskMutation.data.status === "completed"
                        ? "success"
                        : executeTaskMutation.data.status === "interrupted"
                          ? "warning"
                          : "error"
                    }
                    header={`Job ${executeTaskMutation.data.status}`}
                  >
                    <SpaceBetween direction="vertical" size="s">
                      {executeTaskMutation.data.result && (
                        <Box>
                          <strong>Result:</strong>
                          <Box variant="code">
                            {executeTaskMutation.data.result}
                          </Box>
                        </Box>
                      )}
                      {executeTaskMutation.data.requiresAction && (
                        <Box>
                          The job requires additional human input to continue.
                        </Box>
                      )}
                      {executeTaskMutation.data.error && (
                        <Box color="text-status-error">
                          <strong>Error:</strong>{" "}
                          {executeTaskMutation.data.error}
                        </Box>
                      )}
                    </SpaceBetween>
                  </Alert>
                )}

                {executeTaskMutation.error && (
                  <Alert type="error">
                    Execution failed: {executeTaskMutation.error.message}
                  </Alert>
                )}
              </SpaceBetween>
            </Modal>
          </SpaceBetween>
        }
      />
    );
  }

  return (
    <BaseAppLayout
      content={
        <SpaceBetween size="l">
          <Header
            variant="h1"
            description="Create and manage jobs for your agents"
            actions={
              <Button
                variant="primary"
                onClick={() => setShowCreateModal(true)}
              >
                Create New Job
              </Button>
            }
          >
            Job Management
          </Header>

          <Container>
            <Tabs
              activeTabId={activeTabId}
              onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
              tabs={[
                {
                  label: `All (${tabCounts.all})`,
                  id: "all",
                  content: null,
                },
                {
                  label: `Idle (${tabCounts.idle})`,
                  id: "idle",
                  content: null,
                },
                {
                  label: `Running (${tabCounts.busy})`,
                  id: "busy",
                  content: null,
                },
                {
                  label: `Completed (${tabCounts.completed})`,
                  id: "completed",
                  content: null,
                },
                {
                  label: `Interrupted (${tabCounts.interrupted})`,
                  id: "interrupted",
                  content: null,
                },
                {
                  label: `Error (${tabCounts.error})`,
                  id: "error",
                  content: null,
                },
              ]}
            />

            <Table
              resizableColumns
              columnDefinitions={[
                {
                  id: "name",
                  header: "Job Name",
                  cell: (job: Job) => (
                    <Link onFollow={() => handleViewTask(job)}>
                      {job.jobName}
                    </Link>
                  ),
                  sortingField: "jobName",
                },
                {
                  id: "agent",
                  header: "Agent",
                  cell: (job: Job) => getAgentName(job.agentId),
                },
                {
                  id: "type",
                  header: "Type",
                  cell: (job: Job) => (
                    <Badge
                      color={job.jobType === "scheduled" ? "blue" : "grey"}
                    >
                      {job.jobType.replace("_", " ")}
                    </Badge>
                  ),
                },
                {
                  id: "status",
                  header: "Status",
                  cell: (job: Job) => (
                    <StatusIndicator type={getStatusColor(job.status)}>
                      {job.status.replace("_", " ")}
                    </StatusIndicator>
                  ),
                },
                {
                  id: "prompt",
                  header: "Prompt",
                  cell: (job: Job) => (
                    <Box fontSize="body-s">
                      {job.prompt.length > 50
                        ? `${job.prompt.substring(0, 50)}...`
                        : job.prompt}
                    </Box>
                  ),
                },
                {
                  id: "updated",
                  header: "Last Updated",
                  cell: (job: Job) => formatTimestamp(job.updatedAt),
                  sortingField: "updatedAt",
                },
                {
                  id: "actions",
                  header: "Actions",
                  cell: (job: Job) => (
                    <SpaceBetween direction="horizontal" size="xs">
                      {job.requiresAction && (
                        <Button
                          variant="primary"
                          onClick={() => handleExecuteTask(job)}
                        >
                          Respond
                        </Button>
                      )}
                      {(job.status === "idle" ||
                        job.status === "interrupted") && (
                        <Button
                          variant="normal"
                          onClick={() => handleExecuteTask(job)}
                        >
                          Execute
                        </Button>
                      )}
                    </SpaceBetween>
                  ),
                  minWidth: 140,
                },
              ]}
              items={filteredTasks}
              loadingText="Loading jobs"
              selectedItems={selectedItems}
              onSelectionChange={({ detail }) =>
                setSelectedItems(detail.selectedItems)
              }
              selectionType="multi"
              trackBy="jobId"
              empty={
                <Box textAlign="center" color="inherit">
                  <b>No jobs found</b>
                  <Box variant="p" color="inherit">
                    Create your first job to get started.
                  </Box>
                </Box>
              }
              filter={
                <TextFilter
                  filteringText={filteringText}
                  onChange={({ detail }) =>
                    setFilteringText(detail.filteringText)
                  }
                  filteringPlaceholder="Find jobs"
                />
              }
              header={
                <Header
                  counter={`(${filteredTasks.length})`}
                  actions={
                    <SpaceBetween direction="horizontal" size="xs">
                      <Button
                        disabled={selectedItems.length === 0}
                        onClick={handleDeleteSelected}
                        loading={deleteTaskMutation.isPending}
                      >
                        Delete Selected
                      </Button>
                    </SpaceBetween>
                  }
                >
                  Jobs
                </Header>
              }
              pagination={
                <Pagination
                  currentPageIndex={currentPageIndex}
                  onChange={({ detail }) =>
                    setCurrentPageIndex(detail.currentPageIndex)
                  }
                  pagesCount={Math.ceil((tasksData?.total || 0) / pageSize)}
                />
              }
            />
          </Container>

          {/* Recently Viewed Jobs */}
          {recentlyViewedTasks.length > 0 && (
            <Container header={<Header variant="h2">Recently Viewed</Header>}>
              <Table
                resizableColumns
                columnDefinitions={[
                  {
                    id: "name",
                    header: "Job Name",
                    cell: (job: Job) => (
                      <Link onFollow={() => handleViewTask(job)}>
                        {job.jobName}
                      </Link>
                    ),
                  },
                  {
                    id: "agent",
                    header: "Agent",
                    cell: (job: Job) => getAgentName(job.agentId),
                  },
                  {
                    id: "status",
                    header: "Status",
                    cell: (job: Job) => (
                      <StatusIndicator type={getStatusColor(job.status)}>
                        {job.status.replace("_", " ")}
                      </StatusIndicator>
                    ),
                  },
                  {
                    id: "updated",
                    header: "Last Updated",
                    cell: (job: Job) => formatTimestamp(job.updatedAt),
                  },
                  {
                    id: "actions",
                    header: "Actions",
                    cell: (job: Job) => (
                      <SpaceBetween direction="horizontal" size="xs">
                        {job.requiresAction && (
                          <Button
                            variant="primary"
                            onClick={() => handleExecuteTask(job)}
                          >
                            Respond
                          </Button>
                        )}
                        {(job.status === "idle" ||
                          job.status === "interrupted") && (
                          <Button
                            variant="normal"
                            onClick={() => handleExecuteTask(job)}
                          >
                            Execute
                          </Button>
                        )}
                      </SpaceBetween>
                    ),
                    minWidth: 140,
                  },
                ]}
                items={recentlyViewedTasks}
                trackBy="jobId"
                empty={
                  <Box textAlign="center" color="inherit">
                    No recently viewed jobs
                  </Box>
                }
                variant="borderless"
              />
            </Container>
          )}

          {/* Create Job Modal */}
          <Modal
            onDismiss={() => setShowCreateModal(false)}
            visible={showCreateModal}
            closeAriaLabel="Close modal"
            size="large"
            footer={
              <Box float="right">
                <SpaceBetween direction="horizontal" size="xs">
                  <Button
                    variant="link"
                    onClick={() => setShowCreateModal(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="primary"
                    onClick={handleCreateTask}
                    loading={createTaskMutation.isPending}
                    disabled={
                      !formData.agentId || !formData.jobName || !formData.prompt
                    }
                  >
                    Create Job
                  </Button>
                </SpaceBetween>
              </Box>
            }
            header="Create New Job"
          >
            <Form>
              <SpaceBetween direction="vertical" size="l">
                {createTaskMutation.error && (
                  <Alert type="error">
                    Failed to create job: {createTaskMutation.error.message}
                  </Alert>
                )}

                <FormField
                  label="Job Name"
                  description="A descriptive name for your job"
                >
                  <Input
                    value={formData.jobName}
                    onChange={({ detail }) =>
                      setFormData({ ...formData, jobName: detail.value })
                    }
                    placeholder="Analyze quarterly report"
                  />
                </FormField>

                <FormField
                  label="Agent"
                  description="Select which agent will execute this job"
                >
                  <Select
                    selectedOption={
                      agentOptions.find(
                        (opt) => opt.value === formData.agentId,
                      ) || null
                    }
                    onChange={({ detail }) =>
                      setFormData({
                        ...formData,
                        agentId: detail.selectedOption.value || "",
                      })
                    }
                    options={agentOptions}
                    placeholder="Choose an agent"
                  />
                </FormField>

                <FormField
                  label="Job Type"
                  description="How this job should be executed"
                >
                  <Select
                    selectedOption={
                      taskTypeOptions.find(
                        (opt) => opt.value === formData.jobType,
                      ) || null
                    }
                    onChange={({ detail }) =>
                      setFormData({
                        ...formData,
                        jobType: detail.selectedOption.value as any,
                      })
                    }
                    options={taskTypeOptions}
                  />
                </FormField>

                <FormField
                  label="Prompt"
                  description="Detailed instructions for the agent"
                >
                  <Textarea
                    value={formData.prompt}
                    onChange={({ detail }) =>
                      setFormData({ ...formData, prompt: detail.value })
                    }
                    placeholder="Please analyze the quarterly sales data and provide insights on..."
                    rows={5}
                  />
                </FormField>

                {formData.jobType === "scheduled" && (
                  <FormField
                    label="Schedule"
                    description="Configure when this job should run"
                  >
                    <SpaceBetween direction="vertical" size="s">
                      <Toggle
                        checked={formData.schedule?.enabled || false}
                        onChange={({ detail }) =>
                          setFormData({
                            ...formData,
                            schedule: {
                              type: "recurring",
                              pattern: formData.schedule?.pattern || "daily",
                              enabled: detail.checked,
                            },
                          })
                        }
                      >
                        Enable scheduling
                      </Toggle>

                      {formData.schedule?.enabled && (
                        <Input
                          value={formData.schedule?.pattern || ""}
                          onChange={({ detail }) =>
                            setFormData({
                              ...formData,
                              schedule: {
                                ...formData.schedule!,
                                pattern: detail.value,
                              },
                            })
                          }
                          placeholder="daily at 9am, every 6 hours, weekly"
                        />
                      )}
                    </SpaceBetween>
                  </FormField>
                )}
              </SpaceBetween>
            </Form>
          </Modal>

          {/* Execute Job Modal */}
          <Modal
            onDismiss={() => setShowExecuteModal(false)}
            visible={showExecuteModal}
            closeAriaLabel="Close modal"
            footer={
              <Box float="right">
                <SpaceBetween direction="horizontal" size="xs">
                  <Button
                    variant="link"
                    onClick={() => setShowExecuteModal(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="primary"
                    onClick={handleRunExecution}
                    loading={executeTaskMutation.isPending}
                  >
                    {executingTask?.requiresAction
                      ? "Send Response"
                      : "Execute Job"}
                  </Button>
                </SpaceBetween>
              </Box>
            }
            header={`${executingTask?.requiresAction ? "Respond to" : "Execute"} Job: ${executingTask?.jobName}`}
          >
            <SpaceBetween direction="vertical" size="l">
              {executingTask?.requiresAction && (
                <Alert type="info">
                  This job is waiting for human input to continue.
                </Alert>
              )}

              <div>
                <Box variant="awsui-key-label">Job Prompt</Box>
                <Box variant="code">{executingTask?.prompt}</Box>
              </div>

              {executingTask?.requiresAction && (
                <FormField
                  label="Your Response"
                  description="Provide the information the agent needs to continue"
                >
                  <Textarea
                    value={humanResponse}
                    onChange={({ detail }) => setHumanResponse(detail.value)}
                    placeholder="Enter your response here..."
                    rows={4}
                  />
                </FormField>
              )}

              {executeTaskMutation.data && (
                <Alert
                  type={
                    executeTaskMutation.data.status === "completed"
                      ? "success"
                      : executeTaskMutation.data.status === "interrupted"
                        ? "warning"
                        : "error"
                  }
                  header={`Job ${executeTaskMutation.data.status}`}
                >
                  <SpaceBetween direction="vertical" size="s">
                    {executeTaskMutation.data.result && (
                      <Box>
                        <strong>Result:</strong>
                        <Box variant="code">
                          {executeTaskMutation.data.result}
                        </Box>
                      </Box>
                    )}
                    {executeTaskMutation.data.requiresAction && (
                      <Box>
                        The job requires additional human input to continue.
                      </Box>
                    )}
                    {executeTaskMutation.data.error && (
                      <Box color="text-status-error">
                        <strong>Error:</strong> {executeTaskMutation.data.error}
                      </Box>
                    )}
                  </SpaceBetween>
                </Alert>
              )}

              {executeTaskMutation.error && (
                <Alert type="error">
                  Execution failed: {executeTaskMutation.error.message}
                </Alert>
              )}
            </SpaceBetween>
          </Modal>
        </SpaceBetween>
      }
    />
  );
}
