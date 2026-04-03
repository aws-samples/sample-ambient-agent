// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
/**
 * Cache management utilities for handling browser cache clearing
 * and version checking after deployments
 */

export interface CacheHelper {
  clearCache(): Promise<void>;
  checkForUpdates(): Promise<boolean>;
  forceRefresh(): void;
  getAppVersion(): string;
  setAppVersion(version: string): void;
}

class CacheHelperImpl implements CacheHelper {
  private readonly VERSION_KEY = "app_version";
  private readonly CHECK_INTERVAL = 300000; // 5 minutes seconds
  private checkTimer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    this.startPeriodicCheck();
  }

  /**
   * Clear all browser caches including service worker cache
   */
  async clearCache(): Promise<void> {
    try {
      // Clear service worker cache
      if ("serviceWorker" in navigator && navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({ type: "CLEAR_CACHE" });

        // Wait for cache cleared confirmation
        await new Promise<void>((resolve) => {
          const messageHandler = (event: MessageEvent) => {
            if (event.data && event.data.type === "CACHE_CLEARED") {
              navigator.serviceWorker.removeEventListener(
                "message",
                messageHandler,
              );
              resolve();
            }
          };
          navigator.serviceWorker.addEventListener("message", messageHandler);
        });
      }

      // Clear browser storage
      if ("caches" in window) {
        const cacheNames = await caches.keys();
        await Promise.all(
          cacheNames.map((cacheName) => caches.delete(cacheName)),
        );
      }

      // Clear localStorage version
      localStorage.removeItem(this.VERSION_KEY);

      console.log("Cache cleared successfully");
    } catch (error) {
      console.error("Failed to clear cache:", error);
      throw error;
    }
  }

  /**
   * Check if there's a new version available
   */
  async checkForUpdates(): Promise<boolean> {
    try {
      const currentVersion = this.getAppVersion();

      const response = await fetch("/version.json?t=" + Date.now(), {
        cache: "no-cache",
        headers: {
          "Cache-Control": "no-cache, no-store, must-revalidate",
          Pragma: "no-cache",
          Expires: "0",
        },
      });

      if (!response.ok) {
        return false;
      }

      const versionData = await response.json();
      const serverVersion = versionData.buildTime || versionData.version;

      // Only update if we actually have a different version
      if (currentVersion && currentVersion !== serverVersion) {
        console.log("New app version available:", serverVersion);
        return true;
      }

      // If no current version stored, store the server version but don't trigger update
      if (!currentVersion || currentVersion === "1.0.0") {
        this.setAppVersion(serverVersion);
        console.log("App version initialized:", serverVersion);
      }

      return false;
    } catch (error) {
      console.error("Failed to check for updates:", error);
      return false;
    }
  }

  /**
   * Force refresh the page and clear cache
   */
  forceRefresh(): void {
    this.clearCache()
      .then(() => {
        window.location.reload();
      })
      .catch(() => {
        // Fallback: just reload
        window.location.reload();
      });
  }

  /**
   * Get current app version
   */
  getAppVersion(): string {
    return (
      localStorage.getItem(this.VERSION_KEY) ||
      (window as any).APP_VERSION ||
      "1.0.0"
    );
  }

  /**
   * Set app version
   */
  setAppVersion(version: string): void {
    localStorage.setItem(this.VERSION_KEY, version);
    (window as any).APP_VERSION = version;
  }

  /**
   * Start periodic check for updates
   */
  private startPeriodicCheck(): void {
    // Wait a bit before starting periodic checks to allow initialization to complete
    setTimeout(() => {
      this.checkTimer = setInterval(async () => {
        const hasUpdate = await this.checkForUpdates();
        if (hasUpdate) {
          this.handleUpdateAvailable();
        }
      }, this.CHECK_INTERVAL);
    }, 5000); // Wait 5 seconds before starting periodic checks
  }

  /**
   * Handle when update is available
   */
  private handleUpdateAvailable(): void {
    // Automatically refresh when new version is detected
    console.log("New version detected, automatically refreshing...");
    console.log("Current stored version:", this.getAppVersion());

    this.forceRefresh();
  }

  /**
   * Stop periodic checking
   */
  destroy(): void {
    if (this.checkTimer) {
      clearInterval(this.checkTimer);
    }
  }
}

// Export singleton instance
export const cacheHelper: CacheHelper = new CacheHelperImpl();

/**
 * React hook for cache management
 */
export const useCacheHelper = () => {
  const clearCache = async () => {
    await cacheHelper.clearCache();
  };

  const checkForUpdates = async () => {
    return await cacheHelper.checkForUpdates();
  };

  const forceRefresh = () => {
    cacheHelper.forceRefresh();
  };

  return {
    clearCache,
    checkForUpdates,
    forceRefresh,
    getAppVersion: cacheHelper.getAppVersion,
    setAppVersion: cacheHelper.setAppVersion,
  };
};

/**
 * Initialize cache helper when app starts
 */
export const initializeCacheHelper = async () => {
  console.log("Cache helper initializing...");

  // Check for initial version from server
  try {
    const response = await fetch("/version.json?t=" + Date.now(), {
      cache: "no-cache",
    });

    if (response.ok) {
      const versionData = await response.json();
      const serverVersion = versionData.buildTime || versionData.version;

      // Always sync with server version on initialization
      cacheHelper.setAppVersion(serverVersion);
      console.log(
        "Cache helper initialized with server version:",
        serverVersion,
      );
    }
  } catch (error) {
    console.log("Could not fetch initial version, using fallback");
    // Set a fallback version
    cacheHelper.setAppVersion("1.0.0");
  }
};
