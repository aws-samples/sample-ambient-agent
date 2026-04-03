// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import {
  AppLayout,
  Container,
  Header,
  SpaceBetween,
  Grid,
  Box,
  ColumnLayout,
  StatusIndicator,
  Button,
  Cards,
  Badge,
  Link,
  ExpandableSection,
} from "@cloudscape-design/components";
import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import BaseAppLayout from "../components/base-app-layout";
import { apiClient } from "../common/api-client/api-clients";
import { DashboardStats, Job, Agent } from "../types/multi-agent";

export default function WorkflowsPage() {
  const navigate = useNavigate();
  const [selectedItems, setSelectedItems] = useState<Job[]>([]);

  // Fetch dashboard statistics
  const {
    data: dashboardStats,
    isLoading: statsLoading,
    error: statsError,
  } = useQuery({
    queryKey: ["dashboard-stats"],
    queryFn: () => apiClient.multiAgentClient.getDashboardStats(),
    // Using SSE for real-time updates instead of polling
  });

  // Fetch recent jobs (limited to 4 for dashboard)
  const {
    data: tasksData,
    isLoading: tasksLoading,
    error: tasksError,
  } = useQuery({
    queryKey: ["recent-jobs"],
    queryFn: () => apiClient.multiAgentClient.listTasks({ pageSize: 4 }),
    // Using SSE for real-time updates instead of polling
  });

  // Fetch agents for context
  const { data: agentsData, isLoading: agentsLoading } = useQuery({
    queryKey: ["agents-list"],
    queryFn: () => apiClient.multiAgentClient.listAgents({ pageSize: 100 }),
  });

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

  return (
    <BaseAppLayout
      content={
        <SpaceBetween size="l">
          <Header
            variant="h1"
            description="Monitor and manage your multi-agent workflows"
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="primary" onClick={() => navigate("/jobs")}>
                  Create New Job
                </Button>
                <Button onClick={() => navigate("/agents")}>
                  Manage Agents
                </Button>
              </SpaceBetween>
            }
          >
            Workflows Dashboard
          </Header>

          {/* Statistics Overview */}
          <Container header={<Header variant="h2">Overview</Header>}>
            {statsLoading ? (
              <Box textAlign="center">Loading statistics...</Box>
            ) : statsError ? (
              <Box textAlign="center" color="text-status-error">
                Error loading statistics
              </Box>
            ) : dashboardStats ? (
              <ColumnLayout columns={4} variant="text-grid">
                <div>
                  <Box variant="awsui-key-label">Total Agents</Box>
                  <Box variant="awsui-value-large">
                    {dashboardStats.totalAgents}
                  </Box>
                  <Box variant="small">
                    <StatusIndicator type="success">
                      {dashboardStats.activeAgents} active
                    </StatusIndicator>
                  </Box>
                </div>
                <div>
                  <Box variant="awsui-key-label">Total Jobs</Box>
                  <Box variant="awsui-value-large">
                    {dashboardStats.totalTasks}
                  </Box>
                  <Box variant="small">
                    <StatusIndicator type="in-progress">
                      {dashboardStats.runningTasks} running
                    </StatusIndicator>
                  </Box>
                </div>
                <div>
                  <Box variant="awsui-key-label">Completed</Box>
                  <Box variant="awsui-value-large">
                    {dashboardStats.completedTasks}
                  </Box>
                  <Box variant="small" color="text-status-success">
                    Success rate:{" "}
                    {dashboardStats.totalTasks > 0
                      ? Math.round(
                          (dashboardStats.completedTasks /
                            dashboardStats.totalTasks) *
                            100,
                        )
                      : 0}
                    %
                  </Box>
                </div>
                <div>
                  <Box variant="awsui-key-label">Needs Attention</Box>
                  <Box variant="awsui-value-large">
                    {dashboardStats.interruptedTasks}
                  </Box>
                  <Box variant="small">
                    <StatusIndicator type="warning">
                      Requires human input
                    </StatusIndicator>
                  </Box>
                </div>
              </ColumnLayout>
            ) : null}
          </Container>

          <Grid gridDefinition={[{ colspan: 8 }, { colspan: 4 }]}>
            {/* Recent Jobs */}
            <Container header={<Header variant="h2">Recent Jobs</Header>}>
              {tasksLoading ? (
                <Box textAlign="center">Loading jobs...</Box>
              ) : tasksError ? (
                <Box textAlign="center" color="text-status-error">
                  Error loading jobs
                </Box>
              ) : (
                <Cards
                  cardDefinition={{
                    header: (job: Job) => job.jobName,
                    sections: [
                      {
                        id: "basic-info",
                        content: (job: Job) => (
                          <SpaceBetween direction="vertical" size="s">
                            <Grid
                              gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}
                            >
                              <div>
                                <Box variant="awsui-key-label">Agent</Box>
                                <Box>{getAgentName(job.agentId)}</Box>
                              </div>
                              <div>
                                <Box variant="awsui-key-label">Status</Box>
                                <StatusIndicator
                                  type={getStatusColor(job.status)}
                                >
                                  {job.status.replace("_", " ")}
                                </StatusIndicator>
                              </div>
                            </Grid>
                            <Grid
                              gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}
                            >
                              <div>
                                <Box variant="awsui-key-label">Type</Box>
                                <Badge
                                  color={
                                    job.jobType === "scheduled"
                                      ? "blue"
                                      : "grey"
                                  }
                                >
                                  {job.jobType.replace("_", " ")}
                                </Badge>
                              </div>
                              <div>
                                <Box variant="awsui-key-label">
                                  Last Updated
                                </Box>
                                <Box>{formatTimestamp(job.updatedAt)}</Box>
                              </div>
                            </Grid>
                            <ExpandableSection
                              headerText="Job Details"
                              variant="footer"
                            >
                              <SpaceBetween direction="vertical" size="m">
                                <div>
                                  <Box variant="awsui-key-label">Job ID</Box>
                                  <Box variant="code">{job.jobId}</Box>
                                </div>
                                <div>
                                  <Box variant="awsui-key-label">Prompt</Box>
                                  <Box variant="code">{job.prompt}</Box>
                                </div>
                                <div>
                                  <Box variant="awsui-key-label">
                                    Session ID
                                  </Box>
                                  <Box variant="code">{job.sessionId}</Box>
                                </div>
                                <div>
                                  <Box variant="awsui-key-label">Created</Box>
                                  <Box>{formatTimestamp(job.createdAt)}</Box>
                                </div>
                                {job.finalResult && (
                                  <div>
                                    <Box variant="awsui-key-label">
                                      Final Result
                                    </Box>
                                    <Box variant="code">{job.finalResult}</Box>
                                  </div>
                                )}
                                {job.errorMessage && (
                                  <div>
                                    <Box variant="awsui-key-label">
                                      Error Message
                                    </Box>
                                    <Box
                                      variant="code"
                                      color="text-status-error"
                                    >
                                      {job.errorMessage}
                                    </Box>
                                  </div>
                                )}
                                {job.requiresAction && (
                                  <div>
                                    <Box variant="awsui-key-label">
                                      Action Required
                                    </Box>
                                    <StatusIndicator type="warning">
                                      This job requires human input to continue
                                    </StatusIndicator>
                                  </div>
                                )}
                                <div>
                                  <Button
                                    variant="primary"
                                    onClick={() =>
                                      navigate(`/jobs?jobId=${job.jobId}`)
                                    }
                                  >
                                    View Full Details
                                  </Button>
                                </div>
                              </SpaceBetween>
                            </ExpandableSection>
                          </SpaceBetween>
                        ),
                      },
                    ],
                  }}
                  cardsPerRow={[{ cards: 1 }, { minWidth: 500, cards: 2 }]}
                  items={tasksData?.jobs || []}
                  loadingText="Loading jobs"
                  empty={
                    <Box textAlign="center" color="inherit">
                      <b>No jobs found</b>
                      <Box variant="p" color="inherit">
                        Create your first job to get started.
                      </Box>
                    </Box>
                  }
                />
              )}
            </Container>

            {/* Recent Activity */}
            <Container header={<Header variant="h2">Recent Activity</Header>}>
              {statsLoading ? (
                <Box textAlign="center">Loading activity...</Box>
              ) : dashboardStats?.recentActivity ? (
                <SpaceBetween size="s">
                  {dashboardStats.recentActivity
                    .slice(0, 4)
                    .map((activity: any) => (
                      <Box key={activity.id}>
                        <Box variant="small" color="text-body-secondary">
                          {formatTimestamp(activity.timestamp)}
                        </Box>
                        <Box>{activity.message}</Box>
                        {activity.agentName && (
                          <Box variant="small" color="text-body-secondary">
                            Agent: {activity.agentName}
                          </Box>
                        )}
                      </Box>
                    ))}
                </SpaceBetween>
              ) : (
                <Box textAlign="center" color="inherit">
                  No recent activity
                </Box>
              )}
            </Container>
          </Grid>
        </SpaceBetween>
      }
    />
  );
}
