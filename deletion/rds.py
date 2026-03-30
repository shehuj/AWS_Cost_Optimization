import time
from botocore.exceptions import ClientError
from discovery.base import Resource
from .base import BaseDeleter


class RDSDeleter(BaseDeleter):

    def handled_types(self) -> list:
        return [
            "rds:cluster",
            "rds:instance",
            "rds:snapshot",
            "rds:cluster_snapshot",
            "rds:subnet_group",
            "rds:parameter_group",
        ]

    def _delete(self, resource: Resource) -> None:
        rds = self.client("rds", resource.region)
        rtype = resource.resource_type
        rid = resource.resource_id

        if rtype == "rds:cluster":
            # First delete all member instances
            for member_id in resource.metadata.get("members", []):
                try:
                    rds.delete_db_instance(
                        DBInstanceIdentifier=member_id,
                        SkipFinalSnapshot=True,
                        DeleteAutomatedBackups=True,
                    )
                except ClientError as e:
                    if "DBInstanceNotFound" not in str(e):
                        raise
            rds.delete_db_cluster(
                DBClusterIdentifier=rid,
                SkipFinalSnapshot=True,
            )

        elif rtype == "rds:instance":
            # Skip if part of a cluster (cluster deletion handles it)
            if resource.metadata.get("cluster_id"):
                return
            rds.delete_db_instance(
                DBInstanceIdentifier=rid,
                SkipFinalSnapshot=True,
                DeleteAutomatedBackups=True,
            )

        elif rtype == "rds:snapshot":
            rds.delete_db_snapshot(DBSnapshotIdentifier=rid)

        elif rtype == "rds:cluster_snapshot":
            rds.delete_db_cluster_snapshot(DBClusterSnapshotIdentifier=rid)

        elif rtype == "rds:subnet_group":
            rds.delete_db_subnet_group(DBSubnetGroupName=rid)

        elif rtype == "rds:parameter_group":
            rds.delete_db_parameter_group(DBParameterGroupName=rid)
