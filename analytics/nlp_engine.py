import re
import math
from typing import Dict, Any, List, Tuple

# Multi-dimensional Sentiment Lexicons & Markers
POSITIVE_WORDS = {
    'innovative', 'breakthrough', 'stellar', 'incredible', 'promising', 'efficient', 'excited', 'phenomenal',
    'masterpiece', 'revolutionary', 'empowering', 'thriving', 'brilliant', 'delighted', 'triumph', 'love',
    'fantastic', 'excellent', 'superb', 'transformative', 'impressive', 'pioneering', 'leader', 'visionary',
    'valuable', 'seamless', 'gamechanger', 'game-changer', 'optimistic', 'inspiring', 'proud', 'awesome'
}

NEGATIVE_WORDS = {
    'concerning', 'terrible', 'disaster', 'reckless', 'dystopian', 'catastrophic', 'threat', 'alarming',
    'flawed', 'horrible', 'crisis', 'breach', 'vulnerability', 'scam', 'fraud', 'dangerous', 'nightmare',
    'boycott', 'backlash', 'broken', 'disappointing', 'worst', 'failure', 'collapse', 'toxic', 'unacceptable',
    'exploitation', 'harmful', 'unethical', 'chaos', 'ruined', 'deceptive'
}

EMOTION_PATTERNS = {
    'sarcasm': [
        r'\boh yeah\b', r'\byeah right\b', r'\bas if\b', r'\bwhat could possibly go wrong\b',
        r'\bsurely nothing could go wrong\b', r'\bgroundbreaking stuff\b', r'\btotally not a bubble\b',
        r'\btrust the process\b.*🙄', r'\boh wonderful\b', r'\bclown world\b', r'🙄', r'😏', r'🙃'
    ],
    'excitement': [
        r'\bcan\'t wait\b', r'\bmind-blowing\b', r'\bmind blowing\b', r'\bhuge milestone\b',
        r'\bunbelievable\b', r'\bnext level\b', r'\bgame changer\b', r'\blet\'s go\b', r'\bfire\b',
        r'🔥', r'🚀', r'🎉', r'⚡', r'🤯'
    ],
    'anxiety': [
        r'\bworried about\b', r'\bexistential risk\b', r'\bwhat happens if\b', r'\bterrifying\b',
        r'\bpanicking\b', r'\buncontrollable\b', r'\bout of control\b', r'\bjob loss\b', r'\blosing sleep\b',
        r'⚠️', r'😰', r'😱', r'🚨'
    ],
    'opposition': [
        r'\bwe must ban\b', r'\bboycott\b', r'\bshut it down\b', r'\bunacceptable\b', r'\bstop this\b',
        r'\breckless move\b', r'\bcall out\b', r'\bagainst this\b', r'\bhard pass\b', r'\bprotest\b',
        r'🚫', r'❌', r'👎'
    ],
    'support': [
        r'\bfully support\b', r'\bstand behind\b', r'\bhuge fan\b', r'\bkudos to\b', r'\bcongratulations\b',
        r'\bwell deserved\b', r'\bincredible team\b', r'\bbacked by\b', r'\bstrongly endorse\b',
        r'👏', r'💪', r'❤️', r'🙌'
    ],
    'curiosity': [
        r'\bhow does this work\b', r'\bwhat are the implications\b', r'\bcurious to see\b',
        r'\banyone tested\b', r'\bthoughts on\b', r'\bcan someone explain\b', r'\binteresting question\b',
        r'🤔', r'❓'
    ]
}

COUNTRIES_KEYWORDS = {
    'United States': ['usa', 'us', 'san francisco', 'new york', 'silicon valley', 'austin', 'seattle', 'america'],
    'United Kingdom': ['uk', 'london', 'cambridge', 'oxford', 'england', 'britain'],
    'Germany': ['germany', 'berlin', 'munich', 'frankfurt', 'deutschland'],
    'India': ['india', 'bangalore', 'bengaluru', 'delhi', 'mumbai', 'hyderabad'],
    'Japan': ['japan', 'tokyo', 'osaka', 'kyoto'],
    'France': ['france', 'paris', 'lyon', 'french'],
    'Canada': ['canada', 'toronto', 'vancouver', 'montreal', 'waterloo'],
    'Australia': ['australia', 'sydney', 'melbourne', 'canberra'],
    'Singapore': ['singapore', 'sg'],
    'United Arab Emirates': ['uae', 'dubai', 'abu dhabi']
}

AGE_INDICATORS = {
    '18-24': ['student', 'uni', 'college', 'intern', 'undergrad', 'gen-z', 'gamer', 'learning to code', 'junior'],
    '25-34': ['engineer', 'founder', 'dev', 'builder', 'tech lead', 'product manager', 'crypto', 'growth', 'consultant'],
    '35-49': ['director', 'vp', 'investor', 'architect', 'principal', 'head of', 'partner', 'parent', 'seasoned'],
    '50+': ['professor', 'emeritus', 'senior advisor', 'veteran', 'board member', 'executive chairman', 'fellow']
}

