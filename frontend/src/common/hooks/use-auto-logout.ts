// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useEffect, useRef, useCallback } from "react";
import { signOut } from "aws-amplify/auth";
import { useQueryClient } from "@tanstack/react-query";
import { AUTO_LOGOUT_CONFIG } from "../constants";

interface UseAutoLogoutOptions {
  timeoutMinutes?: number;
  warningMinutes?: number;
  onWarning?: () => void;
  onLogout?: () => void;
}

export const useAutoLogout = ({
  timeoutMinutes = AUTO_LOGOUT_CONFIG.TIMEOUT_MINUTES,
  warningMinutes = AUTO_LOGOUT_CONFIG.WARNING_MINUTES,
  onWarning,
  onLogout,
}: UseAutoLogoutOptions = {}) => {
  const queryClient = useQueryClient();

  // If auto-logout is disabled, return early with no-op functions
  if (!AUTO_LOGOUT_CONFIG.ENABLED) {
    return {
      extendSession: () => {},
      logout: () => {},
      getRemainingTime: () => 0,
    };
  }
  const warningTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastActivityRef = useRef<number>(Date.now());
  const warningTriggeredRef = useRef<boolean>(false);

  const handleLogout = useCallback(async () => {
    try {
      // Clear all TanStack Query cache on logout for security
      queryClient.clear();
      console.log("Query cache cleared on logout");

      await signOut();
      onLogout?.();
      // Reload the page to show the sign-in screen
      window.location.reload();
    } catch (error) {
      console.error("Error signing out:", error);
      // Force reload even if signOut fails
      window.location.reload();
    }
  }, [onLogout, queryClient]);

  const handleWarning = useCallback(() => {
    warningTriggeredRef.current = true;
    onWarning?.();
  }, [onWarning]);

  const resetTimer = useCallback(() => {
    lastActivityRef.current = Date.now();
    warningTriggeredRef.current = false;

    // Clear existing timers
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
    if (warningTimeoutRef.current) {
      clearTimeout(warningTimeoutRef.current);
    }

    // Set warning timer
    const warningTime = (timeoutMinutes - warningMinutes) * 60 * 1000;
    if (warningTime > 0) {
      warningTimeoutRef.current = setTimeout(handleWarning, warningTime);
    }

    // Set single logout timer for total timeout - this will fire regardless of modal state
    const logoutTime = timeoutMinutes * 60 * 1000;
    timeoutRef.current = setTimeout(handleLogout, logoutTime);
  }, [timeoutMinutes, warningMinutes, handleWarning, handleLogout]);

  const extendSession = useCallback(() => {
    resetTimer();
  }, [resetTimer]);

  useEffect(() => {
    // Activity events to track
    const events = [
      "mousedown",
      "mousemove",
      "keypress",
      "scroll",
      "touchstart",
      "click",
    ];

    // Throttle activity tracking to avoid excessive timer resets
    let throttleTimeout: ReturnType<typeof setTimeout> | null = null;

    // Local timer reset function to avoid stale closures
    const localResetTimer = () => {
      lastActivityRef.current = Date.now();
      warningTriggeredRef.current = false;

      // Clear existing timers
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
      if (warningTimeoutRef.current) {
        clearTimeout(warningTimeoutRef.current);
      }

      // Set warning timer
      const warningTime = (timeoutMinutes - warningMinutes) * 60 * 1000;
      if (warningTime > 0) {
        warningTimeoutRef.current = setTimeout(handleWarning, warningTime);
      }

      // Set single logout timer for total timeout - this will fire regardless of modal state
      const logoutTime = timeoutMinutes * 60 * 1000;
      timeoutRef.current = setTimeout(handleLogout, logoutTime);
    };

    const handleActivity = () => {
      // Completely ignore all activity once warning is triggered
      if (throttleTimeout || warningTriggeredRef.current) return;

      throttleTimeout = setTimeout(() => {
        // Double check warning state before resetting
        if (!warningTriggeredRef.current) {
          localResetTimer();
        }
        throttleTimeout = null;
      }, 1000); // Throttle to once per second
    };

    // Add event listeners
    events.forEach((event) => {
      document.addEventListener(event, handleActivity, true);
    });

    // Initialize timer
    localResetTimer();

    // Cleanup
    return () => {
      events.forEach((event) => {
        document.removeEventListener(event, handleActivity, true);
      });

      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
      if (warningTimeoutRef.current) {
        clearTimeout(warningTimeoutRef.current);
      }
      if (throttleTimeout) {
        clearTimeout(throttleTimeout);
      }
    };
  }, []); // Remove all dependencies to prevent infinite re-initialization

  return {
    extendSession,
    logout: handleLogout, // Expose the logout function
    getRemainingTime: () => {
      if (warningTriggeredRef.current) {
        // If warning is active, return the warning time remaining
        const elapsed = Date.now() - lastActivityRef.current;
        const warningStartTime = (timeoutMinutes - warningMinutes) * 60 * 1000;
        const remaining = timeoutMinutes * 60 * 1000 - elapsed;
        return Math.max(0, remaining);
      } else {
        const elapsed = Date.now() - lastActivityRef.current;
        const remaining = timeoutMinutes * 60 * 1000 - elapsed;
        return Math.max(0, remaining);
      }
    },
  };
};
