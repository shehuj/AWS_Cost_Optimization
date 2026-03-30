from typing import List
from .base import BaseDiscoverer, Resource, tags_to_dict


class EC2Discoverer(BaseDiscoverer):
    """Discovers EC2 instances, volumes, snapshots, AMIs, EIPs, security groups, key pairs."""

    def discover(self) -> List[Resource]:
        resources: List[Resource] = []
        resources.extend(self._discover_instances())
        resources.extend(self._discover_volumes())
        resources.extend(self._discover_snapshots())
        resources.extend(self._discover_amis())
        resources.extend(self._discover_elastic_ips())
        resources.extend(self._discover_key_pairs())
        return resources

    def _discover_instances(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        paginator = ec2.get_paginator("describe_instances")
        for page in paginator.paginate():
            for reservation in page["Reservations"]:
                for inst in reservation["Instances"]:
                    if inst["State"]["Name"] in ("terminated", "shutting-down"):
                        continue
                    tags = tags_to_dict(inst.get("Tags", []))
                    name = tags.get("Name", inst["InstanceId"])
                    resources.append(Resource(
                        resource_id=inst["InstanceId"],
                        resource_type="ec2:instance",
                        name=name,
                        region=self.region,
                        tags=tags,
                        arn=f"arn:aws:ec2:{self.region}:instance/{inst['InstanceId']}",
                        metadata={
                            "state": inst["State"]["Name"],
                            "vpc_id": inst.get("VpcId", ""),
                            "subnet_id": inst.get("SubnetId", ""),
                        },
                    ))
        return resources

    def _discover_volumes(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        paginator = ec2.get_paginator("describe_volumes")
        for page in paginator.paginate():
            for vol in page["Volumes"]:
                if vol["State"] == "deleted":
                    continue
                tags = tags_to_dict(vol.get("Tags", []))
                name = tags.get("Name", vol["VolumeId"])
                resources.append(Resource(
                    resource_id=vol["VolumeId"],
                    resource_type="ec2:volume",
                    name=name,
                    region=self.region,
                    tags=tags,
                    metadata={
                        "state": vol["State"],
                        "size_gb": vol["Size"],
                        "attachments": [a["InstanceId"] for a in vol.get("Attachments", [])],
                    },
                ))
        return resources

    def _discover_snapshots(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        # Only snapshots owned by this account
        paginator = ec2.get_paginator("describe_snapshots")
        for page in paginator.paginate(OwnerIds=["self"]):
            for snap in page["Snapshots"]:
                tags = tags_to_dict(snap.get("Tags", []))
                name = tags.get("Name", snap["SnapshotId"])
                resources.append(Resource(
                    resource_id=snap["SnapshotId"],
                    resource_type="ec2:snapshot",
                    name=name,
                    region=self.region,
                    tags=tags,
                    metadata={"volume_id": snap.get("VolumeId", "")},
                ))
        return resources

    def _discover_amis(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        response = ec2.describe_images(Owners=["self"])
        for image in response["Images"]:
            tags = tags_to_dict(image.get("Tags", []))
            name = image.get("Name", image["ImageId"])
            resources.append(Resource(
                resource_id=image["ImageId"],
                resource_type="ec2:ami",
                name=name,
                region=self.region,
                tags=tags,
                metadata={
                    "snapshot_ids": [
                        bdm["Ebs"]["SnapshotId"]
                        for bdm in image.get("BlockDeviceMappings", [])
                        if "Ebs" in bdm and "SnapshotId" in bdm["Ebs"]
                    ],
                },
            ))
        return resources

    def _discover_elastic_ips(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        response = ec2.describe_addresses()
        for addr in response["Addresses"]:
            tags = tags_to_dict(addr.get("Tags", []))
            alloc_id = addr.get("AllocationId", addr.get("PublicIp", ""))
            name = tags.get("Name", addr.get("PublicIp", alloc_id))
            resources.append(Resource(
                resource_id=alloc_id,
                resource_type="ec2:elastic_ip",
                name=name,
                region=self.region,
                tags=tags,
                metadata={
                    "public_ip": addr.get("PublicIp", ""),
                    "associated_instance": addr.get("InstanceId", ""),
                    "association_id": addr.get("AssociationId", ""),
                },
            ))
        return resources

    def _discover_key_pairs(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        response = ec2.describe_key_pairs()
        for kp in response["KeyPairs"]:
            tags = tags_to_dict(kp.get("Tags", []))
            resources.append(Resource(
                resource_id=kp["KeyPairId"],
                resource_type="ec2:key_pair",
                name=kp["KeyName"],
                region=self.region,
                tags=tags,
            ))
        return resources
