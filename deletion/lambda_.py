import logging

from botocore.exceptions import ClientError
from discovery.base import Resource
from .base import BaseDeleter

logger = logging.getLogger("aws-reset")


class LambdaDeleter(BaseDeleter):

    def handled_types(self) -> list:
        return ["lambda:function", "lambda:layer"]

    def _delete(self, resource: Resource) -> None:
        lam = self.client("lambda", resource.region)

        if resource.resource_type == "lambda:function":
            lam.delete_function(FunctionName=resource.resource_id)

        elif resource.resource_type == "lambda:layer":
            # Delete all versions of the layer
            latest_version = resource.metadata.get("latest_version", 1)
            for version in range(1, latest_version + 1):
                try:
                    lam.delete_layer_version(
                        LayerName=resource.resource_id,
                        VersionNumber=version,
                    )
                except ClientError as e:
                    code = e.response["Error"]["Code"]
                    if code not in ("ResourceNotFoundException",):
                        logger.warning(
                            f"Could not delete layer version {version} of {resource.resource_id}: {e}"
                        )
