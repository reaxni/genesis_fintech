"""Role B environment. Trusted side only; never imported by a policy."""

from .core import AuditEnv, ToolError

__all__ = ["AuditEnv", "ToolError"]
