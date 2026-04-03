// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import React from "react";
import BaseAppLayout from "../components/base-app-layout";
import {
  Container,
  Header,
  ContentLayout,
} from "@cloudscape-design/components";

export default function Dashboard() {
  return (
    <BaseAppLayout
      content={
        <ContentLayout header={<Header variant="h1">Dashboard</Header>}>
          <Container>
            <p>
              Welcome to the Dashboard page. This is a template page ready for
              your content.
            </p>
          </Container>
        </ContentLayout>
      }
    />
  );
}
