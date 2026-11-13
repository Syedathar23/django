import os
import re
import json
import collections
from datetime import timedelta
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Avg, Count, Sum, Q
from django.conf import settings

from analytics.models import SocialPost, SocialProfile, Topic, TrendAlert, Interaction, ExecutiveBrief, Platform
from analytics.graph_engine import get_network_graph_data, get_propagation_cascade, compute_network_metrics
from analytics.trend_engine import extract_emerging_keywords, update_trend_velocities
from analytics.pipeline import simulate_live_stream_surge, ingest_raw_post, seed_demo_data
from analytics.nlp_engine import analyze_sentiment, analyze_emotions
from analytics.live_collectors import run_live_multiplatform_sync, get_api_connectors_status


# ── Stopwords for keyword extraction ──────────────────────────────────────────
_STOPWORDS = {
    'the','a','an','and','or','but','in','on','at','to','for','of','with',
    'is','are','was','were','be','been','being','have','has','had','do',
    'does','did','will','would','could','should','may','might','shall',
    'this','that','these','those','it','its','by','from','as','into',
    'not','no','if','so','than','then','about','up','out','over','also',
    'he','she','they','we','you','i','me','him','her','us','them','my',
    'their','our','your','his','her','its','said','says','say','just',
    'more','can','all','new','get','one','two','three','how','what',
    'when','where','which','who','why','any','some','much','many','per',
    'been','has','via','vs','am','pm','etc','re','s','t','de','le',
    'https','http','www','com','bit','ly','nbsp',
}


def _extract_keywords(posts_qs, top_n: int = 20):
    """Extract top keywords from posts with sentiment tagging."""
    word_sentiment = collections.defaultdict(list)
    for p in posts_qs.values('content', 'sentiment_score', 'sentiment_label')[:300]:
        words = re.findall(r'\b[a-zA-Z]{4,}\b', (p['content'] or '').lower())
        for w in words:
            if w not in _STOPWORDS:
                word_sentiment[w].append((p['sentiment_score'] or 0.0, p['sentiment_label'] or 'neutral'))

    keywords = []
    for word, entries in sorted(word_sentiment.items(), key=lambda x: -len(x[1]))[:top_n]:
        avg_s = sum(e[0] for e in entries) / len(entries)
        sent = 'positive' if avg_s > 0.1 else ('negative' if avg_s < -0.1 else 'neutral')
        keywords.append({'keyword': word, 'count': len(entries), 'sentiment': sent})
    return sorted(keywords, key=lambda x: -x['count'])[:top_n]


