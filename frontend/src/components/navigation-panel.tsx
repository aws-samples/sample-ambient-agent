// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import {
  SideNavigation,
  SideNavigationProps,
} from "@cloudscape-design/components";
import { useState } from "react";
import { useOnFollow } from "../common/hooks/use-on-follow";
import { APP_NAME } from "../common/constants";
import { useLocation } from "react-router-dom";

export default function NavigationPanel() {
  const location = useLocation();
  const onFollow = useOnFollow();

  const [items] = useState<SideNavigationProps.Item[]>(() => {
    const items: SideNavigationProps.Item[] = [
      {
        type: "link",
        text: "Workflows",
        href: "/workflows",
      },
      {
        type: "link",
        text: "Agents",
        href: "/agents",
      },
      {
        type: "link",
        text: "Jobs",
        href: "/jobs",
      },
      {
        type: "link",
        text: "Signals",
        href: "/signals",
      },
    ];
    return items;
  });

  return (
    <SideNavigation
      onFollow={onFollow}
      header={{ href: "/workflows", text: APP_NAME }}
      activeHref={location.pathname}
      items={items}
    />
  );
}
