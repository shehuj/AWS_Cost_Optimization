# I Built a Terraform-Style Plan/Apply Workflow for AWS Account Resets — Here's Every Design Decision

*Wave-based deletion, a human approval gate, and a destroy plan that looks like this before anything gets touched*

---

There is a particular kind of pain that every DevOps engineer has felt at least once: you spin up a sandbox AWS account for a project, things get busy, and six weeks later you open the billing console and feel your stomach drop.

EC2 instances nobody remembers provisioning. RDS clusters sitting idle at $400/month. NAT Gateways attached to VPCs with zero running workloads. The account has become a graveyard, and manually hunting down everything through the console is somewhere between "tedious" and "genuinely risky" — because one wrong click and you've deleted something that matters.

I built the **AWS Account Reset Framework** to solve this properly. Not a bash script that terminates EC2 instances. A full plan/apply workflow — modular, safe, and aware of resource dependencies — where you see exactly what will be destroyed before anything is touched, and a human has to approve it before it runs.

---

## Why Terraform's UX Is the Right Model Here

Terraform's plan/apply pattern has become the gold standard for infrastructure changes because it separates *intent* from *execution*. You generate a plan, review it, and only then apply. The plan step is always safe. The apply step always shows you what it's about to do.

That's the model this framework follows:

```bash
python main.py plan --regions us-east-1    # show what would be destroyed — safe
python main.py apply --regions us-east-1   # display plan, confirm, then execute
```

`plan` exit codes even mirror Terraform: `0` means nothing to destroy, `2` means the plan has changes. CI pipelines can branch on this.

The full branch workflow looks like this:

```text
push to dev ──► plan only ──► post to PR comment + Slack
                               (nothing deleted, ever)

merge to main ──► plan ──► notify approver ──► approve / reject
                                                    │           │
                                                 apply       abort
                                              (destroy)   (nothing deleted)
```

On `dev`, every push generates a fresh destroy plan and posts it as a PR comment. On `main`, the plan runs, an approver reviews it in the GitHub Actions UI, and only after explicit approval does deletion execute. If they reject, the workflow cancels and nothing happens.

---

## The Problem With Simple Cleanup Scripts

Before getting into the architecture, it's worth being precise about why the naive approach fails. This is the starting point most people reach for:

```python
def cleanup_ec2():
    ec2 = boto3.client('ec2')
    for reservation in ec2.describe_instances()['Reservations']:
        for instance in reservation['Instances']:
            ec2.terminate_instances(InstanceIds=[instance['InstanceId']])

def cleanup_s3():
    s3 = boto3.client('s3')
    for bucket in s3.list_buckets()['Buckets']:
        s3.delete_bucket(Bucket=bucket['Name'])
```

This will fail. Immediately. `delete_bucket` throws `BucketNotEmpty` before it touches half the account. The EC2 instances terminate but leave orphaned EBS volumes that keep billing. The VPC deletion fails because security groups still have cross-references. The RDS instance deletion fails because there's a final snapshot policy that wasn't overridden.

The fundamental problem is that **AWS resources have dependencies**, and any deletion strategy that ignores this will fail in ways that range from annoying to unrecoverable.

---

## Architecture: Three Phases, Two Commands

The framework is built around a pipeline with three distinct phases:

```
Discover → Filter → Delete (in waves)
```

Each phase is decoupled. The orchestrator exposes two public methods — `plan()` and `apply()` — that map directly to the CLI subcommands.

```python
class ResetOrchestrator:

    def plan(self) -> DestroyPlan:
        """Discover and filter resources. Returns a plan. Nothing is deleted."""
        account_id, caller_arn = self._get_caller_identity()
        all_resources = self._discover_all()
        all_resources = apply_filters(all_resources, self.config)
        actionable = [r for r in all_resources if not r.protected]
        protected  = [r for r in all_resources if r.protected]
        waves = group_by_priority(actionable)
        return DestroyPlan(
            account_id=account_id,
            caller_arn=caller_arn,
            waves=waves,
            protected=protected,
            ...
        )

    def apply(self, plan: DestroyPlan) -> Dict:
        """Execute a previously generated plan."""
        results = self._delete_in_waves(plan.waves)
        return summarize(results)
```

`plan()` is read-only. You can call it as many times as you want. `apply()` takes a `DestroyPlan` object and executes it — the plan and the execution are completely separate concerns. This also means the CLI can display the plan before prompting for confirmation, rather than discovering resources twice.