def home_view(request):
    """
    Topic Social Media Analytics Home Page (Capstone-style report).
    Enables users to input any topic, brand, or person and generates
    a deep-dive social media intelligence report with dossier structure.
    """
    topic_query = request.GET.get('q', '').strip() or request.GET.get('topic', '').strip()
    force_live = request.GET.get('live', '').lower() in ('1', 'true', 'yes')

    report_data = None
    if topic_query:
        match_filter = (
            Q(topic__title__icontains=topic_query) |
            Q(content__icontains=topic_query) |
            Q(hashtags__icontains=topic_query)
        )
        existing_count = SocialPost.objects.filter(match_filter).count()

        # Fetch live data if insufficient or forced
        if existing_count < 4 or force_live:
            try:
                run_live_multiplatform_sync(keyword=topic_query, limit_per_source=8)
            except Exception as e:
                print(f"[Live Topic Fetch] {e}")

        # Re-query
        matching_posts = SocialPost.objects.filter(match_filter).select_related('author', 'topic').order_by('-created_at')
        total_posts = matching_posts.count()

        # Broad fallback
        if total_posts == 0:
            words = [w for w in topic_query.split() if len(w) > 2]
            if words:
                broad_filter = Q()
                for w in words:
                    broad_filter |= Q(content__icontains=w) | Q(topic__title__icontains=w)
                matching_posts = SocialPost.objects.filter(broad_filter).select_related('author', 'topic').order_by('-created_at')
                total_posts = matching_posts.count()

        if total_posts > 0:
            # ── Core Aggregates ───────────────────────────────────────────────
            total_likes    = matching_posts.aggregate(s=Sum('likes_count'))['s'] or 0
            total_reposts  = matching_posts.aggregate(s=Sum('reposts_count'))['s'] or 0
            total_comments = matching_posts.aggregate(s=Sum('comments_count'))['s'] or 0
            total_views    = matching_posts.aggregate(s=Sum('views_count'))['s'] or 0
            engagement_total = total_likes + total_reposts + total_comments
            avg_sentiment  = round(matching_posts.aggregate(a=Avg('sentiment_score'))['a'] or 0.0, 2)

            # ── Sentiment Counts ──────────────────────────────────────────────
            pos_count = matching_posts.filter(sentiment_label='positive').count()
            neu_count = matching_posts.filter(sentiment_label='neutral').count()
            neg_count = matching_posts.filter(sentiment_label='negative').count()
            pos_pct = round((pos_count / total_posts) * 100, 1)
            neu_pct = round((neu_count / total_posts) * 100, 1)
            neg_pct = round((neg_count / total_posts) * 100, 1)
            favorable_ratio = round(pos_count / max(1, pos_count + neg_count), 3)
            net_sentiment_score = round((pos_count - neg_count) / max(1, total_posts), 3)

            # ── Emotion Spectrum (capstone-style) ────────────────────────────
            raw_emotions = ['support', 'opposition', 'excitement', 'anxiety', 'sarcasm', 'curiosity', 'neutral']
            emotion_counts = {e: matching_posts.filter(emotion_label=e).count() for e in raw_emotions}
            dominant_emotion = max(emotion_counts, key=emotion_counts.get)
            dominant_emotion_pct = round((emotion_counts[dominant_emotion] / total_posts) * 100, 1) if total_posts else 0

            # Capstone emotion_spectrum mapping
            emotion_spectrum = {
                'enthusiasm':        emotion_counts.get('excitement', 0) + emotion_counts.get('support', 0),
                'satisfaction':      max(0, pos_count - emotion_counts.get('excitement', 0)),
                'neutral_reporting': emotion_counts.get('neutral', 0),
                'skepticism_cost':   emotion_counts.get('sarcasm', 0) + emotion_counts.get('curiosity', 0),
                'friction_issues':   emotion_counts.get('opposition', 0) + emotion_counts.get('anxiety', 0),
            }

            # ── Platform Breakdown ────────────────────────────────────────────
            platform_counts = {}
            for p_code, p_name in Platform.choices:
                cnt = matching_posts.filter(platform=p_code).count()
                if cnt > 0:
                    platform_counts[p_code] = {
                        'name': p_name,
                        'count': cnt,
                        'pct': round((cnt / total_posts) * 100, 1)
                    }
            platforms_list = [
                {'name': v['name'], 'count': v['count']}
                for v in sorted(platform_counts.values(), key=lambda x: -x['count'])
            ]
            top_platform = platforms_list[0]['name'] if platforms_list else 'Multi-platform'

            # ── Topics List ───────────────────────────────────────────────────
            topic_qs = (
                matching_posts.values('topic__title')
                .annotate(count=Count('id'))
                .exclude(topic__title=None)
                .order_by('-count')[:8]
            )
            topics_list = [
                {'name': t['topic__title'], 'count': t['count'], 'growth': round(t['count'] / max(1, total_posts) * 10, 1)}
                for t in topic_qs
            ]

            # ── Top Posts (capstone: up to 25) ────────────────────────────────
            top_posts_qs = matching_posts.order_by('-likes_count', '-views_count')[:25]
            top_posts = []
            for p in top_posts_qs:
                meta = p.raw_metadata or {} if hasattr(p, 'raw_metadata') else {}
                post_url = (meta.get('url') or meta.get('story_url') or
                            f"https://twitter.com/i/web/status/{p.external_id.replace('tw_','')}"
                            if p.platform == 'twitter' else '#')
                top_posts.append({
                    'content':    p.content[:200],
                    'author':     p.author.display_name if p.author else 'Unknown',
                    'platform':   p.platform,
                    'likes':      p.likes_count,
                    'reposts':    p.reposts_count,
                    'comments':   p.comments_count,
                    'views':      p.views_count,
                    'sentiment':  p.sentiment_label,
                    'url':        post_url,
                    'created_at': p.created_at,
                })

            # ── Praised & Critical Quotes ─────────────────────────────────────
            pos_posts = matching_posts.filter(sentiment_label='positive').order_by('-likes_count')
            neg_posts = matching_posts.filter(sentiment_label='negative').order_by('-likes_count')

            praised_quotes = []
            for p in pos_posts[:8]:
                txt = (p.content or '').strip()
                if len(txt) < 25:
                    continue
                meta = p.raw_metadata or {} if hasattr(p, 'raw_metadata') else {}
                praised_quotes.append({
                    'quote':    txt[:200],
                    'author':   p.author.display_name if p.author else 'Anonymous',
                    'platform': p.get_platform_display() if hasattr(p, 'get_platform_display') else p.platform,
                    'topic':    topic_query,
                    'url':      meta.get('url', '#'),
                    'likes':    p.likes_count,
                })

            critical_quotes = []
            for p in neg_posts[:8]:
                txt = (p.content or '').strip()
                if len(txt) < 25:
                    continue
                meta = p.raw_metadata or {} if hasattr(p, 'raw_metadata') else {}
                critical_quotes.append({
                    'quote':    txt[:200],
                    'author':   p.author.display_name if p.author else 'Anonymous',
                    'platform': p.get_platform_display() if hasattr(p, 'get_platform_display') else p.platform,
                    'topic':    topic_query,
                    'url':      meta.get('url', '#'),
                    'likes':    p.likes_count,
                })

            # ── Semantic Keywords ──────────────────────────────────────────────
            semantic_keywords = _extract_keywords(matching_posts, top_n=20)

            # ── Influencer Leaderboard (YouTube-first, then general) ───────────
            author_ids = matching_posts.values_list('author_id', flat=True).distinct()
            yt_posts_qs = matching_posts.filter(platform='youtube').order_by('-views_count')[:10]
            influencer_leaderboard = []
            seen_channels = set()
            for p in yt_posts_qs:
                ch_name = p.author.display_name if p.author else 'Unknown'
                if ch_name in seen_channels:
                    continue
                seen_channels.add(ch_name)
                meta = p.raw_metadata or {} if hasattr(p, 'raw_metadata') else {}
                influencer_leaderboard.append({
                    'channel':     ch_name,
                    'video_title': (p.content or '')[:80],
                    'views':       p.views_count,
                    'likes':       p.likes_count,
                    'sentiment':   p.sentiment_label,
                    'url':         meta.get('url', f"https://youtube.com/search?q={topic_query}"),
                })

            # Fill up with general influencers if needed
            top_influencers = SocialProfile.objects.filter(id__in=author_ids).order_by('-influence_score')[:6]

            # ── Risk & Tone Assessment ─────────────────────────────────────────
            risk_badge = "Low Risk / Favorable"
            risk_color = "var(--emerald)"
            anxiety_opp = emotion_counts.get('anxiety', 0) + emotion_counts.get('opposition', 0)
            if total_posts > 0:
                if (anxiety_opp / total_posts) > 0.35:
                    risk_badge = "Elevated Public Opposition & Backlash"
                    risk_color = "var(--rose)"
                elif (emotion_counts.get('sarcasm', 0) / total_posts) > 0.20:
                    risk_badge = "High Skepticism & Mockery Detected"
                    risk_color = "var(--amber)"
                elif avg_sentiment >= 0.15:
                    risk_badge = "Strong Audience Advocacy & Bullish"
                    risk_color = "var(--cyan)"

            # ── Dossier Fields ────────────────────────────────────────────────
            market_stance = (
                "Bullish & Optimistic" if avg_sentiment > 0.2 else
                "Cautiously Positive" if avg_sentiment > 0.05 else
                "Divided & Uncertain" if avg_sentiment > -0.1 else
                "Critical & Skeptical"
            )
            top_interest_qs = list(
                SocialProfile.objects.filter(id__in=author_ids)
                .values('primary_interest').annotate(count=Count('id')).order_by('-count')[:4]
            )
            top_interests = top_interest_qs
            primary_interest = top_interest_qs[0]['primary_interest'] if top_interest_qs else 'Technology'
            top_praise_driver = praised_quotes[0]['quote'][:100] if praised_quotes else f"Positive sentiment around {topic_query}"
            primary_friction = critical_quotes[0]['quote'][:100] if critical_quotes else f"Skepticism about {topic_query} implementation"

            # ── Auto-Generated Insights (5-8 natural language) ────────────────
            insights = [
                f"Analysis covers {total_posts:,} posts across {len(platform_counts)} platforms with {engagement_total:,} total engagement interactions.",
                f"Net sentiment score is {net_sentiment_score:+.3f} — audience mood is {market_stance.lower()}.",
                f"The dominant emotional tone is {dominant_emotion.capitalize()} ({dominant_emotion_pct}% share), concentrated on {top_platform}.",
                f"Favorable sentiment ratio stands at {round(favorable_ratio * 100, 1)}% positive vs {round((1 - favorable_ratio) * 100, 1)}% negative discourse.",
            ]
            if influencer_leaderboard:
                top_yt = influencer_leaderboard[0]
                insights.append(
                    f"Top YouTube influencer '{top_yt['channel']}' reached {top_yt['views']:,} views on '{top_yt['video_title'][:50]}...'."
                )
            if semantic_keywords:
                top_kws = ', '.join(k['keyword'] for k in semantic_keywords[:5])
                insights.append(f"Primary semantic cluster: {top_kws}.")
            if praised_quotes:
                insights.append(f"Community praise focuses on: \"{top_praise_driver}\"")
            if critical_quotes:
                insights.append(f"Primary friction point: \"{primary_friction}\"")

            # ── Demographic Aggregates ─────────────────────────────────────────
            age_cohorts = list(
                SocialProfile.objects.filter(id__in=author_ids)
                .values('estimated_age_group').annotate(count=Count('id')).order_by('-count')[:4]
            )
            top_countries = list(
                SocialProfile.objects.filter(id__in=author_ids)
                .values('estimated_country').annotate(count=Count('id')).order_by('-count')[:4]
            )

            # ── AI Narrative Synthesis ─────────────────────────────────────────
            sentiment_summary_text = (
                "predominantly positive with strong community excitement" if avg_sentiment > 0.15
                else "marked by skepticism and polarized debate" if avg_sentiment < -0.15
                else "balanced across objective insights and informational updates"
            )
            editorial_consensus = (
                f"Multi-platform editorial consensus signals {market_stance.lower()} sentiment toward {topic_query}. "
                f"Favorable ratio of {round(favorable_ratio * 100, 1)}% with net score {net_sentiment_score:+.3f}."
            )
            ai_synthesis = (
                f"Multi-platform audience intelligence for **{topic_query}** across **{total_posts:,}** verified records. "
                f"Sentiment index: **{avg_sentiment:+.2f}** — {sentiment_summary_text}. "
                f"Dominant emotion: **{dominant_emotion.capitalize()}** ({dominant_emotion_pct}% share). "
                f"Strongest discussion velocity on **{top_platform}**, with primary audience interest in *{primary_interest}*."
            )

            report_data = {
                # ── Capstone-compatible top-level fields ──
                'topic':              topic_query,
                'total_posts':        total_posts,
                'views_total':        total_views,
                'engagement_total':   engagement_total,
                'favorable_ratio':    favorable_ratio,
                'net_sentiment_score': net_sentiment_score,
                # sentiment object
                'sentiment': {
                    'positive': {'count': pos_count, 'percent': pos_pct},
                    'neutral':  {'count': neu_count, 'percent': neu_pct},
                    'negative': {'count': neg_count, 'percent': neg_pct},
                    'favorable_ratio':   favorable_ratio,
                    'net_sentiment_score': net_sentiment_score,
                },
                # flat duplicates for template convenience
                'avg_sentiment':      avg_sentiment,
                'sentiment_label':    'positive' if avg_sentiment >= 0.15 else ('negative' if avg_sentiment <= -0.15 else 'neutral'),
                'pos_count': pos_count, 'neu_count': neu_count, 'neg_count': neg_count,
                'pos_pct':   pos_pct,   'neu_pct':   neu_pct,   'neg_pct':   neg_pct,
                # emotions
                'emotions':            emotion_counts,
                'emotion_spectrum':    emotion_spectrum,
                'dominant_emotion':    dominant_emotion,
                'dominant_emotion_pct': dominant_emotion_pct,
                # platforms
                'platform_counts':    platform_counts,
                'platforms':          platforms_list,
                'top_platform':       top_platform,
                # topics
                'topics':             topics_list,
                # top posts
                'top_posts':          top_posts,
                'recent_posts':       matching_posts[:20],
                # dossier
                'dossier': {
                    'market_stance':      market_stance,
                    'editorial_consensus': editorial_consensus,
                    'top_praise_driver':  top_praise_driver,
                    'primary_friction':   primary_friction,
                    'emotion_spectrum':   emotion_spectrum,
                    'semantic_keywords':  semantic_keywords,
                    'praised_quotes':     praised_quotes,
                    'critical_quotes':    critical_quotes,
                    'influencer_leaderboard': influencer_leaderboard,
                    'views_total':        total_views,
                    'favorable_ratio':    favorable_ratio,
                },
                # flat dossier fields for template convenience
                'market_stance':      market_stance,
                'editorial_consensus': editorial_consensus,
                'praised_quotes':     praised_quotes,
                'critical_quotes':    critical_quotes,
                'semantic_keywords':  semantic_keywords,
                'influencer_leaderboard': influencer_leaderboard,
                'insights':           insights,
                # risk
                'risk_badge':         risk_badge,
                'risk_color':         risk_color,
                # demographics
                'top_influencers':    top_influencers,
                'age_cohorts':        age_cohorts,
                'top_countries':      top_countries,
                'top_interests':      top_interests,
                # totals
                'total_likes':        total_likes,
                'total_reposts':      total_reposts,
                'total_comments':     total_comments,
                'total_views':        total_views,
                # narrative
                'ai_synthesis':       ai_synthesis,
            }

    # Sample trending topics for landing page
    sample_topics = list(Topic.objects.order_by('-velocity_score').values('title', 'velocity_score', 'total_volume', 'dominant_emotion')[:6])
    if not sample_topics:
        sample_topics = [
            {'title': 'AI Agents',      'velocity_score': 3.4, 'total_volume': 48, 'dominant_emotion': 'excitement'},
            {'title': 'Semiconductors', 'velocity_score': 2.9, 'total_volume': 36, 'dominant_emotion': 'curiosity'},
            {'title': 'Climate Tech',   'velocity_score': 2.1, 'total_volume': 29, 'dominant_emotion': 'support'},
            {'title': 'Cybersecurity',  'velocity_score': 1.8, 'total_volume': 22, 'dominant_emotion': 'anxiety'},
        ]

    context = {
        'page_title':    f"{topic_query} — Social Media Analytics Report" if topic_query else "Topic Social Media Intelligence",
        'active_tab':    'home',
        'topic_query':   topic_query,
        'report':        report_data,
        'sample_topics': sample_topics,
        'total_db_posts': SocialPost.objects.count(),
    }
    return render(request, 'home.html', context)


