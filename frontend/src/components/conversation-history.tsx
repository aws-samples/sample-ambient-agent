// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import React from "react";
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  StatusIndicator,
  Alert,
  Spinner,
  Button,
} from "@cloudscape-design/components";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../common/api-client/api-clients";
interface ConversationHistoryProps {
  sessionId: string;
  maxHeight?: string;
}

interface ConversationMessage {
  type: "human" | "ai";
  content: string;
  timestamp: string;
}

export default function ConversationHistory({
  sessionId,
  maxHeight = "400px",
}: ConversationHistoryProps) {
  // Fetch conversation history - but don't show loading initially
  const {
    data: conversation,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ["conversation", sessionId],
    queryFn: () =>
      apiClient.multiAgentClient.getConversation(sessionId, { limit: 50 }),
    enabled: !!sessionId,
    // Prevent duplicate data issues with better caching strategy
    staleTime: 30000, // Consider data fresh for 30 seconds
    refetchOnWindowFocus: false, // Don't refetch on window focus
    refetchOnMount: false, // Don't refetch on component mount if data exists
    refetchOnReconnect: false, // Don't refetch on network reconnect
    retry: false, // Don't retry on failure - conversation might not exist yet
  });

  // Only refetch if we have a sessionId but no conversation data and we're not already loading
  React.useEffect(() => {
    if (sessionId && !conversation && !isLoading && !error) {
      // Single refetch attempt - no retries to prevent duplication
      refetch();
    }
  }, [sessionId]); // Only depend on sessionId to prevent excessive refetches

  // Show "Job Starting" by default when no conversation exists yet or is loading
  if (isLoading || error || !conversation || conversation.totalMessages === 0) {
    return (
      <Container>
        <Box textAlign="center" color="text-body-secondary">
          <Box variant="strong">Job Starting</Box>
          <Box variant="p">
            The agent is beginning to work on this job. Conversation history
            will appear as the agent responds.
          </Box>
        </Box>
      </Container>
    );
  }

  const formatTimestamp = (timestamp: string) => {
    const date = new Date(timestamp);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;

    return date.toLocaleDateString();
  };

  const getMessageIcon = (type: "human" | "ai") => {
    return type === "human" ? "👤" : "🤖";
  };

  const getMessageLabel = (type: "human" | "ai") => {
    return type === "human" ? "You" : conversation?.agentName || "Agent";
  };

  const renderMessage = (message: ConversationMessage, index: number) => {
    return (
      <Box
        key={`${message.type}-${index}-${message.timestamp}`}
        padding="s"
        margin={{ bottom: "s" }}
      >
        <SpaceBetween direction="vertical" size="xs">
          <Box fontSize="body-s" color="text-label">
            <SpaceBetween direction="horizontal" size="xs">
              <span>{getMessageIcon(message.type)}</span>
              <Box variant="strong">{getMessageLabel(message.type)}</Box>
            </SpaceBetween>
          </Box>

          <Box padding="s">
            <pre
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontFamily: "inherit",
                margin: 0,
                fontSize: "14px",
                lineHeight: "1.4",
              }}
            >
              {message.content}
            </pre>
          </Box>
        </SpaceBetween>
      </Box>
    );
  };

  return (
    <Container
      header={
        <Header
          variant="h3"
          description={`${conversation.totalMessages} messages with ${conversation.agentName}`}
          actions={
            conversation.hasMore && (
              <Button
                variant="link"
                onClick={() => {
                  // TODO: Implement load more functionality
                  console.log("Load more messages");
                }}
              >
                Load earlier messages
              </Button>
            )
          }
        >
          Conversation History
        </Header>
      }
    >
      <SpaceBetween direction="vertical" size="l">
        {/* Conversation messages */}
        <div
          style={{
            maxHeight: maxHeight,
            overflowY: "auto",
            border: "1px solid #e9ebed",
            borderRadius: "8px",
            padding: "12px",
          }}
        >
          <SpaceBetween direction="vertical" size="s">
            {conversation.messages.map((message, index) =>
              renderMessage(message, index),
            )}
          </SpaceBetween>
        </div>

        {/* Conversation stats */}
        <Box fontSize="body-s" color="text-body-secondary">
          <SpaceBetween direction="horizontal" size="l">
            <Box>
              <strong>Session:</strong> {conversation.sessionId.substring(0, 8)}
              ...
            </Box>
            <Box>
              <strong>Messages:</strong> {conversation.totalMessages}
            </Box>
            <Box>
              <strong>Last updated:</strong>{" "}
              {formatTimestamp(conversation.updatedAt)}
            </Box>
          </SpaceBetween>
        </Box>
      </SpaceBetween>
    </Container>
  );
}

// Export a simpler version for inline use
export function InlineConversationHistory({
  sessionId,
  maxMessages = 5,
}: {
  sessionId: string;
  maxMessages?: number;
}) {
  const {
    data: conversation,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["conversation", sessionId],
    queryFn: () =>
      apiClient.multiAgentClient.getConversation(sessionId, {
        limit: maxMessages,
      }),
    enabled: !!sessionId,
  });

  if (isLoading) {
    return <Spinner size="normal" />;
  }

  if (error || !conversation || conversation.totalMessages === 0) {
    return (
      <Box fontSize="body-s" color="text-body-secondary">
        No conversation history available
      </Box>
    );
  }

  return (
    <SpaceBetween direction="vertical" size="xs">
      {conversation.messages.slice(-maxMessages).map((message, index) => (
        <Box key={index} fontSize="body-s">
          <SpaceBetween direction="horizontal" size="xs">
            <Box variant="strong">
              {message.type === "human" ? "You:" : "Agent:"}
            </Box>
            <Box color="text-body-secondary">
              {message.content.length > 100
                ? `${message.content.substring(0, 100)}...`
                : message.content}
            </Box>
          </SpaceBetween>
        </Box>
      ))}
      {conversation.totalMessages > maxMessages && (
        <Box fontSize="body-s" color="text-body-secondary">
          ... and {conversation.totalMessages - maxMessages} more messages
        </Box>
      )}
    </SpaceBetween>
  );
}