---

## Phase 1: Discovery

Every service has its own discoverer inheriting from `BaseDiscoverer`. Each speaks to the AWS API and returns a list of typed `Resource` objects.

```python
@dataclass
class Resource:
    resource_id: str
    resource_type: str    # e.g. "ec2:instance", "rds:cluster"
    name: str
    region: str
    tags: Dict[str, str]
    arn: str
    metadata: Dict        # service-specific: vpc_id, cluster_id, is_default, etc.
    protected: bool
```

The `metadata` field carries context the deletion layer needs later — which VPC an instance belongs to, whether a subnet is a default subnet, which instances are members of an RDS cluster. Discovery is the only place expensive `describe_*` API calls are made. Everything else works from this in-memory snapshot.

Eight discoverers cover the account:

| Discoverer | What it finds |
| ---------- | ------------- |
| `CloudFormationDiscoverer` | Root stacks only — nested stacks are handled by their parent |
| `EC2Discoverer` | Instances, volumes, snapshots, AMIs, EIPs, key pairs |
| `S3Discoverer` | Buckets, with versioning state and per-region filtering |
| `RDSDiscoverer` | Instances, clusters, snapshots, subnet groups, parameter groups |
| `VPCDiscoverer` | VPCs, subnets, route tables, IGWs, NAT GWs, endpoints, SGs, NACLs, ELBs |
| `IAMDiscoverer` | Roles, users, groups, customer-managed policies |
| `LambdaDiscoverer` | Functions and all layer versions |
| `DynamoDBDiscoverer` | Tables with item count and size metadata |

One design decision worth explaining: S3 buckets are global, but this framework discovers them per-region by checking each bucket's `LocationConstraint`. Running a multi-region reset without this filter would produce duplicate bucket entries in the plan — which would cause the second deletion attempt to fail on a 404 and add noise to the output.

---

## Phase 2: Filtering — Five Layered Safety Guards

Before anything is flagged for deletion, every resource passes through a filter pipeline. Five independent rules run in order, and any match marks the resource as protected:

**1. Protection tag.** Any resource tagged `do_not_delete=true` is never touched. The tag key and value are configurable.

**2. Explicit skip list.** Resource IDs listed in `config.yaml` under `skip_resource_ids` are always preserved, regardless of tags.

**3. Default VPC / default subnets.** AWS creates a default VPC in every region with default subnets in every AZ. Recreating a deleted default VPC requires a support ticket. The framework skips them by default.

**4. IAM global flag.** IAM resources are excluded unless you pass `--no-skip-iam`. Delete the wrong role and your automation breaks at 3am. This should always require a conscious opt-in.

**5. Caller identity self-protection.** Before the run starts, the framework calls `sts:GetCallerIdentity` and stores the ARN of the identity running the script. If IAM deletion is enabled, this principal is automatically protected. You cannot accidentally delete your own access.

```python
def apply_filters(resources, config):
    for resource in resources:
        if resource.tag(config.protected_tag_key) == config.protected_tag_value:
            resource.protected = True; continue
        if resource.resource_id in config.skip_resource_ids:
            resource.protected = True; continue
        if config.skip_default_vpc and is_default_vpc_resource(resource):
            resource.protected = True; continue
        if config.skip_iam and resource.resource_type.startswith("iam:"):
            resource.protected = True; continue
        if is_caller_identity(resource, config.caller_identity_arn):
            resource.protected = True; continue
```

None of this is complicated. But most scripts skip all of it, which is how people end up deleting their CI/CD roles.

---

## Phase 3: Wave-Based Deletion

This is the core of the framework. AWS resources have hard dependency constraints: you cannot delete a VPC while subnets exist in it, cannot delete a subnet while an EC2 instance is running in it, cannot release an Elastic IP while it's associated with a NAT Gateway. A flat deletion list fails on any real account.

The solution is to assign every resource type a numeric deletion priority and group resources into waves:

```python
DELETION_PRIORITY = {
    "cloudformation:stack": 0,
    "ec2:instance": 10,
    "lambda:function": 10,
    "rds:cluster": 20,
    "s3:bucket": 20,
    "dynamodb:table": 20,
    "elbv2:load_balancer": 30,
    "ec2:nat_gateway": 40,
    "ec2:volume": 50,
    "ec2:elastic_ip": 60,
    "ec2:security_group": 70,
    "ec2:route_table": 80,
    "ec2:subnet": 90,
    "ec2:internet_gateway": 100,
    "ec2:vpc": 110,
    "iam:role": 120,
    "iam:policy": 130,
}
```