def dashboard_view(request):
    """
    Main Executive Intelligence Dashboard.
    Presents high-level metrics, multi-platform timeline, emerging topics,
    sentiment breakdown, and executive AI synthesis.
    """
    posts = SocialPost.objects.all().select_related('author', 'topic')
    profiles = SocialProfile.objects.all()
    topics = Topic.objects.all().order_by('-velocity_score')
    alerts = TrendAlert.objects.filter(is_read=False).order_by('-triggered_at')[:5]
    latest_brief = ExecutiveBrief.objects.first()

    total_posts = posts.count()
    total_profiles = profiles.count()
    avg_sent = posts.aggregate(avg=Avg('sentiment_score'))['avg'] or 0.0
    active_communities = profiles.values('community_id').distinct().count()
    surging_topics = topics.filter(momentum_trend='surging').count()
    
    # Emotion Breakdown
    emotion_counts = {}
    for emo in ['support', 'opposition', 'excitement', 'anxiety', 'sarcasm', 'curiosity', 'neutral']:
        emotion_counts[emo] = posts.filter(emotion_label=emo).count()
        
    # Top Influencers
    top_influencers = profiles.order_by('-influence_score')[:5]
    
    # Recent high-impact posts
    recent_posts = posts.order_by('-created_at')[:8]

    context = {
        'page_title': 'Executive Intelligence Dashboard',
        'active_tab': 'dashboard',
        'total_posts': total_posts,
        'total_profiles': total_profiles,
        'avg_sentiment': round(avg_sent, 2),
        'active_communities': active_communities,
        'surging_topics': surging_topics,
        'alerts': alerts,
        'topics': topics[:6],
        'top_influencers': top_influencers,
        'recent_posts': recent_posts,
        'emotion_counts': emotion_counts,
        'latest_brief': latest_brief,
        'platforms': Platform.choices,
    }
    return render(request, 'dashboard.html', context)


