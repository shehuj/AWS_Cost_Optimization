import time
from botocore.exceptions import ClientError
from discovery.base import Resource
from .base import BaseDeleter


class EC2Deleter(BaseDeleter):

    def handled_types(self) -> list:
        return [
            "ec2:instance",
            "ec2:volume",
            "ec2:snapshot",
            "ec2:ami",
            "ec2:elastic_ip",
            "ec2:key_pair",
        ]

    def _delete(self, resource: Resource) -> None:
        ec2 = self.client("ec2", resource.region)
        rtype = resource.resource_type
        rid = resource.resource_id

        if rtype == "ec2:instance":
            ec2.terminate_instances(InstanceIds=[rid])
            self._wait_for_termination(ec2, rid)

        elif rtype == "ec2:volume":
            # Detach if still attached
            attachments = resource.metadata.get("attachments", [])
            if attachments:
                for instance_id in attachments:
                    try:
                        ec2.detach_volume(VolumeId=rid, Force=True)
                    except ClientError:
                        pass
                time.sleep(3)
            ec2.delete_volume(VolumeId=rid)

        elif rtype == "ec2:snapshot":
            ec2.delete_snapshot(SnapshotId=rid)

        elif rtype == "ec2:ami":
            ec2.deregister_image(ImageId=rid)
            # Optionally delete backing snapshots
            for snap_id in resource.metadata.get("snapshot_ids", []):
                try:
                    ec2.delete_snapshot(SnapshotId=snap_id)
                except ClientError:
                    pass

        elif rtype == "ec2:elastic_ip":
            # Disassociate first if associated
            assoc_id = resource.metadata.get("association_id")
            if assoc_id:
                try:
                    ec2.disassociate_address(AssociationId=assoc_id)
                    time.sleep(1)  # Wait for disassociation to complete
                except ClientError as e:
                    # If address is not associated, continue anyway
                    if "InvalidAssociationID" not in str(e):
                        pass
            
            # Release the address with retry logic
            try:
                ec2.release_address(AllocationId=rid)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                # If it's a permission error, re-raise for retry
                if error_code == "OperationNotPermitted":
                    raise
                # If address doesn't exist, silently ignore
                elif error_code in ["InvalidAllocationID.NotFound", "InvalidAddress.NotFound"]:
                    pass
                else:
                    raise

        elif rtype == "ec2:key_pair":
            ec2.delete_key_pair(KeyPairId=rid)

    def _wait_for_termination(self, ec2_client, instance_id: str, max_wait: int = 300):
        """Poll until instance is terminated (max_wait seconds)."""
        elapsed = 0
        interval = 10
        while elapsed < max_wait:
            try:
                resp = ec2_client.describe_instances(InstanceIds=[instance_id])
                state = resp["Reservations"][0]["Instances"][0]["State"]["Name"]
                if state == "terminated":
                    return
            except ClientError as e:
                # Instance doesn't exist anymore (already terminated)
                if "InvalidInstanceID.NotFound" in str(e):
                    return
                raise
            time.sleep(interval)
            elapsed += interval