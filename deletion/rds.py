import logging
import time
from botocore.exceptions import ClientError
from discovery.base import Resource
from .base import BaseDeleter

logger = logging.getLogger("aws-reset")


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
            # First delete all member instances and wait for each to complete
            for member_id in resource.metadata.get("members", []):
                try:
                    rds.delete_db_instance(
                        DBInstanceIdentifier=member_id,
                        SkipFinalSnapshot=True,
                        DeleteAutomatedBackups=True,
                    )
                except ClientError as e:
                    code = e.response["Error"]["Code"]
                    if code not in ("DBInstanceNotFound", "InvalidDBInstanceState"):
                        raise
                self._wait_instance_deleted(rds, member_id)
            rds.delete_db_cluster(
                DBClusterIdentifier=rid,
                SkipFinalSnapshot=True,
            )
            self._wait_cluster_deleted(rds, rid)

        elif rtype == "rds:instance":
            # Skip if part of a cluster (cluster deletion handles it)
            if resource.metadata.get("cluster_id"):
                return
            # Skip if already being deleted
            current_status = self._get_instance_status(rds, rid)
            if current_status in ("deleting", "deleted"):
                self._wait_instance_deleted(rds, rid)
                return
            rds.delete_db_instance(
                DBInstanceIdentifier=rid,
                SkipFinalSnapshot=True,
                DeleteAutomatedBackups=True,
            )
            self._wait_instance_deleted(rds, rid)

        elif rtype == "rds:snapshot":
            # Only deletable in available or failed state
            current_status = self._get_snapshot_status(rds, rid)
            if current_status not in ("available", "failed", None):
                logger.warning(f"Skipping snapshot {rid} — state is '{current_status}', not deletable")
                return
            rds.delete_db_snapshot(DBSnapshotIdentifier=rid)

        elif rtype == "rds:cluster_snapshot":
            current_status = self._get_cluster_snapshot_status(rds, rid)
            if current_status not in ("available", "failed", None):
                logger.warning(f"Skipping cluster snapshot {rid} — state is '{current_status}', not deletable")
                return
            rds.delete_db_cluster_snapshot(DBClusterSnapshotIdentifier=rid)

        elif rtype == "rds:subnet_group":
            rds.delete_db_subnet_group(DBSubnetGroupName=rid)

        elif rtype == "rds:parameter_group":
            rds.delete_db_parameter_group(DBParameterGroupName=rid)

    def _get_instance_status(self, rds_client, instance_id: str) -> str | None:
        try:
            resp = rds_client.describe_db_instances(DBInstanceIdentifier=instance_id)
            instances = resp.get("DBInstances", [])
            return instances[0]["DBInstanceStatus"] if instances else None
        except ClientError as e:
            if e.response["Error"]["Code"] in ("DBInstanceNotFound",):
                return "deleted"
            raise

    def _wait_instance_deleted(self, rds_client, instance_id: str, max_wait: int = 600) -> None:
        """Poll until the DB instance is fully gone."""
        elapsed = 0
        interval = 15
        while elapsed < max_wait:
            status = self._get_instance_status(rds_client, instance_id)
            if status in ("deleted", None):
                return
            logger.debug(f"Waiting for RDS instance {instance_id} to delete (status: {status})")
            time.sleep(interval)
            elapsed += interval
        logger.warning(f"Timed out waiting for RDS instance {instance_id} to delete")

    def _wait_cluster_deleted(self, rds_client, cluster_id: str, max_wait: int = 600) -> None:
        """Poll until the DB cluster is fully gone."""
        elapsed = 0
        interval = 15
        while elapsed < max_wait:
            try:
                resp = rds_client.describe_db_clusters(DBClusterIdentifier=cluster_id)
                clusters = resp.get("DBClusters", [])
                if not clusters:
                    return
                status = clusters[0]["Status"]
                if status == "deleted":
                    return
                logger.debug(f"Waiting for RDS cluster {cluster_id} to delete (status: {status})")
            except ClientError as e:
                if e.response["Error"]["Code"] in ("DBClusterNotFoundFault",):
                    return
                raise
            time.sleep(interval)
            elapsed += interval
        logger.warning(f"Timed out waiting for RDS cluster {cluster_id} to delete")

    def _get_snapshot_status(self, rds_client, snapshot_id: str) -> str | None:
        try:
            resp = rds_client.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)
            snaps = resp.get("DBSnapshots", [])
            return snaps[0]["Status"] if snaps else None
        except ClientError as e:
            if e.response["Error"]["Code"] in ("DBSnapshotNotFound",):
                return None
            raise

    def _get_cluster_snapshot_status(self, rds_client, snapshot_id: str) -> str | None:
        try:
            resp = rds_client.describe_db_cluster_snapshots(DBClusterSnapshotIdentifier=snapshot_id)
            snaps = resp.get("DBClusterSnapshots", [])
            return snaps[0]["Status"] if snaps else None
        except ClientError as e:
            if e.response["Error"]["Code"] in ("DBClusterSnapshotNotFoundFault",):
                return None
            raise
