// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { writeFileSync, mkdirSync, existsSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Generate version info
const versionInfo = {
  version: process.env.npm_package_version || "1.0.0",
  buildTime: new Date().toISOString(),
  hash: Date.now().toString(),
  gitCommit: process.env.GITHUB_SHA || process.env.GIT_COMMIT || "unknown",
};

// Write to public/version.json
const publicDir = join(__dirname, "../public");
const versionPath = join(publicDir, "version.json");

// Create public directory if it doesn't exist
if (!existsSync(publicDir)) {
  mkdirSync(publicDir, { recursive: true });
}

writeFileSync(versionPath, JSON.stringify(versionInfo, null, 2));

console.log("Version file updated:", versionInfo);
