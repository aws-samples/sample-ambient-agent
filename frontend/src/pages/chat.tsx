// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
/**
 * Standalone Chat page. The left column is a sidebar of the user's chat
 * threads; the right column renders the selected thread via ChatPanel. A
 * "New chat" modal lets the user pick an active agent and start a thread.
 * The outer page does not scroll; the sidebar and chat surface each own
 * their own scroll area.
 */

import {
  Box,
  Button,
  FormField,
  Header,
  Modal,
  Select,
  SelectProps,
  SpaceBetween,
  Spinner,
  StatusIndicator,
} from "@cloudscape-design/components";


import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import BaseAppLayout from "../components/base-app-layout";
import ChatPanel, { ChatPanelStatus } from "../components/chat-panel";
import { apiClient } from "../common/api-client/api-clients";
import { ChatThread } from "../types/multi-agent";


export default function ChatPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { threadId: threadIdFromUrl } = useParams<{ threadId?: string }>();

  const [newChatOpen, setNewChatOpen] = useState(false);
  const [newChatAgent, setNewChatAgent] = useState<
    SelectProps.Option | undefined
  >(undefined);

  // --- Data ------------------------------------------------------------

  const {
    data: threadsData,
    isLoading: threadsLoading,
    refetch: refetchThreads,
  } = useQuery({
    queryKey: ["chat-threads"],
    queryFn: () => apiClient.multiAgentClient.listChatThreads(),
    // Only poll while at least one thread is actively processing. Otherwise
    // rely on explicit invalidation after send / delete / create.
    refetchInterval: (query) => {
      const data = query.state.data;
      const anyBusy = data?.threads?.some((t) => t.status === "busy");
      return anyBusy ? 4000 : false;
    },
    refetchIntervalInBackground: false,
    staleTime: 2000,
  });

  const { data: agentsData, isLoading: agentsLoading } = useQuery({
    queryKey: ["agents-for-chat"],
    // Fetch all agents and filter client-side. The backend list endpoint's
    // status filter path can error on some records with missing attributes,
    // so filtering here keeps the UI resilient.
    queryFn: () => apiClient.multiAgentClient.listAgents({ pageSize: 100 }),
  });

  const threads: ChatThread[] = threadsData?.threads ?? [];
  const selectedThread = useMemo(
    () => threads.find((t) => t.threadId === threadIdFromUrl),
    [threads, threadIdFromUrl],
  );

  // If the URL points to a thread that isn't in the listing anymore
  // (deleted, TTL'd), fetch it on demand so the panel still renders.
  const { data: fallbackThread } = useQuery({
    queryKey: ["chat-thread", threadIdFromUrl],
    queryFn: () =>
      threadIdFromUrl
        ? apiClient.multiAgentClient.getChatThread(threadIdFromUrl)
        : null,
    enabled: !!threadIdFromUrl && !selectedThread && !threadsLoading,
  });

  const activeThread = selectedThread ?? (fallbackThread as ChatThread | null);

  // --- Mutations -------------------------------------------------------

  const createThreadMutation = useMutation({
    mutationFn: async (agentId: string) =>
      apiClient.multiAgentClient.createChatThread({ agentId }),
    onSuccess: (thread) => {
      queryClient.invalidateQueries({ queryKey: ["chat-threads"] });
      setNewChatOpen(false);
      setNewChatAgent(undefined);
      navigate(`/chat/${thread.threadId}`);
    },
  });

  const deleteThreadMutation = useMutation({
    mutationFn: (threadId: string) =>
      apiClient.multiAgentClient.deleteChatThread(threadId),
    onSuccess: (_data, threadId) => {
      queryClient.invalidateQueries({ queryKey: ["chat-threads"] });
      if (threadIdFromUrl === threadId) {
        navigate("/chat");
      }
    },
  });

  // --- Send handler for ChatPanel -------------------------------------

  const handleSendMessage = async (message: string) => {
    if (!activeThread) return;
    await apiClient.multiAgentClient.sendChatMessage(activeThread.threadId, {
      message,
    });
    // Optimistically bump the thread's status so the panel locks input while
    // the agent is thinking. The next poll cycle will confirm or correct it.
    queryClient.setQueryData(["chat-threads"], (old: any) => {
      if (!old?.threads) return old;
      return {
        ...old,
        threads: old.threads.map((t: ChatThread) =>
          t.threadId === activeThread.threadId
            ? { ...t, status: "busy" as const }
            : t,
        ),
      };
    });
    // Invalidate the session conversation so the user message shows up
    // instantly without waiting on the 3s poll.
    queryClient.invalidateQueries({
      queryKey: ["conversation", activeThread.sessionId],
    });
  };

  // --- Render helpers -------------------------------------------------

  const renderThreadList = () => {
    if (threadsLoading) {
      return (
        <Box textAlign="center" padding="m">
          <Spinner size="normal" />
        </Box>
      );
    }
    if (threads.length === 0) {
      return (
        <Box textAlign="center" color="text-body-secondary" padding="m">
          <Box variant="p">
            <strong>No chats yet</strong>
          </Box>
          <Box variant="p">Click &quot;New chat&quot; to start one.</Box>
        </Box>
      );
    }
    return (
      <div>
        {threads.map((t) => {
          const active = t.threadId === threadIdFromUrl;
          const deleting =
            deleteThreadMutation.isPending &&
            deleteThreadMutation.variables === t.threadId;
          return (
            <ThreadRow
              key={t.threadId}
              thread={t}
              active={active}
              deleting={deleting}
              onSelect={() => navigate(`/chat/${t.threadId}`)}
              onDelete={() => deleteThreadMutation.mutate(t.threadId)}
            />
          );
        })}
      </div>
    );
  };

  const renderActivePanel = () => {
    if (!activeThread) {
      return (
        <div
          style={{
            height: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "1px solid #e9ebed",
            borderRadius: "12px",
            backgroundColor: "#ffffff",
          }}
        >
          <Box textAlign="center" color="text-body-secondary" padding="l">
            <Box variant="strong">Select a chat or start a new one</Box>
            <Box variant="p">
              Pick a conversation from the list on the left, or click &quot;New
              chat&quot; to begin chatting with one of your registered agents.
            </Box>
          </Box>
        </div>
      );
    }
    return (
      <ChatPanel

        sessionId={activeThread.sessionId}
        status={activeThread.status as ChatPanelStatus}
        onSendMessage={handleSendMessage}
        title={activeThread.title}
        subtitle={`Agent: ${activeThread.agentName}`}
        maxHeight="100%"
      />
    );
  };


  // --- New chat modal -------------------------------------------------

  const agentOptions: SelectProps.Options = (agentsData?.agents ?? [])
    .filter((a) => a.status !== "error")
    .map((a) => ({
      label: a.agentName,
      value: a.agentId,
      description: a.description || undefined,
    }));

  const newChatModal = (
    <Modal
      visible={newChatOpen}
      header="Start a new chat"
      onDismiss={() => {
        setNewChatOpen(false);
        setNewChatAgent(undefined);
      }}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button
              variant="link"
              onClick={() => {
                setNewChatOpen(false);
                setNewChatAgent(undefined);
              }}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!newChatAgent}
              loading={createThreadMutation.isPending}
              onClick={() =>
                newChatAgent?.value &&
                createThreadMutation.mutate(newChatAgent.value)
              }
            >
              Start chat
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <FormField
        label="Agent"
        description="Pick an active agent to chat with."
      >
        <Select
          options={agentOptions}
          selectedOption={newChatAgent ?? null}
          onChange={({ detail }) => setNewChatAgent(detail.selectedOption)}
          placeholder={agentsLoading ? "Loading..." : "Choose an agent"}
          loadingText="Loading agents..."
          empty="No active agents available."
          statusType={agentsLoading ? "loading" : "finished"}
        />
      </FormField>
    </Modal>
  );

  // Fill the BaseAppLayout content slot via pure flex sizing so we never
  // spill past the viewport and trigger a document-level scrollbar.
  return (

    <BaseAppLayout
      content={
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            height: "100%",
            minHeight: 0,
            gap: "16px",
          }}
        >
          <Header variant="h1">Chat</Header>

          <div
            style={{
              flex: 1,
              minHeight: 0,
              display: "grid",
              gridTemplateColumns: "minmax(240px, 25%) 1fr",
              gap: "16px",
            }}
          >
            <div
              style={{
                border: "1px solid #e9ebed",
                borderRadius: "12px",
                backgroundColor: "#ffffff",
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
                minHeight: 0,
              }}
            >
              <div
                style={{
                  padding: "12px 14px",
                  borderBottom: "1px solid #e9ebed",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "8px",
                  backgroundColor: "#fafbfc",
                }}
              >
                <div style={{ fontWeight: 700, fontSize: "14px" }}>Chats</div>
                <SpaceBetween direction="horizontal" size="xxs">
                  <Button
                    iconName="refresh"
                    variant="icon"
                    ariaLabel="Refresh chat list"
                    onClick={() => refetchThreads()}
                    disabled={threadsLoading}
                  />
                  <Button
                    variant="primary"
                    iconName="add-plus"
                    onClick={() => setNewChatOpen(true)}
                  >
                    New chat
                  </Button>
                </SpaceBetween>
              </div>
              <div style={{ flex: 1, overflowY: "auto", padding: "4px 0" }}>
                {renderThreadList()}
              </div>
            </div>
            <div style={{ minHeight: 0, display: "flex" }}>
              <div style={{ flex: 1, minHeight: 0 }}>{renderActivePanel()}</div>
            </div>
          </div>

          {newChatModal}
        </div>
      }
    />
  );
}