INTEREST_KEYWORDS = {
    'Tech & AI': ['ai', 'llm', 'machine learning', 'neural', 'software', 'python', 'cloud', 'data', 'algorithm', 'gpu', 'deep learning'],
    'Finance & Crypto': ['crypto', 'bitcoin', 'eth', 'market', 'stock', 'investing', 'venture', 'macro', 'trading', 'defi', 'capital'],
    'Policy & Law': ['policy', 'regulation', 'antitrust', 'act', 'compliance', 'senate', 'commission', 'eu', 'treaty', 'rights'],
    'Media & Journalism': ['journalist', 'reporter', 'editor', 'press', 'coverage', 'podcast', 'creator', 'media', 'news'],
    'Science & Climate': ['climate', 'energy', 'battery', 'fusion', 'physics', 'quantum', 'space', 'biology', 'materials'],
    'Gaming & Lifestyle': ['gaming', 'esports', 'twitch', 'streamer', 'travel', 'design', 'ux', 'lifestyle']
}


def clean_text(text: str) -> str:
    """Normalize text while preserving emoji and hashtag cues."""
    return re.sub(r'\s+', ' ', text).strip()


def extract_hashtags_and_mentions(text: str) -> Tuple[List[str], List[str]]:
    """Extract hashtags and mentions from text."""
    hashtags = re.findall(r'#(\w+)', text)
    mentions = re.findall(r'@(\w+)', text)
    return hashtags, mentions


def analyze_sentiment(text: str) -> Tuple[str, float]:
    """
    Perform multi-dimensional sentiment scoring.
    Returns: (label: 'positive' | 'neutral' | 'negative', score: float between -1.0 and 1.0)
    """
    lower = text.lower()
    words = re.findall(r'\b\w+\b', lower)
    
    pos_count = sum(1 for w in words if w in POSITIVE_WORDS)
    neg_count = sum(1 for w in words if w in NEGATIVE_WORDS)
    
    # Exclamation & emoji amplification
    if any(e in text for e in ['🔥', '🚀', '🎉', '👏', '❤️', '🙌']):
        pos_count += 1.5
    if any(e in text for e in ['⚠️', '😰', '😱', '🚨', '🚫', '❌', '👎']):
        neg_count += 1.5
    
    # Sarcasm inversion heuristic
    for pat in EMOTION_PATTERNS['sarcasm']:
        if re.search(pat, lower):
            neg_count += 1.5
            pos_count = max(0, pos_count - 1.0)
            break
            
    total = pos_count + neg_count
    if total == 0:
        return 'neutral', 0.05
    
    score = (pos_count - neg_count) / total
    score = round(max(-1.0, min(1.0, score)), 2)
    
    if score >= 0.2:
        return 'positive', score
    elif score <= -0.2:
        return 'negative', score
    else:
        return 'neutral', score


def analyze_emotions(text: str) -> Tuple[str, Dict[str, float]]:
    """
    Detect multi-dimensional emotional nuances:
    Support, Opposition, Excitement, Anxiety, Sarcasm, Curiosity, Neutral.
    """
    lower = text.lower()
    scores = {
        'support': 0.1,
        'opposition': 0.1,
        'excitement': 0.1,
        'anxiety': 0.1,
        'sarcasm': 0.05,
        'curiosity': 0.1,
        'neutral': 0.2
    }
    
    # Match patterns
    for emotion, patterns in EMOTION_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, lower):
                scores[emotion] += 1.5
                scores['neutral'] = max(0.05, scores['neutral'] - 0.2)
                
    # Normalize scores to probabilities summing to 1.0
    total = sum(scores.values())
    normalized = {k: round(v / total, 3) for k, v in scores.items()}
    
    dominant_emotion = max(normalized, key=normalized.get)
    return dominant_emotion, normalized


def infer_demographics(bio: str, content: str, username: str) -> Dict[str, Any]:
    """
    Automated, aggregate, and anonymized audience demographic analysis.
    Infers broad cohort indicators (age group, country, interest, language).
    No sensitive personal data is extracted or retained.
    """
    full_text = f"{bio} {content} {username}".lower()
    
    # Geographic Signal
    inferred_country = "Global / International"
    for country, keywords in COUNTRIES_KEYWORDS.items():
        if any(kw in full_text for kw in keywords):
            inferred_country = country
            break
            
    # Age Cohort
    inferred_age = "25-34"
    max_age_matches = 0
    for cohort, indicators in AGE_INDICATORS.items():
        matches = sum(1 for ind in indicators if ind in full_text)
        if matches > max_age_matches:
            max_age_matches = matches
            inferred_age = cohort
            
    # Primary Interest
    inferred_interest = "Tech & AI"
    max_interest_matches = 0
    for interest, keywords in INTEREST_KEYWORDS.items():
        matches = sum(1 for kw in keywords if kw in full_text)
        if matches > max_interest_matches:
            max_interest_matches = matches
            inferred_interest = interest
            
    # Language detection (heuristic fallback)
    detected_lang = 'en'
    if re.search(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]', full_text):
        detected_lang = 'ja'
    elif re.search(r'[\u0900-\u097F]', full_text):
        detected_lang = 'hi'
    elif any(w in full_text.split() for w in ['el', 'la', 'para', 'con', 'por', 'que', 'en']):
        detected_lang = 'es'
    elif any(w in full_text.split() for w in ['der', 'die', 'das', 'und', 'mit', 'nicht']):
        detected_lang = 'de'
    elif any(w in full_text.split() for w in ['le', 'la', 'les', 'avec', 'pour', 'dans']):
        detected_lang = 'fr'

    return {
        'country': inferred_country,
        'age_group': inferred_age,
        'primary_interest': inferred_interest,
        'language': detected_lang
    }