Resources in the same wave have no dependencies on each other and are deleted concurrently. The orchestrator uses a `ThreadPoolExecutor` within each wave, then blocks until the wave completes before starting the next:

```python
def _delete_in_waves(self, waves):
    for i, wave in enumerate(waves, 1):
        logger.info(f"--- Wave {i}: {len(wave)} resources ---")
        self._delete_wave(wave)      # concurrent within wave, serial across waves

def _delete_wave(self, resources):
    with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
        futures = {executor.submit(deleter.delete, r): r for r in resources}
        for future in as_completed(futures):
            result = future.result()
            # log success or failure per resource
```

CloudFormation stacks land at priority 0 — deleted first — for a specific reason. When you delete a stack, AWS cascades the deletion through all managed resources. If you let service-specific deleters run first, you can end up deleting an RDS instance that a stack owns, causing the stack deletion to fail with `DELETE_FAILED`. That state requires manual intervention to resolve. Delete the stacks first, let CloudFormation handle its own teardown.

---

## The Plan Formatter

One of the bigger additions in the latest refactor is a dedicated plan formatter with three output modes.

**Terminal output** uses Rich to produce colored wave tables directly in the console — resource type, ID, name, and region per row, grouped by wave with counts.

**Markdown output** generates a GitHub-flavored markdown document for posting to `$GITHUB_STEP_SUMMARY` or as a PR comment. This is what approvers see in the Actions UI before deciding to approve or reject:

```markdown
# AWS Destroy Plan — :red_circle: Resources Queued for Destruction

## Run Details
| Field | Value |
| ----- | ----- |
| Account | `123456789012` |
| Identity | `arn:aws:iam::123456789012:user/deploy` |
| To destroy | **14** |
| Protected / skipped | 6 |

## Wave 0 — CloudFormation Stacks (1)
| Action | Type | Resource ID | Name | Region |
| ------ | ---- | ----------- | ---- | ------ |
| 🗑 destroy | `cloudformation:stack` | `arn:aws:...` | my-app-stack | us-east-1 |
...
```

**JSON output** serializes the full plan — account ID, caller ARN, each wave with its resources — for downstream tooling or audit logging.

```bash
python main.py plan --regions us-east-1 --output markdown --save-plan plan.md
python main.py plan --regions us-east-1 --output json | jq '.summary'
```

---

## Deletion Handlers: The Edge Cases That Matter

Each service's deletion handler is where the actual complexity lives.

**S3** — You cannot delete a non-empty bucket. For versioned buckets, you must delete every object version and every delete marker before the bucket can go. The handler branches on versioning state and paginates through both `list_object_versions` and `list_objects_v2` accordingly.

**RDS Clusters** — An Aurora cluster cannot be deleted while member instances exist. The cluster deleter reads the `members` list from the resource's `metadata`, terminates each instance first, then deletes the cluster with `SkipFinalSnapshot=True`. Final snapshots left behind were the source of several surprise bills in the original single-file script.

**VPC Security Groups** — Security groups can reference each other in ingress and egress rules. Deleting SG-A while SG-B has a rule referencing SG-A fails with a dependency violation. The handler revokes all ingress and egress rules before attempting deletion.

**NAT Gateways** — Deletion is asynchronous. The handler submits the delete call, then polls until the NAT Gateway state reaches `deleted` before returning. Elastic IPs are released in a later wave (priority 60 vs 40), after NAT Gateways are confirmed gone — otherwise the EIP release fails because the association still exists.

**IAM Roles** — Before a role can be deleted: detach all managed policies, delete all inline policies, remove the role from any instance profiles, delete those profiles if now empty. The IAM deleter handles all of this in sequence, because each step is a prerequisite for the next.

---

## Retry Logic and Idempotency

The `BaseDeleter` handles two failure modes that come up constantly on real accounts:

```python
def delete(self, resource):
    for attempt in range(1, self.MAX_RETRIES + 1):
        try:
            self._delete(resource)
            return DeletionResult(resource, success=True)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            # Already deleted — treat as success, don't fail
            if code in ("NoSuchEntity", "ResourceNotFoundException",
                        "InvalidInstanceID.NotFound", "NoSuchBucket",
                        "DBInstanceNotFound", "DBClusterNotFoundFault"):
                return DeletionResult(resource, success=True)
            # Rate limited — exponential backoff
            if code in ("Throttling", "RequestLimitExceeded", "ThrottlingException"):
                if attempt < self.MAX_RETRIES:
                    time.sleep(RETRY_SLEEP * attempt)
                    continue
            return DeletionResult(resource, success=False, error=str(e))
```