// ---------------------------------------------------------------------------
// ThreadRow
// ---------------------------------------------------------------------------

interface ThreadRowProps {
  thread: ChatThread;
  active: boolean;
  deleting: boolean;
  onSelect: () => void;
  onDelete: () => void;
}

/**
 * Sidebar row for a single chat thread. Extracted so hover styling can be
 * owned locally without re-rendering the whole list.
 */
function ThreadRow({

  thread,
  active,
  deleting,
  onSelect,
  onDelete,
}: ThreadRowProps) {
  return (
    <div
      onClick={onSelect}
      style={{
        cursor: "pointer",
        margin: "2px 6px",
        padding: "10px 12px",
        borderRadius: "8px",
        backgroundColor: active ? "#e8f1fa" : "transparent",
        borderLeft: active
          ? "3px solid #0972d3"
          : "3px solid transparent",
        display: "flex",
        flexDirection: "column",
        gap: "2px",
        position: "relative",
        transition: "background-color 0.12s ease",
      }}
      onMouseEnter={(e) => {
        if (!active) {
          (e.currentTarget as HTMLDivElement).style.backgroundColor = "#f4f6f8";
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          (e.currentTarget as HTMLDivElement).style.backgroundColor =
            "transparent";
        }
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          gap: "6px",
          paddingRight: "28px",
        }}
      >
        <div
          style={{
            fontWeight: 600,
            fontSize: "13px",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            color: "#1b1b1b",
          }}
        >
          {thread.title}
        </div>
        <div style={{ flexShrink: 0 }}>
          {renderThreadStatus(thread.status as ChatPanelStatus)}
        </div>
      </div>
      <div style={{ fontSize: "11px", color: "#5f6b7a" }}>
        {thread.agentName}
      </div>
      {thread.lastMessagePreview ? (
        <div
          style={{
            fontSize: "12px",
            color: "#5f6b7a",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {thread.lastMessagePreview}
        </div>
      ) : null}
      <div
        style={{ position: "absolute", top: "6px", right: "6px" }}
        // Stop the row's click handler so the delete button doesn't also
        // navigate into the thread that's being deleted.
        onClick={(e) => e.stopPropagation()}
      >
        <Button
          iconName="remove"
          variant="icon"
          ariaLabel={`Delete chat "${thread.title}"`}
          loading={deleting}
          onClick={onDelete}
        />
      </div>
    </div>
  );
}

function renderThreadStatus(status: ChatPanelStatus) {
  switch (status) {

    case "busy":
      return <StatusIndicator type="in-progress">Thinking</StatusIndicator>;
    case "awaiting_human":
      return <StatusIndicator type="warning">Needs reply</StatusIndicator>;
    default:
      return <StatusIndicator type="success">Idle</StatusIndicator>;
  }
}
