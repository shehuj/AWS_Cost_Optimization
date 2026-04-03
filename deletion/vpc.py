import logging
import time
from botocore.exceptions import ClientError
from discovery.base import Resource
from .base import BaseDeleter

logger = logging.getLogger("aws-reset")


class VPCDeleter(BaseDeleter):

    def handled_types(self) -> list:
        return [
            "ec2:vpc",
            "ec2:subnet",
            "ec2:route_table",
            "ec2:internet_gateway",
            "ec2:nat_gateway",
            "ec2:vpc_endpoint",
            "ec2:network_interface",
            "ec2:security_group",
            "ec2:network_acl",
            "elbv2:load_balancer",
            "elb:load_balancer",
        ]

    def _delete(self, resource: Resource) -> None:
        ec2 = self.client("ec2", resource.region)
        rtype = resource.resource_type
        rid = resource.resource_id

        if rtype == "ec2:vpc":
            ec2.delete_vpc(VpcId=rid)

        elif rtype == "ec2:subnet":
            # ENIs should already be gone from the dedicated ec2:network_interface wave.
            # Do a quick cleanup of any stragglers created after discovery.
            self._delete_available_enis(ec2, [{"Name": "subnet-id", "Values": [rid]}], f"subnet {rid}")
            ec2.delete_subnet(SubnetId=rid)

        elif rtype == "ec2:route_table":
            if resource.metadata.get("is_main"):
                return  # Can't delete the main route table
            # Disassociate subnet associations first
            rt = ec2.describe_route_tables(RouteTableIds=[rid])["RouteTables"][0]
            for assoc in rt.get("Associations", []):
                if not assoc.get("Main", False) and "RouteTableAssociationId" in assoc:
                    try:
                        ec2.disassociate_route_table(AssociationId=assoc["RouteTableAssociationId"])
                    except ClientError:
                        pass
            ec2.delete_route_table(RouteTableId=rid)

        elif rtype == "ec2:internet_gateway":
            # Re-query current attachments (metadata may be stale from discovery)
            igw_resp = ec2.describe_internet_gateways(InternetGatewayIds=[rid])
            igw_attachments = igw_resp["InternetGateways"][0].get("Attachments", []) if igw_resp["InternetGateways"] else []
            for attachment in igw_attachments:
                vpc_id = attachment.get("VpcId")
                if vpc_id:
                    try:
                        ec2.detach_internet_gateway(InternetGatewayId=rid, VpcId=vpc_id)
                    except ClientError:
                        pass
            ec2.delete_internet_gateway(InternetGatewayId=rid)

        elif rtype == "ec2:nat_gateway":
            ec2.delete_nat_gateway(NatGatewayId=rid)
            self._wait_nat_deleted(ec2, rid)

        elif rtype == "ec2:vpc_endpoint":
            ec2.delete_vpc_endpoints(VpcEndpointIds=[rid])

        elif rtype == "ec2:network_interface":
            self._delete_network_interface(ec2, rid, resource)

        elif rtype == "ec2:security_group":
            # Remove all ingress/egress rules that may reference other SGs
            sg = ec2.describe_security_groups(GroupIds=[rid])["SecurityGroups"][0]
            if sg.get("IpPermissions"):
                try:
                    ec2.revoke_security_group_ingress(GroupId=rid, IpPermissions=sg["IpPermissions"])
                except ClientError:
                    pass
            if sg.get("IpPermissionsEgress"):
                try:
                    ec2.revoke_security_group_egress(GroupId=rid, IpPermissions=sg["IpPermissionsEgress"])
                except ClientError:
                    pass
            # Quick cleanup of any stragglers (dedicated ENI wave handles the main wait)
            self._delete_available_enis(ec2, [{"Name": "group-id", "Values": [rid]}], f"SG {rid}")
            ec2.delete_security_group(GroupId=rid)

        elif rtype == "ec2:network_acl":
            ec2.delete_network_acl(NetworkAclId=rid)

        elif rtype == "elbv2:load_balancer":
            elbv2 = self.client("elbv2", resource.region)
            elbv2.delete_load_balancer(LoadBalancerArn=rid)

        elif rtype == "elb:load_balancer":
            elb = self.client("elb", resource.region)
            elb.delete_load_balancer(LoadBalancerName=rid)

    def _delete_network_interface(self, ec2_client, eni_id: str, resource: Resource,
                                   max_wait: int = 900) -> None:
        """
        Delete an ENI.  If it is still in-use (managed by Lambda, RDS, ECS, etc.),
        wait for the owning service to release it before deleting.
        max_wait defaults to 15 minutes — Lambda VPC ENI cleanup can be slow.
        """
        interval = 15
        elapsed = 0
        while elapsed <= max_wait:
            try:
                resp = ec2_client.describe_network_interfaces(NetworkInterfaceIds=[eni_id])
            except ClientError as e:
                if e.response["Error"]["Code"] == "InvalidNetworkInterfaceID.NotFound":
                    return  # Already gone
                raise
            enis = resp.get("NetworkInterfaces", [])
            if not enis:
                return  # Already gone

            status = enis[0]["Status"]
            if status == "available":
                ec2_client.delete_network_interface(NetworkInterfaceId=eni_id)
                return

            itype = resource.metadata.get("interface_type", "interface")
            logger.info(
                f"  ENI {eni_id} ({itype}) is {status} — waiting for owning service to release it "
                f"({elapsed}s / {max_wait}s)"
            )
            time.sleep(interval)
            elapsed += interval

        raise RuntimeError(
            f"ENI {eni_id} did not become available within {max_wait}s — "
            "the owning service may still be running"
        )

    def _delete_available_enis(self, ec2_client, filters: list, context: str) -> None:
        """Quick sweep: delete any ENIs already in 'available' state. No waiting."""
        try:
            paginator = ec2_client.get_paginator("describe_network_interfaces")
            for page in paginator.paginate(Filters=filters + [{"Name": "status", "Values": ["available"]}]):
                for eni in page.get("NetworkInterfaces", []):
                    eni_id = eni["NetworkInterfaceId"]
                    try:
                        ec2_client.delete_network_interface(NetworkInterfaceId=eni_id)
                    except ClientError as e:
                        logger.warning(f"Could not delete straggler ENI {eni_id} for {context}: {e}")
        except ClientError as e:
            logger.warning(f"Could not list straggler ENIs for {context}: {e}")

    def _wait_nat_deleted(self, ec2_client, nat_id: str, max_wait: int = 180):
        elapsed = 0
        interval = 15
        while elapsed < max_wait:
            resp = ec2_client.describe_nat_gateways(NatGatewayIds=[nat_id])
            gateways = resp.get("NatGateways", [])
            if not gateways or gateways[0]["State"] == "deleted":
                return
            time.sleep(interval)
            elapsed += interval
