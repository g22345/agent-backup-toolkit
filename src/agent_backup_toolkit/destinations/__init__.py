"""Encrypted backup destination adapters."""

from agent_backup_toolkit.destinations.github import GitHubDestinationAdapter
from agent_backup_toolkit.destinations.local import LocalDestinationAdapter
from agent_backup_toolkit.destinations.s3 import S3DestinationAdapter

__all__ = ["GitHubDestinationAdapter", "LocalDestinationAdapter", "S3DestinationAdapter"]