def sentiment_view(request):
    """
    Sentiment & Audience Intelligence View.
    Multi-dimensional emotion patterns, sarcasm/opposition analysis,
    and aggregated anonymized demographic signals (age, country, interests, language).
    """
    posts = SocialPost.objects.all()
    profiles = SocialProfile.objects.all()

    # Emotion distributions
    emotions = ['support', 'opposition', 'excitement', 'anxiety', 'sarcasm', 'curiosity', 'neutral']
    emotion_data = {e: posts.filter(emotion_label=e).count() for e in emotions}
    
    # Demographic Aggregations
    age_cohorts = list(profiles.values('estimated_age_group').annotate(count=Count('id')).order_by('-count'))
    countries = list(profiles.values('estimated_country').annotate(count=Count('id')).order_by('-count')[:8])
    interests = list(profiles.values('primary_interest').annotate(count=Count('id')).order_by('-count'))
    languages = list(profiles.values('detected_language').annotate(count=Count('id')).order_by('-count'))
    
    # Cross-segment analysis: Sentiment by Interest
    interest_sentiment = []
    for item in interests:
        int_name = item['primary_interest']
        s_avg = SocialPost.objects.filter(author__primary_interest=int_name).aggregate(avg=Avg('sentiment_score'))['avg'] or 0.0
        interest_sentiment.append({
            'interest': int_name,
            'count': item['count'],
            'avg_sentiment': round(s_avg, 2)
        })

    # High Sarcasm & High Opposition samples
    sarcasm_posts = posts.filter(emotion_label='sarcasm').order_by('-likes_count')[:4]
    opposition_posts = posts.filter(emotion_label='opposition').order_by('-likes_count')[:4]

    context = {
        'page_title': 'Sentiment & Audience Intelligence',
        'active_tab': 'sentiment',
        'emotion_data': emotion_data,
        'age_cohorts': age_cohorts,
        'countries': countries,
        'interests': interests,
        'languages': languages,
        'interest_sentiment': interest_sentiment,
        'sarcasm_posts': sarcasm_posts,
        'opposition_posts': opposition_posts,
    }
    return render(request, 'sentiment.html', context)