Idempotency matters because a reset run may be interrupted and restarted — a network blip mid-run, a workflow timeout, a manual cancellation. If a resource was deleted in the previous run, the handler returns success rather than propagating a 404. Throttling gets backoff with up to three attempts before the error is logged and the wave moves on.

---

## The GitHub Actions Approval Gate

The CI integration is where this framework stops feeling like a script and starts feeling like production infrastructure tooling.

Two workflows:

**`destroy-plan.yml`** — Triggered on every push to `dev` and on pull requests targeting `main`. Generates the destroy plan, posts it to the workflow step summary, and comments it on the PR (updating the comment on re-push rather than creating duplicates). No approvals, no deletions — just visibility.

**`destroy-apply.yml`** — Triggered on merge to `main`. Three sequential jobs:

```yaml
jobs:
  plan:
    # Discover resources, generate plan, post to summary, notify Slack
    steps:
      - run: python main.py plan --output markdown --save-plan destroy-plan.md
      - run: cat destroy-plan.md >> $GITHUB_STEP_SUMMARY
      - run: curl ... $SLACK_WEBHOOK_URL  # "Approval required" message

  approve:
    needs: plan
    environment: reset-approval    # GitHub Environment with required reviewers
    steps:
      - run: echo "Approved"

  apply:
    needs: [plan, approve]
    steps:
      - run: python main.py apply --confirm
```

The `approve` job uses a GitHub Environment named `reset-approval` configured with required reviewers in the repo settings. When this job starts, GitHub pauses the workflow, emails every required reviewer, and shows them the plan from Job 1 in the Actions UI. They click **Approve** or **Reject**. Approval lets Job 3 run. Rejection cancels the workflow — nothing is deleted.

If the plan produces zero resources to destroy, the approval gate is skipped entirely. No reason to interrupt a reviewer when there's nothing to review.

---

## What This Demonstrates as an Engineering Practice

Beyond the specific AWS problem it solves, the framework reflects a set of practices worth naming explicitly.

**Separation of concerns.** Discovery, filtering, and deletion are completely decoupled. You can run discovery without ever touching deletion. Adding a new service means adding one discoverer and one deleter — nothing else changes.

**Safety as architecture, not afterthought.** The default configuration is maximally conservative: IAM skipped, default VPC preserved, plan-only mode is the entry point. Every dangerous operation requires an explicit opt-in flag. The approval gate is structural, not just a README warning.

**Plan before act.** The `plan()` method is always called before `apply()` — including inside the `apply` command itself. There is no path to deletion without first seeing the full inventory of what will be destroyed. This is the most important design decision in the whole codebase.

**Fail informatively, not silently.** Every deletion result is tracked. The final summary reports exactly how many resources were destroyed, skipped, and errored. Failed deletions are logged but don't halt the run — the wave continues and remaining resources are attempted.

**Idempotent by design.** Running the framework twice produces the same end state as running it once. Already-deleted resources are not errors. Interrupted runs can be safely retried.

**Auditable deletion order.** The priority table in `core/config.py` is explicit and readable. Anyone can see exactly why security groups are deleted before subnets, why CloudFormation stacks go first, why Elastic IPs come after NAT Gateways. There is no emergent behavior.

---

## What's Next

The framework covers the most common services. Extending it follows the same pattern: one discoverer, one deleter, one entry in the priority table.

Services not yet covered:

- **ElastiCache** — clusters and replication groups
- **EKS** — clusters, node groups, managed add-ons
- **ECS** — services, task definitions, clusters
- **Secrets Manager** and **SSM Parameter Store**
- **CloudWatch** — log groups, alarms, dashboards
- **SNS / SQS** — topics and queues
- **Multi-account support** via AWS Organizations and cross-account role chaining

---

The full source is at [github.com/captcloud01/AWS_Cost_Optimization](https://github.com/captcloud01/AWS_Cost_Optimization). PRs and issues welcome.

---

*If this is the kind of infrastructure engineering you're working on — safe by default, reviewable before execution, explicit over magical — I'm always open to a conversation. Find me on LinkedIn or drop a comment below.*
