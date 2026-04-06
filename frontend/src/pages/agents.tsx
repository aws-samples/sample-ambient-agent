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
  Multiselect,
  Alert,
  Pagination,
  TextFilter,
} from "@cloudscape-design/components";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import BaseAppLayout from "../components/base-app-layout";
import { apiClient } from "../common/api-client/api-clients";
import {
  Agent,
  CreateAgentRequest,
  AgentCapability,
  AgentFormData,
} from "../types/multi-agent";

export default function AgentsPage() {
  const queryClient = useQueryClient();
  const [selectedItems, setSelectedItems] = useState<Agent[]>([]);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [filteringText, setFilteringText] = useState("");
  const [currentPageIndex, setCurrentPageIndex] = useState(1);
  const pageSize = 10;

  // Form state for creating new agent
  const [formData, setFormData] = useState<AgentFormData>({
    agentName: "",
    agentArn: "",
    agentType: "user_initiated",
    description: "",
    capabilities: [],
  });

  // State for capability input
  const [newCapability, setNewCapability] = useState("");

  // Fetch agents
  const {
    data: agentsData,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["agents", filteringText, currentPageIndex],
    queryFn: () =>
      apiClient.multiAgentClient.listAgents({
        page: currentPageIndex,
        pageSize,
      }),
    // Using SSE for real-time updates instead of polling
  });

  // Create agent mutation
  const createAgentMutation = useMutation({
    mutationFn: (agentData: CreateAgentRequest) =>
      apiClient.multiAgentClient.createAgent(agentData),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      setShowCreateModal(false);
      resetForm();
    },
  });

  // Delete agent mutation
  const deleteAgentMutation = useMutation({
    mutationFn: (agentId: string) =>
      apiClient.multiAgentClient.deleteAgent(agentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      setSelectedItems([]);
    },
  });

  const resetForm = () => {
    setFormData({
      agentName: "",
      agentArn: "",
      agentType: "user_initiated",
      description: "",
      capabilities: [],
    });
    setNewCapability("");
  };

  const handleCreateAgent = () => {
    createAgentMutation.mutate({
      agentName: formData.agentName,
      agentArn: formData.agentArn,
      agentType: formData.agentType,
      description: formData.description,
      capabilities: formData.capabilities.join(", "), // Convert array to comma-separated string
    });
  };

  // Capability management functions
  const addCapability = () => {
    if (
      newCapability.trim() &&
      !formData.capabilities.includes(newCapability.trim())
    ) {
      setFormData({
        ...formData,
        capabilities: [...formData.capabilities, newCapability.trim()],
      });
      setNewCapability("");
    }
  };

  const removeCapability = (capabilityToRemove: string) => {
    setFormData({
      ...formData,
      capabilities: formData.capabilities.filter(
        (cap) => cap !== capabilityToRemove,
      ),
    });
  };

  const handleKeyDown = (event: any) => {
    if (event.detail.key === "Enter") {
      event.preventDefault();
      addCapability();
    }
  };

  const handleDeleteSelected = () => {
    selectedItems.forEach((agent) => {
      deleteAgentMutation.mutate(agent.agentId);
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

  const formatTimestamp = (timestamp: string) => {
    return new Date(timestamp).toLocaleString();
  };

  const capabilityOptions = [
    { label: "Human Interruption", value: "human_interruption" },
    { label: "Conversation Continuity", value: "conversation_continuity" },
    { label: "Scheduled Execution", value: "scheduled_execution" },
  ];

  const agentTypeOptions = [
    { label: "User Initiated", value: "user_initiated" },
    { label: "Scheduled", value: "scheduled" },
    { label: "Ambient", value: "ambient" },
  ];

  const filteredAgents =
    agentsData?.agents.filter(
      (agent) =>
        agent.agentName.toLowerCase().includes(filteringText.toLowerCase()) ||
        agent.description.toLowerCase().includes(filteringText.toLowerCase()),
    ) || [];

  return (
    <BaseAppLayout
      content={
        <SpaceBetween size="l">
          <Header
            variant="h1"
            description="Register and manage your Bedrock Agent Core agents"
            actions={
              <Button
                variant="primary"
                onClick={() => setShowCreateModal(true)}
              >
                Register New Agent
              </Button>
            }
          >
            Agent Management
          </Header>

          <Container>
            <Table
              resizableColumns
              columnDefinitions={[
                {
                  id: "name",
                  header: "Agent Name",
                  cell: (agent: Agent) => agent.agentName,
                  sortingField: "agentName",
                },
                {
                  id: "arn",
                  header: "Agent ARN",
                  cell: (agent: Agent) => (
                    <Box fontSize="body-s" color="text-body-secondary">
                      {agent.agentArn}
                    </Box>
                  ),
                },
                {
                  id: "type",
                  header: "Type",
                  cell: (agent: Agent) => (
                    <Badge
                      color={agent.agentType === "scheduled" ? "blue" : "grey"}
                    >
                      {agent.agentType.replace("_", " ")}
                    </Badge>
                  ),
                },
                {
                  id: "status",
                  header: "Status",
                  cell: (agent: Agent) => (
                    <StatusIndicator type={getStatusColor(agent.status)}>
                      {agent.status}
                    </StatusIndicator>
                  ),
                },
                {
                  id: "capabilities",
                  header: "Capabilities",
                  cell: (agent: Agent) => (
                    <SpaceBetween direction="horizontal" size="xs">
                      {agent.capabilities ? (
                        agent.capabilities.split(",").map((cap) => (
                          <Badge key={cap.trim()} color="green">
                            {cap.trim()}
                          </Badge>
                        ))
                      ) : (
                        <Box fontSize="body-s" color="text-body-secondary">
                          No capabilities specified
                        </Box>
                      )}
                    </SpaceBetween>
                  ),
                },
                {
                  id: "created",
                  header: "Created",
                  cell: (agent: Agent) => formatTimestamp(agent.createdAt),
                  sortingField: "createdAt",
                },
              ]}
              items={filteredAgents}
              loadingText="Loading agents"
              selectedItems={selectedItems}
              onSelectionChange={({ detail }) =>
                setSelectedItems(detail.selectedItems)
              }
              selectionType="multi"
              trackBy="agentId"
              empty={
                <Box textAlign="center" color="inherit">
                  <b>No agents registered</b>
                  <Box variant="p" color="inherit">
                    Register your first Bedrock Agent Core agent to get started.
                  </Box>
                </Box>
              }
              filter={
                <TextFilter
                  filteringText={filteringText}
                  onChange={({ detail }) =>
                    setFilteringText(detail.filteringText)
                  }
                  filteringPlaceholder="Find agents"
                />
              }
              header={
                <Header
                  counter={`(${filteredAgents.length})`}
                  actions={
                    <SpaceBetween direction="horizontal" size="xs">
                      <Button
                        disabled={selectedItems.length === 0}
                        onClick={handleDeleteSelected}
                        loading={deleteAgentMutation.isPending}
                      >
                        Delete Selected
                      </Button>
                    </SpaceBetween>
                  }
                >
                  Registered Agents
                </Header>
              }
              pagination={
                <Pagination
                  currentPageIndex={currentPageIndex}
                  onChange={({ detail }) =>
                    setCurrentPageIndex(detail.currentPageIndex)
                  }
                  pagesCount={Math.ceil((agentsData?.total || 0) / pageSize)}
                />
              }
            />
          </Container>

          {/* Create Agent Modal */}
          <Modal
            onDismiss={() => setShowCreateModal(false)}
            visible={showCreateModal}
            closeAriaLabel="Close modal"
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
                    onClick={handleCreateAgent}
                    loading={createAgentMutation.isPending}
                    disabled={!formData.agentName || !formData.agentArn}
                  >
                    Register Agent
                  </Button>
                </SpaceBetween>
              </Box>
            }
            header="Register New Agent"
          >
            <Form>
              <SpaceBetween direction="vertical" size="l">
                {createAgentMutation.error && (
                  <Alert type="error">
                    Failed to register agent:{" "}
                    {createAgentMutation.error.message}
                  </Alert>
                )}

                <FormField
                  label="Agent Name"
                  description="A friendly name for your agent"
                >
                  <Input
                    value={formData.agentName}
                    onChange={({ detail }) =>
                      setFormData({ ...formData, agentName: detail.value })
                    }
                    placeholder="My Helpful Agent"
                  />
                </FormField>

                <FormField
                  label="Agent ARN"
                  description="The ARN of your deployed Bedrock Agent Core agent"
                >
                  <Input
                    value={formData.agentArn}
                    onChange={({ detail }) =>
                      setFormData({ ...formData, agentArn: detail.value })
                    }
                    placeholder="arn:aws:bedrock:us-east-1:123456789012:agent/ABCDEFGHIJ"
                  />
                </FormField>

                <FormField
                  label="Description"
                  description="Describe what this agent does"
                >
                  <Textarea
                    value={formData.description}
                    onChange={({ detail }) =>
                      setFormData({ ...formData, description: detail.value })
                    }
                    placeholder="This agent helps with..."
                    rows={3}
                  />
                </FormField>

                <FormField
                  label="Capabilities"
                  description="Add capabilities that your agent supports. Type and press Enter or click Add."
                >
                  <SpaceBetween direction="vertical" size="s">
                    <SpaceBetween direction="horizontal" size="xs">
                      <Input
                        value={newCapability}
                        onChange={({ detail }) =>
                          setNewCapability(detail.value)
                        }
                        onKeyDown={handleKeyDown}
                        placeholder="e.g., Human Interruption, Document Analysis..."
                      />
                      <Button
                        variant="normal"
                        onClick={addCapability}
                        disabled={
                          !newCapability.trim() ||
                          formData.capabilities.includes(newCapability.trim())
                        }
                      >
                        Add
                      </Button>
                    </SpaceBetween>

                    {formData.capabilities.length > 0 && (
                      <SpaceBetween direction="horizontal" size="xs">
                        {formData.capabilities.map((capability) => (
                          <SpaceBetween
                            key={capability}
                            direction="horizontal"
                            size="xxs"
                          >
                            <Badge color="blue">{capability}</Badge>
                            <Button
                              variant="icon"
                              iconName="close"
                              onClick={() => removeCapability(capability)}
                              ariaLabel={`Remove ${capability}`}
                            />
                          </SpaceBetween>
                        ))}
                      </SpaceBetween>
                    )}

                    {formData.capabilities.length === 0 && (
                      <Box fontSize="body-s" color="text-body-secondary">
                        No capabilities added yet. Add some capabilities to
                        describe what your agent can do.
                      </Box>
                    )}
                  </SpaceBetween>
                </FormField>
              </SpaceBetween>
            </Form>
          </Modal>
        </SpaceBetween>
      }
    />
  );
}
