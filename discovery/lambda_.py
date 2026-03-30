from typing import List
from .base import BaseDiscoverer, Resource


class LambdaDiscoverer(BaseDiscoverer):
    """Discovers Lambda functions and layers."""

    def discover(self) -> List[Resource]:
        resources: List[Resource] = []
        resources.extend(self._discover_functions())
        resources.extend(self._discover_layers())
        return resources

    def _discover_functions(self) -> List[Resource]:
        lam = self.client("lambda")
        resources = []
        paginator = lam.get_paginator("list_functions")
        for page in paginator.paginate():
            for fn in page["Functions"]:
                arn = fn["FunctionArn"]
                raw_tags = lam.list_tags(Resource=arn).get("Tags", {})
                resources.append(Resource(
                    resource_id=fn["FunctionName"],
                    resource_type="lambda:function",
                    name=fn["FunctionName"],
                    region=self.region,
                    tags=raw_tags,
                    arn=arn,
                    metadata={"runtime": fn.get("Runtime", ""), "role": fn.get("Role", "")},
                ))
        return resources

    def _discover_layers(self) -> List[Resource]:
        lam = self.client("lambda")
        resources = []
        paginator = lam.get_paginator("list_layers")
        for page in paginator.paginate():
            for layer in page["Layers"]:
                latest = layer.get("LatestMatchingVersion", {})
                resources.append(Resource(
                    resource_id=layer["LayerName"],
                    resource_type="lambda:layer",
                    name=layer["LayerName"],
                    region=self.region,
                    tags={},
                    arn=layer["LayerArn"],
                    metadata={"latest_version": latest.get("Version", 0)},
                ))
        return resources
