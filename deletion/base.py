import time
import logging
from abc import ABC, abstractmethod
from typing import Optional
from botocore.exceptions import ClientError

from discovery.base import Resource

logger = logging.getLogger("aws-reset")


class DeletionResult:
    def __init__(self, resource: Resource, success: bool, error: Optional[str] = None, skipped: bool = False):
        self.resource = resource
        self.success = success
        self.error = error
        self.skipped = skipped


class BaseDeleter(ABC):
    """Base class for all resource deleters."""

    MAX_RETRIES = 3
    RETRY_SLEEP = 5  # seconds

    def __init__(self, dry_run: bool = False, session=None):
        import boto3
        self.dry_run = dry_run
        self.session = session or boto3.Session()

    def client(self, service: str, region: str):
        return self.session.client(service, region_name=region)

    def delete(self, resource: Resource) -> DeletionResult:
        if resource.protected:
            return DeletionResult(resource, success=True, skipped=True)

        if self.dry_run:
            logger.info(f"[DRY-RUN] Would delete {resource.resource_type} {resource.resource_id}")
            return DeletionResult(resource, success=True)

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                self._delete(resource)
                return DeletionResult(resource, success=True)
            except ClientError as e:
                code = e.response["Error"]["Code"]
                # Already deleted
                if code in ("NoSuchEntity", "ResourceNotFoundException",
                            "InvalidInstanceID.NotFound", "NoSuchBucket",
                            "DBInstanceNotFound", "DBClusterNotFoundFault"):
                    return DeletionResult(resource, success=True)
                # Rate limit — back off and retry
                if code in ("Throttling", "RequestLimitExceeded", "ThrottlingException"):
                    if attempt < self.MAX_RETRIES:
                        time.sleep(self.RETRY_SLEEP * attempt)
                        continue
                return DeletionResult(resource, success=False, error=str(e))
            except Exception as e:
                return DeletionResult(resource, success=False, error=str(e))

        return DeletionResult(resource, success=False, error="Max retries exceeded")

    @abstractmethod
    def _delete(self, resource: Resource) -> None:
        """Perform the actual deletion. Must raise on failure."""
        ...

    def can_handle(self, resource_type: str) -> bool:
        return resource_type in self.handled_types()

    @abstractmethod
    def handled_types(self) -> list:
        ...
