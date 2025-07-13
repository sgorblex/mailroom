from .interfaces import Filter

def apply_filters(filters: list[Filter], dry_run: bool = False) -> None:
    for filter in filters:
        filter.apply(dry_run=dry_run)
