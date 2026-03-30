from botocore.exceptions import ClientError
from discovery.base import Resource
from .base import BaseDeleter


class IAMDeleter(BaseDeleter):

    def handled_types(self) -> list:
        return ["iam:role", "iam:user", "iam:group", "iam:policy"]

    def _delete(self, resource: Resource) -> None:
        iam = self.client("iam", "us-east-1")  # IAM is global
        rtype = resource.resource_type
        rid = resource.resource_id

        if rtype == "iam:role":
            self._delete_role(iam, rid)

        elif rtype == "iam:user":
            self._delete_user(iam, rid)

        elif rtype == "iam:group":
            self._delete_group(iam, rid)

        elif rtype == "iam:policy":
            self._delete_policy(iam, resource.arn)

    def _delete_role(self, iam, role_name: str) -> None:
        # Detach managed policies
        paginator = iam.get_paginator("list_attached_role_policies")
        for page in paginator.paginate(RoleName=role_name):
            for policy in page["AttachedPolicies"]:
                iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])

        # Delete inline policies
        paginator = iam.get_paginator("list_role_policies")
        for page in paginator.paginate(RoleName=role_name):
            for policy_name in page["PolicyNames"]:
                iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

        # Remove from instance profiles
        for profile in iam.list_instance_profiles_for_role(RoleName=role_name)["InstanceProfiles"]:
            iam.remove_role_from_instance_profile(
                InstanceProfileName=profile["InstanceProfileName"],
                RoleName=role_name,
            )
            try:
                iam.delete_instance_profile(InstanceProfileName=profile["InstanceProfileName"])
            except ClientError:
                pass

        iam.delete_role(RoleName=role_name)

    def _delete_user(self, iam, user_name: str) -> None:
        # Detach managed policies
        paginator = iam.get_paginator("list_attached_user_policies")
        for page in paginator.paginate(UserName=user_name):
            for policy in page["AttachedPolicies"]:
                iam.detach_user_policy(UserName=user_name, PolicyArn=policy["PolicyArn"])

        # Delete inline policies
        paginator = iam.get_paginator("list_user_policies")
        for page in paginator.paginate(UserName=user_name):
            for policy_name in page["PolicyNames"]:
                iam.delete_user_policy(UserName=user_name, PolicyName=policy_name)

        # Delete access keys
        for key in iam.list_access_keys(UserName=user_name)["AccessKeyMetadata"]:
            iam.delete_access_key(UserName=user_name, AccessKeyId=key["AccessKeyId"])

        # Delete MFA devices
        for mfa in iam.list_mfa_devices(UserName=user_name)["MFADevices"]:
            iam.deactivate_mfa_device(UserName=user_name, SerialNumber=mfa["SerialNumber"])
            try:
                iam.delete_virtual_mfa_device(SerialNumber=mfa["SerialNumber"])
            except ClientError:
                pass

        # Remove from groups
        for group in iam.list_groups_for_user(UserName=user_name)["Groups"]:
            iam.remove_user_from_group(GroupName=group["GroupName"], UserName=user_name)

        # Delete signing certificates, SSH keys, service-specific credentials
        for cert in iam.list_signing_certificates(UserName=user_name).get("Certificates", []):
            iam.delete_signing_certificate(UserName=user_name, CertificateId=cert["CertificateId"])

        iam.delete_user(UserName=user_name)

    def _delete_group(self, iam, group_name: str) -> None:
        paginator = iam.get_paginator("list_attached_group_policies")
        for page in paginator.paginate(GroupName=group_name):
            for policy in page["AttachedPolicies"]:
                iam.detach_group_policy(GroupName=group_name, PolicyArn=policy["PolicyArn"])

        paginator = iam.get_paginator("list_group_policies")
        for page in paginator.paginate(GroupName=group_name):
            for policy_name in page["PolicyNames"]:
                iam.delete_group_policy(GroupName=group_name, PolicyName=policy_name)

        iam.delete_group(GroupName=group_name)

    def _delete_policy(self, iam, policy_arn: str) -> None:
        # Detach from all entities first
        paginator = iam.get_paginator("list_entities_for_policy")
        for page in paginator.paginate(PolicyArn=policy_arn):
            for role in page.get("PolicyRoles", []):
                iam.detach_role_policy(RoleName=role["RoleName"], PolicyArn=policy_arn)
            for user in page.get("PolicyUsers", []):
                iam.detach_user_policy(UserName=user["UserName"], PolicyArn=policy_arn)
            for group in page.get("PolicyGroups", []):
                iam.detach_group_policy(GroupName=group["GroupName"], PolicyArn=policy_arn)

        # Delete non-default versions
        for version in iam.list_policy_versions(PolicyArn=policy_arn)["Versions"]:
            if not version["IsDefaultVersion"]:
                iam.delete_policy_version(PolicyArn=policy_arn, VersionId=version["VersionId"])

        iam.delete_policy(PolicyArn=policy_arn)
