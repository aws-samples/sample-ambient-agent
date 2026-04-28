// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
/**
 * Reusable chat surface used by the standalone /chat page and the Jobs
 * detail Chat tab. Message history is polled from
 * `/conversations/:sessionId`; sending is delegated to `onSendMessage` so
 * the component stays agnostic about the underlying driver (chat thread vs
 * job execution).
 *
 * Messages render as GitHub-flavoured Markdown, user turns are right-aligned
 * in an accent bubble, agent turns are left-aligned with an avatar, and the
 * composer sends on Enter (Shift+Enter for newline).
 */

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Header,
  Spinner,
  StatusIndicator,
} from "@cloudscape-design/components";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import TextareaAutosize from "react-textarea-autosize";
import { apiClient } from "../common/api-client/api-clients";

export type ChatPanelStatus = "idle" | "busy" | "awaiting_human";

export interface ChatPanelProps {
  /** Conversation-store session id that owns the message history. */
  sessionId: string;
  /** Current runtime status used to lock input while the agent is thinking. */
  status: ChatPanelStatus;
  /** Called when the user submits a message. Must not throw. */
  onSendMessage: (message: string) => Promise<void>;
  /** Optional display title for the panel header. */
  title?: string;
  /** Optional subtitle shown beneath the title (e.g. agent name). */
  subtitle?: string;
  /** Maximum height of the message scroll area (falls back to full height). */
  maxHeight?: string;
  /** Render the composer as disabled (e.g. when the job is completed). */
  disabled?: boolean;
  /** Message shown below the composer when disabled. */
  disabledReason?: string;
}

interface ConversationMessage {
  type: "human" | "ai";
  content: string;
  timestamp: string;
}

/** Poll every 3s while the agent is still working. */
const POLL_INTERVAL_MS = 3000;

