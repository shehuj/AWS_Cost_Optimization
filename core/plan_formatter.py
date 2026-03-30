"""
Destroy plan formatting — terminal (Rich), GitHub Markdown, and JSON.
Mirrors the terraform plan output style.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Optional

from discovery.base import Resource
from core.config import DELETION_PRIORITY

# Human-readable wave labels
_WAVE_LABELS: Dict[int, str] = {
    0: "CloudFormation Stacks",
    10: "Compute (EC2 / Lambda)",
    20: "Data (RDS / S3 / DynamoDB)",
    21: "RDS Instances",
    22: "RDS Snapshots",
    25: "RDS Subnet Groups",
    26: "RDS Parameter Groups",
    30: "Load Balancers",
    40: "NAT Gateways",
    50: "EBS Volumes & Snapshots",
    51: "AMIs",
    60: "Elastic IPs",
    65: "VPC Endpoints",
    70: "Security Groups",
    80: "Route Tables",
    81: "Network ACLs",
    90: "Subnets",
    100: "Internet Gateways",
    110: "VPCs",
    120: "IAM Roles",
    121: "IAM Users",
    122: "IAM Groups",
    130: "IAM Policies",
}

_DEFAULT_PRIORITY = 200


def _wave_label(priority: int) -> str:
    return _WAVE_LABELS.get(priority, f"Other (priority {priority})")


@dataclass
class DestroyPlan:
    account_id: str
    caller_arn: str
    regions: List[str]
    services: List[str]
    waves: List[List[Resource]]        # ordered by deletion priority
    protected: List[Resource]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    )

    @property
    def total_destroy(self) -> int:
        return sum(len(w) for w in self.waves)

    @property
    def total_protected(self) -> int:
        return len(self.protected)

    def wave_priority(self, wave: List[Resource]) -> int:
        if not wave:
            return _DEFAULT_PRIORITY
        return DELETION_PRIORITY.get(wave[0].resource_type, _DEFAULT_PRIORITY)


# ---------------------------------------------------------------------------
# Terminal output (Rich)
# ---------------------------------------------------------------------------

def format_terminal(plan: DestroyPlan) -> None:
    """Print the destroy plan to the terminal using Rich."""
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text

    console = Console()

    # Header panel
    header = (
        f"[bold]Account :[/bold]  {plan.account_id}\n"
        f"[bold]Identity:[/bold]  {plan.caller_arn}\n"
        f"[bold]Regions :[/bold]  {', '.join(plan.regions)}\n"
        f"[bold]Services:[/bold]  {', '.join(plan.services)}\n"
        f"[bold]Generated:[/bold] {plan.generated_at}"
    )
    console.print(Panel(header, title="[bold red]AWS Destroy Plan[/bold red]", border_style="red"))

    if plan.total_destroy == 0:
        console.print("\n[bold green]No resources to destroy.[/bold green]\n")
        return

    console.print(f"\n[bold]Resources scheduled for destruction[/bold] "
                  f"([red]{plan.total_destroy} to destroy[/red], "
                  f"[dim]{plan.total_protected} protected[/dim])\n")

    for wave in plan.waves:
        priority = plan.wave_priority(wave)
        label = _wave_label(priority)

        table = Table(
            show_header=True,
            header_style="bold white",
            border_style="dim",
            title=f"[yellow]Wave {priority}[/yellow]  {label}  [dim]({len(wave)} resource{'s' if len(wave) != 1 else ''})[/dim]",
            title_justify="left",
        )
        table.add_column("Action", style="bold red", width=9)
        table.add_column("Type", style="cyan", min_width=24)
        table.add_column("ID", style="white", min_width=28)
        table.add_column("Name", style="dim white", min_width=20)
        table.add_column("Region", style="dim", width=14)

        for r in wave:
            table.add_row("destroy", r.resource_type, r.resource_id, r.name, r.region)

        console.print(table)
        console.print()

    # Footer
    console.rule()
    if plan.total_destroy > 0:
        console.print(
            f"\n  [bold]Plan:[/bold] "
            f"[bold red]{plan.total_destroy} to destroy[/bold red], "
            f"[dim]{plan.total_protected} protected/skipped[/dim]\n"
        )
        console.print(
            "  [dim]This plan was generated from a live discovery of your AWS account.\n"
            "  Protected resources (tagged do_not_delete=true, default VPC, IAM) are excluded.\n"
            "  To apply: approve the deployment in the GitHub Actions workflow.\n"
            "  To abort: reject the deployment approval request.[/dim]\n"
        )


# ---------------------------------------------------------------------------
# Markdown output (GitHub Actions step summary)
# ---------------------------------------------------------------------------

def format_markdown(plan: DestroyPlan) -> str:
    """Return a GitHub-flavored Markdown string for use in $GITHUB_STEP_SUMMARY."""
    lines = []

    status = ":red_circle: Resources Queued for Destruction" if plan.total_destroy > 0 else ":white_check_mark: Nothing to Destroy"
    lines.append(f"# AWS Destroy Plan — {status}\n")

    lines.append("## Run Details\n")
    lines.append("| Field | Value |")
    lines.append("| ----- | ----- |")
    lines.append(f"| **Account** | `{plan.account_id}` |")
    lines.append(f"| **Identity** | `{plan.caller_arn}` |")
    lines.append(f"| **Regions** | `{', '.join(plan.regions)}` |")
    lines.append(f"| **Services** | `{', '.join(plan.services)}` |")
    lines.append(f"| **Generated** | {plan.generated_at} |")
    lines.append(f"| **To destroy** | **{plan.total_destroy}** |")
    lines.append(f"| **Protected / skipped** | {plan.total_protected} |\n")

    if plan.total_destroy == 0:
        lines.append("> No resources will be destroyed.")
        return "\n".join(lines)

    lines.append("---\n")
    lines.append("## Destruction Plan\n")
    lines.append("> Resources are destroyed in wave order. Within each wave, deletions run concurrently.\n")

    for wave in plan.waves:
        priority = plan.wave_priority(wave)
        label = _wave_label(priority)
        lines.append(f"### Wave {priority} — {label} `({len(wave)})`\n")
        lines.append("| Action | Type | Resource ID | Name | Region |")
        lines.append("| ------ | ---- | ----------- | ---- | ------ |")
        for r in wave:
            lines.append(f"| 🗑 destroy | `{r.resource_type}` | `{r.resource_id}` | {r.name} | {r.region} |")
        lines.append("")

    lines.append("---\n")
    lines.append(f"**Plan: {plan.total_destroy} to destroy, {plan.total_protected} protected**\n")
    lines.append(
        "> To apply this plan — approve the deployment in the **GitHub Actions** workflow.\n"
        "> To abort — reject the deployment approval request."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def format_json(plan: DestroyPlan) -> str:
    """Return a JSON string of the plan for machine-readable consumption."""
    data = {
        "generated_at": plan.generated_at,
        "account_id": plan.account_id,
        "caller_arn": plan.caller_arn,
        "regions": plan.regions,
        "services": plan.services,
        "summary": {
            "to_destroy": plan.total_destroy,
            "protected": plan.total_protected,
        },
        "waves": [
            {
                "priority": plan.wave_priority(wave),
                "label": _wave_label(plan.wave_priority(wave)),
                "count": len(wave),
                "resources": [
                    {
                        "resource_id": r.resource_id,
                        "resource_type": r.resource_type,
                        "name": r.name,
                        "region": r.region,
                        "arn": r.arn,
                    }
                    for r in wave
                ],
            }
            for wave in plan.waves
        ],
        "protected": [
            {
                "resource_id": r.resource_id,
                "resource_type": r.resource_type,
                "name": r.name,
                "region": r.region,
            }
            for r in plan.protected
        ],
    }
    return json.dumps(data, indent=2)
