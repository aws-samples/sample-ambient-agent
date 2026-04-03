// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import AppConfigured from "./components/app-configured";
import { StorageHelper } from "./common/helpers/storage-helper";
import { initializeCacheHelper } from "./common/helpers/cache-helper";
import { queryClient } from "./lib/query-client";
import "@cloudscape-design/global-styles/index.css";

const root = ReactDOM.createRoot(
  document.getElementById("root") as HTMLElement,
);

// Don't apply theme globally on initial load to prevent flash on login page
// Theme will be applied by the authenticated app or isolated login layout as needed

// Initialize cache helper for automatic cache management
initializeCacheHelper().catch(console.error);

root.render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AppConfigured />
      {/* React Query Devtools - only shows in development */}
      <ReactQueryDevtools initialIsOpen={false} />
    </QueryClientProvider>
  </React.StrictMode>,
);
