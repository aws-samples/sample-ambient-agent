// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useState, useEffect } from "react";
import { useAutoLogout } from "../common/hooks/use-auto-logout";
import { AUTO_LOGOUT_CONFIG } from "../common/constants";
import { StorageHelper } from "../common/helpers/storage-helper";
import AutoLogoutWarningModal from "./auto-logout-warning-modal";
import App from "../app";

export default function AuthenticatedAppWrapper() {
  const [showWarningModal, setShowWarningModal] = useState(false);
  const [remainingTime, setRemainingTime] = useState(0);

  // Apply the saved theme when user is authenticated and ensure it's properly set
  useEffect(() => {
    // Clean up any login-specific styles that might interfere
    const loginStyles = document.getElementById("isolated-login-styles");
    if (loginStyles) {
      loginStyles.remove();
    }

    // Remove login-specific body classes
    document.body.classList.remove("isolated-login-active");

    // Apply the saved theme
    const savedTheme = StorageHelper.getTheme();
    StorageHelper.applyTheme(savedTheme);

    // Force a re-render of theme-dependent components by triggering a small delay
    setTimeout(() => {
      StorageHelper.applyTheme(savedTheme);
    }, 100);
  }, []);

  const { extendSession, logout, getRemainingTime } = useAutoLogout({
    timeoutMinutes: AUTO_LOGOUT_CONFIG.TIMEOUT_MINUTES,
    warningMinutes: AUTO_LOGOUT_CONFIG.WARNING_MINUTES,
    onWarning: () => {
      setRemainingTime(getRemainingTime());
      setShowWarningModal(true);
    },
    onLogout: () => {
      console.log("User automatically logged out due to inactivity");
    },
  });

  const handleExtendSession = () => {
    extendSession();
    setShowWarningModal(false);
  };

  const handleCloseWarning = () => {
    setShowWarningModal(false);
  };

  const handleLogoutNow = () => {
    logout(); // Use the logout function from the hook
  };

  return (
    <>
      <App />
      {AUTO_LOGOUT_CONFIG.ENABLED && (
        <AutoLogoutWarningModal
          visible={showWarningModal}
          remainingTimeMs={remainingTime}
          onExtendSession={handleExtendSession}
          onLogoutNow={handleLogoutNow}
          onClose={handleCloseWarning}
        />
      )}
    </>
  );
}
