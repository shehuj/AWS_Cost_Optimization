# AWS Account Reset Framework

A modular, dependency-safe framework for fully resetting an AWS account. Discovers resources across services and regions, filters protected resources, then deletes everything in the correct dependency order.

---

## Architecture

```text
aws-account-reset/
├── main.py                        # CLI entry point (click)
├── config.yaml                    # Default configuration
├── requirements.txt
│
├── core/
│   ├── config.py                  # Config dataclass + env/file loading
│   └── orchestrator.py            # Discovery → Filter → Delete pipeline
│
├── discovery/                     # Per-service resource finders
│   ├── base.py                    # Resource dataclass + BaseDiscoverer ABC
│   ├── ec2.py                     # Instances, volumes, snapshots, AMIs, EIPs, key pairs
│   ├── s3.py                      # Buckets (region-filtered, versioning-aware)
│   ├── rds.py                     # Instances, clusters, snapshots, subnet/param groups
│   ├── vpc.py                     # VPCs, subnets, IGW, NAT GW, endpoints, SGs, NACLs, ELBs
│   ├── iam.py                     # Roles, users, groups, customer-managed policies
│   ├── lambda_.py                 # Functions and layers
│   ├── dynamodb.py                # Tables
│   └── cloudformation.py         # Root stacks (nested stacks skipped)
│
├── filters/
│   └── tag_filter.py              # Tag protection + skip list + IAM guard + caller identity
│
├── graph/
│   └── dependency_graph.py        # Groups resources into ordered deletion waves
│
├── deletion/                      # Per-service deletion handlers
│   ├── base.py                    # BaseDeleter with retry + idempotency + dry-run
│   ├── ec2.py                     # Terminates instances, waits, releases EIPs
│   ├── s3.py                      # Empties versioned/unversioned buckets before delete
│   ├── rds.py                     # Deletes cluster members before cluster, skips final snapshot
│   ├── vpc.py                     # Detaches IGWs, revokes SG rules, waits for NAT GW
│   ├── iam.py                     # Detaches/deletes policies before roles/users/groups
│   ├── lambda_.py                 # Deletes all layer versions
│   ├── dynamodb.py
│   └── cloudformation.py         # Polls until stack deletion completes
│
└── utils/
    └── logger.py                  # Rich-formatted console logging
```

---

## Deletion Waves

Resources are grouped into waves and deleted in order. Within each wave, deletions run concurrently. This ensures children are always removed before parents.

| Wave | Resource Types |
| ---- | -------------- |
| 0 | CloudFormation stacks |
| 10 | EC2 instances, Lambda functions |
| 20 | RDS clusters, DynamoDB tables, S3 buckets |
| 21–26 | RDS instances, snapshots, subnet groups, parameter groups |
| 30 | Load Balancers (ALB / NLB / Classic) |
| 40 | NAT Gateways |
| 50–51 | EBS volumes, snapshots, AMIs |
| 60 | Elastic IPs |
| 65 | VPC Endpoints |
| 70 | Security Groups |
| 80–81 | Route Tables, Network ACLs |
| 90 | Subnets |
| 100 | Internet Gateways |
| 110 | VPCs |
| 120–130 | IAM Roles, Users, Groups, Policies |

---

## Protection Mechanisms

A resource is **never deleted** if any of the following apply:

- Tagged with `do_not_delete=true` (key/value are configurable)
- Its resource ID is in the `skip_resource_ids` list
- It is the default VPC or a default subnet (`skip_default_vpc: true` by default)
- It is any IAM resource (`skip_iam: true` by default)
- It is the IAM role or user currently running the script (auto-detected via STS)
- It is an AWS service-linked role (`/aws-service-role/` path prefix)
- It is a main route table or default NACL (AWS prevents deletion of these anyway)
- It is a nested CloudFormation stack (parent stack handles it)

---

## Usage

### Install dependencies

```bash
pip install -r requirements.txt
```

### Always dry-run first

```bash
python main.py --dry-run --regions us-east-1
```

Prints the full deletion plan — no resources are touched.

### Reset a single region

```bash
python main.py --regions us-east-1
# Interactive prompt: Type "CONFIRM RESET" to proceed
```

### Reset multiple regions

```bash
python main.py --regions us-east-1,us-west-2,eu-west-1
```

### Limit to specific services

```bash
python main.py --regions us-east-1 --services ec2,s3,rds
```

Available services: `cloudformation`, `ec2`, `s3`, `rds`, `lambda`, `dynamodb`, `vpc`, `iam`

### Include IAM resources (use with extreme caution)

```bash
python main.py --regions us-east-1 --no-skip-iam
```

### Use a config file

```bash
python main.py --config-file config.yaml
```

### Non-interactive / CI mode

```bash
python main.py --regions us-east-1 --confirm
```

### Full CLI reference

```text
Options:
  -r, --regions TEXT              Comma-separated AWS regions  [default: us-east-1]
  -s, --services TEXT             Comma-separated services to reset
      --dry-run                   Preview plan without deleting
      --skip-iam / --no-skip-iam  Skip IAM resources (default: skip)
      --skip-default-vpc / --no-skip-default-vpc
      --skip-cloudformation       Skip CloudFormation stacks
  -c, --config-file PATH          Path to YAML config file
      --protected-tag-key TEXT    [default: do_not_delete]
      --protected-tag-value TEXT  [default: true]
      --confirm                   Skip interactive confirmation (for CI)
```

---

## GitHub Actions

Trigger via **Actions → AWS Account Reset → Run workflow**.

| Input | Required | Description |
| ----- | -------- | ----------- |
| `confirm` | Yes | Must be `CONFIRM RESET` for a live run |
| `regions` | No | Comma-separated regions (default: `us-east-1`) |
| `services` | No | Comma-separated services (default: all except IAM) |
| `dry_run` | No | Set `true` to preview without deleting |
| `skip_iam` | No | Set `false` to include IAM resources (default: `true`) |

### Required GitHub secrets

| Secret | Description |
| ------ | ----------- |
| `AWS_ACCESS_KEY_ID` | IAM user access key ID |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret access key |

The IAM user needs `AdministratorAccess` or equivalent broad permissions across the services being reset.

---

## Environment Variables

All config options can be set via environment variables (useful for CI outside GitHub Actions):

```bash
AWS_REGIONS=us-east-1,us-west-2
SERVICES=ec2,s3,rds,lambda,dynamodb,vpc
PROTECTED_TAG_KEY=do_not_delete
PROTECTED_TAG_VALUE=true
DRY_RUN=true
SKIP_IAM=true
SKIP_DEFAULT_VPC=true
```

---

## License

GPL v3 — see [LICENSE](LICENSE).
