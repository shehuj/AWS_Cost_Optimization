#!/usr/bin/env python3
"""
AWS Account Reset Framework
Discovers and deletes AWS resources in dependency-safe order.
"""
import sys
import click
from rich.console import Console
from rich.panel import Panel

from core.config import Config, ALL_SERVICES
from core.orchestrator import ResetOrchestrator
from utils.logger import get_logger

console = Console()
logger = get_logger("aws-reset")


@click.command()
@click.option("--regions", "-r", default="us-east-1",
              help="Comma-separated list of AWS regions (default: us-east-1)")
@click.option("--services", "-s", default=",".join(ALL_SERVICES),
              help=f"Comma-separated services to reset (default: all). Available: {', '.join(ALL_SERVICES)}")
@click.option("--dry-run", is_flag=True, default=False,
              help="Discover and print plan without deleting anything")
@click.option("--skip-iam", is_flag=True, default=True,
              help="Skip IAM resources (default: True — explicit --no-skip-iam to include)")
@click.option("--no-skip-iam", "skip_iam", flag_value=False,
              help="Include IAM resources in the reset (dangerous)")
@click.option("--skip-default-vpc", is_flag=True, default=True,
              help="Skip default VPC and its subnets (default: True)")
@click.option("--no-skip-default-vpc", "skip_default_vpc", flag_value=False)
@click.option("--skip-cloudformation", is_flag=True, default=False,
              help="Skip CloudFormation stacks")
@click.option("--config-file", "-c", default=None,
              help="Path to a YAML config file")
@click.option("--protected-tag-key", default="do_not_delete",
              help="Tag key used to protect resources (default: do_not_delete)")
@click.option("--protected-tag-value", default="true",
              help="Tag value used to protect resources (default: true)")
@click.option("--confirm", is_flag=True, default=False,
              help="Skip the interactive confirmation prompt (for CI/automation)")
def main(
    regions, services, dry_run, skip_iam, skip_default_vpc,
    skip_cloudformation, config_file, protected_tag_key,
    protected_tag_value, confirm
):
    """
    AWS Account Reset Framework

    Discovers all resources across specified regions and deletes them in
    dependency-safe order. Respects protection tags and default VPC preservation.

    WARNING: This performs IRREVERSIBLE destructive operations.
    Always run with --dry-run first to preview what will be deleted.
    """
    # Load config (file takes precedence, then CLI, then env)
    if config_file:
        cfg = Config.from_file(config_file)
    else:
        cfg = Config.from_env()
        cfg.regions = [r.strip() for r in regions.split(",")]
        cfg.services = [s.strip() for s in services.split(",")]
        cfg.dry_run = dry_run
        cfg.skip_iam = skip_iam
        cfg.skip_default_vpc = skip_default_vpc
        cfg.skip_cloudformation = skip_cloudformation
        cfg.protected_tag_key = protected_tag_key
        cfg.protected_tag_value = protected_tag_value

    # Print banner
    mode = "[bold red]LIVE[/bold red]" if not cfg.dry_run else "[bold magenta]DRY-RUN[/bold magenta]"
    console.print(Panel(
        f"[bold]AWS Account Reset Framework[/bold]\n"
        f"Regions : {', '.join(cfg.regions)}\n"
        f"Services: {', '.join(cfg.services)}\n"
        f"IAM     : {'skip' if cfg.skip_iam else '[bold red]INCLUDED[/bold red]'}\n"
        f"Mode    : {mode}",
        title="aws-reset",
        border_style="red" if not cfg.dry_run else "magenta",
    ))

    if not cfg.dry_run:
        if not confirm:
            console.print(
                "\n[bold red]WARNING:[/bold red] This will PERMANENTLY DELETE resources. "
                "This action is [bold]irreversible[/bold].\n"
            )
            response = click.prompt(
                'Type "CONFIRM RESET" to proceed',
                default="",
            )
            if response != "CONFIRM RESET":
                console.print("[yellow]Aborted.[/yellow]")
                sys.exit(0)

    orchestrator = ResetOrchestrator(config=cfg)
    summary = orchestrator.run()

    if summary.get("errors", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
