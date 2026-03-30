from typing import List
from .base import BaseDiscoverer, Resource, tags_to_dict

# Stacks in these statuses are already gone or being torn down
_SKIP_STATUSES = {
    "DELETE_COMPLETE", "DELETE_IN_PROGRESS",
}


class CloudFormationDiscoverer(BaseDiscoverer):
    """Discovers CloudFormation stacks."""

    def discover(self) -> List[Resource]:
        cfn = self.client("cloudformation")
        resources = []
        paginator = cfn.get_paginator("describe_stacks")
        for page in paginator.paginate():
            for stack in page["Stacks"]:
                if stack["StackStatus"] in _SKIP_STATUSES:
                    continue
                # Skip nested stacks — parent stack deletion handles them
                if stack.get("ParentId"):
                    continue
                tags = tags_to_dict(stack.get("Tags", []))
                resources.append(Resource(
                    resource_id=stack["StackId"],
                    resource_type="cloudformation:stack",
                    name=stack["StackName"],
                    region=self.region,
                    tags=tags,
                    arn=stack["StackId"],
                    metadata={"status": stack["StackStatus"]},
                ))
        return resources
