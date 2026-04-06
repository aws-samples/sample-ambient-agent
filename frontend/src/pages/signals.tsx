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
  Link,
} from "@cloudscape-design/components";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useSearchParams, useNavigate } from "react-router-dom";
import BaseAppLayout from "../components/base-app-layout";
import ConversationHistory from "../components/conversation-history";
import { apiClient } from "../common/api-client/api-clients";
import {
  Signal,
  CreateSignalRequest,
  SignalFormData,
  Agent,
} from "../types/multi-agent";
export default function SignalsPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedItems, setSelectedItems] = useState<Signal[]>([]);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [filteringText, setFilteringText] = useState("");
  const [currentPageIndex, setCurrentPageIndex] = useState(1);
  const [activeTabId, setActiveTabId] = useState("all");
  const pageSize = 10;

  // Get signalId from URL params if present
  const signalIdFromUrl = searchParams.get("signalId");

  // Form state for creating new signal
  const [formData, setFormData] = useState<SignalFormData>({
    signalName: "",
    signalType: "s3_file_upload",
    agentId: "",
    description: "",
    configuration: {
      bucketName: "",
      prefix: "",
      fileTypes: ["*"],
    },
    enabled: true,
  });

  // Handle signal viewing
  const handleViewSignal = (signal: Signal) => {
    setSearchParams({ signalId: signal.signalId });
  };

  const handleBackToSignals = () => {
    setSearchParams({});
  };

  // Fetch signals
  const {
    data: signalsData,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["signals", activeTabId, filteringText, currentPageIndex],
    queryFn: () => {
      let statusFilter = activeTabId === "all" ? undefined : activeTabId;

      return apiClient.multiAgentClient.listSignals({
        status: statusFilter,
        page: currentPageIndex,
        pageSize: pageSize,
      });
    },
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
    refetchInterval: false,
  });

  // Fetch all signals for tab counts
  const { data: allSignalsData } = useQuery({
    queryKey: ["all-signals-for-counts"],
    queryFn: () => apiClient.multiAgentClient.listSignals({ pageSize: 1000 }),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
    refetchInterval: false,
  });

  // Fetch agents for dropdown
  const { data: agentsData } = useQuery({
    queryKey: ["agents-for-signals"],
    queryFn: () => apiClient.multiAgentClient.listAgents({ pageSize: 100 }),
  });

  // Fetch specific signal if signalId in URL
  const { data: specificSignal } = useQuery({
    queryKey: ["signal", signalIdFromUrl],
    queryFn: () =>
      signalIdFromUrl
        ? apiClient.multiAgentClient.getSignal(signalIdFromUrl)
        : null,
    enabled: !!signalIdFromUrl,
  });

  // Create signal mutation
  const createSignalMutation = useMutation({
    mutationFn: (signalData: CreateSignalRequest) =>
      apiClient.multiAgentClient.createSignal(signalData),
    onSuccess: (newSignal) => {
      queryClient.setQueryData(
        ["signals", activeTabId, filteringText, currentPageIndex],
        (old: any) => {
          if (!old) return old;
          return {
            ...old,
            signals: [newSignal, ...old.signals],
            total: old.total + 1,
          };
        },
      );

      queryClient.setQueryData(["all-signals-for-counts"], (old: any) => {
        if (!old) return old;
        return {
          ...old,
          signals: [newSignal, ...old.signals],
          total: old.total + 1,
        };
      });

      queryClient.invalidateQueries({ queryKey: ["signals"] });
      queryClient.invalidateQueries({ queryKey: ["all-signals-for-counts"] });

      setShowCreateModal(false);
      resetForm();
    },
  });

  // Delete signal mutation
  const deleteSignalMutation = useMutation({
    mutationFn: (signalId: string) =>
      apiClient.multiAgentClient.deleteSignal(signalId),
    onSuccess: (_, deletedSignalId) => {
      queryClient.setQueryData(
        ["signals", activeTabId, filteringText, currentPageIndex],
        (old: any) => {
          if (!old) return old;
          return {
            ...old,
            signals: old.signals.filter(
              (signal: any) => signal.signalId !== deletedSignalId,
            ),
            total: old.total - 1,
          };
        },
      );

      queryClient.setQueryData(["all-signals-for-counts"], (old: any) => {
        if (!old) return old;
        return {
          ...old,
          signals: old.signals.filter(
            (signal: any) => signal.signalId !== deletedSignalId,
          ),
          total: old.total - 1,
        };
      });

      queryClient.invalidateQueries({ queryKey: ["signals"] });
      queryClient.invalidateQueries({ queryKey: ["all-signals-for-counts"] });

      setSelectedItems([]);
    },
  });

  // Toggle signal status mutation
  const toggleSignalMutation = useMutation({
    mutationFn: ({
      signalId,
      enabled,
    }: {
      signalId: string;
      enabled: boolean;
    }) => apiClient.multiAgentClient.updateSignal(signalId, { enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["signals"] });
      queryClient.invalidateQueries({ queryKey: ["all-signals-for-counts"] });
    },
  });

  const resetForm = () => {
    setFormData({
      signalName: "",
      signalType: "s3_file_upload",
      agentId: "",
      description: "",
      configuration: {
        bucketName: "",
        prefix: "",
        fileTypes: ["*"],
      },
      enabled: true,
    });
  };

  const handleCreateSignal = () => {
    createSignalMutation.mutate({
      signalName: formData.signalName,
      signalType: formData.signalType,
      agentId: formData.agentId,
      description: formData.description,
      configuration: formData.configuration,
      enabled: formData.enabled,
    });
  };

  const handleDeleteSelected = () => {
    selectedItems.forEach((signal) => {
      deleteSignalMutation.mutate(signal.signalId);
    });
  };

  const handleToggleSignal = (signal: Signal) => {
    toggleSignalMutation.mutate({
      signalId: signal.signalId,
      enabled: !signal.enabled,
    });
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "active":
        return "success";
      case "inactive":
        return "stopped";
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

  const signalTypeOptions = [
    { label: "S3 File Upload", value: "s3_file_upload" },
  ];

  // Apply filtering to the signals
  const filteredSignals =
    signalsData?.signals.filter((signal) => {
      // Apply status filtering based on active tab
      if (activeTabId !== "all") {
        const status = signal.enabled ? "active" : "inactive";
        if (status !== activeTabId) {
          return false;
        }
      }

      // Apply text filtering
      if (filteringText) {
        return (
          signal.signalName
            .toLowerCase()
            .includes(filteringText.toLowerCase()) ||
          signal.description
            .toLowerCase()
            .includes(filteringText.toLowerCase()) ||
          getAgentName(signal.agentId)
            .toLowerCase()
            .includes(filteringText.toLowerCase())
        );
      }

      return true;
    }) || [];

  const tabCounts = {
    all: allSignalsData?.total || 0,
    active: allSignalsData?.signals.filter((s) => s.enabled).length || 0,
    inactive: allSignalsData?.signals.filter((s) => !s.enabled).length || 0,
  };

  // If viewing a specific signal, show full-page signal details
  if (specificSignal) {
    return (
      <BaseAppLayout
        content={
          <SpaceBetween size="l">
            <Header
              variant="h1"
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button iconName="arrow-left" onClick={handleBackToSignals}>
                    Back to Signals
                  </Button>
                  <Button
                    variant={specificSignal.enabled ? "normal" : "primary"}
                    onClick={() => handleToggleSignal(specificSignal)}
                  >
                    {specificSignal.enabled ? "Disable" : "Enable"}
                  </Button>
                </SpaceBetween>
              }
            >
              {specificSignal.signalName}
            </Header>

            <Container>
              <Grid gridDefinition={[{ colspan: 8 }, { colspan: 4 }]}>
                <SpaceBetween direction="vertical" size="l">
                  <div>
                    <Box variant="awsui-key-label">Signal ID</Box>
                    <Box variant="code">{specificSignal.signalId}</Box>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Agent</Box>
                    <Box>{getAgentName(specificSignal.agentId)}</Box>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Description</Box>
                    <Box>{specificSignal.description}</Box>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Configuration</Box>
                    <Box variant="code">
                      {JSON.stringify(specificSignal.configuration, null, 2)}
                    </Box>
                  </div>
                  {specificSignal.lastTriggered && (
                    <div>
                      <Box variant="awsui-key-label">Last Triggered</Box>
                      <Box>{formatTimestamp(specificSignal.lastTriggered)}</Box>
                    </div>
                  )}
                </SpaceBetween>
                <SpaceBetween direction="vertical" size="l">
                  <div>
                    <Box variant="awsui-key-label">Status</Box>
                    <StatusIndicator
                      type={specificSignal.enabled ? "success" : "stopped"}
                    >
                      {specificSignal.enabled ? "Active" : "Inactive"}
                    </StatusIndicator>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Type</Box>
                    <Badge color="blue">
                      {specificSignal.signalType.replace("_", " ")}
                    </Badge>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Trigger Count</Box>
                    <Box>{specificSignal.triggerCount || 0}</Box>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Created</Box>
                    <Box>{formatTimestamp(specificSignal.createdAt)}</Box>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Last Updated</Box>
                    <Box>{formatTimestamp(specificSignal.updatedAt)}</Box>
                  </div>
                </SpaceBetween>
              </Grid>
            </Container>

            {/* Show recent signal activity */}
            {specificSignal.recentActivity &&
              specificSignal.recentActivity.length > 0 && (
                <Container
                  header={<Header variant="h2">Recent Activity</Header>}
                >
                  <Table
                    resizableColumns
                    columnDefinitions={[
                      {
                        id: "timestamp",
                        header: "Timestamp",
                        cell: (activity: any) =>
                          formatTimestamp(activity.timestamp),
                      },
                      {
                        id: "event",
                        header: "Event",
                        cell: (activity: any) => activity.event,
                      },
                      {
                        id: "payload",
                        header: "Payload",
                        cell: (activity: any) => (
                          <Box variant="code" fontSize="body-s">
                            {JSON.stringify(activity.payload, null, 2)}
                          </Box>
                        ),
                      },
                      {
                        id: "status",
                        header: "Status",
                        cell: (activity: any) => (
                          <StatusIndicator
                            type={
                              activity.status === "success"
                                ? "success"
                                : "error"
                            }
                          >
                            {activity.status}
                          </StatusIndicator>
                        ),
                      },
                    ]}
                    items={specificSignal.recentActivity}
                    trackBy="id"
                    empty={
                      <Box textAlign="center" color="inherit">
                        No recent activity
                      </Box>
                    }
                    variant="borderless"
                  />
                </Container>
              )}
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
            description="Configure ambient signals to trigger agent responses"
            actions={
              <Button
                variant="primary"
                onClick={() => setShowCreateModal(true)}
              >
                Create New Signal
              </Button>
            }
          >
            Signal Management
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
                  label: `Active (${tabCounts.active})`,
                  id: "active",
                  content: null,
                },
                {
                  label: `Inactive (${tabCounts.inactive})`,
                  id: "inactive",
                  content: null,
                },
              ]}
            />

            <Table
              resizableColumns
              columnDefinitions={[
                {
                  id: "name",
                  header: "Signal Name",
                  cell: (signal: Signal) => (
                    <Link onFollow={() => handleViewSignal(signal)}>
                      {signal.signalName}
                    </Link>
                  ),
                  sortingField: "signalName",
                },
                {
                  id: "type",
                  header: "Type",
                  cell: (signal: Signal) => (
                    <Badge color="blue">
                      {signal.signalType.replace("_", " ")}
                    </Badge>
                  ),
                },
                {
                  id: "agent",
                  header: "Agent",
                  cell: (signal: Signal) => getAgentName(signal.agentId),
                },
                {
                  id: "status",
                  header: "Status",
                  cell: (signal: Signal) => (
                    <StatusIndicator
                      type={signal.enabled ? "success" : "stopped"}
                    >
                      {signal.enabled ? "Active" : "Inactive"}
                    </StatusIndicator>
                  ),
                },
                {
                  id: "triggers",
                  header: "Triggers",
                  cell: (signal: Signal) => signal.triggerCount || 0,
                },
                {
                  id: "lastTriggered",
                  header: "Last Triggered",
                  cell: (signal: Signal) =>
                    signal.lastTriggered
                      ? formatTimestamp(signal.lastTriggered)
                      : "Never",
                },
                {
                  id: "actions",
                  header: "Actions",
                  cell: (signal: Signal) => (
                    <SpaceBetween direction="horizontal" size="xs">
                      <Button
                        variant="normal"
                        onClick={() => handleToggleSignal(signal)}
                        loading={toggleSignalMutation.isPending}
                      >
                        {signal.enabled ? "Disable" : "Enable"}
                      </Button>
                    </SpaceBetween>
                  ),
                  minWidth: 110,
                },
              ]}
              items={filteredSignals}
              loadingText="Loading signals"
              selectedItems={selectedItems}
              onSelectionChange={({ detail }) =>
                setSelectedItems(detail.selectedItems)
              }
              selectionType="multi"
              trackBy="signalId"
              empty={
                <Box textAlign="center" color="inherit">
                  <b>No signals found</b>
                  <Box variant="p" color="inherit">
                    Create your first ambient signal to get started.
                  </Box>
                </Box>
              }
              filter={
                <TextFilter
                  filteringText={filteringText}
                  onChange={({ detail }) =>
                    setFilteringText(detail.filteringText)
                  }
                  filteringPlaceholder="Find signals"
                />
              }
              header={
                <Header
                  counter={`(${filteredSignals.length})`}
                  actions={
                    <SpaceBetween direction="horizontal" size="xs">
                      <Button
                        disabled={selectedItems.length === 0}
                        onClick={handleDeleteSelected}
                        loading={deleteSignalMutation.isPending}
                      >
                        Delete Selected
                      </Button>
                    </SpaceBetween>
                  }
                >
                  Ambient Signals
                </Header>
              }
              pagination={
                <Pagination
                  currentPageIndex={currentPageIndex}
                  onChange={({ detail }) =>
                    setCurrentPageIndex(detail.currentPageIndex)
                  }
                  pagesCount={Math.ceil((signalsData?.total || 0) / pageSize)}
                />
              }
            />
          </Container>

          {/* Create Signal Modal */}
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
                    onClick={handleCreateSignal}
                    loading={createSignalMutation.isPending}
                    disabled={!formData.signalName || !formData.agentId}
                  >
                    Create Signal
                  </Button>
                </SpaceBetween>
              </Box>
            }
            header="Create New Ambient Signal"
          >
            <Form>
              <SpaceBetween direction="vertical" size="l">
                {createSignalMutation.error && (
                  <Alert type="error">
                    Failed to create signal:{" "}
                    {createSignalMutation.error.message}
                  </Alert>
                )}

                <FormField
                  label="Signal Name"
                  description="A descriptive name for your ambient signal"
                >
                  <Input
                    value={formData.signalName}
                    onChange={({ detail }) =>
                      setFormData({ ...formData, signalName: detail.value })
                    }
                    placeholder="S3 Document Upload Monitor"
                  />
                </FormField>

                <FormField
                  label="Signal Type"
                  description="Monitors S3 bucket for file uploads and triggers the selected agent"
                >
                  <Input value="S3 File Upload" disabled readOnly />
                </FormField>

                <FormField
                  label="Agent"
                  description="Select which agent will respond to this signal"
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
                  label="Description"
                  description="Describe what this signal monitors and how the agent should respond"
                >
                  <Textarea
                    value={formData.description}
                    onChange={({ detail }) =>
                      setFormData({ ...formData, description: detail.value })
                    }
                    placeholder="Monitor for new documents uploaded to S3 and have the agent summarize them..."
                    rows={3}
                  />
                </FormField>

                {formData.signalType === "s3_file_upload" && (
                  <SpaceBetween direction="vertical" size="s">
                    <FormField
                      label="S3 Bucket Name"
                      description="The S3 bucket to monitor for file uploads"
                    >
                      <Input
                        value={formData.configuration.bucketName || ""}
                        onChange={({ detail }) =>
                          setFormData({
                            ...formData,
                            configuration: {
                              ...formData.configuration,
                              bucketName: detail.value,
                            },
                          })
                        }
                        placeholder="my-document-bucket"
                      />
                    </FormField>

                    <FormField
                      label="Prefix (Optional)"
                      description="Only monitor files with this prefix (e.g., 'documents/' or 'uploads/2024/')"
                    >
                      <Input
                        value={formData.configuration.prefix || ""}
                        onChange={({ detail }) =>
                          setFormData({
                            ...formData,
                            configuration: {
                              ...formData.configuration,
                              prefix: detail.value,
                            },
                          })
                        }
                        placeholder="documents/"
                      />
                    </FormField>

                    <FormField
                      label="Suffix (Optional)"
                      description="Only monitor files with this suffix (e.g., '.pdf', '.json', '.txt')"
                    >
                      <Input
                        value={formData.configuration.suffix || ""}
                        onChange={({ detail }) =>
                          setFormData({
                            ...formData,
                            configuration: {
                              ...formData.configuration,
                              suffix: detail.value,
                            },
                          })
                        }
                        placeholder=".pdf"
                      />
                    </FormField>
                  </SpaceBetween>
                )}

                <FormField
                  label="Enable Signal"
                  description="Whether this signal should be active immediately"
                >
                  <Toggle
                    checked={formData.enabled}
                    onChange={({ detail }) =>
                      setFormData({ ...formData, enabled: detail.checked })
                    }
                  >
                    Signal is {formData.enabled ? "enabled" : "disabled"}
                  </Toggle>
                </FormField>
              </SpaceBetween>
            </Form>
          </Modal>
        </SpaceBetween>
      }
    />
  );
}
