from django.db import models
from django.utils import timezone


class Platform(models.TextChoices):
    TWITTER = 'twitter', 'X (Twitter)'
    TELEGRAM = 'telegram', 'Telegram'
    REDDIT = 'reddit', 'Reddit'
    YOUTUBE = 'youtube', 'YouTube'
    INSTAGRAM = 'instagram', 'Instagram'
    FACEBOOK = 'facebook', 'Facebook'


class SocialProfile(models.Model):
    platform = models.CharField(max_length=20, choices=Platform.choices, default=Platform.TWITTER)
    platform_user_id = models.CharField(max_length=120)
    username = models.CharField(max_length=120)
    display_name = models.CharField(max_length=150, blank=True)
    bio = models.TextField(blank=True)
    avatar_url = models.CharField(max_length=300, blank=True)
    
    followers_count = models.IntegerField(default=0)
    following_count = models.IntegerField(default=0)
    posts_count = models.IntegerField(default=0)
    is_verified = models.BooleanField(default=False)
    
    # Anonymized / Aggregated Demographic Signals
    estimated_age_group = models.CharField(
        max_length=20,
        choices=[
            ('18-24', '18-24'),
            ('25-34', '25-34'),
            ('35-49', '35-49'),
            ('50+', '50+'),
            ('unknown', 'Unknown')
        ],
        default='25-34'
    )
    estimated_country = models.CharField(max_length=100, default='Global')
    estimated_region = models.CharField(max_length=100, default='International')
    detected_language = models.CharField(max_length=10, default='en')
    primary_interest = models.CharField(
        max_length=60,
        choices=[
            ('Tech & AI', 'Tech & AI'),
            ('Finance & Crypto', 'Finance & Crypto'),
            ('Policy & Law', 'Policy & Law'),
            ('Media & Journalism', 'Media & Journalism'),
            ('Science & Climate', 'Science & Climate'),
            ('Gaming & Lifestyle', 'Gaming & Lifestyle'),
            ('General', 'General')
        ],
        default='Tech & AI'
    )
    
    # Network Centrality & Influence Attributes
    influence_score = models.FloatField(default=0.0, help_text="PageRank derived influence score (0-100)")
    betweenness_score = models.FloatField(default=0.0, help_text="Broker/bridge score between communities")
    in_degree = models.IntegerField(default=0, help_text="Received mentions/replies/reposts")
    out_degree = models.IntegerField(default=0, help_text="Initiated interactions")
    community_id = models.IntegerField(default=0, help_text="Detected community cluster ID")
    is_bot_suspicious = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('platform', 'platform_user_id')
        indexes = [
            models.Index(fields=['platform', 'username']),
            models.Index(fields=['influence_score']),
            models.Index(fields=['community_id']),
        ]

    def __str__(self):
        return f"@{self.username} ({self.get_platform_display()})"


