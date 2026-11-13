from django.core.management.base import BaseCommand
from analytics.pipeline import seed_demo_data


class Command(BaseCommand):
    help = 'Seeds SMA.AI platform with comprehensive multi-platform data, networks, sentiments, and topics'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Clear existing data and reseed from scratch')

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Initializing SMA.AI intelligence seed pipeline..."))
        result = seed_demo_data(force=options.get('force', False))
        self.stdout.write(self.style.SUCCESS(f"Done! Seed status: {result}"))
