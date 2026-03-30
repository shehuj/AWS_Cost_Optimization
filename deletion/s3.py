from botocore.exceptions import ClientError
from discovery.base import Resource
from .base import BaseDeleter


class S3Deleter(BaseDeleter):

    def handled_types(self) -> list:
        return ["s3:bucket"]

    def _delete(self, resource: Resource) -> None:
        bucket = resource.resource_id
        s3 = self.client("s3", resource.region)

        # Empty the bucket (required before deletion)
        self._empty_bucket(s3, bucket)

        s3.delete_bucket(Bucket=bucket)

    def _empty_bucket(self, s3_client, bucket: str) -> None:
        """Delete all object versions and delete markers."""
        versioning = False
        try:
            resp = s3_client.get_bucket_versioning(Bucket=bucket)
            versioning = resp.get("Status") == "Enabled"
        except ClientError:
            pass

        if versioning:
            self._delete_all_versions(s3_client, bucket)
        else:
            self._delete_all_objects(s3_client, bucket)

    def _delete_all_objects(self, s3_client, bucket: str) -> None:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                s3_client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})

    def _delete_all_versions(self, s3_client, bucket: str) -> None:
        paginator = s3_client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket):
            objects = []
            for v in page.get("Versions", []):
                objects.append({"Key": v["Key"], "VersionId": v["VersionId"]})
            for dm in page.get("DeleteMarkers", []):
                objects.append({"Key": dm["Key"], "VersionId": dm["VersionId"]})
            if objects:
                s3_client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
