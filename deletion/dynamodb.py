from discovery.base import Resource
from .base import BaseDeleter


class DynamoDBDeleter(BaseDeleter):

    def handled_types(self) -> list:
        return ["dynamodb:table"]

    def _delete(self, resource: Resource) -> None:
        ddb = self.client("dynamodb", resource.region)
        ddb.delete_table(TableName=resource.resource_id)
