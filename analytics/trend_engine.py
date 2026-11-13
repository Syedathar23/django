import math
from datetime import timedelta
from typing import Dict, List, Any
from django.utils import timezone
from django.db.models import Avg, Count, Sum
from sklearn.feature_extraction.text import TfidfVectorizer

from analytics.models import SocialPost, Topic, TrendAlert


def extract_emerging_keywords(posts_qs, top_n: int = 15) -> List[Dict[str, Any]]:
    """
    Extracts high-velocity n-grams and keywords using TF-IDF.
    """
    texts = [p.content for p in posts_qs if len(p.content) > 10]
    if not texts or len(texts) < 3:
        return []

    try:
        vectorizer = TfidfVectorizer(
            stop_words='english',
            ngram_range=(1, 2),
            max_df=0.85,
            min_df=2,
            max_features=top_n * 2
        )
        tfidf_matrix = vectorizer.fit_transform(texts)
        feature_names = vectorizer.get_feature_names_out()
        sums = tfidf_matrix.sum(axis=0)
        
        data = []
        for col, term in enumerate(feature_names):
            score = float(sums[0, col])
            data.append({'term': term, 'score': round(score, 2)})
            
        data.sort(key=lambda x: x['score'], reverse=True)
        return data[:top_n]
    except Exception:
        # Fallback if scikit-learn runs into trivial vocabulary
        word_freq = {}
        for t in texts:
            for w in t.lower().split():
                clean = ''.join(c for c in w if c.isalnum())
                if len(clean) > 4:
                    word_freq[clean] = word_freq.get(clean, 0) + 1
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [{'term': k, 'score': v} for k, v in sorted_words]


def update_trend_velocities() -> Dict[str, Any]:
    """
    Calculates velocity for all topics, detects sudden breakouts and sentiment polarization,
    and creates real-time Trend Alerts.
    """
    now = timezone.now()
    recent_threshold = now - timedelta(hours=6)
    historical_threshold = now - timedelta(days=3)
    
    topics = Topic.objects.all()
    updated_count = 0
    alerts_created = 0
    
    for t in topics:
        posts = SocialPost.objects.filter(topic=t)
        total_vol = posts.count()
        if total_vol == 0:
            continue
            
        recent_posts = posts.filter(created_at__gte=recent_threshold)
        recent_vol = recent_posts.count()
        
        # Interactions sum in recent window
        recent_metrics = recent_posts.aggregate(
            total_likes=Sum('likes_count'),
            total_shares=Sum('reposts_count')
        )
        recent_eng = (recent_metrics['total_likes'] or 0) + (recent_metrics['total_shares'] or 0) * 2
        
        # Historical baseline
        hist_posts = posts.filter(created_at__lt=recent_threshold, created_at__gte=historical_threshold)
        hist_vol = hist_posts.count()
        
        # Velocity ratio
        baseline_rate = max(1.0, hist_vol / 12.0) # posts per 6 hours average
        velocity = (recent_vol / baseline_rate) * math.log(1.0 + recent_eng + 1.0)
        velocity = round(max(0.5, velocity), 2)
        
        # Sentiment & Emotion stats
        avg_sent = posts.aggregate(avg=Avg('sentiment_score'))['avg'] or 0.0
        
        emotion_counts = {}
        for p in posts.values('emotion_label').annotate(c=Count('id')):
            emotion_counts[p['emotion_label']] = p['c']
        dominant_emo = max(emotion_counts, key=emotion_counts.get) if emotion_counts else 'neutral'
        
        # Momentum tag
        if velocity >= 3.5:
            momentum = 'surging'
            is_emerging = True
        elif velocity >= 1.5:
            momentum = 'steady'
            is_emerging = False
        else:
            momentum = 'declining'
            is_emerging = False
            
        t.velocity_score = velocity
        t.momentum_trend = momentum
        t.is_emerging = is_emerging
        t.total_volume = total_vol
        t.avg_sentiment = round(avg_sent, 2)
        t.dominant_emotion = dominant_emo
        t.last_active_at = now
        t.save()
        updated_count += 1
        
        # Evaluate for Automated Trend Alerts
        # 1. Viral Spike
        if velocity >= 4.0:
            existing = TrendAlert.objects.filter(
                topic=t,
                alert_type='VIRAL_SPIKE',
                triggered_at__gte=now - timedelta(hours=12)
            ).exists()
            if not existing:
                TrendAlert.objects.create(
                    topic=t,
                    alert_type='VIRAL_SPIKE',
                    severity='critical' if velocity > 6.0 else 'warning',
                    title=f"Viral Velocity Surge: {t.title}",
                    message=f"Topic '{t.title}' is experiencing abnormal velocity ({velocity}x baseline) with {recent_vol} new posts in the last 6 hours.",
                    metric_value=f"{velocity}x Momentum"
                )
                alerts_created += 1

        # 2. High Opposition Backlash
        opp_count = emotion_counts.get('opposition', 0)
        opp_pct = (opp_count / total_vol) if total_vol > 0 else 0
        if opp_pct >= 0.35 and total_vol >= 10:
            existing = TrendAlert.objects.filter(
                topic=t,
                alert_type='HIGH_OPPOSITION',
                triggered_at__gte=now - timedelta(hours=12)
            ).exists()
            if not existing:
                TrendAlert.objects.create(
                    topic=t,
                    alert_type='HIGH_OPPOSITION',
                    severity='critical',
                    title=f"Escalating Opposition: {t.title}",
                    message=f"High community resistance detected: {round(opp_pct * 100, 1)}% of conversations express direct opposition or boycott calls.",
                    metric_value=f"{round(opp_pct * 100)}% Opposition"
                )
                alerts_created += 1

        # 3. Sentiment Polarization Shift
        if avg_sent < -0.3 and total_vol >= 8:
            existing = TrendAlert.objects.filter(
                topic=t,
                alert_type='SENTIMENT_SHIFT',
                triggered_at__gte=now - timedelta(hours=12)
            ).exists()
            if not existing:
                TrendAlert.objects.create(
                    topic=t,
                    alert_type='SENTIMENT_SHIFT',
                    severity='warning',
                    title=f"Negative Sentiment Shift: {t.title}",
                    message=f"Sentiment for '{t.title}' shifted sharply negative to an average score of {round(avg_sent, 2)}.",
                    metric_value=f"Score: {round(avg_sent, 2)}"
                )
                alerts_created += 1

    return {
        'updated_topics': updated_count,
        'alerts_triggered': alerts_created
    }
