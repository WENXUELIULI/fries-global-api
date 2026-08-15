from django.db import models


class EngageEvent(models.Model):
    at = models.DateTimeField(db_index=True)
    kind = models.CharField(max_length=32, db_index=True)
    section_id = models.CharField(max_length=64, db_index=True)
    target_id = models.CharField(max_length=128, blank=True, default="")
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    path = models.CharField(max_length=512, blank=True, default="")
    client_key = models.CharField(max_length=128, blank=True, default="")

    def __str__(self) -> str:
        return f"{self.kind}:{self.section_id}"
