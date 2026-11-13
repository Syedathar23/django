import time
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from analytics.live_collectors import run_live_multiplatform_sync, get_api_connectors_status
from analytics.models import SocialPost, SocialProfile
from analytics.graph_engine import compute_network_metrics
from analytics.trend_engine import update_trend_velocities


class Command(BaseCommand):
    help = 'Fetches real-world live social media data across Twitter/X, Reddit, YouTube, Telegram, Hacker News, and Mastodon'

    def add_arguments(self, parser):
        parser.add_argument('--keyword', type=str, default='AI', help='Topic or keyword to track')
        parser.add_argument(
            '--sources',
            nargs='+',
            default=['twitter', 'reddit', 'youtube', 'telegram', 'hackernews', 'mastodon'],
            help='Platforms to scrape (twitter, reddit, youtube, telegram, hackernews, mastodon)'
        )
        parser.add_argument('--limit', type=int, default=10, help='Max items per platform')
        parser.add_argument('--clear-old', action='store_true', help='Clear synthetic or old mock posts before ingesting')
        parser.add_argument('--status', action='store_true', help='Show connection status for all social media APIs and exit')
        parser.add_argument('--daemon', action='store_true', help='Run continuous background live ingestion loop')
        parser.add_argument('--interval', type=int, default=60, help='Seconds between fetches in daemon mode')

    def handle(self, *args, **options):
        if options['status']:
            self.stdout.write(self.style.MIGRATE_HEADING("=== Social Media API Connectors Matrix ==="))
            status_map = get_api_connectors_status()
            for key, info in status_map.items():
                status_color = self.style.SUCCESS if info['configured'] else self.style.WARNING
                self.stdout.write(
                    f"[{status_color(info['status'])}] {info['name']} - {info['auth_type']}: {info['description']}"
                )
            return

        keyword = options['keyword']
        sources = options['sources']
        limit = options['limit']
        clear_old = options['clear_old']
        daemon = options['daemon']
        interval = options['interval']

        if clear_old:
            self.stdout.write(self.style.NOTICE("Clearing synthetic/mock data..."))
            mock_posts = SocialPost.objects.filter(
                Q(external_id__startswith='seed_') |
                Q(external_id__startswith='hist_') |
                Q(external_id__startswith='live_')
            )
            deleted_count = mock_posts.count()
            mock_posts.delete()
            SocialProfile.objects.filter(posts__isnull=True).delete()
            compute_network_metrics()
            update_trend_velocities()
            self.stdout.write(self.style.SUCCESS(f"Cleared {deleted_count} mock posts."))

        def run_cycle():
            self.stdout.write(self.style.NOTICE(
                f"[{timezone.now().strftime('%H:%M:%S')}] Connecting to live social networks for keyword: '{keyword}'..."
            ))
            result = run_live_multiplatform_sync(
                sources=sources,
                keyword=keyword,
                limit_per_source=limit
            )
            breakdown_str = ", ".join(f"{k}: {v}" for k, v in result['breakdown'].items())
            self.stdout.write(self.style.SUCCESS(
                f"Successfully collected {result['total_real_posts_ingested']} real live posts! "
                f"Breakdown: [{breakdown_str}]"
            ))

        if daemon:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"Starting Continuous Social Media Live Collector daemon (Interval: {interval}s). Press Ctrl+C to stop."
            ))
            try:
                while True:
                    run_cycle()
                    time.sleep(interval)
            except KeyboardInterrupt:
                self.stdout.write(self.style.NOTICE("\nStopped live collector daemon."))
        else:
            run_cycle()
