"""github-dkg: Ingest GitHub issues, PRs, and reviews into DKG v10 Working Memory."""

from .client import DKGClient
from .github_client import GitHubClient
from .ingestor import GitHubDKGIngestor

__all__ = ["DKGClient", "GitHubClient", "GitHubDKGIngestor"]
__version__ = "0.1.1"
