"""Entities and schemas of the built-in authorization resources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass
class Policy:
    """An access rule, created and edited from the front end."""

    id: str
    name: str
    effect: str  # allow | deny
    actions: list[str]
    subjects: dict[str, Any] = field(default_factory=dict)
    condition: Any = None
    description: str | None = None
    enabled: bool = True
    created_at: datetime | None = None
    created_by: str | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


@dataclass
class Role:
    """A custom role (system roles live in code and are not stored)."""

    name: str
    description: str | None = None
    created_at: datetime | None = None


@dataclass
class RoleAssignment:
    """A role given to a principal (``Principal.id``) or to a group."""

    id: str
    role: str
    principal: str | None = None
    group: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None


class Subjects(BaseModel):
    """Who a policy applies to. Empty means nobody, ``everyone`` any authenticated
    principal."""

    roles: list[str] = Field(default_factory=list, description="Application roles")
    groups: list[str] = Field(default_factory=list, description="Identity provider groups")
    users: list[str] = Field(default_factory=list, description="Principal ids (provider:subject)")
    everyone: bool = Field(default=False, description="Any authenticated principal")


class PolicyCreate(BaseModel):
    """A new access policy."""

    name: str = Field(min_length=1, max_length=200, description="Short name")
    description: str | None = Field(default=None, description="What it allows or denies")
    effect: Literal["allow", "deny"] = Field(description="allow or deny")
    subjects: Subjects = Field(description="Who it applies to")
    actions: list[str] = Field(min_length=1, description="Action patterns, e.g. invoices:*")
    condition: Any = Field(default=None, description="Condition (JSON), see GET /authz/catalog")
    enabled: bool = Field(default=True, description="Disabled policies are ignored")


class PolicyUpdate(BaseModel):
    """Changes to a policy."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    effect: Literal["allow", "deny"] | None = None
    subjects: Subjects | None = None
    actions: list[str] | None = Field(default=None, min_length=1)
    condition: Any = None
    enabled: bool | None = None


class PolicyRead(BaseModel):
    """An access policy."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Policy id")
    name: str = Field(description="Short name")
    description: str | None = Field(default=None, description="What it allows or denies")
    effect: str = Field(description="allow or deny")
    subjects: dict[str, Any] = Field(description="Who it applies to")
    actions: list[str] = Field(description="Action patterns")
    condition: Any = Field(default=None, description="Condition (JSON)")
    enabled: bool = Field(description="Whether it is evaluated")
    created_at: datetime | None = Field(default=None, description="Creation time")
    created_by: str | None = Field(default=None, description="Who created it")
    updated_at: datetime | None = Field(default=None, description="Last change")
    updated_by: str | None = Field(default=None, description="Who changed it last")


class RoleCreate(BaseModel):
    """A new custom role."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$", max_length=100, description="Role name")
    description: str | None = Field(default=None, description="What the role is for")


class RoleUpdate(BaseModel):
    """Changes to a custom role."""

    description: str | None = None


class RoleRead(BaseModel):
    """A custom role."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(description="Role name")
    description: str | None = Field(default=None, description="What the role is for")
    created_at: datetime | None = Field(default=None, description="Creation time")


class AssignmentCreate(BaseModel):
    """Give a role to a principal or a group (exactly one of them)."""

    role: str = Field(min_length=1, description="Role name (system or custom)")
    principal: str | None = Field(default=None, description="Principal id (provider:subject)")
    group: str | None = Field(default=None, description="Identity provider group")


class AssignmentRead(BaseModel):
    """A role assignment."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Assignment id")
    role: str = Field(description="Role name")
    principal: str | None = Field(default=None, description="Principal id")
    group: str | None = Field(default=None, description="Group")
    created_at: datetime | None = Field(default=None, description="Creation time")
    created_by: str | None = Field(default=None, description="Who assigned it")
