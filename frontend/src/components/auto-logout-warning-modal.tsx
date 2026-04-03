// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useState, useEffect, useRef } from "react";
import {
  Modal,
  Box,
  SpaceBetween,
  Button,
} from "@cloudscape-design/components";
import { AUTO_LOGOUT_CONFIG } from "../common/constants";

interface AutoLogoutWarningModalProps {
  visible: boolean;
  remainingTimeMs: number;
  onExtendSession: () => void;
  onLogoutNow: () => void;
  onClose: () => void;
}

export default function AutoLogoutWarningModal({
  visible,
  onExtendSession,
  onLogoutNow,
  onClose,
}: AutoLogoutWarningModalProps) {
  // Use warning minutes from constants for the countdown timer
  const warningTimeSeconds = Math.ceil(AUTO_LOGOUT_CONFIG.WARNING_MINUTES * 60);
  const [timeLeft, setTimeLeft] = useState(warningTimeSeconds);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const hasTriggeredLogoutRef = useRef(false);

  // Add custom CSS to ensure modal appears above authenticated content
  useEffect(() => {
    const style = document.createElement("style");
    style.textContent = `
      .awsui-modal-container {
        z-index: 20000 !important;
      }
      .awsui-modal-backdrop {
        z-index: 19999 !important;
      }
      .awsui-modal {
        z-index: 20001 !important;
      }
    `;
    document.head.appendChild(style);

    return () => {
      document.head.removeChild(style);
    };
  }, []);

  useEffect(() => {
    // Clear any existing interval
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }

    if (!visible) {
      // Reset state when modal is hidden
      hasTriggeredLogoutRef.current = false;
      setTimeLeft(warningTimeSeconds);
      return;
    }

    // Initialize timer when modal becomes visible - use fixed warning time
    setTimeLeft(warningTimeSeconds);
    hasTriggeredLogoutRef.current = false;

    intervalRef.current = setInterval(() => {
      setTimeLeft((prevTime) => {
        const newTime = prevTime - 1;
        if (newTime <= 0) {
          // Just reset to warning time when countdown reaches 0
          // The hook will handle the actual logout
          return warningTimeSeconds;
        }
        return newTime;
      });
    }, 1000);

    // Cleanup function
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [visible, warningTimeSeconds, onLogoutNow, onClose]);

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const handleExtendSession = () => {
    onExtendSession();
    onClose();
  };

  const handleLogoutNow = () => {
    onLogoutNow();
    onClose();
  };

  return (
    <Modal
      visible={visible}
      onDismiss={() => {}} // Prevent dismissing by clicking outside
      header="Session Timeout Warning"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="normal" onClick={handleLogoutNow}>
              Log out now
            </Button>
            <Button variant="primary" onClick={handleExtendSession}>
              Extend session
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        <Box>
          Your session will expire due to inactivity. You will be automatically
          signed out in <strong>{formatTime(timeLeft)}</strong>.
        </Box>
        <Box>
          Click "Extend session" to continue working, or "Log out now" to sign
          out immediately.
        </Box>
      </SpaceBetween>
    </Modal>
  );
}
