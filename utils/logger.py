import logging
import sys
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "skip": "dim white",
    "dry_run": "bold magenta",
})

console = Console(theme=custom_theme)


def get_logger(name: str = "aws-reset") -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, markup=True)],
    )
    logger = logging.getLogger(name)
    return logger


def log_resource_action(logger: logging.Logger, action: str, resource_type: str,
                        resource_id: str, dry_run: bool = False) -> None:
    if dry_run:
        logger.info(f"[dry_run][DRY-RUN][/dry_run] Would {action}: [bold]{resource_type}[/bold] {resource_id}")
    else:
        icon = "🗑" if action == "delete" else "⏭"
        logger.info(f"{icon}  {action.capitalize()}: [bold]{resource_type}[/bold] {resource_id}")


def log_skip(logger: logging.Logger, resource_type: str, resource_id: str, reason: str) -> None:
    logger.info(f"[skip]⏭  Skipping {resource_type} {resource_id} ({reason})[/skip]")


def log_success(logger: logging.Logger, message: str) -> None:
    logger.info(f"[success]✅  {message}[/success]")


def log_error(logger: logging.Logger, message: str) -> None:
    logger.error(f"[error]❌  {message}[/error]")


def log_warning(logger: logging.Logger, message: str) -> None:
    logger.warning(f"[warning]⚠️  {message}[/warning]")
