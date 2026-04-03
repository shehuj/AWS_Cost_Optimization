from typing import List
from botocore.exceptions import ClientError
from .base import BaseDiscoverer, Resource, tags_to_dict


class S3Discoverer(BaseDiscoverer):
    """Discovers S3 buckets (global, but filtered by region)."""

    def discover(self) -> List[Resource]:
        s3 = self.client("s3")
        resources = []

        response = s3.list_buckets()
        for bucket in response.get("Buckets", []):
            name = bucket["Name"]

            # Filter by region if not us-east-1 (us-east-1 is the default/global region)
            try:
                loc = s3.get_bucket_location(Bucket=name)
                bucket_region = loc["LocationConstraint"] or "us-east-1"
            except ClientError:
                continue

            if bucket_region != self.region:
                continue

            tags = {}
            try:
                tag_response = s3.get_bucket_tagging(Bucket=name)
                tags = tags_to_dict(tag_response.get("TagSet", []))
            except ClientError as e:
                if e.response["Error"]["Code"] != "NoSuchTagSet":
                    raise  # unexpected error — propagate

            resources.append(Resource(
                resource_id=name,
                resource_type="s3:bucket",
                name=name,
                region=bucket_region,
                tags=tags,
                arn=f"arn:aws:s3:::{name}",
                metadata={"versioning": self._get_versioning(s3, name)},
            ))
        return resources

    def _get_versioning(self, s3_client, bucket_name: str) -> bool:
        try:
            resp = s3_client.get_bucket_versioning(Bucket=bucket_name)
            return resp.get("Status") == "Enabled"
        except ClientError:
            return False
