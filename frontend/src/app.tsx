// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import {
  HashRouter,
  BrowserRouter,
  Routes,
  Route,
  Navigate,
} from "react-router-dom";
import { USE_BROWSER_ROUTER } from "./common/constants";
import GlobalHeader from "./components/global-header";
import NotFound from "./pages/not-found";
import WorkflowsPage from "./pages/workflows";
import AgentsPage from "./pages/agents";
import JobsPage from "./pages/jobs";
import SignalsPage from "./pages/signals";
import "./styles/app.scss";

export default function App() {
  const Router = USE_BROWSER_ROUTER ? BrowserRouter : HashRouter;

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <Router>
        <GlobalHeader />
        <div
          style={{
            flex: 1,
            minHeight: 0,
            overflow: "auto",
          }}
        >
          <Routes>
            <Route
              index
              path="/"
              element={<Navigate to="/workflows" replace />}
            />
            <Route path="/workflows" element={<WorkflowsPage />} />
            <Route path="/agents" element={<AgentsPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/signals" element={<SignalsPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </div>
      </Router>
    </div>
  );
}
