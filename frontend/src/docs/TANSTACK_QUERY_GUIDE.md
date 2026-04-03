# TanStack Query Integration Guide

## Overview

This React Starter Pack now includes TanStack Query (React Query) v5 integration to solve state persistence issues when navigating between pages. Data fetched from Lambda functions is now cached and persists across navigation.

## Key Benefits

- **State Persistence**: Data remains available when navigating between pages
- **Automatic Caching**: Intelligent caching with configurable stale times
- **Background Refetching**: Keeps data fresh automatically
- **Error Handling**: Built-in error states and retry logic
- **Loading States**: Consistent loading indicators
- **Optimistic Updates**: Immediate UI updates with rollback on failure

## Architecture

### Query Client Setup

The QueryClient is configured in `src/lib/query-client.ts` with optimized defaults:

```typescript
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      gcTime: 10 * 60 * 1000, // 10 minutes
      retry: 3, // Retry failed requests 3 times
      refetchOnWindowFocus: true, // Refetch on window focus
    },
    mutations: {
      retry: 1, // Retry mutations once
    },
  },
});
```

### Provider Setup

The QueryClient is provided at the app level in `src/main.tsx`:

```typescript
root.render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AppConfigured />
      <ReactQueryDevtools initialIsOpen={false} />
    </QueryClientProvider>
  </React.StrictMode>,
);
```

## Usage Patterns

### 1. Basic Query Hook

For simple data fetching that should be cached:

```typescript
import { useApiQuery } from '../hooks/use-api-query';

function MyComponent() {
  const { data, isLoading, error } = useApiQuery(
    ['my-data', userId], // Query key
    () => apiClient.getData(userId), // Query function
    {
      enabled: !!userId, // Only run when userId exists
      staleTime: 10 * 60 * 1000, // 10 minutes
    }
  );

  if (isLoading) return <StatusIndicator type="loading" />;
  if (error) return <Alert type="error">{error.message}</Alert>;

  return <div>{data?.message}</div>;
}
```

### 2. Mutation Hook

For POST/PUT/DELETE operations:

```typescript
import { useApiMutation } from '../hooks/use-api-query';

function MyComponent() {
  const mutation = useApiMutation(
    (data) => apiClient.createItem(data),
    {
      onSuccess: (data) => {
        // Update cache or invalidate queries
        queryClient.invalidateQueries(['items']);
      },
      onError: (error) => {
        console.error('Failed to create item:', error);
      },
    }
  );

  const handleSubmit = (formData) => {
    mutation.mutate(formData);
  };

  return (
    <Button
      loading={mutation.isPending}
      onClick={handleSubmit}
    >
      Create Item
    </Button>
  );
}
```

### 3. Bedrock Test Example

The main page demonstrates the integration:

```typescript
import { useBedrockTestMutation, useCachedBedrockTest } from '../hooks/use-bedrock-test';

function MainPage() {
  const bedrockTestMutation = useBedrockTestMutation();
  const cachedTestResult = useCachedBedrockTest(userName || '');

  // Get result from either mutation or cache
  const testMessage = bedrockTestMutation.data || cachedTestResult;
  const isLoading = bedrockTestMutation.isPending;
  const error = bedrockTestMutation.error?.message || null;

  const handleTest = () => {
    bedrockTestMutation.mutate({ userName });
  };

  return (
    <Button loading={isLoading} onClick={handleTest}>
      Test Bedrock
    </Button>
  );
}
```

## Query Key Patterns

Consistent query keys are crucial for cache management:

```typescript
// Entity-based keys
const queryKeys = {
  users: ["users"] as const,
  user: (id: string) => [...queryKeys.users, id] as const,
  userPosts: (id: string) => [...queryKeys.user(id), "posts"] as const,
};

// Usage
useApiQuery(queryKeys.user(userId), () => fetchUser(userId));
useApiQuery(queryKeys.userPosts(userId), () => fetchUserPosts(userId));
```

## Cache Management

### Invalidating Queries

```typescript
import { useInvalidateQueries } from "../hooks/use-api-query";

function MyComponent() {
  const invalidate = useInvalidateQueries();

  const handleRefresh = () => {
    // Invalidate all user-related queries
    invalidate.invalidateAll("users");

    // Or invalidate specific query
    invalidate.invalidateSpecific(["users", userId]);
  };
}
```

### Manual Cache Updates

```typescript
// Update cache directly after mutation
mutation.mutate(data, {
  onSuccess: (newData) => {
    queryClient.setQueryData(["item", itemId], newData);
  },
});
```

## Error Handling

### Global Error Handling

Configure global error handling in the QueryClient:

```typescript
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      onError: (error) => {
        console.error("Query error:", error);
        // Show global error notification
      },
    },
    mutations: {
      onError: (error) => {
        console.error("Mutation error:", error);
        // Show global error notification
      },
    },
  },
});
```

### Component-Level Error Handling

