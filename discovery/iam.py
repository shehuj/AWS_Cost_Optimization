from typing import List
from .base import BaseDiscoverer, Resource, tags_to_dict


class IAMDiscoverer(BaseDiscoverer):
    """Discovers IAM users, roles, groups, and customer-managed policies."""

    def discover(self) -> List[Resource]:
        resources: List[Resource] = []
        resources.extend(self._discover_roles())
        resources.extend(self._discover_users())
        resources.extend(self._discover_groups())
        resources.extend(self._discover_policies())
        return resources

    def _discover_roles(self) -> List[Resource]:
        iam = self.client("iam")
        resources = []
        paginator = iam.get_paginator("list_roles")
        for page in paginator.paginate():
            for role in page["Roles"]:
                # Skip AWS service-linked roles
                if role["Path"].startswith("/aws-service-role/"):
                    continue
                tags = tags_to_dict(iam.list_role_tags(RoleName=role["RoleName"])["Tags"])
                resources.append(Resource(
                    resource_id=role["RoleName"],
                    resource_type="iam:role",
                    name=role["RoleName"],
                    region="global",
                    tags=tags,
                    arn=role["Arn"],
                    metadata={"path": role["Path"]},
                ))
        return resources

    def _discover_users(self) -> List[Resource]:
        iam = self.client("iam")
        resources = []
        paginator = iam.get_paginator("list_users")
        for page in paginator.paginate():
            for user in page["Users"]:
                tags = tags_to_dict(iam.list_user_tags(UserName=user["UserName"])["Tags"])
                resources.append(Resource(
                    resource_id=user["UserName"],
                    resource_type="iam:user",
                    name=user["UserName"],
                    region="global",
                    tags=tags,
                    arn=user["Arn"],
                    metadata={"path": user["Path"]},
                ))
        return resources

    def _discover_groups(self) -> List[Resource]:
        iam = self.client("iam")
        resources = []
        paginator = iam.get_paginator("list_groups")
        for page in paginator.paginate():
            for group in page["Groups"]:
                resources.append(Resource(
                    resource_id=group["GroupName"],
                    resource_type="iam:group",
                    name=group["GroupName"],
                    region="global",
                    tags={},
                    arn=group["Arn"],
                ))
        return resources

    def _discover_policies(self) -> List[Resource]:
        iam = self.client("iam")
        resources = []
        paginator = iam.get_paginator("list_policies")
        for page in paginator.paginate(Scope="Local"):  # Local = customer-managed only
            for policy in page["Policies"]:
                tags = tags_to_dict(
                    iam.list_policy_tags(PolicyArn=policy["Arn"]).get("Tags", [])
                )
                resources.append(Resource(
                    resource_id=policy["PolicyName"],
                    resource_type="iam:policy",
                    name=policy["PolicyName"],
                    region="global",
                    tags=tags,
                    arn=policy["Arn"],
                    metadata={"attachment_count": policy.get("AttachmentCount", 0)},
                ))
        return resources
