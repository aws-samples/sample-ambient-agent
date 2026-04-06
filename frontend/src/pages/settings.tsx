// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import React from "react";
import BaseAppLayout from "../components/base-app-layout";
import {
  Container,
  Header,
  ContentLayout,
} from "@cloudscape-design/components";

export default function Settings() {
  return (
    <BaseAppLayout
      content={
        <ContentLayout header={<Header variant="h1">Settings</Header>}>
          <Container>
            <p>
              Welcome to the Settings page. This is a template page ready for
              your configuration options.
            </p>
          </Container>
        </ContentLayout>
      }
    />
  );
}
