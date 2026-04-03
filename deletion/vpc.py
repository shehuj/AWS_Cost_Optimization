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
            # Delete available ENIs in the subnet (left by Lambda, RDS, ECS, etc.)
            self._delete_enis_in_subnet(ec2, rid)
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
            # Delete available ENIs still referencing this SG (left by Lambda, RDS, etc.)
            self._delete_enis_for_sg(ec2, rid)
            ec2.delete_security_group(GroupId=rid)

        elif rtype == "ec2:network_acl":
            ec2.delete_network_acl(NetworkAclId=rid)

        elif rtype == "elbv2:load_balancer":
            elbv2 = self.client("elbv2", resource.region)
            elbv2.delete_load_balancer(LoadBalancerArn=rid)

        elif rtype == "elb:load_balancer":
            elb = self.client("elb", resource.region)
            elb.delete_load_balancer(LoadBalancerName=rid)

    def _delete_enis_in_subnet(self, ec2_client, subnet_id: str, max_wait: int = 300) -> None:
        """Wait for all in-use ENIs in a subnet to release, then delete them."""
        self._wait_and_delete_enis(
            ec2_client,
            filters=[{"Name": "subnet-id", "Values": [subnet_id]}],
            context=f"subnet {subnet_id}",
            max_wait=max_wait,
        )

    def _delete_enis_for_sg(self, ec2_client, sg_id: str, max_wait: int = 300) -> None:
        """Wait for all in-use ENIs referencing this SG to release, then delete them."""
        self._wait_and_delete_enis(
            ec2_client,
            filters=[{"Name": "group-id", "Values": [sg_id]}],
            context=f"SG {sg_id}",
            max_wait=max_wait,
        )

    def _wait_and_delete_enis(self, ec2_client, filters: list, context: str, max_wait: int) -> None:
        """
        Poll until all ENIs matching filters are in 'available' state, then delete them.
        ENIs left by Lambda, RDS, and ECS can take several minutes to release after
        their parent resource is deleted.
        """
        interval = 15
        elapsed = 0

        while elapsed <= max_wait:
            try:
                paginator = ec2_client.get_paginator("describe_network_interfaces")
                enis = []
                for page in paginator.paginate(Filters=filters):
                    enis.extend(page.get("NetworkInterfaces", []))
            except ClientError as e:
                logger.warning(f"Could not list ENIs for {context}: {e}")
                return

            if not enis:
                return

            in_use = [e for e in enis if e["Status"] == "in-use"]
            available = [e for e in enis if e["Status"] == "available"]

            # Delete any that are already available
            for eni in available:
                eni_id = eni["NetworkInterfaceId"]
                try:
                    ec2_client.delete_network_interface(NetworkInterfaceId=eni_id)
                except ClientError as e:
                    logger.warning(f"Could not delete ENI {eni_id} for {context}: {e}")

            if not in_use:
                return

            logger.debug(
                f"Waiting for {len(in_use)} in-use ENI(s) to release for {context} "
                f"({elapsed}s elapsed)"
            )
            time.sleep(interval)
            elapsed += interval

        logger.warning(f"Timed out waiting for ENIs to release for {context}")

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
