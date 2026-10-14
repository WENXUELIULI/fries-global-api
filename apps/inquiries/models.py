from django.db import models


class Inquiry(models.Model):
    name = models.CharField(max_length=120)
    company = models.CharField(max_length=160, blank=True, default="")
    country = models.CharField(max_length=120)
    email = models.EmailField(blank=True, default="")
    whatsapp = models.CharField(max_length=40, blank=True, default="")
    category = models.CharField(max_length=160)
    specs = models.TextField()
    locale = models.CharField(max_length=8, blank=True, default="en")
    attachment_path = models.CharField(max_length=512, blank=True, default="")
    email_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} · {self.category}"
