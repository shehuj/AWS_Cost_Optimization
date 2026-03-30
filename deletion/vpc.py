import time
from botocore.exceptions import ClientError
from discovery.base import Resource
from .base import BaseDeleter


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
            # Detach from all VPCs first
            for vpc_id in resource.metadata.get("attached_vpcs", []):
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
                ec2.revoke_security_group_ingress(GroupId=rid, IpPermissions=sg["IpPermissions"])
            if sg.get("IpPermissionsEgress"):
                ec2.revoke_security_group_egress(GroupId=rid, IpPermissions=sg["IpPermissionsEgress"])
            ec2.delete_security_group(GroupId=rid)

        elif rtype == "ec2:network_acl":
            ec2.delete_network_acl(NetworkAclId=rid)

        elif rtype == "elbv2:load_balancer":
            elbv2 = self.client("elbv2", resource.region)
            elbv2.delete_load_balancer(LoadBalancerArn=rid)

        elif rtype == "elb:load_balancer":
            elb = self.client("elb", resource.region)
            elb.delete_load_balancer(LoadBalancerName=rid)

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
