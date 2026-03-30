# AWS Account Reset Framework

> Terraform-style plan/apply workflow for safely wiping an AWS account — with a human approval gate before anything is deleted.

![Python](https://img.shields.io/badge/python-3.11-blue)
![License](https://img.shields.io/badge/license-GPL--3.0-green)
![Workflows](https://img.shields.io/badge/CI-GitHub%20Actions-black)

---

## Overview

This framework discovers every resource across one or more AWS regions, shows you a detailed destroy plan grouped by service and deletion wave, then waits for explicit human approval before executing. Nothing is deleted without a reviewed plan and a confirmed approval.

The design mirrors `terraform plan` / `terraform apply`:

| Command | What it does |
| ------- | ------------ |
| `python main.py plan` | Discover resources, print destroy plan, exit — **no deletions** |
| `python main.py apply` | Generate plan, display it, prompt for confirmation, then destroy |

---

## Branch Workflow

```text
┌─────────────┐    push     ┌──────────────────────────────────────────────┐
│   dev       │ ──────────► │  destroy-plan.yml                            │
│   branch    │             │  1. Generate destroy plan                    │
└─────────────┘             │  2. Post to workflow summary                 │
                            │  3. Comment on PR (if open)                  │
                            │  4. Notify Slack (optional)                  │
                            │  ✅ Nothing is deleted                       │
                            └──────────────────────────────────────────────┘

┌─────────────┐   merge     ┌──────────────────────────────────────────────┐
│   main      │ ──────────► │  destroy-apply.yml                           │
│   branch    │             │                                              │
└─────────────┘             │  Job 1 — plan                                │
                            │    Generate plan → post summary → Slack      │
                            │                    ▼                         │
                            │  Job 2 — approve  (PAUSED)                   │
                            │    Reviewer sees plan in Actions UI          │
                            │    ✅ Approve ──► Job 3                      │
                            │    ❌ Reject  ──► Workflow cancelled         │
                            │                    ▼                         │
                            │  Job 3 — apply                               │
                            │    Execute destroy plan → Slack result       │
                            └──────────────────────────────────────────────┘
```

---

## Quick Start

### Prerequisites

```bash
pip install -r requirements.txt
```

Requires Python 3.11+ and AWS credentials with broad read/delete permissions.

### 1. Preview what would be destroyed

```bash
python main.py plan --regions us-east-1
```

Safe to run at any time — no resources are touched.

### 2. Review the plan, then apply

```bash
python main.py apply --regions us-east-1
# Displays the plan, then prompts:
# Type "CONFIRM RESET" to apply
```

### 3. For CI / automation (no prompt)

```bash
python main.py apply --regions us-east-1 --confirm
```

---

## Repository Structure

```text
aws-account-reset/
├── main.py                        # CLI entry point — plan and apply subcommands
├── config.yaml                    # Default configuration
├── requirements.txt
│
├── core/
│   ├── config.py                  # Config dataclass + deletion priority table
│   ├── orchestrator.py            # plan() → DestroyPlan | apply(plan) → results
│   └── plan_formatter.py          # Output: Rich terminal / GitHub Markdown / JSON
│
├── discovery/                     # One discoverer per AWS service
│   ├── base.py                    # Resource dataclass + BaseDiscoverer ABC
│   ├── ec2.py                     # Instances, volumes, snapshots, AMIs, EIPs, key pairs
│   ├── s3.py                      # Buckets — region-filtered, versioning-aware
│   ├── rds.py                     # Instances, clusters, snapshots, subnet/param groups
│   ├── vpc.py                     # VPCs, subnets, IGW, NAT GW, endpoints, SGs, NACLs, ELBs
│   ├── iam.py                     # Roles, users, groups, customer-managed policies
│   ├── lambda_.py                 # Functions and layers
│   ├── dynamodb.py                # Tables
│   └── cloudformation.py         # Root stacks only (nested stacks handled by parent)
│
├── filters/
│   └── tag_filter.py              # Tag protection, skip list, IAM guard, caller identity
│
├── graph/
│   └── dependency_graph.py        # Groups resources into ordered, concurrent deletion waves
│
├── deletion/                      # One deleter per AWS service
│   ├── base.py                    # Retry logic, idempotency, dry-run support
│   ├── ec2.py                     # Terminates instances, waits, force-detaches volumes
│   ├── s3.py                      # Drains versioned and unversioned buckets before delete
│   ├── rds.py                     # Removes cluster members before deleting cluster
│   ├── vpc.py                     # Detaches IGWs, revokes SG rules, waits for NAT GW
│   ├── iam.py                     # Detaches and removes policies before deleting principals
│   ├── lambda_.py                 # Deletes all layer versions
│   ├── dynamodb.py
│   └── cloudformation.py         # Polls until DELETE_COMPLETE before continuing
│
├── utils/
│   └── logger.py                  # Rich-formatted console output
│
└── .github/workflows/
    ├── destroy-plan.yml           # dev branch — plan only, no deletions
    └── destroy-apply.yml          # main branch — plan → approval gate → apply
```

---

## CLI Reference

### `plan`

Discover resources and print the destroy plan. Nothing is deleted.

```bash
# Terminal output (default)
python main.py plan --regions us-east-1

# Markdown output for GitHub step summary
python main.py plan --regions us-east-1 --output markdown

# JSON output (machine-readable)
python main.py plan --regions us-east-1 --output json

# Save plan to file
python main.py plan --regions us-east-1 --output markdown --save-plan plan.md

# Multiple regions
python main.py plan --regions us-east-1,us-west-2,eu-west-1

# Specific services only
python main.py plan --regions us-east-1 --services ec2,s3,rds,lambda,dynamodb
```

**Exit codes:** `0` = nothing to destroy · `2` = resources found, plan has changes

---

### `apply`

Generate the plan, display it, then execute after confirmation.

```bash
# Interactive — prompts: Type "CONFIRM RESET" to apply
python main.py apply --regions us-east-1

# Non-interactive for CI (after approval gate has already run)
python main.py apply --regions us-east-1 --confirm

# Include IAM resources — disabled by default, use with extreme caution
python main.py apply --regions us-east-1 --no-skip-iam

# Load from config file
python main.py apply --config-file config.yaml
```

---

### Shared Options

```text
  -r, --regions TEXT                  Comma-separated AWS regions  [default: us-east-1]
  -s, --services TEXT                 Comma-separated services     [default: all]
      --skip-iam / --no-skip-iam      Exclude IAM resources        [default: skip]
      --skip-default-vpc / --no-...   Preserve default VPC         [default: preserve]
      --skip-cloudformation           Skip CloudFormation stacks
      --protected-tag-key TEXT        Protection tag key           [default: do_not_delete]
      --protected-tag-value TEXT      Protection tag value         [default: true]
  -c, --config-file PATH              YAML config file (overrides all flags)

  plan only:
  -o, --output [text|markdown|json]   Output format                [default: text]
      --save-plan PATH                Write plan to this file

  apply only:
      --confirm                       Skip interactive confirmation prompt
```

---

## GitHub Actions Setup

### Step 1 — Add GitHub Secrets

**Repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret | Required | Description |
| ------ | -------- | ----------- |
| `AWS_ACCESS_KEY_ID` | Yes | IAM access key with broad delete permissions |
| `AWS_SECRET_ACCESS_KEY` | Yes | Corresponding secret key |
| `SLACK_WEBHOOK_URL` | No | Slack incoming webhook for plan/approval/result notifications |

---

### Step 2 — Create the Approval Environment

**Repo → Settings → Environments → New environment**

1. Name it exactly **`reset-approval`**
2. Enable **Required reviewers** — add one or more approvers
3. *(Optional)* Set a **Wait timer** (e.g. 5 minutes) as an additional buffer

When the `destroy-apply.yml` workflow reaches the approval job, GitHub emails all required reviewers. They click through to the Actions UI, review the full destroy plan from Job 1, then choose **Approve** or **Reject**.

---

### Step 3 — Add Repository Variables *(optional)*

**Repo → Settings → Variables → Actions → New variable**

| Variable | Description | Default |
| -------- | ----------- | ------- |
| `AWS_REGIONS` | Comma-separated regions | `us-east-1` |
| `RESET_SERVICES` | Comma-separated services to include | all except IAM |

---

### Workflow Triggers

| Workflow | Trigger | Deletes anything? |
| -------- | ------- | ----------------- |
| `destroy-plan.yml` | Push to `dev`, PR targeting `main` | Never |
| `destroy-apply.yml` | Push/merge to `main`, manual dispatch | Only after approval |

---

### Notification Flow (with `SLACK_WEBHOOK_URL`)

| Event | Message |
| ----- | ------- |
| Plan complete (dev push) | Plan ready for review — link to workflow |
| Plan complete (main push) | Approval required — resource count, approver list, link |
| Approval granted | Applying now — who approved, how many resources |
| Apply complete | Success or failure — link to run |

---

## Deletion Waves

Resources are grouped into dependency-ordered waves. All resources within a wave are deleted concurrently. The next wave does not start until the previous one finishes.

| Wave | Label | Resource Types |
| ---- | ----- | -------------- |
| 0 | CloudFormation Stacks | `cloudformation:stack` |
| 10 | Compute | `ec2:instance` · `lambda:function` |
| 20 | Data | `rds:cluster` · `s3:bucket` · `dynamodb:table` |
| 21–26 | RDS Cleanup | instances · snapshots · subnet groups · param groups |
| 30 | Load Balancers | `elbv2:load_balancer` · `elb:load_balancer` |
| 40 | NAT Gateways | `ec2:nat_gateway` |
| 50–51 | Block Storage | `ec2:volume` · `ec2:snapshot` · `ec2:ami` |
| 60 | Elastic IPs | `ec2:elastic_ip` |
| 65 | VPC Endpoints | `ec2:vpc_endpoint` |
| 70 | Security Groups | `ec2:security_group` |
| 80–81 | Routing | `ec2:route_table` · `ec2:network_acl` |
| 90 | Subnets | `ec2:subnet` |
| 100 | Internet Gateways | `ec2:internet_gateway` |
| 110 | VPCs | `ec2:vpc` |
| 120–130 | IAM | roles · users · groups · policies |

> CloudFormation stacks are always deleted first. When a stack is deleted, AWS tears down its managed resources automatically — this prevents race conditions with the service-specific deleters.

---

## Protection Rules

A resource is **never deleted** if any of the following conditions are true:

| Rule | Condition |
| ---- | --------- |
| **Protection tag** | Tagged `do_not_delete=true` (key/value configurable) |
| **Skip list** | Resource ID listed in `skip_resource_ids` in `config.yaml` |
| **Default VPC** | Is the default VPC or a default subnet (`--skip-default-vpc` on by default) |
| **IAM global flag** | Any IAM resource when `--skip-iam` is set (on by default) |
| **Caller identity** | The IAM role/user running the script (auto-detected via `sts:GetCallerIdentity`) |
| **Service-linked role** | IAM roles under `/aws-service-role/` path |
| **AWS-managed** | Main route tables, default NACLs (AWS blocks deletion regardless) |
| **Nested stacks** | CloudFormation nested stacks — parent stack handles teardown |

---

## Environment Variables

Every configuration option can be set via environment variable, useful for local runs or CI environments outside GitHub Actions:

```bash
AWS_REGIONS=us-east-1,us-west-2
SERVICES=ec2,s3,rds,lambda,dynamodb,vpc
PROTECTED_TAG_KEY=do_not_delete
PROTECTED_TAG_VALUE=true
SKIP_IAM=true
SKIP_DEFAULT_VPC=true
```

---

## License

GPL v3 — see [LICENSE](LICENSE).
