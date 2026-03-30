from typing import List
from .base import BaseDiscoverer, Resource, tags_to_dict


class DynamoDBDiscoverer(BaseDiscoverer):
    """Discovers DynamoDB tables."""

    def discover(self) -> List[Resource]:
        ddb = self.client("dynamodb")
        resources = []
        paginator = ddb.get_paginator("list_tables")
        for page in paginator.paginate():
            for table_name in page["TableNames"]:
                desc = ddb.describe_table(TableName=table_name)["Table"]
                arn = desc["TableArn"]
                tags_resp = ddb.list_tags_of_resource(ResourceArn=arn)
                tags = tags_to_dict(tags_resp.get("Tags", []))
                resources.append(Resource(
                    resource_id=table_name,
                    resource_type="dynamodb:table",
                    name=table_name,
                    region=self.region,
                    tags=tags,
                    arn=arn,
                    metadata={
                        "status": desc["TableStatus"],
                        "item_count": desc.get("ItemCount", 0),
                        "size_bytes": desc.get("TableSizeBytes", 0),
                    },
                ))
        return resources
