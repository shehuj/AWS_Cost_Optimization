import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict
import time
import boto3

from core.config import Config
from core.plan_formatter import DestroyPlan
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self) -> DestroyPlan:
        """
        Discover all resources, apply filters, and return a DestroyPlan.
        Nothing is deleted. Safe to call at any time.
        """
        account_id, caller_arn = self._get_caller_identity()

        logger.info("=== DISCOVERY PHASE ===")
        all_resources = self._discover_all()
        logger.info(f"Discovered {len(all_resources)} resources across {len(self.config.regions)} region(s)")

        logger.info("=== FILTER PHASE ===")
        all_resources = apply_filters(all_resources, self.config)
        actionable = [r for r in all_resources if not r.protected]
        protected = [r for r in all_resources if r.protected]
        logger.info(f"To destroy: {len(actionable)} | Protected/skipped: {len(protected)}")

        waves = group_by_priority(actionable)

        return DestroyPlan(
            account_id=account_id,
            caller_arn=caller_arn,
            regions=self.config.regions,
            services=self.config.services,
            waves=waves,
            protected=protected,
        )

    def apply(self, plan: DestroyPlan) -> Dict:
        """
        Execute a previously generated DestroyPlan.
        Deletes resources wave by wave in dependency-safe order.
        Retries failed deletions with exponential backoff.
        """
        if plan.total_destroy == 0:
            log_success(logger, "Nothing to destroy.")
            return {"destroyed": 0, "skipped": plan.total_protected, "errors": 0}

        logger.info(f"=== APPLY PHASE — {plan.total_destroy} resources to destroy ===")
        results = self._delete_in_waves_with_retries(plan.waves)

        destroyed = sum(1 for r in results if r.success and not r.skipped)
        errors = sum(1 for r in results if not r.success)
        skipped = sum(1 for r in results if r.skipped) + plan.total_protected

        log_success(logger, f"Apply complete — destroyed: {destroyed} | skipped: {skipped} | errors: {errors}")
        if errors:
            log_warning(logger, "Some deletions failed. Check logs above for details.")

        return {"destroyed": destroyed, "skipped": skipped, "errors": errors}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_deleters(self) -> List[BaseDeleter]:
        return [
            CloudFormationDeleter(dry_run=False, session=self.session),
            EC2Deleter(dry_run=False, session=self.session),
            S3Deleter(dry_run=False, session=self.session),
            RDSDeleter(dry_run=False, session=self.session),
            VPCDeleter(dry_run=False, session=self.session),
            IAMDeleter(dry_run=False, session=self.session),
            LambdaDeleter(dry_run=False, session=self.session),
            DynamoDBDeleter(dry_run=False, session=self.session),
        ]

    def _get_deleter(self, resource_type: str) -> BaseDeleter | None:
        for d in self._deleters:
            if d.can_handle(resource_type):
                return d
        return None

    def _get_caller_identity(self):
        try:
            sts = self.session.client("sts")
            identity = sts.get_caller_identity()
            arn = identity["Arn"]
            account_id = identity["Account"]
            self.config.caller_identity_arn = arn
            logger.info(f"Running as: {arn}")
            return account_id, arn
        except Exception as e:
            log_warning(logger, f"Could not determine caller identity: {e}")
            return "unknown", "unknown"

    def _discover_all(self) -> List[Resource]:
        all_resources: List[Resource] = []
        services = list(self.config.services)

        if self.config.skip_cloudformation and "cloudformation" in services:
            services.remove("cloudformation")

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

    def _is_dependency_error(self, error_msg: str) -> bool:
        """Check if error is a dependency-related issue that may resolve with retry."""
        dependency_keywords = [
            "DependencyViolation",
            "dependencies",
            "dependent object",
            "has dependencies",
            "cannot be deleted",
        ]
        error_lower = error_msg.lower()
        return any(keyword.lower() in error_lower for keyword in dependency_keywords)

    def _delete_in_waves_with_retries(self, waves: List[List[Resource]]) -> List[DeletionResult]:
        """Delete resources wave by wave with retry logic for dependency errors."""
        all_results: List[DeletionResult] = []
        max_retries = 3
        base_delay = 2  # seconds

        # First pass: delete all waves
        for i, wave in enumerate(waves, 1):
            logger.info(f"--- Wave {i}: {len(wave)} resources ---")
            wave_results = self._delete_wave(wave)
            all_results.extend(wave_results)

        # Retry phase: retry failed deletions that had dependency errors
        failed_resources = [
            r for r in all_results 
            if not r.success and self._is_dependency_error(r.error)
        ]

        retry_attempt = 0
        while failed_resources and retry_attempt < max_retries:
            retry_attempt += 1
            delay = base_delay * (2 ** (retry_attempt - 1))  # exponential backoff
            logger.info(f"--- Retry {retry_attempt}/{max_retries}: {len(failed_resources)} resources (waiting {delay}s) ---")
            time.sleep(delay)

            # Attempt to delete failed resources again
            retry_results = self._delete_wave([r.resource for r in failed_resources])

            # Update results and filter for next retry
            for i, result in enumerate(retry_results):
                # Find and update the original result
                for j, orig_result in enumerate(all_results):
                    if orig_result.resource.resource_id == result.resource.resource_id:
                        all_results[j] = result
                        break

            # Prepare next retry batch (only dependency errors)
            failed_resources = [
                r for r in retry_results 
                if not r.success and self._is_dependency_error(r.error)
            ]

            # Log results
            retry_success = sum(1 for r in retry_results if r.success)
            if retry_success > 0:
                logger.info(f"  Retry {retry_attempt}: {retry_success} resources deleted")

        # Log final errors
        for r in all_results:
            if not r.success:
                log_error(logger, f"Failed: {r.resource.resource_type} {r.resource.resource_id}: {r.error}")

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
                    logger.info(f"  Destroyed: [{resource.resource_type}] {resource.resource_id}")
                elif result.skipped:
                    logger.debug(f"  Skipped:   [{resource.resource_type}] {resource.resource_id}")
                else:
                    log_error(logger, f"  Error:     [{resource.resource_type}] {resource.resource_id}: {result.error}")
                results.append(result)

        return results
