from django.db import models


class ProductCategory(models.Model):
    title = models.CharField(max_length=120)
    image_path = models.CharField(max_length=512, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.title