def trends_view(request):
    """
    Emerging Trend & Topic Detection View.
    Distinguishes emerging breakout trends from persistent discussions,
    displays trend velocity scores, TF-IDF keywords, and automated alerts.
    """
    topics = Topic.objects.all().order_by('-velocity_score')
    alerts = TrendAlert.objects.all().order_by('-triggered_at')[:15]
    
    # Extract emerging n-grams & keywords across recent posts
    emerging_keywords = extract_emerging_keywords(SocialPost.objects.all(), top_n=16)

    context = {
        'page_title': 'Emerging Trend & Topic Detection',
        'active_tab': 'trends',
        'topics': topics,
        'alerts': alerts,
        'emerging_keywords': emerging_keywords,
    }
    return render(request, 'trends.html', context)


def network_view(request):
    """
    Social Network & Influence Analysis View.
    Interactive graph visualization (Vis-Network), community clustering,
    centrality leaderboards, and propagation cascades.
    """
    profiles = SocialProfile.objects.all().order_by('-influence_score')
    communities = list(profiles.values_list('community_id', flat=True).distinct().order_by('community_id'))
    
    # Top viral posts eligible for cascade tracing
    viral_posts = SocialPost.objects.filter(
        reposts_count__gte=10
    ).select_related('author', 'topic').order_by('-reposts_count')[:10]

    context = {
        'page_title': 'Social Network & Influence Analysis',
        'active_tab': 'network',
        'profiles': profiles[:25],
        'communities': communities,
        'viral_posts': viral_posts,
    }
    return render(request, 'network.html', context)


