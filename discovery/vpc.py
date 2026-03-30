from typing import List
from botocore.exceptions import ClientError
from .base import BaseDiscoverer, Resource, tags_to_dict


class VPCDiscoverer(BaseDiscoverer):
    """Discovers VPCs and all associated networking resources."""

    def discover(self) -> List[Resource]:
        resources: List[Resource] = []
        resources.extend(self._discover_vpcs())
        resources.extend(self._discover_subnets())
        resources.extend(self._discover_route_tables())
        resources.extend(self._discover_internet_gateways())
        resources.extend(self._discover_nat_gateways())
        resources.extend(self._discover_vpc_endpoints())
        resources.extend(self._discover_security_groups())
        resources.extend(self._discover_network_acls())
        resources.extend(self._discover_load_balancers())
        return resources

    def _discover_vpcs(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        paginator = ec2.get_paginator("describe_vpcs")
        for page in paginator.paginate():
            for vpc in page["Vpcs"]:
                tags = tags_to_dict(vpc.get("Tags", []))
                name = tags.get("Name", vpc["VpcId"])
                resources.append(Resource(
                    resource_id=vpc["VpcId"],
                    resource_type="ec2:vpc",
                    name=name,
                    region=self.region,
                    tags=tags,
                    metadata={"is_default": vpc.get("IsDefault", False), "cidr": vpc["CidrBlock"]},
                ))
        return resources

    def _discover_subnets(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        paginator = ec2.get_paginator("describe_subnets")
        for page in paginator.paginate():
            for subnet in page["Subnets"]:
                tags = tags_to_dict(subnet.get("Tags", []))
                name = tags.get("Name", subnet["SubnetId"])
                resources.append(Resource(
                    resource_id=subnet["SubnetId"],
                    resource_type="ec2:subnet",
                    name=name,
                    region=self.region,
                    tags=tags,
                    metadata={
                        "vpc_id": subnet["VpcId"],
                        "cidr": subnet["CidrBlock"],
                        "is_default": subnet.get("DefaultForAz", False),
                    },
                ))
        return resources

    def _discover_route_tables(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        paginator = ec2.get_paginator("describe_route_tables")
        for page in paginator.paginate():
            for rt in page["RouteTables"]:
                # Skip main route tables (can't delete directly)
                is_main = any(
                    assoc.get("Main", False)
                    for assoc in rt.get("Associations", [])
                )
                tags = tags_to_dict(rt.get("Tags", []))
                name = tags.get("Name", rt["RouteTableId"])
                resources.append(Resource(
                    resource_id=rt["RouteTableId"],
                    resource_type="ec2:route_table",
                    name=name,
                    region=self.region,
                    tags=tags,
                    metadata={"vpc_id": rt["VpcId"], "is_main": is_main},
                ))
        return resources

    def _discover_internet_gateways(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        paginator = ec2.get_paginator("describe_internet_gateways")
        for page in paginator.paginate():
            for igw in page["InternetGateways"]:
                tags = tags_to_dict(igw.get("Tags", []))
                name = tags.get("Name", igw["InternetGatewayId"])
                attached_vpcs = [a["VpcId"] for a in igw.get("Attachments", []) if "VpcId" in a]
                resources.append(Resource(
                    resource_id=igw["InternetGatewayId"],
                    resource_type="ec2:internet_gateway",
                    name=name,
                    region=self.region,
                    tags=tags,
                    metadata={"attached_vpcs": attached_vpcs},
                ))
        return resources

    def _discover_nat_gateways(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        paginator = ec2.get_paginator("describe_nat_gateways")
        for page in paginator.paginate():
            for nat in page["NatGateways"]:
                if nat["State"] in ("deleted", "deleting"):
                    continue
                tags = tags_to_dict(nat.get("Tags", []))
                name = tags.get("Name", nat["NatGatewayId"])
                resources.append(Resource(
                    resource_id=nat["NatGatewayId"],
                    resource_type="ec2:nat_gateway",
                    name=name,
                    region=self.region,
                    tags=tags,
                    metadata={"vpc_id": nat["VpcId"], "subnet_id": nat["SubnetId"], "state": nat["State"]},
                ))
        return resources

    def _discover_vpc_endpoints(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        paginator = ec2.get_paginator("describe_vpc_endpoints")
        for page in paginator.paginate():
            for ep in page["VpcEndpoints"]:
                if ep["State"] in ("deleted", "deleting"):
                    continue
                tags = tags_to_dict(ep.get("Tags", []))
                name = tags.get("Name", ep["VpcEndpointId"])
                resources.append(Resource(
                    resource_id=ep["VpcEndpointId"],
                    resource_type="ec2:vpc_endpoint",
                    name=name,
                    region=self.region,
                    tags=tags,
                    metadata={"vpc_id": ep["VpcId"], "service": ep["ServiceName"]},
                ))
        return resources

    def _discover_security_groups(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        paginator = ec2.get_paginator("describe_security_groups")
        for page in paginator.paginate():
            for sg in page["SecurityGroups"]:
                if sg["GroupName"] == "default":
                    continue  # Can't delete default SGs
                tags = tags_to_dict(sg.get("Tags", []))
                name = tags.get("Name", sg.get("GroupName", sg["GroupId"]))
                resources.append(Resource(
                    resource_id=sg["GroupId"],
                    resource_type="ec2:security_group",
                    name=name,
                    region=self.region,
                    tags=tags,
                    metadata={"vpc_id": sg.get("VpcId", ""), "group_name": sg["GroupName"]},
                ))
        return resources

    def _discover_network_acls(self) -> List[Resource]:
        ec2 = self.client("ec2")
        resources = []
        paginator = ec2.get_paginator("describe_network_acls")
        for page in paginator.paginate():
            for nacl in page["NetworkAcls"]:
                if nacl.get("IsDefault", False):
                    continue  # Can't delete default NACLs
                tags = tags_to_dict(nacl.get("Tags", []))
                name = tags.get("Name", nacl["NetworkAclId"])
                resources.append(Resource(
                    resource_id=nacl["NetworkAclId"],
                    resource_type="ec2:network_acl",
                    name=name,
                    region=self.region,
                    tags=tags,
                    metadata={"vpc_id": nacl["VpcId"]},
                ))
        return resources

    def _discover_load_balancers(self) -> List[Resource]:
        resources = []
        # ALB/NLB (ELBv2)
        try:
            elbv2 = self.client("elbv2")
            paginator = elbv2.get_paginator("describe_load_balancers")
            for page in paginator.paginate():
                for lb in page["LoadBalancers"]:
                    if lb["State"]["Code"] in ("deleted",):
                        continue
                    tags_resp = elbv2.describe_tags(ResourceArns=[lb["LoadBalancerArn"]])
                    tags = tags_to_dict(
                        tags_resp["TagDescriptions"][0]["Tags"] if tags_resp["TagDescriptions"] else []
                    )
                    resources.append(Resource(
                        resource_id=lb["LoadBalancerArn"],
                        resource_type="elbv2:load_balancer",
                        name=lb["LoadBalancerName"],
                        region=self.region,
                        tags=tags,
                        arn=lb["LoadBalancerArn"],
                        metadata={"vpc_id": lb.get("VpcId", ""), "type": lb["Type"]},
                    ))
        except Exception:
            pass

        # Classic ELB
        try:
            elb = self.client("elb")
            paginator = elb.get_paginator("describe_load_balancers")
            for page in paginator.paginate():
                for lb in page["LoadBalancerDescriptions"]:
                    resources.append(Resource(
                        resource_id=lb["LoadBalancerName"],
                        resource_type="elb:load_balancer",
                        name=lb["LoadBalancerName"],
                        region=self.region,
                        tags={},
                        metadata={"vpc_id": lb.get("VPCId", "")},
                    ))
        except Exception:
            pass

        return resources
