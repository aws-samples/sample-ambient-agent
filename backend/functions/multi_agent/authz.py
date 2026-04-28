# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Shared ownership-check helpers.

Every API handler that reads, updates, or deletes a user-owned resource
should verify that the caller's Cognito `sub` matches the `userId`
attribute on the resource record. Centralising the helpers here means a
missed check is easy to spot in code review.
"""

from typing import Any, Dict, Optional


def caller_user_id(event: Dict[str, Any]) -> Optional[str]:
    """Return the Cognito `sub` claim from the API Gateway event, if present."""
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {})
    )
    return claims.get("sub") or claims.get("cognito:username")


def is_owner(item: Optional[Dict[str, Any]], user_id: str) -> bool:
    """Return True if `item` exists and carries a matching `userId`."""
    if not item:
        return False
    return item.get("userId") == user_id


def require_owner(
    item: Optional[Dict[str, Any]], user_id: str
) -> None:
    """Raise PermissionError when the caller does not own `item`.

    Handlers can translate the exception into the 403 response shape
    appropriate for their API.
    """
    if not is_owner(item, user_id):
        raise PermissionError("User does not own this resource")


__all__ = ["caller_user_id", "is_owner", "require_owner"]
