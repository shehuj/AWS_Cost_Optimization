from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Resource:
    resource_id: str
    resource_type: str   # e.g. "ec2:instance", "s3:bucket"
    name: str
    region: str
    tags: Dict[str, str] = field(default_factory=dict)
    arn: str = ""
    metadata: Dict = field(default_factory=dict)
    protected: bool = False

    def tag(self, key: str) -> Optional[str]:
        return self.tags.get(key)

    def __repr__(self) -> str:
        return f"<Resource {self.resource_type} id={self.resource_id} name={self.name}>"


def tags_to_dict(tag_list: list) -> Dict[str, str]:
    """Convert AWS [{'Key': k, 'Value': v}] format to plain dict."""
    if not tag_list:
        return {}
    return {t["Key"]: t["Value"] for t in tag_list}


class BaseDiscoverer(ABC):
    """Base class for all resource discoverers."""

    def __init__(self, region: str, session=None):
        import boto3
        self.region = region
        self.session = session or boto3.Session(region_name=region)

    def client(self, service: str):
        return self.session.client(service, region_name=self.region)

    def resource(self, service: str):
        return self.session.resource(service, region_name=self.region)

    @abstractmethod
    def discover(self) -> List[Resource]:
        """Return all discovered resources in this region."""
        ...
