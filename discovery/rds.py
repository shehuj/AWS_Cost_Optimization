from typing import List
from .base import BaseDiscoverer, Resource, tags_to_dict


class RDSDiscoverer(BaseDiscoverer):
    """Discovers RDS instances, clusters, snapshots, subnet groups, parameter groups."""

    def discover(self) -> List[Resource]:
        resources: List[Resource] = []
        resources.extend(self._discover_clusters())
        resources.extend(self._discover_instances())
        resources.extend(self._discover_cluster_snapshots())
        resources.extend(self._discover_snapshots())
        resources.extend(self._discover_subnet_groups())
        resources.extend(self._discover_parameter_groups())
        return resources

    def _discover_clusters(self) -> List[Resource]:
        rds = self.client("rds")
        resources = []
        paginator = rds.get_paginator("describe_db_clusters")
        for page in paginator.paginate():
            for cluster in page["DBClusters"]:
                arn = cluster["DBClusterArn"]
                tags = tags_to_dict(rds.list_tags_for_resource(ResourceName=arn)["TagList"])
                resources.append(Resource(
                    resource_id=cluster["DBClusterIdentifier"],
                    resource_type="rds:cluster",
                    name=cluster["DBClusterIdentifier"],
                    region=self.region,
                    tags=tags,
                    arn=arn,
                    metadata={
                        "status": cluster["Status"],
                        "engine": cluster["Engine"],
                        "members": [m["DBInstanceIdentifier"] for m in cluster.get("DBClusterMembers", [])],
                    },
                ))
        return resources

    def _discover_instances(self) -> List[Resource]:
        rds = self.client("rds")
        resources = []
        paginator = rds.get_paginator("describe_db_instances")
        for page in paginator.paginate():
            for db in page["DBInstances"]:
                if db["DBInstanceStatus"] in ("deleted", "deleting"):
                    continue
                arn = db["DBInstanceArn"]
                tags = tags_to_dict(rds.list_tags_for_resource(ResourceName=arn)["TagList"])
                resources.append(Resource(
                    resource_id=db["DBInstanceIdentifier"],
                    resource_type="rds:instance",
                    name=db["DBInstanceIdentifier"],
                    region=self.region,
                    tags=tags,
                    arn=arn,
                    metadata={
                        "status": db["DBInstanceStatus"],
                        "engine": db["Engine"],
                        "cluster_id": db.get("DBClusterIdentifier", ""),
                    },
                ))
        return resources

    def _discover_snapshots(self) -> List[Resource]:
        rds = self.client("rds")
        resources = []
        paginator = rds.get_paginator("describe_db_snapshots")
        for page in paginator.paginate(SnapshotType="manual"):
            for snap in page["DBSnapshots"]:
                arn = snap["DBSnapshotArn"]
                tags = tags_to_dict(rds.list_tags_for_resource(ResourceName=arn)["TagList"])
                resources.append(Resource(
                    resource_id=snap["DBSnapshotIdentifier"],
                    resource_type="rds:snapshot",
                    name=snap["DBSnapshotIdentifier"],
                    region=self.region,
                    tags=tags,
                    arn=arn,
                    metadata={"status": snap["Status"]},
                ))
        return resources

    def _discover_cluster_snapshots(self) -> List[Resource]:
        rds = self.client("rds")
        resources = []
        paginator = rds.get_paginator("describe_db_cluster_snapshots")
        for page in paginator.paginate(SnapshotType="manual"):
            for snap in page["DBClusterSnapshots"]:
                arn = snap["DBClusterSnapshotArn"]
                tags = tags_to_dict(rds.list_tags_for_resource(ResourceName=arn)["TagList"])
                resources.append(Resource(
                    resource_id=snap["DBClusterSnapshotIdentifier"],
                    resource_type="rds:cluster_snapshot",
                    name=snap["DBClusterSnapshotIdentifier"],
                    region=self.region,
                    tags=tags,
                    arn=arn,
                    metadata={"status": snap["Status"]},
                ))
        return resources

    def _discover_subnet_groups(self) -> List[Resource]:
        rds = self.client("rds")
        resources = []
        paginator = rds.get_paginator("describe_db_subnet_groups")
        for page in paginator.paginate():
            for sg in page["DBSubnetGroups"]:
                arn = sg["DBSubnetGroupArn"]
                tags = tags_to_dict(rds.list_tags_for_resource(ResourceName=arn)["TagList"])
                resources.append(Resource(
                    resource_id=sg["DBSubnetGroupName"],
                    resource_type="rds:subnet_group",
                    name=sg["DBSubnetGroupName"],
                    region=self.region,
                    tags=tags,
                    arn=arn,
                ))
        return resources

    def _discover_parameter_groups(self) -> List[Resource]:
        rds = self.client("rds")
        resources = []
        paginator = rds.get_paginator("describe_db_parameter_groups")
        for page in paginator.paginate():
            for pg in page["DBParameterGroups"]:
                # Skip AWS-managed default groups
                if pg["DBParameterGroupName"].startswith("default."):
                    continue
                arn = pg["DBParameterGroupArn"]
                tags = tags_to_dict(rds.list_tags_for_resource(ResourceName=arn)["TagList"])
                resources.append(Resource(
                    resource_id=pg["DBParameterGroupName"],
                    resource_type="rds:parameter_group",
                    name=pg["DBParameterGroupName"],
                    region=self.region,
                    tags=tags,
                    arn=arn,
                ))
        return resources
