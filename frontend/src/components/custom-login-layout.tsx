// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useEffect } from "react";
import { Authenticator, useTheme } from "@aws-amplify/ui-react";
import { APP_NAME } from "../common/constants";

interface CustomLoginLayoutProps {
  children: React.ReactNode;
}

export default function CustomLoginLayout({
  children,
}: CustomLoginLayoutProps) {
  const { tokens } = useTheme();

  // Add custom CSS to remove borders and make form seamless
  useEffect(() => {
    // Add a small delay to ensure DOM is ready
    const timer = setTimeout(() => {
      // Inject custom styles with higher specificity
      const style = document.createElement("style");
      style.id = "custom-login-styles";
      style.textContent = `
        /* Remove default body/html margins and padding to prevent scrollbars */
        html, body {
          margin: 0 !important;
          padding: 0 !important;
          overflow: hidden !important;
          box-sizing: border-box !important;
        }

        /* Remove all form borders and backgrounds + Set focus colors */
        .amplify-authenticator,
        .amplify-authenticator * {
          --amplify-components-authenticator-router-background-color: transparent !important;
          --amplify-components-authenticator-router-border: none !important;
          --amplify-components-authenticator-router-box-shadow: none !important;
          --amplify-components-authenticator-container-width-max: none !important;
          --amplify-components-field-control-focus-box-shadow: 0 0 0 3px rgba(37, 47, 62, 0.1) !important;
          --amplify-components-field-control-focus-border-color: #252F3E !important;
        }

        /* Fix authenticator width to match container */
        .amplify-authenticator,
        [data-amplify-authenticator] {
          width: 100% !important;
          max-width: none !important;
        }

        .amplify-card,
        .amplify-authenticator__form,
        [data-amplify-authenticator-signin],
        .amplify-tabs {
          background-color: transparent !important;
          background: transparent !important;
          border: none !important;
          box-shadow: none !important;
          padding: 0 !important;
        }

        .amplify-field-group__field-wrapper {
          margin-bottom: 1.5rem !important;
        }

        /* Input field styling - target exact classes */
        .amplify-input,
        input[data-amplify-input],
        .amplify-input.amplify-field-group__control,
        input.amplify-input.amplify-field-group__control {
          border: 1px solid #e1e5e9 !important;
          border-radius: 8px !important;
          padding: 12px 16px !important;
          font-size: 16px !important;
          transition: border-color 0.2s ease !important;
          height: 48px !important;
          box-sizing: border-box !important;
          background: white !important;
        }

        /* Focus styling - target exact classes with enhanced navy blue highlight */
        .amplify-input:focus,
        input[data-amplify-input]:focus,
        .amplify-input.amplify-field-group__control:focus,
        input.amplify-input.amplify-field-group__control:focus,
        .amplify-field-group__control input:focus,
        input:focus,
        #amplify-id-\\:r1\\::focus,
        #amplify-id-\\:r4\\::focus {
          border-color:rgba(11, 95, 151, 0.36) !important;
          box-shadow: 0 0 0 3px rgba(14, 141, 215, 0.28) !important;
          outline: none !important;
        }

        /* Additional focus styling for better coverage */
        .amplify-field-group input:focus,
        .amplify-field-group .amplify-input:focus,
        [data-amplify-field-group] input:focus,
        [data-amplify-field-group] .amplify-input:focus {
          border-color: #252F3E !important;
          box-shadow: 0 0 0 3px rgba(37, 47, 62, 0.2) !important;
          outline: none !important;
        }

        /* Show password button - use multiple selectors for higher specificity */
        .amplify-button.amplify-field-group__control.amplify-field__show-password,
        .amplify-field-group__control .amplify-button[data-amplify-fieldshowpassword],
        button[data-amplify-fieldshowpassword] {
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

        .amplify-button.amplify-field-group__control.amplify-field__show-password:hover,
        .amplify-field-group__control .amplify-button[data-amplify-fieldshowpassword]:hover,
        button[data-amplify-fieldshowpassword]:hover {
          background-color: #e9ecef !important;
          border-color: #FF9900 !important;
        }

        /* Fix password field alignment - disable flex on outer-end */
        .amplify-field-group__outer-end {
          display: block !important;
        }

        /* Password field container alignment */
        .amplify-field-group__control,
        .amplify-field-group[data-variation="password"] .amplify-field-group__control {
          display: flex !important;
          align-items: stretch !important;
          position: relative !important;
        }

        /* Password input when show button is present */
        .amplify-field-group__control .amplify-input,
        .amplify-field-group[data-variation="password"] .amplify-input {
          border-radius: 8px 0 0 8px !important;
          flex: 1 !important;
          margin: 0 !important;
        }

        /* Remove all form outlines and borders - AGGRESSIVE */
        .amplify-authenticator,
        .amplify-authenticator *,
        .amplify-card,
        .amplify-authenticator__form,
        [data-amplify-authenticator-signin],
        .amplify-tabs,
        .amplify-field-group,
        .amplify-field-group__field-wrapper,
        .amplify-authenticator__form-container,
        .amplify-authenticator__form-wrapper,
        .amplify-authenticator div,
        .amplify-authenticator form,
        [data-amplify-router],
        [data-amplify-authenticator] {
          outline: none !important;
          border: none !important;
          box-shadow: none !important;
          background: transparent !important;
          background-color: transparent !important;
        }

        /* Target any remaining form containers */
        .amplify-authenticator > div,
        .amplify-authenticator > div > div,
        .amplify-authenticator > div > div > div {
          border: none !important;
          outline: none !important;
          box-shadow: none !important;
          background: transparent !important;
        }

        /* Primary button styling - target exact class structure */
        .amplify-button[data-variation="primary"],
        button[data-amplify-button][data-variation="primary"],
        .amplify-button.amplify-field-group__control.amplify-button--primary,
        button.amplify-button.amplify-field-group__control.amplify-button--primary,
        .amplify-authenticator .amplify-button[data-variation="primary"],
        .amplify-authenticator button[data-variation="primary"],
        [data-amplify-authenticator] .amplify-button[data-variation="primary"],
        [data-amplify-authenticator] button[data-variation="primary"] {
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

        .amplify-button[data-variation="primary"]:hover,
        button[data-amplify-button][data-variation="primary"]:hover,
        .amplify-button.amplify-field-group__control.amplify-button--primary:hover,
        button.amplify-button.amplify-field-group__control.amplify-button--primary:hover,
        .amplify-authenticator .amplify-button[data-variation="primary"]:hover,
        .amplify-authenticator button[data-variation="primary"]:hover,
        [data-amplify-authenticator] .amplify-button[data-variation="primary"]:hover,
        [data-amplify-authenticator] button[data-variation="primary"]:hover {
          background-color: #e88900 !important;
          background: #e88900 !important;
        }
      `;
      document.head.appendChild(style);
    }, 100);

    return () => {
      clearTimeout(timer);
      const existingStyle = document.getElementById("custom-login-styles");
      if (existingStyle) {
        document.head.removeChild(existingStyle);
      }
    };
  }, []);

  return (
    <div
      style={{
        minHeight: "100vh",
        backgroundColor: "white",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        width: "100vw",
        height: "100vh",
        overflow: "hidden",
        margin: 0,
        padding: "40px 20px",
        boxSizing: "border-box",
        position: "relative",
      }}
    >
      {/* Left Background Image */}
      <img
        src="/images/background-left.png"
        alt=""
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          zIndex: 0,
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
          zIndex: 0,
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
          maxWidth: "480px",
          width: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          position: "relative",
          zIndex: 1,
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
          <Authenticator
            hideSignUp={true}
            components={{
              SignIn: {
                Header: () => null, // Hide default header since we have our own
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
                    {/* Footer only visible during login */}
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
                      © 2025 Amazon Web Services, Inc. or its affiliates. All
                      rights reserved.
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
                  zIndex: 1000,
                }}
              >
                {children}
              </div>
            )}
          </Authenticator>
        </div>
      </div>
    </div>
  );
}