export default function ChatPanel({
  sessionId,
  status,
  onSendMessage,
  title = "Chat",
  subtitle,
  maxHeight,
  disabled = false,
  disabledReason,
}: ChatPanelProps) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const previousStatusRef = useRef<ChatPanelStatus>(status);
  const queryClient = useQueryClient();

  const { data: conversation, isLoading, error, isFetching } = useQuery({
    queryKey: ["conversation", sessionId],
    queryFn: async () => {
      // Treat 404 as "conversation not started yet" rather than an error so
      // the panel can sit patiently with zero messages until the first turn
      // is persisted. Other errors still surface normally.
      try {
        return await apiClient.multiAgentClient.getConversation(sessionId, {
          limit: 200,
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        if (message.includes("404") || message.toLowerCase().includes("not found")) {
          return {
            sessionId,
            agentId: "",
            agentName: "",
            messages: [],
            totalMessages: 0,
            hasMore: false,
            createdAt: "",
            updatedAt: "",
          };
        }
        throw err;
      }
    },
    enabled: !!sessionId,
    staleTime: 3000,
    refetchOnWindowFocus: true,
    refetchOnMount: true,
    retry: false,
    refetchInterval: status === "busy" ? POLL_INTERVAL_MS : false,
    refetchIntervalInBackground: false,
  });

  // Auto-scroll to the newest message whenever content changes or the status
  // indicator appears/disappears (the typing dots push the content up).
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [conversation?.totalMessages, status]);

  // When the agent transitions out of "busy", force one final refetch so
  // the AI turn is picked up even if it was persisted a beat after the
  // parent flipped status to "idle".
  useEffect(() => {

    const previous = previousStatusRef.current;
    if (previous === "busy" && status !== "busy") {
      queryClient.invalidateQueries({ queryKey: ["conversation", sessionId] });
    }
    previousStatusRef.current = status;
  }, [status, sessionId, queryClient]);

  const handleSend = async () => {
    const text = draft.trim();
    if (!text || sending || status === "busy" || disabled) return;
    setSending(true);

    // Optimistically append the user's message to the cached conversation so
    // it appears instantly, before the server confirms the send. The
    // subsequent invalidation + refetch replaces this cache with the
    // authoritative server copy (which will also contain the AI reply).
    const optimisticMessage = {
      type: "human" as const,
      content: text,
      timestamp: new Date().toISOString(),
    };
    queryClient.setQueryData(["conversation", sessionId], (old: any) => {
      if (!old) {
        return {
          sessionId,
          agentId: "",
          agentName: "",
          messages: [optimisticMessage],
          totalMessages: 1,
          hasMore: false,
          createdAt: optimisticMessage.timestamp,
          updatedAt: optimisticMessage.timestamp,
        };
      }
      return {
        ...old,
        messages: [...(old.messages ?? []), optimisticMessage],
        totalMessages: (old.totalMessages ?? 0) + 1,
        updatedAt: optimisticMessage.timestamp,
      };
    });

    try {
      await onSendMessage(text);
      setDraft("");
    } catch (err) {
      // Keep the draft so the user can retry, and roll back the optimistic
      // message so it doesn't linger in the UI after a failure.
      console.error("Failed to send chat message", err);
      queryClient.setQueryData(["conversation", sessionId], (old: any) => {
        if (!old?.messages?.length) return old;
        return {
          ...old,
          messages: old.messages.slice(0, -1),
          totalMessages: Math.max(0, (old.totalMessages ?? 1) - 1),
        };
      });
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends the message; Shift+Enter inserts a newline. Mirrors the
    // convention set by ChatGPT, Slack, Discord, etc.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      handleSend();
    }
  };

  const statusIndicator = (() => {
    switch (status) {
      case "busy":
        return <StatusIndicator type="in-progress">Thinking</StatusIndicator>;
      case "awaiting_human":
        return (
          <StatusIndicator type="warning">Awaiting your response</StatusIndicator>
        );
      default:
        return <StatusIndicator type="success">Ready</StatusIndicator>;
    }
  })();

  const messages: ConversationMessage[] = conversation?.messages ?? [];
  const agentLabel = conversation?.agentName?.trim() || "Agent";

  const canSend =
    !disabled && !sending && status !== "busy" && draft.trim().length > 0;

  const scrollStyle = useMemo<React.CSSProperties>(

    () => ({
      flex: 1,
      minHeight: 0,
      overflowY: "auto",
      padding: "24px 16px",
      backgroundColor: "#ffffff",
      maxHeight: maxHeight ?? undefined,
    }),
    [maxHeight],
  );

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: maxHeight ?? "calc(100vh - 160px)",
        border: "1px solid #e9ebed",
        borderRadius: "12px",
        backgroundColor: "#ffffff",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid #e9ebed",
          backgroundColor: "#fafbfc",
        }}
      >
        <Header variant="h3" description={subtitle} actions={statusIndicator}>
          {title}
        </Header>
      </div>

      <div ref={scrollRef} style={scrollStyle}>
        {/*
          Only show the loading spinner on the very first fetch before
          any message data has landed (`isLoading`). `isFetching` flips
          to true on every poll interval; gating the spinner on it too
          caused a 2-second flicker between poll cycles whenever the
          message list was briefly empty. Background refetches now
          happen silently; the existing messages stay rendered.
        */}
        {isLoading && messages.length === 0 ? (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: "#5f6b7a",
            }}
          >
            <Spinner size="normal" />
          </div>
        ) : error && messages.length === 0 ? (
          <Box textAlign="center" padding="l" color="text-status-error">
            <Box variant="strong">Failed to load conversation</Box>
            <Box variant="p">
              {error instanceof Error ? error.message : String(error)}
            </Box>
          </Box>
        ) : messages.length === 0 ? (
          // If the job is already running against an empty session,
          // show the typing indicator instead of the "start chatting"
          // empty state. This covers signal-triggered jobs where the
          // worker is invoking the agent before any turns have landed.
          status === "busy" ? (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "16px",
                maxWidth: "820px",
                margin: "0 auto",
              }}
            >
              <TypingIndicator agentLabel={agentLabel} />
            </div>
          ) : (
            <EmptyState agentLabel={agentLabel} />
          )
        ) : (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "16px",
              maxWidth: "820px",
              margin: "0 auto",
            }}
          >
            {messages.map((msg, idx) => (
              <MessageBubble
                key={`${msg.type}-${idx}-${msg.timestamp}`}
                message={msg}
                agentLabel={agentLabel}
              />
            ))}
            {status === "busy" && <TypingIndicator agentLabel={agentLabel} />}
          </div>
        )}
      </div>

      <div
        style={{
          padding: "12px 16px 16px",
          borderTop: "1px solid #e9ebed",
          backgroundColor: "#fafbfc",
        }}
      >
        <div
          style={{
            maxWidth: "820px",
            margin: "0 auto",
            display: "flex",
            alignItems: "flex-end",
            gap: "8px",
            backgroundColor: "#ffffff",
            border: "1px solid #d1d5db",
            borderRadius: "14px",
            padding: "8px 8px 8px 14px",
            boxShadow: "0 1px 2px rgba(0, 0, 0, 0.04)",
          }}
        >
          <TextareaAutosize
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            minRows={1}
            maxRows={8}
            placeholder={
              disabled
                ? disabledReason || "Input disabled"
                : status === "awaiting_human"
                  ? "Answer the agent's question..."
                  : `Message ${agentLabel}...`
            }
            disabled={disabled || sending || status === "busy"}
            style={{
              flex: 1,
              resize: "none",
              border: "none",
              outline: "none",
              fontSize: "15px",
              lineHeight: "1.5",
              fontFamily: "inherit",
              padding: "6px 0",
              backgroundColor: "transparent",
              color: "#1b1b1b",
            }}
          />
          <SendButton
            onClick={handleSend}
            disabled={!canSend}
            loading={sending}
          />
        </div>
        <div
          style={{
            maxWidth: "820px",
            margin: "6px auto 0",
            fontSize: "11px",
            color: "#5f6b7a",
            textAlign: "center",
          }}
        >
          {disabled && disabledReason
            ? disabledReason
            : "Press Enter to send, Shift+Enter for a new line"}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function MessageBubble({
  message,
  agentLabel,
}: {
  message: ConversationMessage;
  agentLabel: string;
}) {
  const isHuman = message.type === "human";

  // Human turns: right-aligned blue bubble, no avatar or label.
  // Agent turns: left-aligned grey bubble with avatar + agent name.
  return (

    <div
      style={{
        display: "flex",
        justifyContent: isHuman ? "flex-end" : "flex-start",
        gap: "10px",
        alignItems: "flex-start",
      }}
    >
      {!isHuman && <AgentAvatar />}
      <div
        style={{
          maxWidth: "78%",
          display: "flex",
          flexDirection: "column",
          alignItems: isHuman ? "flex-end" : "flex-start",
        }}
      >

        {!isHuman && (
          <div
            style={{
              fontSize: "12px",
              color: "#5f6b7a",
              marginBottom: "4px",
              fontWeight: 600,
            }}
          >
            {agentLabel}
          </div>
        )}
        <div
          style={{
            backgroundColor: isHuman ? "#0972d3" : "#f2f3f5",
            color: isHuman ? "#ffffff" : "#1b1b1b",
            padding: "10px 14px",
            borderRadius: isHuman
              ? "14px 14px 4px 14px"
              : "14px 14px 14px 4px",
            fontSize: "15px",
            lineHeight: "1.55",
            wordBreak: "break-word",
          }}
          className={isHuman ? "chat-bubble-human" : "chat-bubble-agent"}

        >
          <MessageMarkdown content={message.content} inverted={isHuman} />
        </div>
      </div>
    </div>
  );
}