```typescript
const { data, error, isError } = useApiQuery(
  ['data'],
  fetchData,
  {
    onError: (error) => {
      // Handle specific error
      console.error('Specific error:', error);
    },
  }
);

if (isError) {
  return <Alert type="error">{error.message}</Alert>;
}
```

## Best Practices

### 1. Query Key Organization

```typescript
// Good: Hierarchical and consistent
const queryKeys = {
  posts: ["posts"] as const,
  post: (id: string) => [...queryKeys.posts, id] as const,
  postComments: (id: string) => [...queryKeys.post(id), "comments"] as const,
};

// Avoid: Inconsistent or flat keys
const badKeys = {
  allPosts: ["posts"],
  singlePost: ["post", id],
  comments: ["comments", postId], // Should be nested under post
};
```

### 2. Stale Time Configuration

```typescript
// Frequently changing data
useApiQuery(key, fn, { staleTime: 30 * 1000 }); // 30 seconds

// Moderately changing data
useApiQuery(key, fn, { staleTime: 5 * 60 * 1000 }); // 5 minutes

// Rarely changing data
useApiQuery(key, fn, { staleTime: 30 * 60 * 1000 }); // 30 minutes
```

### 3. Conditional Queries

```typescript
// Only fetch when dependencies are available
const { data } = useApiQuery(
  ["user-profile", userId],
  () => fetchUserProfile(userId),
  {
    enabled: !!userId && isAuthenticated,
  },
);
```

### 4. Optimistic Updates

```typescript
const mutation = useApiMutation(updateItem, {
  onMutate: async (newData) => {
    // Cancel outgoing refetches
    await queryClient.cancelQueries(["items"]);

    // Snapshot previous value
    const previousItems = queryClient.getQueryData(["items"]);

    // Optimistically update
    queryClient.setQueryData(["items"], (old) =>
      old.map((item) => (item.id === newData.id ? newData : item)),
    );

    return { previousItems };
  },
  onError: (err, newData, context) => {
    // Rollback on error
    queryClient.setQueryData(["items"], context.previousItems);
  },
  onSettled: () => {
    // Refetch after mutation
    queryClient.invalidateQueries(["items"]);
  },
});
```

## Development Tools

### React Query Devtools

The devtools are automatically included in development mode:

```typescript
// In main.tsx
<ReactQueryDevtools initialIsOpen={false} />
```

Access the devtools by clicking the React Query icon in the bottom corner of your app during development.

### Debugging Queries

```typescript
// Enable detailed logging
const queryClient = new QueryClient({
  logger: {
    log: console.log,
    warn: console.warn,
    error: console.error,
  },
});
```

## Migration from Local State

### Before (Local State)

```typescript
function MyComponent() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const result = await apiClient.getData();
        setData(result);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  // Data is lost on navigation
}
```

### After (TanStack Query)

```typescript
function MyComponent() {
  const { data, isLoading, error } = useApiQuery(
    ["my-data"],
    () => apiClient.getData(),
    {
      staleTime: 5 * 60 * 1000, // Cache for 5 minutes
    },
  );

  // Data persists across navigation!
}
```

## Common Patterns

### 1. Dependent Queries

```typescript
// Fetch user first, then user's posts
const { data: user } = useApiQuery(["user", userId], () => fetchUser(userId));

const { data: posts } = useApiQuery(
  ["user-posts", userId],
  () => fetchUserPosts(userId),
  {
    enabled: !!user, // Only fetch posts after user is loaded
  },
);
```

### 2. Parallel Queries

```typescript
// Fetch multiple independent queries
const userQuery = useApiQuery(["user", userId], () => fetchUser(userId));
const postsQuery = useApiQuery(["posts"], () => fetchPosts());
const commentsQuery = useApiQuery(["comments"], () => fetchComments());

// All queries run in parallel
const isLoading =
  userQuery.isLoading || postsQuery.isLoading || commentsQuery.isLoading;
```

### 3. Infinite Queries

```typescript
import { useInfiniteQuery } from "@tanstack/react-query";

const { data, fetchNextPage, hasNextPage, isFetchingNextPage } =
  useInfiniteQuery({
    queryKey: ["posts"],
    queryFn: ({ pageParam = 0 }) => fetchPosts(pageParam),
    getNextPageParam: (lastPage, pages) => lastPage.nextCursor,
  });
```

## Troubleshooting

### Common Issues

1. **Data not persisting**: Check query keys are consistent
2. **Stale data**: Adjust `staleTime` and `gcTime` settings
3. **Too many requests**: Use `enabled` option to control when queries run
4. **Memory leaks**: Ensure proper cleanup with `gcTime`

### Debug Steps

1. Check React Query Devtools
2. Verify query keys are consistent
3. Check network tab for API calls
4. Review cache configuration
5. Test with different stale times

## Next Steps

1. Add more API endpoints using the established patterns
2. Implement optimistic updates for better UX
3. Add offline support with background sync
4. Configure global error boundaries
5. Add query prefetching for anticipated user actions

This integration provides a solid foundation for scalable state management in your React Starter Pack!
