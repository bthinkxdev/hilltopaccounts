from django.db import models


class LiveManager(models.Manager):
    """Default manager for records that are voided instead of deleted: hides voided rows from every query,
    including reverse relations and M2M, so a voided amount can never leak into a total.
    Use `all_objects` when the voided rows themselves are needed (history pages, the void action)."""

    def get_queryset(self):
        return super().get_queryset().filter(voided_at__isnull=True)