function MessageMarkdown({
  content,
  inverted,
}: {
  content: string;
  inverted: boolean;
}) {
  // react-markdown does not render raw HTML by default, so agent content
  // cannot inject arbitrary markup through this path.
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "8px",
      }}
    >

      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => (
            <p style={{ margin: 0 }}>{children}</p>
          ),
          ul: ({ children }) => (
            <ul style={{ margin: "4px 0", paddingLeft: "20px" }}>
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol style={{ margin: "4px 0", paddingLeft: "20px" }}>
              {children}
            </ol>
          ),
          li: ({ children }) => (
            <li style={{ marginBottom: "2px" }}>{children}</li>
          ),
          code: ({ className, children, ...props }: any) => {
            const isBlock =
              /language-/.test(className || "") ||
              String(children).includes("\n");
            if (isBlock) {
              return (
                <pre
                  style={{
                    backgroundColor: inverted
                      ? "rgba(255, 255, 255, 0.15)"
                      : "#0f1419",
                    color: inverted ? "#ffffff" : "#e6e6e6",
                    padding: "10px 12px",
                    borderRadius: "8px",
                    overflowX: "auto",
                    fontSize: "13px",
                    lineHeight: "1.5",
                    margin: "4px 0",
                  }}
                >
                  <code {...props} className={className}>
                    {children}
                  </code>
                </pre>
              );
            }
            return (
              <code
                {...props}
                style={{
                  backgroundColor: inverted
                    ? "rgba(255, 255, 255, 0.18)"
                    : "rgba(15, 20, 25, 0.08)",
                  padding: "1px 6px",
                  borderRadius: "4px",
                  fontSize: "0.9em",
                  fontFamily:
                    "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace",
                }}
              >
                {children}
              </code>
            );
          },
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                color: inverted ? "#ffffff" : "#0972d3",
                textDecoration: "underline",
              }}
            >
              {children}
            </a>
          ),
          table: ({ children }) => (
            <div style={{ overflowX: "auto", margin: "4px 0" }}>
              <table
                style={{
                  borderCollapse: "collapse",
                  fontSize: "14px",
                }}
              >
                {children}
              </table>
            </div>
          ),
          th: ({ children }) => (
            <th
              style={{
                border: `1px solid ${inverted ? "rgba(255,255,255,0.3)" : "#d1d5db"}`,
                padding: "6px 10px",
                textAlign: "left",
                fontWeight: 600,
              }}
            >
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td
              style={{
                border: `1px solid ${inverted ? "rgba(255,255,255,0.3)" : "#d1d5db"}`,
                padding: "6px 10px",
              }}
            >
              {children}
            </td>
          ),
          strong: ({ children }) => (
            <strong style={{ fontWeight: 700 }}>{children}</strong>
          ),
          em: ({ children }) => <em style={{ fontStyle: "italic" }}>{children}</em>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function AgentAvatar() {
  return (

    <div
      aria-hidden="true"
      style={{
        width: "32px",
        height: "32px",
        minWidth: "32px",
        borderRadius: "50%",
        backgroundColor: "#0972d3",
        color: "#ffffff",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: "14px",
        fontWeight: 600,
        flexShrink: 0,
      }}
    >
      AI
    </div>
  );
}


