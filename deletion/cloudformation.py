import time
from botocore.exceptions import ClientError
from discovery.base import Resource
from .base import BaseDeleter

_TERMINAL_STATUSES = {"DELETE_COMPLETE", "DELETE_FAILED"}


class CloudFormationDeleter(BaseDeleter):

    def handled_types(self) -> list:
        return ["cloudformation:stack"]

    def _delete(self, resource: Resource) -> None:
        cfn = self.client("cloudformation", resource.region)
        stack_name = resource.name

        cfn.delete_stack(StackName=stack_name)
        self._wait_for_deletion(cfn, stack_name)

    def _wait_for_deletion(self, cfn_client, stack_name: str, max_wait: int = 600):
        elapsed = 0
        interval = 20
        while elapsed < max_wait:
            try:
                resp = cfn_client.describe_stacks(StackName=stack_name)
                stacks = resp.get("Stacks", [])
                if not stacks:
                    return
                status = stacks[0]["StackStatus"]
                if status == "DELETE_COMPLETE":
                    return
                if status == "DELETE_FAILED":
                    raise RuntimeError(f"CloudFormation stack deletion failed: {stack_name}")
            except ClientError as e:
                if e.response["Error"]["Code"] == "ValidationError" and "does not exist" in str(e):
                    return
                raise
            time.sleep(interval)
            elapsed += interval
