from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.engage.models import EngageEvent


class Command(BaseCommand):
    help = "Delete EngageEvent rows older than about 90 days."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=90)
        deleted, _ = EngageEvent.objects.filter(at__lt=cutoff).delete()
        self.stdout.write(str(deleted))
