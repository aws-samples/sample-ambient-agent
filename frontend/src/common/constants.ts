// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
export const USE_BROWSER_ROUTER = false;
export const APP_NAME = "Agent Management Application";

// Auto-logout configuration
export const AUTO_LOGOUT_CONFIG = {
  TIMEOUT_MINUTES: 5, // Total session timeout in minutes
  WARNING_MINUTES: 30 / 60, // Show warning this many minutes before logout
  ENABLED: true, // Enable/disable auto-logout feature
};

// TanStack Query configuration constants
export const QUERY_CONFIG = {
  // Default stale times for different data types
  STALE_TIME: {
    SHORT: 30 * 1000, // 30 seconds - for frequently changing data
    MEDIUM: 5 * 60 * 1000, // 5 minutes - for moderately changing data
    LONG: 30 * 60 * 1000, // 30 minutes - for rarely changing data
    USER_DATA: 10 * 60 * 1000, // 10 minutes - for user-specific data
    STATIC: 60 * 60 * 1000, // 1 hour - for static/configuration data
  },

  // Garbage collection times
  GC_TIME: {
    SHORT: 5 * 60 * 1000, // 5 minutes
    MEDIUM: 10 * 60 * 1000, // 10 minutes
    LONG: 30 * 60 * 1000, // 30 minutes
  },

  // Retry configuration
  RETRY: {
    DEFAULT: 3, // Default retry count
    MUTATIONS: 1, // Retry count for mutations
    MAX_DELAY: 30000, // Maximum retry delay (30 seconds)
  },

  // Refetch configuration
  REFETCH: {
    ON_WINDOW_FOCUS: true, // Refetch when window gains focus
    ON_RECONNECT: "always" as const, // Refetch on network reconnect
    INTERVAL: undefined, // No automatic refetch interval by default
  },
};

// Query key prefixes for consistent cache management
export const QUERY_KEYS = {
  BEDROCK: "bedrock",
  USER: "user",
  AUTH: "auth",
  CONFIG: "config",
  API: "api",
} as const;