def stream_view(request):
    """
    Real-Time Data Pipeline & Live Stream Monitor.
    Displays continuous multi-platform ingestion, live stream simulator,
    and manual post submission sandbox.
    """
    posts = SocialPost.objects.all().select_related('author', 'topic').order_by('-created_at')[:40]
    platforms = Platform.choices
    topics = Topic.objects.all()
    connectors = get_api_connectors_status()

    context = {
        'page_title': 'Real-Time Data Pipeline & Feed',
        'active_tab': 'stream',
        'posts': posts,
        'platforms': platforms,
        'topics': topics,
        'connectors': connectors,
    }
    return render(request, 'stream.html', context)


# ==================== REST / JSON API ENDPOINTS ====================

def api_network_data(request):
    """Returns nodes and edges JSON for Vis-Network graph."""
    community_param = request.GET.get('community')
    community_id = int(community_param) if (community_param is not None and community_param != 'all') else None
    limit = int(request.GET.get('limit', 80))
    
    data = get_network_graph_data(limit_nodes=limit, community_filter=community_id)
    return JsonResponse(data)


def api_cascade_data(request, post_id):
    """Returns cascade propagation tree for a given post."""
    data = get_propagation_cascade(post_id)
    return JsonResponse(data)


def api_chart_data(request):
    """Returns aggregated chart metrics for timelines and distributions."""
    now = timezone.now()
    # 24-hour time series buckets
    timeline_labels = []
    volume_by_platform = {p[0]: [] for p in Platform.choices}
    sentiment_timeline = []

    for h in reversed(range(12)):
        start = now - timedelta(hours=(h + 1) * 2)
        end = now - timedelta(hours=h * 2)
        label = end.strftime('%H:%M')
        timeline_labels.append(label)
        
        window_posts = SocialPost.objects.filter(created_at__gte=start, created_at__lt=end)
        s_avg = window_posts.aggregate(avg=Avg('sentiment_score'))['avg'] or 0.0
        sentiment_timeline.append(round(s_avg, 2))
        
        for p_code, _ in Platform.choices:
            cnt = window_posts.filter(platform=p_code).count()
            volume_by_platform[p_code].append(cnt)

    return JsonResponse({
        'timeline_labels': timeline_labels,
        'volume_by_platform': volume_by_platform,
        'sentiment_timeline': sentiment_timeline
    })


