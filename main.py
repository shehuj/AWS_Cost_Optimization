#!/usr/bin/env python3
"""
AWS Account Reset Framework
Plan and apply destruction of AWS resources in dependency-safe order.

Usage:
  python main.py plan   [options]   # generate destroy plan, print and exit
  python main.py apply  [options]   # execute the destroy plan
"""
import sys
import click
from rich.console import Console

from core.config import Config, ALL_SERVICES
from core.orchestrator import ResetOrchestrator
from core.plan_formatter import format_terminal, format_markdown, format_json
from utils.logger import get_logger

console = Console()
logger = get_logger("aws-reset")


# ---------------------------------------------------------------------------
# Shared options factory
# ---------------------------------------------------------------------------

def _shared_options(fn):
    """Decorator that attaches shared CLI options to a command."""
    options = [
        click.option("--regions", "-r", default="us-east-1",
                     help="Comma-separated AWS regions  [default: us-east-1]"),
        click.option("--services", "-s", default=",".join(ALL_SERVICES),
                     help="Comma-separated services to target  [default: all]"),
        click.option("--skip-iam/--no-skip-iam", default=True,
                     help="Skip IAM resources  [default: skip]"),
        click.option("--skip-default-vpc/--no-skip-default-vpc", default=True,
                     help="Preserve default VPC and its subnets  [default: preserve]"),
        click.option("--skip-cloudformation", is_flag=True, default=False,
                     help="Skip CloudFormation stacks"),
        click.option("--protected-tag-key", default="do_not_delete",
                     help="Tag key marking a resource as protected"),
        click.option("--protected-tag-value", default="true",
                     help="Tag value marking a resource as protected"),
        click.option("--config-file", "-c", default=None,
                     help="Path to a YAML config file (overrides CLI flags)"),
    ]
    for option in reversed(options):
        fn = option(fn)
    return fn


def _build_config(regions, services, skip_iam, skip_default_vpc,
                  skip_cloudformation, protected_tag_key,
                  protected_tag_value, config_file) -> Config:
    if config_file:
        return Config.from_file(config_file)
    cfg = Config.from_env()
    cfg.regions = [r.strip() for r in regions.split(",")]
    cfg.services = [s.strip() for s in services.split(",")]
    cfg.skip_iam = skip_iam
    cfg.skip_default_vpc = skip_default_vpc
    cfg.skip_cloudformation = skip_cloudformation
    cfg.protected_tag_key = protected_tag_key
    cfg.protected_tag_value = protected_tag_value
    return cfg


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """AWS Account Reset Framework — plan and apply destructive account resets."""
    pass


# ---------------------------------------------------------------------------
# plan subcommand
# ---------------------------------------------------------------------------

@cli.command()
@_shared_options
@click.option("--output", "-o",
              type=click.Choice(["text", "markdown", "json"], case_sensitive=False),
              default="text",
              help="Output format  [default: text]")
@click.option("--save-plan", default=None,
              help="Write plan output to this file path (respects --output format)")
def plan(regions, services, skip_iam, skip_default_vpc, skip_cloudformation,
         protected_tag_key, protected_tag_value, config_file, output, save_plan):
    """
    Discover all resources and print the destruction plan.

    No resources are deleted. Safe to run at any time.
    Equivalent to 'terraform plan'.
    """
    cfg = _build_config(
        regions, services, skip_iam, skip_default_vpc,
        skip_cloudformation, protected_tag_key, protected_tag_value, config_file,
    )

    orchestrator = ResetOrchestrator(config=cfg)
    destroy_plan = orchestrator.plan()

    if output == "text":
        format_terminal(destroy_plan)
    elif output == "markdown":
        md = format_markdown(destroy_plan)
        click.echo(md)
    elif output == "json":
        click.echo(format_json(destroy_plan))

    if save_plan:
        _write_plan_file(destroy_plan, save_plan, output)
        logger.info(f"Plan saved to: {save_plan}")

    # Exit 2 when there are resources to destroy — lets CI distinguish
    # "nothing to do" (0) from "plan has changes" (2), same as terraform.
    sys.exit(2 if destroy_plan.total_destroy > 0 else 0)


# ---------------------------------------------------------------------------
# apply subcommand
# ---------------------------------------------------------------------------

@cli.command()
@_shared_options
@click.option("--confirm", is_flag=True, default=False,
              help="Skip interactive confirmation prompt (required for CI)")
def apply(regions, services, skip_iam, skip_default_vpc, skip_cloudformation,
          protected_tag_key, protected_tag_value, config_file, confirm):
    """
    Generate the destruction plan and execute it.

    Prompts for confirmation unless --confirm is passed.
    Equivalent to 'terraform apply'.

    WARNING: This permanently deletes AWS resources. Always run 'plan' first.
    """
    cfg = _build_config(
        regions, services, skip_iam, skip_default_vpc,
        skip_cloudformation, protected_tag_key, protected_tag_value, config_file,
    )

    orchestrator = ResetOrchestrator(config=cfg)

    # Always generate and display the plan first
    console.print("\n[bold yellow]Generating destroy plan...[/bold yellow]\n")
    destroy_plan = orchestrator.plan()
    format_terminal(destroy_plan)

    if destroy_plan.total_destroy == 0:
        console.print("[bold green]Nothing to destroy. Exiting.[/bold green]")
        sys.exit(0)

    # Confirmation gate
    if not confirm:
        console.print(
            "\n[bold red]WARNING:[/bold red] The above resources will be "
            "[bold]permanently deleted[/bold]. This cannot be undone.\n"
        )
        response = click.prompt('Type "CONFIRM RESET" to apply', default="")
        if response != "CONFIRM RESET":
            console.print("[yellow]Aborted — no resources were deleted.[/yellow]")
            sys.exit(0)

    summary = orchestrator.apply(destroy_plan)

    if summary.get("errors", 0) > 0:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_plan_file(destroy_plan, path: str, fmt: str) -> None:
    from core.plan_formatter import format_markdown, format_json
    if fmt == "json":
        content = format_json(destroy_plan)
    else:
        content = format_markdown(destroy_plan)
    with open(path, "w") as f:
        f.write(content)


if __name__ == "__main__":
    cli()
