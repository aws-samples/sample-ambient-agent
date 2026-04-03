// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { QueryClient } from "@tanstack/react-query";
import { QUERY_CONFIG } from "../common/constants";

// Create a query client with optimized settings for the React Starter Pack
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Cache data for 5 minutes by default
      staleTime: QUERY_CONFIG.STALE_TIME.MEDIUM,
      // Keep data in cache for 10 minutes
      gcTime: QUERY_CONFIG.GC_TIME.MEDIUM,
      // Retry failed requests 3 times
      retry: QUERY_CONFIG.RETRY.DEFAULT,
      // Retry with exponential backoff
      retryDelay: (attemptIndex: number) =>
        Math.min(1000 * 2 ** attemptIndex, QUERY_CONFIG.RETRY.MAX_DELAY),
      // Refetch on window focus for fresh data
      refetchOnWindowFocus: QUERY_CONFIG.REFETCH.ON_WINDOW_FOCUS,
      // Refetch on reconnect
      refetchOnReconnect: QUERY_CONFIG.REFETCH.ON_RECONNECT,
      // No automatic refetch interval by default
      refetchInterval: QUERY_CONFIG.REFETCH.INTERVAL,
    },
    mutations: {
      // Retry mutations once on failure
      retry: QUERY_CONFIG.RETRY.MUTATIONS,
      // Retry delay for mutations
      retryDelay: 1000,
    },
  },
});