@csrf_exempt
def api_simulate_surge(request):
    """Triggers an instantaneous live surge to demonstrate real-time pipeline."""
    if request.method == 'POST':
        result = simulate_live_stream_surge()
        return JsonResponse(result)
    return JsonResponse({'error': 'POST required'}, status=405)


@csrf_exempt
def api_ingest_post(request):
    """Accepts manual or external crawler payload to ingest live post."""
    if request.method == 'POST':
        try:
            body = json.loads(request.body.decode('utf-8')) if request.body else request.POST
            platform = body.get('platform', 'twitter')
            username = body.get('username', 'citizen_analyst')
            display_name = body.get('display_name', username)
            bio = body.get('bio', 'Social analyst and community member')
            content = body.get('content', '')
            topic_title = body.get('topic_title', None)
            
            if not content.strip():
                return JsonResponse({'error': 'Content cannot be empty'}, status=400)
                
            post = ingest_raw_post(
                platform=platform,
                external_id=f"manual_{timezone.now().timestamp()}",
                username=username,
                display_name=display_name,
                bio=bio,
                content=content,
                topic_title=topic_title
            )
            
            compute_network_metrics()
            update_trend_velocities()
            
            return JsonResponse({
                'status': 'success',
                'post_id': post.id,
                'sentiment': post.sentiment_label,
                'sentiment_score': post.sentiment_score,
                'emotion': post.emotion_label,
                'emotion_scores': post.emotion_scores
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'POST required'}, status=405)


@csrf_exempt
def api_generate_brief(request):
    """Generates an on-demand AI Executive Intelligence Brief."""
    if request.method == 'POST':
        now = timezone.now()
        posts = SocialPost.objects.all()
        topics = Topic.objects.order_by('-velocity_score')
        top_topic = topics.first()
        top_topic_title = top_topic.title if top_topic else "Technology Governance"
        
        brief = ExecutiveBrief.objects.create(
            title=f"SMA.AI Executive Intelligence Brief ({now.strftime('%b %d, %H:%M UTC')})",
            timeframe="Real-time Snapshot",
            summary_markdown=f"""
### Automated Real-Time Audience Intelligence Synthesis
Generated at **{now.strftime('%Y-%m-%d %H:%M:%S UTC')}** across multi-platform feeds.

- **Primary Narrative Anchor**: **{top_topic_title}** dominates community bandwidth with elevated velocity ({top_topic.velocity_score if top_topic else 2.5}x baseline).
- **Public Emotion Spectrum**: Sentiment currently registers at an aggregate balance, with emerging spikes in **support** and **opposition** indicating active polarization in technical discussions.
- **Network Dynamic**: Core information cascades are mediated by top authority nodes in Community #{top_topic.id if top_topic else 0}.
            """,
            key_findings=[
                {"headline": "Velocity Leadership", "desc": f"Highest engagement acceleration currently tracked in '{top_topic_title}'."},
                {"headline": "Information Flow Velocity", "desc": f"Active network graph comprises {SocialProfile.objects.count()} nodes across {Interaction.objects.count()} recorded directional interactions."},
                {"headline": "Risk Surveillance", "desc": f"{TrendAlert.objects.filter(is_read=False).count()} active alerts require operational monitoring."}
            ],
            metrics_snapshot={
                "total_posts": posts.count(),
                "tracked_profiles": SocialProfile.objects.count(),
                "active_alerts": TrendAlert.objects.filter(is_read=False).count()
            }
        )
        return JsonResponse({
            'status': 'success',
            'brief_id': brief.id,
            'title': brief.title,
            'summary': brief.summary_markdown,
            'generated_at': brief.generated_at.strftime('%Y-%m-%d %H:%M')
        })
    return JsonResponse({'error': 'POST required'}, status=405)


