from typing import List
from discovery.base import Resource
from core.config import Config
from utils.logger import get_logger, log_skip

logger = get_logger("filters")


def apply_filters(resources: List[Resource], config: Config) -> List[Resource]:
    """
    Mark resources as protected based on:
      1. Protection tag (do_not_delete = true)
      2. Explicit skip list (resource IDs)
      3. Default VPC / default subnets
      4. IAM skip flag
      5. Caller identity role (never delete the role running the script)
    """
    for resource in resources:
        if resource.protected:
            continue

        # 1. Protection tag
        if resource.tag(config.protected_tag_key) == config.protected_tag_value:
            resource.protected = True
            log_skip(logger, resource.resource_type, resource.resource_id, "protected tag")
            continue

        # 2. Explicit skip list
        if resource.resource_id in config.skip_resource_ids:
            resource.protected = True
            log_skip(logger, resource.resource_type, resource.resource_id, "skip list")
            continue

        # 3. Default VPC / default subnets
        if config.skip_default_vpc:
            if resource.resource_type == "ec2:vpc" and resource.metadata.get("is_default"):
                resource.protected = True
                log_skip(logger, resource.resource_type, resource.resource_id, "default VPC")
                continue
            if resource.resource_type == "ec2:subnet" and resource.metadata.get("is_default"):
                resource.protected = True
                log_skip(logger, resource.resource_type, resource.resource_id, "default subnet")
                continue

        # 4. IAM global skip
        if config.skip_iam and resource.resource_type.startswith("iam:"):
            resource.protected = True
            log_skip(logger, resource.resource_type, resource.resource_id, "IAM skip enabled")
            continue

        # 5. Never delete the caller identity role
        if config.caller_identity_arn and resource.arn:
            caller_role = _extract_role_name(config.caller_identity_arn)
            if caller_role and resource.resource_type == "iam:role" and resource.resource_id == caller_role:
                resource.protected = True
                log_skip(logger, resource.resource_type, resource.resource_id, "caller identity role")
                continue

    return resources


def _extract_role_name(arn: str) -> str:
    """Extract role name from an assumed-role ARN like arn:aws:sts::123:assumed-role/MyRole/session."""
    if "assumed-role" in arn:
        parts = arn.split("/")
        if len(parts) >= 2:
            return parts[-2]
    if ":role/" in arn:
        return arn.split(":role/")[-1]
    return ""
