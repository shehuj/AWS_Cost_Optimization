import os
import yaml
from dataclasses import dataclass, field
from typing import List, Optional


ALL_SERVICES = [
    "cloudformation",
    "ec2",
    "s3",
    "rds",
    "lambda",
    "dynamodb",
    "vpc",
    "iam",
]

# Deletion priority — lower number is deleted first
DELETION_PRIORITY = {
    "cloudformation:stack": 0,
    "ec2:instance": 10,
    "lambda:function": 10,
    "lambda:layer": 10,
    "ec2:key_pair": 10,
    "rds:cluster": 20,
    "rds:instance": 21,
    "rds:snapshot": 22,
    "rds:cluster_snapshot": 22,
    "rds:subnet_group": 25,
    "rds:parameter_group": 26,
    "dynamodb:table": 20,
    "s3:bucket": 20,
    "elb:load_balancer": 30,
    "elbv2:load_balancer": 30,
    "ec2:nat_gateway": 40,
    "ec2:volume": 50,
    "ec2:snapshot": 50,
    "ec2:ami": 51,
    "ec2:elastic_ip": 60,
    "ec2:vpc_endpoint": 65,
    "ec2:security_group": 70,
    "ec2:route_table": 80,
    "ec2:network_acl": 81,
    "ec2:subnet": 90,
    "ec2:internet_gateway": 100,
    "ec2:vpc": 110,
    "iam:role": 120,
    "iam:user": 121,
    "iam:group": 122,
    "iam:policy": 130,
}


@dataclass
class Config:
    regions: List[str] = field(default_factory=lambda: ["us-east-1"])
    services: List[str] = field(default_factory=lambda: list(ALL_SERVICES))
    protected_tag_key: str = "do_not_delete"
    protected_tag_value: str = "true"
    dry_run: bool = False
    skip_iam: bool = True
    skip_default_vpc: bool = True
    skip_cloudformation: bool = False
    skip_resource_ids: List[str] = field(default_factory=list)
    max_workers: int = 5
    caller_identity_arn: Optional[str] = None  # Set at runtime; never delete this role

    @classmethod
    def from_file(cls, path: str) -> "Config":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls()
        if os.environ.get("AWS_REGIONS"):
            cfg.regions = [r.strip() for r in os.environ["AWS_REGIONS"].split(",")]
        if os.environ.get("PROTECTED_TAG_KEY"):
            cfg.protected_tag_key = os.environ["PROTECTED_TAG_KEY"]
        if os.environ.get("PROTECTED_TAG_VALUE"):
            cfg.protected_tag_value = os.environ["PROTECTED_TAG_VALUE"]
        if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
            cfg.dry_run = True
        if os.environ.get("SKIP_IAM", "true").lower() in ("1", "true", "yes"):
            cfg.skip_iam = True
        if os.environ.get("SKIP_DEFAULT_VPC", "true").lower() in ("1", "true", "yes"):
            cfg.skip_default_vpc = True
        if os.environ.get("SERVICES"):
            cfg.services = [s.strip() for s in os.environ["SERVICES"].split(",")]
        return cfg
