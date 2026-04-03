// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { AppLayout, AppLayoutProps } from "@cloudscape-design/components";
import { useNavigationPanelState } from "../common/hooks/use-navigation-panel-state";
import NavigationPanel from "./navigation-panel";

export default function BaseAppLayout(props: AppLayoutProps) {
  const [navigationPanelState, setNavigationPanelState] =
    useNavigationPanelState();

  return (
    <AppLayout
      headerSelector="#awsui-top-navigation"
      navigation={<NavigationPanel />}
      navigationOpen={!navigationPanelState.collapsed}
      onNavigationChange={({ detail }) =>
        setNavigationPanelState({ collapsed: !detail.open })
      }
      toolsHide={true}
      contentType="default"
      {...props}
      content={
        <div
          style={{
            minHeight: "0",
            padding: "16px",
            height: "100%",
            overflow: "auto",
          }}
        >
          {props.content}
        </div>
      }
    />
  );
}
