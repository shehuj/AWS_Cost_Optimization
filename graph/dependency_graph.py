from typing import List
from discovery.base import Resource
from core.config import DELETION_PRIORITY


def sort_by_deletion_order(resources: List[Resource]) -> List[Resource]:
    """
    Sort resources so that dependencies are deleted before the resources
    that depend on them (e.g., EC2 instances before VPCs).

    Resources with no known priority are assigned a default of 200,
    placing them before IAM (120+) but after most infrastructure.
    """
    DEFAULT_PRIORITY = 200

    def priority(r: Resource) -> int:
        return DELETION_PRIORITY.get(r.resource_type, DEFAULT_PRIORITY)

    return sorted(resources, key=priority)


def group_by_priority(resources: List[Resource]) -> List[List[Resource]]:
    """
    Return resources grouped into deletion waves.
    Resources in the same wave can be deleted concurrently.
    Waves are ordered from first-to-delete to last-to-delete.
    """
    DEFAULT_PRIORITY = 200
    buckets: dict = {}
    for r in resources:
        p = DELETION_PRIORITY.get(r.resource_type, DEFAULT_PRIORITY)
        buckets.setdefault(p, []).append(r)

    return [buckets[k] for k in sorted(buckets.keys())]
