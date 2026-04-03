// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useEffect, useState } from "react";
import {
  ThemeProvider,
  defaultDarkModeOverride,
  useTheme,
} from "@aws-amplify/ui-react";
import { StorageHelper } from "../common/helpers/storage-helper";
import { Mode } from "@cloudscape-design/global-styles";
import { StatusIndicator } from "@cloudscape-design/components";
import { Amplify, ResourcesConfig } from "aws-amplify";
import { Hub } from "aws-amplify/utils";
import AuthenticatedAppWrapper from "./authenticated-app-wrapper";
import IsolatedLoginLayout from "./isolated-login-layout";
import App from "../app";
import "@aws-amplify/ui-react/styles.css";

export default function AppConfigured() {
  const { tokens } = useTheme();
  const [config, setConfig] = useState<ResourcesConfig | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [theme, setTheme] = useState(StorageHelper.getTheme());
  const [authStateKey, setAuthStateKey] = useState(0); // Force re-mount on auth changes

  useEffect(() => {
    (async () => {
      try {
        const result = await fetch("/aws-exports.json");
        const awsExports: ResourcesConfig = await result.json();
        Amplify.configure(awsExports);
        setConfig(awsExports);
      } catch (e) {
        console.error("Error loading aws-exports.json:", e);
        const mockresult = await fetch("/aws-exports.template.json");
        const mockConfig: ResourcesConfig = await mockresult.json();
        Amplify.configure(mockConfig);
        setConfig(mockConfig);
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  // Listen for authentication state changes to force re-mount of login layout
  useEffect(() => {
    const hubListener = (data: any) => {
      const { event } = data.payload;

      if (event === "signedOut" || event === "signOut") {
        console.log(
          "Auth state changed: signed out, forcing login layout re-mount",
        );
        setAuthStateKey((prev) => prev + 1);
      }
    };

    const unsubscribe = Hub.listen("auth", hubListener);

    return () => {
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (
          mutation.type === "attributes" &&
          mutation.attributeName === "style"
        ) {
          const newValue =
            document.documentElement.style.getPropertyValue(
              "--app-color-scheme",
            );

          const mode = newValue === "dark" ? Mode.Dark : Mode.Light;
          if (mode !== theme) {
            setTheme(mode);
          }
        }
      });
    });

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["style"],
    });

    return () => {
      observer.disconnect();
    };
  }, [theme]);

  if (isLoading) {
    return (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <StatusIndicator type="loading">Loading</StatusIndicator>
      </div>
    );
  }

  return (
    <ThemeProvider
      theme={{
        name: "default-theme",
        overrides: [defaultDarkModeOverride],
      }}
      colorMode={theme === Mode.Dark ? "dark" : "light"}
    >
      {config?.Auth?.Cognito?.userPoolClientId !== "i-am-fake" ? (
        <IsolatedLoginLayout key={`login-layout-${authStateKey}`}>
          <AuthenticatedAppWrapper />
        </IsolatedLoginLayout>
      ) : (
        <>
          <div
            style={{
              padding: "10px",
              backgroundColor: "#fff3cd",
              color: "#856404",
              borderRadius: "4px",
              margin: "10px",
            }}
          >
            <strong>Development Mode:</strong> Running with mock configuration.
            Some features may be limited.
          </div>
          <App />
        </>
      )}
    </ThemeProvider>
  );
}
