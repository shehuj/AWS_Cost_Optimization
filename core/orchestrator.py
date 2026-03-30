import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict

import boto3

from core.config import Config
from discovery.base import Resource
from discovery.ec2 import EC2Discoverer
from discovery.s3 import S3Discoverer
from discovery.rds import RDSDiscoverer
from discovery.vpc import VPCDiscoverer
from discovery.iam import IAMDiscoverer
from discovery.lambda_ import LambdaDiscoverer
from discovery.dynamodb import DynamoDBDiscoverer
from discovery.cloudformation import CloudFormationDiscoverer
from filters.tag_filter import apply_filters
from graph.dependency_graph import group_by_priority
from deletion.base import BaseDeleter, DeletionResult
from deletion.ec2 import EC2Deleter
from deletion.s3 import S3Deleter
from deletion.rds import RDSDeleter
from deletion.vpc import VPCDeleter
from deletion.iam import IAMDeleter
from deletion.lambda_ import LambdaDeleter
from deletion.dynamodb import DynamoDBDeleter
from deletion.cloudformation import CloudFormationDeleter
from utils.logger import log_success, log_error, log_warning

logger = logging.getLogger("aws-reset")

_DISCOVERER_MAP = {
    "cloudformation": CloudFormationDiscoverer,
    "ec2": EC2Discoverer,
    "s3": S3Discoverer,
    "rds": RDSDiscoverer,
    "lambda": LambdaDiscoverer,
    "dynamodb": DynamoDBDiscoverer,
    "vpc": VPCDiscoverer,
    "iam": IAMDiscoverer,
}


class ResetOrchestrator:

    def __init__(self, config: Config):
        self.config = config
        self.session = boto3.Session()
        self._deleters: List[BaseDeleter] = self._build_deleters()

    def _build_deleters(self) -> List[BaseDeleter]:
        return [
            CloudFormationDeleter(dry_run=self.config.dry_run, session=self.session),
            EC2Deleter(dry_run=self.config.dry_run, session=self.session),
            S3Deleter(dry_run=self.config.dry_run, session=self.session),
            RDSDeleter(dry_run=self.config.dry_run, session=self.session),
            VPCDeleter(dry_run=self.config.dry_run, session=self.session),
            IAMDeleter(dry_run=self.config.dry_run, session=self.session),
            LambdaDeleter(dry_run=self.config.dry_run, session=self.session),
            DynamoDBDeleter(dry_run=self.config.dry_run, session=self.session),
        ]

    def _get_deleter(self, resource_type: str) -> BaseDeleter | None:
        for d in self._deleters:
            if d.can_handle(resource_type):
                return d
        return None

    def run(self) -> Dict:
        # Resolve caller identity to protect the running role
        self._set_caller_identity()

        # 1. Discover
        logger.info("=== DISCOVERY PHASE ===")
        all_resources = self._discover_all()
        logger.info(f"Discovered {len(all_resources)} resources across {len(self.config.regions)} region(s)")

        # 2. Filter
        logger.info("=== FILTER PHASE ===")
        all_resources = apply_filters(all_resources, self.config)
        actionable = [r for r in all_resources if not r.protected]
        protected = [r for r in all_resources if r.protected]
        logger.info(f"Actionable: {len(actionable)} | Protected/skipped: {len(protected)}")

        if not actionable:
            log_success(logger, "Nothing to delete.")
            return {"deleted": 0, "skipped": len(protected), "errors": 0}

        # 3. Print plan
        self._print_plan(actionable)

        if self.config.dry_run:
            log_success(logger, "Dry-run complete — no resources were deleted.")
            return {"deleted": 0, "skipped": len(protected), "errors": 0, "dry_run": True}

        # 4. Delete in dependency order (wave by wave)
        logger.info("=== DELETION PHASE ===")
        results = self._delete_in_waves(actionable)

        deleted = sum(1 for r in results if r.success and not r.skipped)
        errors = sum(1 for r in results if not r.success)
        skipped = sum(1 for r in results if r.skipped) + len(protected)

        log_success(logger, f"Reset complete — deleted: {deleted} | skipped: {skipped} | errors: {errors}")
        if errors:
            log_warning(logger, "Some deletions failed. Check logs above for details.")

        return {"deleted": deleted, "skipped": skipped, "errors": errors}

    def _set_caller_identity(self):
        try:
            sts = self.session.client("sts")
            identity = sts.get_caller_identity()
            self.config.caller_identity_arn = identity["Arn"]
            logger.info(f"Running as: {identity['Arn']}")
        except Exception as e:
            log_warning(logger, f"Could not determine caller identity: {e}")

    def _discover_all(self) -> List[Resource]:
        all_resources: List[Resource] = []
        services = self.config.services

        if self.config.skip_cloudformation and "cloudformation" in services:
            services = [s for s in services if s != "cloudformation"]

        for region in self.config.regions:
            logger.info(f"Discovering resources in region: {region}")
            for service in services:
                discoverer_cls = _DISCOVERER_MAP.get(service)
                if not discoverer_cls:
                    log_warning(logger, f"No discoverer for service: {service}")
                    continue
                try:
                    discoverer = discoverer_cls(region=region, session=self.session)
                    found = discoverer.discover()
                    logger.info(f"  {service}: {len(found)} resources")
                    all_resources.extend(found)
                except Exception as e:
                    log_error(logger, f"Discovery failed for {service} in {region}: {e}")

        return all_resources

    def _print_plan(self, resources: List[Resource]) -> None:
        logger.info("=== DELETION PLAN ===")
        waves = group_by_priority(resources)
        for i, wave in enumerate(waves, 1):
            logger.info(f"Wave {i}:")
            for r in wave:
                logger.info(f"  - [{r.resource_type}] {r.resource_id} ({r.name}) in {r.region}")

    def _delete_in_waves(self, resources: List[Resource]) -> List[DeletionResult]:
        waves = group_by_priority(resources)
        all_results: List[DeletionResult] = []

        for i, wave in enumerate(waves, 1):
            logger.info(f"--- Wave {i}: {len(wave)} resources ---")
            wave_results = self._delete_wave(wave)
            all_results.extend(wave_results)

            failed = [r for r in wave_results if not r.success]
            if failed:
                for f in failed:
                    log_error(logger, f"Failed: {f.resource.resource_type} {f.resource.resource_id}: {f.error}")

        return all_results

    def _delete_wave(self, resources: List[Resource]) -> List[DeletionResult]:
        results: List[DeletionResult] = []
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {}
            for resource in resources:
                deleter = self._get_deleter(resource.resource_type)
                if not deleter:
                    log_warning(logger, f"No deleter for {resource.resource_type} — skipping {resource.resource_id}")
                    results.append(DeletionResult(resource, success=True, skipped=True))
                    continue
                futures[executor.submit(deleter.delete, resource)] = resource

            for future in as_completed(futures):
                result = future.result()
                resource = futures[future]
                if result.success and not result.skipped:
                    logger.info(f"  Deleted: [{resource.resource_type}] {resource.resource_id}")
                elif result.skipped:
                    logger.debug(f"  Skipped: [{resource.resource_type}] {resource.resource_id}")
                else:
                    log_error(logger, f"  Error: [{resource.resource_type}] {resource.resource_id}: {result.error}")
                results.append(result)

        return results
