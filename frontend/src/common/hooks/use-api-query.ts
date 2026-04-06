// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import {
  useQuery,
  useMutation,
  useQueryClient,
  UseQueryOptions,
  UseMutationOptions,
} from "@tanstack/react-query";
import { ApiClientBase } from "../api-client/api-client-base";

// Generic hook for API queries with authentication
export function useApiQuery<TData = unknown, TError = Error>(
  queryKey: readonly unknown[],
  queryFn: () => Promise<TData>,
  options?: Omit<UseQueryOptions<TData, TError>, "queryKey" | "queryFn">,
) {
  return useQuery({
    queryKey,
    queryFn,
    staleTime: 5 * 60 * 1000, // 5 minutes default
    gcTime: 10 * 60 * 1000, // 10 minutes default
    retry: 2,
    retryDelay: (attemptIndex: number) =>
      Math.min(1000 * 2 ** attemptIndex, 5000),
    ...options,
  });
}

// Generic hook for API mutations with authentication
export function useApiMutation<
  TData = unknown,
  TError = Error,
  TVariables = void,
>(
  mutationFn: (variables: TVariables) => Promise<TData>,
  options?: UseMutationOptions<TData, TError, TVariables>,
) {
  return useMutation({
    mutationFn,
    retry: 1,
    retryDelay: 1000,
    ...options,
  });
}

// Hook for authenticated API calls with automatic token handling
export function useAuthenticatedApiCall() {
  const apiClient = new (class extends ApiClientBase {
    async makeRequest<T>(
      method: "GET" | "POST" | "PUT" | "DELETE",
      path: string,
      body?: unknown,
    ): Promise<T> {
      const headers = await this.getHeaders();

      const response = await fetch(`/api${path}`, {
        method,
        headers: {
          "Content-Type": "application/json",
          ...headers,
        },
        body: body ? JSON.stringify(body) : undefined,
      });

      if (!response.ok) {
        throw new Error(
          `API call failed: ${response.status} ${response.statusText}`,
        );
      }

      return response.json();
    }
  })();

  return {
    get: <T>(path: string) => apiClient.makeRequest<T>("GET", path),
    post: <T>(path: string, body?: unknown) =>
      apiClient.makeRequest<T>("POST", path, body),
    put: <T>(path: string, body?: unknown) =>
      apiClient.makeRequest<T>("PUT", path, body),
    delete: <T>(path: string) => apiClient.makeRequest<T>("DELETE", path),
  };
}

// Utility to create query keys consistently
export const createQueryKey = {
  all: (entity: string) => [entity] as const,
  lists: (entity: string) => [...createQueryKey.all(entity), "list"] as const,
  list: (entity: string, filters?: Record<string, unknown>) =>
    [...createQueryKey.lists(entity), filters] as const,
  details: (entity: string) =>
    [...createQueryKey.all(entity), "detail"] as const,
  detail: (entity: string, id: string | number) =>
    [...createQueryKey.details(entity), id] as const,
};

// Hook to invalidate queries by pattern
export function useInvalidateQueries() {
  const queryClient = useQueryClient();

  return {
    invalidateAll: (entity: string) => {
      queryClient.invalidateQueries({ queryKey: createQueryKey.all(entity) });
    },
    invalidateLists: (entity: string) => {
      queryClient.invalidateQueries({ queryKey: createQueryKey.lists(entity) });
    },
    invalidateDetails: (entity: string) => {
      queryClient.invalidateQueries({
        queryKey: createQueryKey.details(entity),
      });
    },
    invalidateSpecific: (queryKey: readonly unknown[]) => {
      queryClient.invalidateQueries({ queryKey });
    },
  };
}