function TypingIndicator({ agentLabel }: { agentLabel: string }) {
  return (

    <div
      style={{
        display: "flex",
        justifyContent: "flex-start",
        gap: "10px",
        alignItems: "flex-start",
      }}
    >
      <AgentAvatar />
      <div
        style={{
          backgroundColor: "#f2f3f5",
          padding: "14px 16px",
          borderRadius: "14px 14px 14px 4px",
          display: "flex",
          alignItems: "center",
          gap: "4px",
        }}
        aria-label={`${agentLabel} is typing`}
      >

        <Dot delay="0s" />
        <Dot delay="0.15s" />
        <Dot delay="0.3s" />
      </div>
    </div>
  );
}

function Dot({ delay }: { delay: string }) {
  // A single bouncing dot. The <style> element is rendered inline on each
  // Dot so the keyframes are available without a separate global stylesheet;
  // browsers dedupe identical <style> tags.
  return (
    <>
      <style>{dotsKeyframes}</style>

      <span
        style={{
          width: "7px",
          height: "7px",
          borderRadius: "50%",
          backgroundColor: "#5f6b7a",
          display: "inline-block",
          animation: "chatpanel-bounce 1.2s infinite ease-in-out",
          animationDelay: delay,
        }}
      />
    </>
  );
}

const dotsKeyframes = `
@keyframes chatpanel-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.3; }
  40% { transform: translateY(-4px); opacity: 1; }
}
`;

function EmptyState({ agentLabel }: { agentLabel: string }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        textAlign: "center",
        color: "#414d5c",
        padding: "32px 16px",
      }}
    >
      <div
        style={{
          width: "56px",
          height: "56px",
          borderRadius: "50%",
          backgroundColor: "#0972d3",
          color: "#ffffff",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: "22px",
          fontWeight: 600,
          marginBottom: "16px",
        }}
      >
        AI
      </div>
      <div style={{ fontSize: "18px", fontWeight: 600, marginBottom: "6px" }}>
        Chat with {agentLabel}
      </div>
      <div style={{ fontSize: "14px", color: "#5f6b7a", maxWidth: "420px" }}>
        Ask a question or describe what you want help with. Your messages will
        appear here.
      </div>
    </div>
  );
}

function SendButton({
  onClick,
  disabled,
  loading,
}: {
  onClick: () => void;
  disabled: boolean;
  loading: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label="Send message"
      style={{
        width: "36px",
        height: "36px",
        borderRadius: "10px",
        border: "none",
        backgroundColor: disabled ? "#d1d5db" : "#0972d3",
        color: "#ffffff",
        cursor: disabled ? "not-allowed" : "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        transition: "background-color 0.15s ease",
        flexShrink: 0,
      }}
    >
      {loading ? (
        <Spinner size="normal" />
      ) : (
        // Upward arrow glyph.
        <svg

          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <line x1="12" y1="19" x2="12" y2="5" />
          <polyline points="5 12 12 5 19 12" />
        </svg>
      )}
    </button>
  );
}
