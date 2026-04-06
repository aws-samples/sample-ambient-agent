// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useEffect, useState } from "react";
import {
  Authenticator,
  ThemeProvider,
  defaultDarkModeOverride,
} from "@aws-amplify/ui-react";
import { APP_NAME } from "../common/constants";

interface IsolatedLoginLayoutProps {
  children: React.ReactNode;
}

export default function IsolatedLoginLayout({
  children,
}: IsolatedLoginLayoutProps) {
  const [isStylesInjected, setIsStylesInjected] = useState(false);

  // Inject styles synchronously to prevent flash
  useEffect(() => {
    // Force light mode globally when login page is active
    document.body.classList.remove("awsui-dark-mode");
    document.body.classList.add("awsui-light-mode");
    document.documentElement.style.setProperty("--app-color-scheme", "light");
    document.documentElement.setAttribute("data-awsui-theme", "light");

    // Inject custom styles immediately
    const style = document.createElement("style");
    style.id = "isolated-login-styles";
    style.textContent = `
      /* Force light mode globally when login is active */
      html, body {
        background-color: #ffffff !important;
        color: #333333 !important;
      }

      /* Override any dark mode classes globally */
      body.awsui-dark-mode {
        background-color: #ffffff !important;
        color: #333333 !important;
      }

      /* Force light mode on all AWS UI components */
      [data-awsui-theme="dark"] {
        background-color: #ffffff !important;
        color: #333333 !important;
      }

      /* Isolated login container - override any global styles */
      .isolated-login-container {
        /* Reset any global theme variables for this container */
        --app-color-scheme: light !important;
        color-scheme: light !important;

        /* Full viewport coverage */
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        width: 100vw !important;
        height: 100vh !important;
        z-index: 9999 !important;

        /* Force light theme styling */
        background-color: #ffffff !important;
        color: #333333 !important;

        /* Prevent any overflow or scrolling issues */
        overflow: hidden !important;
        margin: 0 !important;
        padding: 0 !important;
        box-sizing: border-box !important;
      }

      /* Ensure all child elements inherit light theme */
      .isolated-login-container * {
        color-scheme: light !important;
      }

      /* Remove default body/html margins and padding during login */
      .isolated-login-active {
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
      }

      /* Amplify authenticator overrides for isolation */
      .isolated-login-container .amplify-authenticator,
      .isolated-login-container .amplify-authenticator * {
        --amplify-components-authenticator-router-background-color: transparent !important;
        --amplify-components-authenticator-router-border: none !important;
        --amplify-components-authenticator-router-box-shadow: none !important;
        --amplify-components-authenticator-container-width-max: none !important;
        --amplify-components-field-control-focus-box-shadow: 0 0 0 3px rgba(37, 47, 62, 0.1) !important;
        --amplify-components-field-control-focus-border-color: #252F3E !important;

        /* Force light theme colors */
        color: #333333 !important;
        background-color: transparent !important;
      }

      /* Fix authenticator width to match container */
      .isolated-login-container .amplify-authenticator,
      .isolated-login-container [data-amplify-authenticator] {
        width: 100% !important;
        max-width: none !important;
      }

      .isolated-login-container .amplify-card,
      .isolated-login-container .amplify-authenticator__form,
      .isolated-login-container [data-amplify-authenticator-signin],
      .isolated-login-container .amplify-tabs {
        background-color: transparent !important;
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding: 0 !important;
      }

      /* Remove all form borders, shadows, and outlines - AGGRESSIVE */
      .isolated-login-container .amplify-authenticator *,
      .isolated-login-container .amplify-authenticator,
      .isolated-login-container [data-amplify-authenticator],
      .isolated-login-container [data-amplify-router],
      .isolated-login-container .amplify-authenticator div,
      .isolated-login-container .amplify-authenticator form,
      .isolated-login-container .amplify-authenticator section,
      .isolated-login-container .amplify-field-group,
      .isolated-login-container .amplify-field-group__field-wrapper {
        border: none !important;
        box-shadow: none !important;
        outline: none !important;
        background: transparent !important;
        background-color: transparent !important;
      }

      .isolated-login-container .amplify-field-group__field-wrapper {
        margin-bottom: 1.5rem !important;
      }

      /* Input field styling */
      .isolated-login-container .amplify-input,
      .isolated-login-container input[data-amplify-input],
      .isolated-login-container .amplify-input.amplify-field-group__control,
      .isolated-login-container input.amplify-input.amplify-field-group__control {
        border: 1px solid #e1e5e9 !important;
        border-radius: 8px !important;
        padding: 12px 16px !important;
        font-size: 16px !important;
        transition: border-color 0.2s ease !important;
        height: 48px !important;
        box-sizing: border-box !important;
        background: white !important;
        color: #333333 !important;
      }

      /* Focus styling */
      .isolated-login-container .amplify-input:focus,
      .isolated-login-container input[data-amplify-input]:focus,
      .isolated-login-container .amplify-input.amplify-field-group__control:focus,
      .isolated-login-container input.amplify-input.amplify-field-group__control:focus,
      .isolated-login-container .amplify-field-group__control input:focus,
      .isolated-login-container input:focus {
        border-color: rgba(11, 95, 151, 0.36) !important;
        box-shadow: 0 0 0 3px rgba(14, 141, 215, 0.28) !important;
        outline: none !important;
      }

      /* Show password button */
      .isolated-login-container .amplify-button.amplify-field-group__control.amplify-field__show-password,
      .isolated-login-container .amplify-field-group__control .amplify-button[data-amplify-fieldshowpassword],
      .isolated-login-container button[data-amplify-fieldshowpassword] {
        height: 48px !important;
        border: 1px solid #e1e5e9 !important;
        border-left: none !important;
        border-radius: 0 8px 8px 0 !important;
        background-color: #f8f9fa !important;
        color: #666 !important;
        padding: 12px !important;
        min-width: 48px !important;
        margin: 0 !important;
      }

      .isolated-login-container .amplify-button.amplify-field-group__control.amplify-field__show-password:hover,
      .isolated-login-container .amplify-field-group__control .amplify-button[data-amplify-fieldshowpassword]:hover,
      .isolated-login-container button[data-amplify-fieldshowpassword]:hover {
        background-color: #e9ecef !important;
        border-color: #FF9900 !important;
      }

      /* Password field alignment */
      .isolated-login-container .amplify-field-group__outer-end {
        display: block !important;
      }

      .isolated-login-container .amplify-field-group__control,
      .isolated-login-container .amplify-field-group[data-variation="password"] .amplify-field-group__control {
        display: flex !important;
        align-items: stretch !important;
        position: relative !important;
      }

      .isolated-login-container .amplify-field-group__control .amplify-input,
      .isolated-login-container .amplify-field-group[data-variation="password"] .amplify-input {
        border-radius: 8px 0 0 8px !important;
        flex: 1 !important;
        margin: 0 !important;
      }

      /* Primary button styling */
      .isolated-login-container .amplify-button[data-variation="primary"],
      .isolated-login-container button[data-amplify-button][data-variation="primary"],
      .isolated-login-container .amplify-button.amplify-field-group__control.amplify-button--primary,
      .isolated-login-container button.amplify-button.amplify-field-group__control.amplify-button--primary,
      .isolated-login-container .amplify-authenticator .amplify-button[data-variation="primary"],
      .isolated-login-container .amplify-authenticator button[data-variation="primary"],
      .isolated-login-container [data-amplify-authenticator] .amplify-button[data-variation="primary"],
      .isolated-login-container [data-amplify-authenticator] button[data-variation="primary"] {
        background-color: #FF9900 !important;
        background: #FF9900 !important;
        border: none !important;
        border-radius: 8px !important;
        padding: 12px 24px !important;
        font-size: 16px !important;
        font-weight: 600 !important;
        width: 100% !important;
        margin-top: 1rem !important;
        height: 48px !important;
        color: white !important;
      }

      .isolated-login-container .amplify-button[data-variation="primary"]:hover,
      .isolated-login-container button[data-amplify-button][data-variation="primary"]:hover {
        background-color: #e88900 !important;
        background: #e88900 !important;
      }

      /* Labels and text styling */
      .isolated-login-container .amplify-label,
      .isolated-login-container label,
      .isolated-login-container .amplify-text,
      .isolated-login-container p,
      .isolated-login-container span,
      .isolated-login-container div {
        color: #333333 !important;
      }

      /* Links styling */
      .isolated-login-container a {
        color: #0073bb !important;
      }

      .isolated-login-container a:hover {
        color: #005a9e !important;
      }
    `;

    document.head.appendChild(style);

    // Add class to body to prevent global styles from interfering
    document.body.classList.add("isolated-login-active");

    setIsStylesInjected(true);

    return () => {
      const existingStyle = document.getElementById("isolated-login-styles");
      if (existingStyle) {
        document.head.removeChild(existingStyle);
      }
      document.body.classList.remove("isolated-login-active");
    };
  }, []);

  // Don't render until styles are injected to prevent flash
  if (!isStylesInjected) {
    return (
      <div
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          width: "100vw",
          height: "100vh",
          backgroundColor: "#ffffff",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 9999,
        }}
      >
        <div>Loading...</div>
      </div>
    );
  }

  return (
    <div className="isolated-login-container">
      {/* Left Background Image */}
      <img
        src="/images/background-left.png"
        alt=""
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          zIndex: 1,
          opacity: 1.0,
          pointerEvents: "none",
          height: "60vh",
          width: "auto",
          objectFit: "contain",
        }}
      />

      {/* Right Background Image */}
      <img
        src="/images/background-right.png"
        alt=""
        style={{
          position: "absolute",
          bottom: 0,
          right: 0,
          zIndex: 1,
          opacity: 1.0,
          pointerEvents: "none",
          height: "60vh",
          width: "auto",
          objectFit: "contain",
        }}
      />

      {/* Centered Login Form */}
      <div
        style={{
          position: "relative",
          zIndex: 2,
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "40px 20px",
          boxSizing: "border-box",
        }}
      >
        <div
          style={{
            maxWidth: "480px",
            width: "100%",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
          }}
        >
          <div
            style={{
              textAlign: "center",
              marginBottom: "40px",
            }}
          >
            <h1
              style={{
                fontSize: "32px",
                fontWeight: "bold",
                color: "#333",
                marginBottom: "10px",
              }}
            >
              Welcome to {APP_NAME}
            </h1>
            <p
              style={{
                color: "#666",
                fontSize: "16px",
                marginBottom: "30px",
              }}
            >
              Please sign in to continue
            </p>

            {/* AWS Logo */}
            <img
              src="https://us-east-2.signin.aws.amazon.com/v2/assets/_next/static/media/aws-logo@2x.7c50e6f9.png"
              alt="AWS Logo"
              style={{
                height: "40px",
                width: "auto",
                marginBottom: "30px",
              }}
            />
          </div>

          <div style={{ width: "100%" }}>
            {/* Isolated ThemeProvider for login only */}
            <ThemeProvider
              theme={{
                name: "login-theme",
                overrides: [defaultDarkModeOverride],
              }}
              colorMode="light" // Always light mode for login
            >
              <Authenticator
                hideSignUp={true}
                components={{
                  SignIn: {
                    Header: () => null,
                    Footer: () => (
                      <>
                        <div
                          style={{
                            textAlign: "center",
                            marginTop: "20px",
                            fontSize: "14px",
                            color: "#666",
                          }}
                        >
                          Need help?{" "}
                          <a
                            href="https://docs.aws.amazon.com/signin/latest/userguide/troubleshooting-sign-in-issues.html#credentials-not-working"
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{
                              color: "#0073bb",
                              textDecoration: "underline",
                            }}
                          >
                            Contact your administrator
                          </a>
                        </div>
                        <div
                          style={{
                            position: "fixed",
                            bottom: 0,
                            left: 0,
                            right: 0,
                            backgroundColor: "white",
                            borderTop: "1px solid #e1e5e9",
                            padding: "10px 0",
                            textAlign: "center",
                            fontSize: "12px",
                            color: "#666",
                            zIndex: 10,
                          }}
                        >
                          © 2025 Amazon Web Services, Inc. or its affiliates.
                          All rights reserved.
                        </div>
                      </>
                    ),
                  },
                }}
                formFields={{
                  signIn: {
                    username: {
                      placeholder: "Enter your email address",
                      label: "Email Address",
                      labelHidden: false,
                    },
                    password: {
                      placeholder: "Enter your password",
                      label: "Password",
                      labelHidden: false,
                    },
                  },
                }}
              >
                {() => (
                  <div
                    style={{
                      position: "fixed",
                      top: 0,
                      left: 0,
                      width: "100vw",
                      height: "100vh",
                      backgroundColor: "white",
                      zIndex: 10000,
                    }}
                  >
                    {children}
                  </div>
                )}
              </Authenticator>
            </ThemeProvider>
          </div>
        </div>
      </div>
    </div>
  );
}