def api_collector_status(request):
    """Returns status and configuration readiness for all social media APIs."""
    status_map = get_api_connectors_status()
    return JsonResponse({'status': 'success', 'connectors': status_map})


@csrf_exempt
def api_fetch_live_social(request):
    """
    Fetches real-world posts from Twitter/X, Reddit, YouTube, Telegram,
    Hacker News, and Mastodon in real time.
    """
    if request.method == 'POST':
        try:
            body = json.loads(request.body.decode('utf-8')) if request.body else request.POST
            keyword = body.get('keyword', 'AI')
            sources = body.get('sources', ['twitter', 'reddit', 'youtube', 'telegram', 'hackernews', 'mastodon'])
            limit = int(body.get('limit', 10))
            credentials = body.get('credentials', {})
            clear_old = body.get('clear_old', False)
            
            if clear_old:
                # Remove mock posts if requested
                SocialPost.objects.filter(
                    Q(external_id__startswith='seed_') |
                    Q(external_id__startswith='hist_') |
                    Q(external_id__startswith='live_')
                ).delete()
                SocialProfile.objects.filter(posts__isnull=True).delete()

            res = run_live_multiplatform_sync(
                sources=sources,
                keyword=keyword,
                limit_per_source=limit,
                credentials=credentials,
                clear_old=clear_old
            )
            return JsonResponse({'status': 'success', 'data': res})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'POST required'}, status=405)


@csrf_exempt
def api_save_api_keys(request):
    """
    Saves API keys to .env file and updates runtime environment.
    """
    if request.method == 'POST':
        try:
            body = json.loads(request.body.decode('utf-8')) if request.body else request.POST
            base_dir = settings.BASE_DIR
            env_path = os.path.join(base_dir, '.env')
            
            existing = {}
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            parts = line.split('=', 1)
                            existing[parts[0].strip()] = parts[1].strip()
            
            key_map = {
                'twitter_bearer_token': 'TWITTER_BEARER_TOKEN',
                'youtube_api_key':      'YOUTUBE_API_KEY',
                'reddit_client_id':     'REDDIT_CLIENT_ID',
                'reddit_client_secret': 'REDDIT_CLIENT_SECRET',
                'telegram_bot_token':   'TELEGRAM_BOT_TOKEN',
                'news_api_key':         'NEWS_API_KEY',
                'openai_api_key':       'OPENAI_API_KEY',
            }
            
            for param, env_var in key_map.items():
                if param in body:
                    val = body[param].strip()
                    if val:
                        existing[env_var] = val
                        os.environ[env_var] = val
                    elif env_var in existing and val == '':
                        del existing[env_var]
                        os.environ.pop(env_var, None)
            
            with open(env_path, 'w', encoding='utf-8') as f:
                f.write("# Social Media Analytics (SMA.AI) Environment Config\n")
                for k, v in existing.items():
                    f.write(f"{k}={v}\n")
                    
            from dotenv import load_dotenv
            load_dotenv(env_path, override=True)
            
            return JsonResponse({
                'status': 'success',
                'connectors': get_api_connectors_status(),
                'message': 'API keys saved and activated successfully.'
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'POST required'}, status=405)


@csrf_exempt
def api_clear_mock_data(request):
    """Clears all synthetic/mock posts, leaving only real social media data."""
    if request.method == 'POST':
        mock_posts = SocialPost.objects.filter(
            Q(external_id__startswith='seed_') |
            Q(external_id__startswith='hist_') |
            Q(external_id__startswith='live_')
        )
        count = mock_posts.count()
        mock_posts.delete()
        
        # Clean up orphaned profiles without posts
        SocialProfile.objects.annotate(pc=Count('posts')).filter(pc=0).delete()
        
        compute_network_metrics()
        update_trend_velocities()
        return JsonResponse({'status': 'success', 'cleared_count': count})
    return JsonResponse({'error': 'POST required'}, status=405)