class Topic(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    category = models.CharField(max_length=80, default='General')
    description = models.TextField(blank=True)
    keywords = models.JSONField(default=list, help_text="Extracted n-grams and keywords")
    
    first_detected_at = models.DateTimeField(default=timezone.now)
    last_active_at = models.DateTimeField(default=timezone.now)
    
    velocity_score = models.FloatField(default=1.0, help_text="Discussion growth momentum score")
    momentum_trend = models.CharField(
        max_length=20,
        choices=[('surging', 'Surging 🔥'), ('steady', 'Steady ⚡'), ('declining', 'Declining 📉')],
        default='steady'
    )
    is_emerging = models.BooleanField(default=False)
    total_volume = models.IntegerField(default=0)
    avg_sentiment = models.FloatField(default=0.0, help_text="-1.0 to 1.0")
    dominant_emotion = models.CharField(max_length=30, default='support')
    cascade_depth = models.IntegerField(default=1)

    class Meta:
        ordering = ['-velocity_score', '-total_volume']

    def __str__(self):
        return self.title


class SocialPost(models.Model):
    platform = models.CharField(max_length=20, choices=Platform.choices, default=Platform.TWITTER)
    external_id = models.CharField(max_length=150, unique=True)
    author = models.ForeignKey(SocialProfile, related_name='posts', on_delete=models.CASCADE)
    content = models.TextField()
    
    created_at = models.DateTimeField(default=timezone.now)
    collected_at = models.DateTimeField(auto_now_add=True)
    
    post_type = models.CharField(
        max_length=20,
        choices=[
            ('post', 'Original Post'),
            ('reply', 'Reply/Comment'),
            ('repost', 'Repost/Retweet'),
            ('quote', 'Quote Post')
        ],
        default='post'
    )
    parent_post = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        related_name='child_posts',
        on_delete=models.SET_NULL
    )
    
    # Interactions / Metrics
    likes_count = models.IntegerField(default=0)
    reposts_count = models.IntegerField(default=0)
    comments_count = models.IntegerField(default=0)
    views_count = models.IntegerField(default=0)
    shares_count = models.IntegerField(default=0)
    
    # AI / NLP Insights
    topic = models.ForeignKey(Topic, null=True, blank=True, related_name='posts', on_delete=models.SET_NULL)
    sentiment_label = models.CharField(
        max_length=20,
        choices=[('positive', 'Positive'), ('neutral', 'Neutral'), ('negative', 'Negative')],
        default='neutral'
    )
    sentiment_score = models.FloatField(default=0.0) # -1.0 to 1.0
    emotion_label = models.CharField(
        max_length=30,
        choices=[
            ('support', 'Support'),
            ('opposition', 'Opposition'),
            ('excitement', 'Excitement'),
            ('anxiety', 'Anxiety'),
            ('sarcasm', 'Sarcasm/Irony'),
            ('curiosity', 'Curiosity/Inquiry'),
            ('neutral', 'Objective/Neutral'),
        ],
        default='neutral'
    )
    emotion_scores = models.JSONField(default=dict)
    language = models.CharField(max_length=10, default='en')
    hashtags = models.JSONField(default=list)
    entities = models.JSONField(default=list)
    raw_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['platform', 'created_at']),
            models.Index(fields=['sentiment_label']),
            models.Index(fields=['emotion_label']),
        ]

    def __str__(self):
        return f"[{self.get_platform_display()}] @{self.author.username}: {self.content[:50]}"


class Interaction(models.Model):
    source_profile = models.ForeignKey(SocialProfile, related_name='interactions_sent', on_delete=models.CASCADE)
    target_profile = models.ForeignKey(SocialProfile, related_name='interactions_received', on_delete=models.CASCADE)
    post = models.ForeignKey(SocialPost, null=True, blank=True, related_name='interactions', on_delete=models.SET_NULL)
    interaction_type = models.CharField(
        max_length=20,
        choices=[('mention', 'Mention'), ('repost', 'Repost'), ('reply', 'Reply'), ('quote', 'Quote')],
        default='mention'
    )
    weight = models.FloatField(default=1.0)
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['source_profile', 'target_profile']),
            models.Index(fields=['interaction_type']),
            models.Index(fields=['timestamp']),
        ]

    def __str__(self):
        return f"@{self.source_profile.username} -> {self.interaction_type} -> @{self.target_profile.username}"


class TrendAlert(models.Model):
    topic = models.ForeignKey(Topic, related_name='alerts', on_delete=models.CASCADE)
    alert_type = models.CharField(
        max_length=30,
        choices=[
            ('VIRAL_SPIKE', 'Viral Volume Spike'),
            ('SENTIMENT_SHIFT', 'Rapid Sentiment Polarization'),
            ('PROPAGATION_BURST', 'High Velocity Information Cascade'),
            ('HIGH_OPPOSITION', 'Sudden Surge in Opposition & Backlash'),
        ],
        default='VIRAL_SPIKE'
    )
    severity = models.CharField(
        max_length=20,
        choices=[('critical', 'Critical 🚨'), ('warning', 'Warning ⚠️'), ('info', 'Notice ℹ️')],
        default='info'
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    metric_value = models.CharField(max_length=80, blank=True)
    triggered_at = models.DateTimeField(default=timezone.now)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-triggered_at']

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.title}"


class ExecutiveBrief(models.Model):
    title = models.CharField(max_length=200, default="SMA.AI Daily Audience Intelligence Synthesis")
    generated_at = models.DateTimeField(default=timezone.now)
    timeframe = models.CharField(max_length=50, default="Last 24 Hours")
    summary_markdown = models.TextField()
    key_findings = models.JSONField(default=list)
    metrics_snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ['-generated_at']

    def __str__(self):
        return f"{self.title} ({self.generated_at.strftime('%Y-%m-%d %H:%M')})"
