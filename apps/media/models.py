from django.db import models


class SiteImageSlot(models.Model):
    slot_key = models.CharField(max_length=64, unique=True)
    file_path = models.CharField(max_length=512)
    content_type = models.CharField(max_length=64)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=80, blank=True, default="")

    def __str__(self) -> str:
        return self.slot_key
