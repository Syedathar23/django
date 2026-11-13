from typing import Dict, Any, List
import random
import uuid
from datetime import timedelta
from django.utils import timezone
from django.utils.text import slugify
from django.db.models import Q

from analytics.models import SocialProfile, SocialPost, Interaction, Topic, Platform, TrendAlert, ExecutiveBrief
from analytics.nlp_engine import analyze_sentiment, analyze_emotions, infer_demographics, extract_hashtags_and_mentions
from analytics.graph_engine import compute_network_metrics
from analytics.trend_engine import update_trend_velocities


def ingest_raw_post(
    platform: str,
    external_id: str,
    username: str,
    display_name: str,
    bio: str,
    content: str,
    post_type: str = 'post',
    parent_external_id: str = None,
    created_at = None,
    metrics: dict = None,
    topic_title: str = None,
    avatar_url: str = None,
    raw_metadata: dict = None
) -> SocialPost:
    """
    Core normalization and ingestion pipeline.
    Transforms raw multi-platform payload into structured, timestamped database records,
    executes NLP sentiment & emotion detection, and extracts interaction graph edges.
    """
    if not created_at:
        created_at = timezone.now()
    if metrics is None:
        metrics = {
            'likes': random.randint(5, 120),
            'reposts': random.randint(1, 40),
            'comments': random.randint(0, 15),
            'views': random.randint(100, 3500)
        }
        
    # 1. Infer demographic heuristics
    demo = infer_demographics(bio=bio, content=content, username=username)
    
    # 2. Get or create SocialProfile
    profile, created = SocialProfile.objects.get_or_create(
        platform=platform,
        platform_user_id=f"{platform}_{username.lower()}",
        defaults={
            'username': username,
            'display_name': display_name or username,
            'bio': bio,
            'followers_count': random.randint(500, 45000),
            'following_count': random.randint(100, 2000),
            'estimated_age_group': demo['age_group'],
            'estimated_country': demo['country'],
            'primary_interest': demo['primary_interest'],
            'detected_language': demo['language'],
            'avatar_url': avatar_url or f"https://api.dicebear.com/7.x/identicon/svg?seed={username}"
        }
    )
    if not created and bio and not profile.bio:
        profile.bio = bio
        profile.save()
        
    # 3. NLP Multi-dimensional Sentiment & Emotion Analysis
    sent_label, sent_score = analyze_sentiment(content)
    emo_label, emo_scores = analyze_emotions(content)
    hashtags, mentions = extract_hashtags_and_mentions(content)
    
    # 4. Resolve Topic
    topic_obj = None
    if topic_title:
        base_slug = slugify(topic_title)[:180] or "topic"
        topic_obj = Topic.objects.filter(Q(title__iexact=topic_title) | Q(slug=base_slug)).first()
        if not topic_obj:
            slug = base_slug
            counter = 1
            while Topic.objects.filter(slug=slug).exists():
                slug = f"{base_slug[:170]}-{counter}"
                counter += 1
            try:
                topic_obj = Topic.objects.create(
                    title=topic_title[:200],
                    slug=slug,
                    category=demo.get('primary_interest', 'General'),
                    description=f"Discussions regarding {topic_title}",
                    keywords=hashtags or [topic_title.lower()],
                    first_detected_at=created_at
                )
            except Exception:
                topic_obj = Topic.objects.filter(Q(title__iexact=topic_title) | Q(slug=slug)).first()
        
    # 5. Resolve Parent Post if threaded
    parent_post = None
    if parent_external_id:
        parent_post = SocialPost.objects.filter(external_id=parent_external_id).first()
        
    # 6. Create or update SocialPost
    post_defaults = {
        'platform': platform,
        'author': profile,
        'content': content,
        'created_at': created_at,
        'post_type': post_type,
        'parent_post': parent_post,
        'likes_count': metrics.get('likes', 0),
        'reposts_count': metrics.get('reposts', 0),
        'comments_count': metrics.get('comments', 0),
        'views_count': metrics.get('views', 0),
        'topic': topic_obj,
        'sentiment_label': sent_label,
        'sentiment_score': sent_score,
        'emotion_label': emo_label,
        'emotion_scores': emo_scores,
        'language': demo['language'],
        'hashtags': hashtags,
        'entities': mentions,
        'raw_metadata': raw_metadata or {}
    }
    ext_id = external_id or str(uuid.uuid4())
    post, post_created = SocialPost.objects.update_or_create(
        external_id=ext_id,
        defaults=post_defaults
    )
    
    if post_created:
        profile.posts_count += 1
        profile.save(update_fields=['posts_count'])
    
    # 7. Record Graph Interactions
    # From mentions
    for m in mentions:
        target_profile = SocialProfile.objects.filter(username__iexact=m).first()
        if target_profile and target_profile.id != profile.id:
            Interaction.objects.create(
                source_profile=profile,
                target_profile=target_profile,
                post=post,
                interaction_type='mention',
                weight=1.0,
                timestamp=created_at
            )
            
    # From thread / reply / quote
    if parent_post and parent_post.author.id != profile.id:
        Interaction.objects.create(
            source_profile=profile,
            target_profile=parent_post.author,
            post=post,
            interaction_type=post_type if post_type in ['reply', 'repost', 'quote'] else 'reply',
            weight=1.5 if post_type == 'repost' else 1.2,
            timestamp=created_at
        )

    return post


def seed_demo_data(force: bool = False):
    """
    Populates rich, high-quality demonstration dataset covering all 4 core objectives:
    - Multi-platform: X/Twitter, Telegram, Reddit, YouTube
    - Multi-dimensional Sentiment: positive, neutral, negative, support, opposition, excitement, anxiety, sarcasm
    - Audience Demographics: Age cohorts, Countries, Interests, Languages
    - Emerging Topics & Velocity
    - Graph Networks: PageRank, communities, cascade trees
    """
    if SocialPost.objects.exists() and not force:
        return {'status': 'already_seeded', 'posts_count': SocialPost.objects.count()}
        
    if force:
        SocialPost.objects.all().delete()
        Interaction.objects.all().delete()
        SocialProfile.objects.all().delete()
        Topic.objects.all().delete()
        TrendAlert.objects.all().delete()
        ExecutiveBrief.objects.all().delete()

    now = timezone.now()
    
    # 1. Define Topics
    topics_def = [
        {
            'title': 'Autonomous AI Agents in Enterprise',
            'category': 'Tech & AI',
            'desc': 'Enterprise deployments of autonomous multi-agent software and oversight guardrails.',
            'keywords': ['agents', 'llm', 'autonomous', 'enterprise', 'workflows', 'guardrails', 'automation']
        },
        {
            'title': 'Quantum Computing Breakthrough & Chip Export Curbs',
            'category': 'Science & Climate',
            'desc': 'Breakthrough in room-temperature quantum coherence and tightened geopolitical semiconductor export policies.',
            'keywords': ['quantum', 'chips', 'semiconductors', 'coherence', 'trade', 'foundry', 'supercomputing']
        },
        {
            'title': 'Global Grid & Solid-State EV Battery Storage',
            'category': 'Science & Climate',
            'desc': 'Commercialization of solid-state high-density batteries and renewable grid integration.',
            'keywords': ['battery', 'ev', 'solidstate', 'energy', 'cleanpower', 'grid', 'storage']
        },
        {
            'title': 'Algorithmic Antitrust & Platform Free Speech Regulation',
            'category': 'Policy & Law',
            'desc': 'Global parliamentary debates, compliance mandates, and content moderation lawsuits.',
            'keywords': ['antitrust', 'monopoly', 'freespeech', 'compliance', 'regulation', 'algorithms', 'legislation']
        }
    ]
    
    created_topics = {}
    for td in topics_def:
        t = Topic.objects.create(
            title=td['title'],
            slug=slugify(td['title']),
            category=td['category'],
            description=td['desc'],
            keywords=td['keywords'],
            first_detected_at=now - timedelta(days=2),
            last_active_at=now
        )
        created_topics[td['title']] = t

    # 2. Define High-Fidelity Profiles
    profiles_data = [
        # AI & Tech Influencers / Builders
        {'user': 'dr_elena_ai', 'name': 'Dr. Elena Vance', 'bio': 'AI Research Director @ DeepLogic Labs. Former Stanford AI fellow. Exploring alignment & agents.', 'platform': 'twitter', 'fol': 84200, 'country': 'United States', 'age': '35-49', 'interest': 'Tech & AI'},
        {'user': 'dev_marcus', 'name': 'Marcus Vance', 'bio': 'Full-stack builder & autonomous agent engineer. Building open source tooling.', 'platform': 'twitter', 'fol': 28900, 'country': 'Germany', 'age': '25-34', 'interest': 'Tech & AI'},
        {'user': 'neural_digest', 'name': 'Neural Digest', 'bio': 'Telegram channel curating daily deep learning papers, agent benchmarks, and hardware news.', 'platform': 'telegram', 'fol': 56000, 'country': 'United Kingdom', 'age': '25-34', 'interest': 'Tech & AI'},
        {'user': 'sarah_code', 'name': 'Sarah Jenkins', 'bio': 'CS undergrad & AI researcher in training. Gen-Z hacker.', 'platform': 'reddit', 'fol': 4200, 'country': 'Canada', 'age': '18-24', 'interest': 'Tech & AI'},
        {'user': 'prof_holloway', 'name': 'Prof. Arthur Holloway', 'bio': 'Emeritus Chair in Computer Science. Senior advisor to national computing council.', 'platform': 'twitter', 'fol': 31000, 'country': 'United Kingdom', 'age': '50+', 'interest': 'Tech & AI'},
        
        # Policy & Law
        {'user': 'commissioner_kline', 'name': 'Valerie Kline', 'bio': 'Digital Policy Directorate. EU tech regulation, algorithmic accountability and safety.', 'platform': 'twitter', 'fol': 49000, 'country': 'France', 'age': '35-49', 'interest': 'Policy & Law'},
        {'user': 'tech_watchdog', 'name': 'OpenWeb Watchdog', 'bio': 'Nonprofit civil society defending user privacy against corporate monopolies.', 'platform': 'reddit', 'fol': 72000, 'country': 'United States', 'age': '25-34', 'interest': 'Policy & Law'},
        {'user': 'senator_reyes', 'name': 'Senator Carlos Reyes', 'bio': 'Senate Commerce & Technology Committee. Advocating for domestic manufacturing.', 'platform': 'twitter', 'fol': 118000, 'country': 'United States', 'age': '50+', 'interest': 'Policy & Law'},
        {'user': 'legal_bytes', 'name': 'Legal Bytes Channel', 'bio': 'Telegram hub for IP, tech law, and antitrust litigation summaries.', 'platform': 'telegram', 'fol': 21000, 'country': 'India', 'age': '25-34', 'interest': 'Policy & Law'},
        
        # Science & Hardware / Semiconductors
        {'user': 'quantum_kenji', 'name': 'Kenji Takahashi', 'bio': 'Superconducting qubit researcher @ RIKEN. Nanotech & quantum fabrication.', 'platform': 'twitter', 'fol': 38000, 'country': 'Japan', 'age': '35-49', 'interest': 'Science & Climate'},
        {'user': 'silicon_insider', 'name': 'Silicon Insider', 'bio': 'Semiconductor supply chain analyst covering TSMC, ASML, Nvidia, and geopolitical curbs.', 'platform': 'youtube', 'fol': 92000, 'country': 'United States', 'age': '35-49', 'interest': 'Science & Climate'},
        {'user': 'battery_breakthrough', 'name': 'Dr. Amara Patel', 'bio': 'Materials chemist. Solid-state lithium sulfur batteries. Clean energy advocate.', 'platform': 'twitter', 'fol': 44000, 'country': 'India', 'age': '25-34', 'interest': 'Science & Climate'},
        {'user': 'grid_futures', 'name': 'Renewable Grid Lab', 'bio': 'Decentralized energy grids and high-voltage DC transmission research.', 'platform': 'reddit', 'fol': 19000, 'country': 'Australia', 'age': '35-49', 'interest': 'Science & Climate'},
        
        # Finance & Venture
        {'user': 'vc_vikram', 'name': 'Vikram Mehta', 'bio': 'Partner @ Frontier Ventures. Investing in hardware, AI compute, and autonomous systems.', 'platform': 'twitter', 'fol': 65000, 'country': 'Singapore', 'age': '35-49', 'interest': 'Finance & Crypto'},
        {'user': 'crypto_sceptic', 'name': 'Realist Panda', 'bio': 'Calling out tech bubbles, vaporware, and predatory tokenomics with dry humor.', 'platform': 'reddit', 'fol': 34000, 'country': 'United States', 'age': '25-34', 'interest': 'Finance & Crypto'},
        {'user': 'macro_tariq', 'name': 'Tariq Al-Mansoor', 'bio': 'Sovereign wealth macro analyst. Energy capital expenditures and infrastructure.', 'platform': 'telegram', 'fol': 41000, 'country': 'United Arab Emirates', 'age': '35-49', 'interest': 'Finance & Crypto'},
        
        # Journalists & Community Voices
        {'user': 'tech_chronicle', 'name': 'Tech Chronicle Global', 'bio': 'Investigative reporting on big tech, artificial intelligence, and platform politics.', 'platform': 'twitter', 'fol': 210000, 'country': 'United States', 'age': '35-49', 'interest': 'Media & Journalism'},
        {'user': 'lex_reviewer', 'name': 'Lex Morgan', 'bio': 'YouTube tech essayist. Deep dives into emerging computing paradigms.', 'platform': 'youtube', 'fol': 175000, 'country': 'United States', 'age': '25-34', 'interest': 'Media & Journalism'},
        {'user': 'student_arav', 'name': 'Aravind Nair', 'bio': 'Computer engineering student @ IIT. Coding bots and exploring multi-agent protocols.', 'platform': 'reddit', 'fol': 1500, 'country': 'India', 'age': '18-24', 'interest': 'Tech & AI'},
        {'user': 'clara_berlin', 'name': 'Clara Weber', 'bio': 'UX and climate tech activist in Berlin. Advocating for green computing.', 'platform': 'twitter', 'fol': 8900, 'country': 'Germany', 'age': '25-34', 'interest': 'Science & Climate'},
    ]
    
    created_profiles = {}
    for pd in profiles_data:
        p, _ = SocialProfile.objects.get_or_create(
            platform=pd['platform'],
            platform_user_id=f"{pd['platform']}_{pd['user']}",
            defaults={
                'username': pd['user'],
                'display_name': pd['name'],
                'bio': pd['bio'],
                'followers_count': pd['fol'],
                'following_count': random.randint(150, 1800),
                'estimated_age_group': pd['age'],
                'estimated_country': pd['country'],
                'primary_interest': pd['interest'],
                'avatar_url': f"https://api.dicebear.com/7.x/identicon/svg?seed={pd['user']}",
                'is_verified': pd['fol'] > 30000
            }
        )
        created_profiles[pd['user']] = p

    # 3. Authentic Conversation Clusters, Seed Posts & Cascades
    seed_narratives = [
        # AI Agents Thread
        {
            'author': 'dr_elena_ai',
            'topic': 'Autonomous AI Agents in Enterprise',
            'content': 'We just published our benchmark on autonomous multi-agent systems handling corporate workflows. Zero-shot execution improved by 34%, but recursive failures remain concerning when ambiguity arises. #AI #AutonomousAgents',
            'type': 'post',
            'likes': 1420,
            'reposts': 380,
            'hours_ago': 28,
            'replies': [
                {
                    'author': 'dev_marcus',
                    'content': '@dr_elena_ai Incredible work! We deployed this in our staging environment and the speedup is mind-blowing 🚀 Let\'s go!',
                    'type': 'reply',
                    'likes': 112,
                    'reposts': 15,
                    'hours_ago': 26
                },
                {
                    'author': 'prof_holloway',
                    'content': '@dr_elena_ai A promising milestone, but I am worried about auditability. What happens if an unconstrained sub-agent triggers contractual commitments? ⚠️',
                    'type': 'reply',
                    'likes': 340,
                    'reposts': 82,
                    'hours_ago': 25,
                    'sub_replies': [
                        {
                            'author': 'dr_elena_ai',
                            'content': '@prof_holloway Exactly why deterministic sandbox verification gates are essential before final commit.',
                            'type': 'reply',
                            'likes': 195,
                            'reposts': 24,
                            'hours_ago': 24
                        },
                        {
                            'author': 'tech_watchdog',
                            'content': '@prof_holloway @dr_elena_ai This is why we must ban unmonitored agent autonomy in financial services! Unacceptable liability loophole. 🚫',
                            'type': 'reply',
                            'likes': 88,
                            'reposts': 31,
                            'hours_ago': 23
                        }
                    ]
                },
                {
                    'author': 'crypto_sceptic',
                    'content': '@dr_elena_ai "34% improvement in zero-shot execution" yeah right... surely nothing could go wrong when delegating root access to an LLM 🙄 totally not a bubble.',
                    'type': 'quote',
                    'likes': 210,
                    'reposts': 45,
                    'hours_ago': 22
                }
            ]
        },
        # Quantum & Chip Export Curbs
        {
            'author': 'silicon_insider',
            'topic': 'Quantum Computing Breakthrough & Chip Export Curbs',
            'content': 'BREAKING: New multinational trade sanctions now prohibit export of sub-2nm fabrication equipment and quantum lithography components. Massive supply chain disruption ahead. #Semiconductors #Geopolitics',
            'type': 'post',
            'likes': 3890,
            'reposts': 1240,
            'hours_ago': 18,
            'replies': [
                {
                    'author': 'quantum_kenji',
                    'content': '@silicon_insider This will severely choke joint university research. Science should not be weaponized. Catastrophic decision for global physics collaboration.',
                    'type': 'reply',
                    'likes': 420,
                    'reposts': 110,
                    'hours_ago': 16
                },
                {
                    'author': 'senator_reyes',
                    'content': '@silicon_insider National security must take precedence. We cannot allow adversaries access to dual-use supercomputing architectures. I strongly endorse this policy.',
                    'type': 'quote',
                    'likes': 890,
                    'reposts': 270,
                    'hours_ago': 15
                },
                {
                    'author': 'vc_vikram',
                    'content': '@silicon_insider Capital will aggressively rotate toward domestic foundry alternatives. Huge opportunity for emerging fabless startups in APAC.',
                    'type': 'reply',
                    'likes': 190,
                    'reposts': 34,
                    'hours_ago': 14
                }
            ]
        },
        # Solid State Batteries & EV Grid
        {
            'author': 'battery_breakthrough',
            'topic': 'Global Grid & Solid-State EV Battery Storage',
            'content': 'Proud to share our pilot cell tests: 450 Wh/kg energy density with over 1,200 fast charge cycles! Clean energy storage is reaching the tipping point. ⚡🔋 #CleanTech #Batteries',
            'type': 'post',
            'likes': 4120,
            'reposts': 980,
            'hours_ago': 12,
            'replies': [
                {
                    'author': 'grid_futures',
                    'content': '@battery_breakthrough Transformative breakthrough! If utility scale costs fall below $60/kWh, coal and peaker gas plants are officially obsolete.',
                    'type': 'reply',
                    'likes': 340,
                    'reposts': 80,
                    'hours_ago': 10
                },
                {
                    'author': 'clara_berlin',
                    'content': '@battery_breakthrough Brilliant achievement! But how sustainable is the lithium-free anode sourcing? What are the implications for mining ethics? 🤔',
                    'type': 'reply',
                    'likes': 115,
                    'reposts': 19,
                    'hours_ago': 9
                },
                {
                    'author': 'lex_reviewer',
                    'content': '@battery_breakthrough Testing sample cells in the studio next week! Super excited to benchmark the thermal dissipation curve 🔥',
                    'type': 'reply',
                    'likes': 260,
                    'reposts': 45,
                    'hours_ago': 8
                }
            ]
        },
        # Platform Regulation & Antitrust
        {
            'author': 'commissioner_kline',
            'topic': 'Algorithmic Antitrust & Platform Free Speech Regulation',
            'content': 'Today the Commission issues formal notices regarding recommendation algorithm audits. Dominant digital platforms must open their content weighting models to independent academic scrutiny. #TechPolicy',
            'type': 'post',
            'likes': 2750,
            'reposts': 840,
            'hours_ago': 8,
            'replies': [
                {
                    'author': 'tech_chronicle',
                    'content': '@commissioner_kline Big tech lobbyists are already preparing injunctions citing trade secrets. This will be the defining antitrust showdown of the decade.',
                    'type': 'quote',
                    'likes': 620,
                    'reposts': 190,
                    'hours_ago': 7
                },
                {
                    'author': 'crypto_sceptic',
                    'content': '@commissioner_kline Oh wonderful, government committees deciding what algorithms can show us. Surely the state has never abused censorship powers 😏',
                    'type': 'reply',
                    'likes': 380,
                    'reposts': 78,
                    'hours_ago': 6
                },
                {
                    'author': 'legal_bytes',
                    'content': '@commissioner_kline Full breakdown of the statutory compliance guidelines posted on our channel for all enterprise counsel.',
                    'type': 'reply',
                    'likes': 140,
                    'reposts': 22,
                    'hours_ago': 5
                }
            ]
        }
    ]
    
    # Ingest seed cascades
    for thread in seed_narratives:
        t_obj = created_topics[thread['topic']]
        auth = created_profiles[thread['author']]
        root_post = ingest_raw_post(
            platform=auth.platform,
            external_id=f"seed_{thread['author']}_{random.randint(1000, 9999)}",
            username=auth.username,
            display_name=auth.display_name,
            bio=auth.bio,
            content=thread['content'],
            post_type='post',
            created_at=now - timedelta(hours=thread['hours_ago']),
            metrics={'likes': thread['likes'], 'reposts': thread['reposts'], 'comments': len(thread.get('replies', [])), 'views': thread['likes'] * 15},
            topic_title=t_obj.title
        )
        
        for rep in thread.get('replies', []):
            rep_auth = created_profiles[rep['author']]
            c_post = ingest_raw_post(
                platform=rep_auth.platform,
                external_id=f"rep_{rep['author']}_{random.randint(1000, 9999)}",
                username=rep_auth.username,
                display_name=rep_auth.display_name,
                bio=rep_auth.bio,
                content=rep['content'],
                post_type=rep['type'],
                parent_external_id=root_post.external_id,
                created_at=now - timedelta(hours=rep['hours_ago']),
                metrics={'likes': rep['likes'], 'reposts': rep['reposts'], 'comments': len(rep.get('sub_replies', [])), 'views': rep['likes'] * 12},
                topic_title=t_obj.title
            )
            
            for sub_rep in rep.get('sub_replies', []):
                sub_auth = created_profiles[sub_rep['author']]
                ingest_raw_post(
                    platform=sub_auth.platform,
                    external_id=f"sub_{sub_rep['author']}_{random.randint(1000, 9999)}",
                    username=sub_auth.username,
                    display_name=sub_auth.display_name,
                    bio=sub_auth.bio,
                    content=sub_rep['content'],
                    post_type=sub_rep['type'],
                    parent_external_id=c_post.external_id,
                    created_at=now - timedelta(hours=sub_rep['hours_ago']),
                    metrics={'likes': sub_rep['likes'], 'reposts': sub_rep['reposts'], 'comments': 0, 'views': sub_rep['likes'] * 8},
                    topic_title=t_obj.title
                )

    # 4. Generate distributed historical posts across all profiles to establish high-fidelity network & timeline
    all_users = list(created_profiles.keys())
    sample_templates = [
        "Testing the new latency limits on multi-threaded orchestration. Promising benchmarks!",
        "Very concerned about energy consumption of large clusters. We need sustainable green compute immediately.",
        "Groundbreaking updates coming out of the summit today. Full coverage in tomorrow's thread.",
        "Anyone noticing severe hallucination regressions in latest fine-tunes? Curious to see community findings 🤔",
        "We must oppose closed-door regulatory carve-outs that favor incumbent monopolies. Boycott closed gatekeepers!",
        "Excited to announce our open source dataset for community evaluation 🔥🚀 Let's go!",
        "Sovereign data sovereignty laws are getting complex. Full legal analysis published.",
        "Solid state electrolytes look great in the lab, but scaling mass manufacturing remains tricky.",
        "Yeah right, tech companies self-regulating their AI models... what could possibly go wrong 🙄",
        "Remarkable efficiency gains achieved today. Kudos to the entire research team!"
    ]
    
    for i in range(120):
        u_name = random.choice(all_users)
        prof = created_profiles[u_name]
        chosen_topic = random.choice(list(created_topics.values()))
        hrs_offset = random.uniform(1.0, 72.0)
        
        # occasionally mention another user
        other_u = random.choice(all_users)
        content_text = random.choice(sample_templates)
        if other_u != u_name and random.random() < 0.4:
            content_text = f"@{other_u} {content_text}"
            
        ingest_raw_post(
            platform=prof.platform,
            external_id=f"hist_{i}_{random.randint(10000, 99999)}",
            username=prof.username,
            display_name=prof.display_name,
            bio=prof.bio,
            content=content_text,
            post_type='post',
            created_at=now - timedelta(hours=hrs_offset),
            metrics={
                'likes': random.randint(10, 450),
                'reposts': random.randint(2, 95),
                'comments': random.randint(0, 25),
                'views': random.randint(200, 5000)
            },
            topic_title=chosen_topic.title
        )

    # 5. Compute Network Centrality & Communities
    compute_network_metrics()
    
    # 6. Update Trend Velocities & Alerts
    update_trend_velocities()
    
    # 7. Generate Initial AI Executive Intelligence Brief
    ExecutiveBrief.objects.create(
        title="SMA.AI Daily Audience Intelligence Synthesis",
        timeframe="Last 24 Hours",
        summary_markdown="""
### Executive AI Synthesis
Over the past 24 hours, **SMA.AI** monitored multi-platform conversations across X (Twitter), Telegram, Reddit, and YouTube.
Discussion velocity peaked around **Autonomous AI Agents in Enterprise** and **Semiconductor Export Sanctions**, with distinct polarization observed between technical practitioners and governance advocates.

Key Observations:
1. **Strong Optimism vs. Governance Anxiety**: Technical communities express high excitement (42%) around autonomous agent performance, but enterprise risk officers and policymakers are raising alarms (31% anxiety/opposition) regarding auditability and unsupervised execution.
2. **Geopolitical Hardware Fracturing**: Hardware supply chain discussions show a surge in viral velocity following tightening export controls on sub-2nm equipment.
3. **High-Authority Nodes**: Influence analysis highlights `@dr_elena_ai` and `@silicon_insider` as primary information broadcasters driving over 64% of cascading retweets and replies.
        """,
        key_findings=[
            {"headline": "Autonomous Agents Narrative Surge", "desc": "34% velocity increase with split sentiment: 58% excitement among developers vs 35% anxiety among policy leaders."},
            {"headline": "Hardware Geopolitics Mobilization", "desc": "Sub-2nm semiconductor sanctions triggered a rapid propagation burst across US, Japan, and German audience clusters."},
            {"headline": "Solid-State Clean Tech Momentum", "desc": "Overwhelmingly positive community sentiment (+0.74 score) for solid-state battery pilot announcements."}
        ],
        metrics_snapshot={
            "total_posts_analyzed": SocialPost.objects.count(),
            "active_profiles": SocialProfile.objects.count(),
            "trending_topics": Topic.objects.count(),
            "critical_alerts": TrendAlert.objects.count()
        }
    )
    
    return {
        'status': 'seeded_successfully',
        'posts': SocialPost.objects.count(),
        'profiles': SocialProfile.objects.count(),
        'topics': Topic.objects.count()
    }


def simulate_live_stream_surge() -> Dict[str, Any]:
    """
    Simulates real-time live ingestion of a breaking surge,
    demonstrating immediate platform reactivity, graph recalculation,
    and live dashboard updates.
    """
    topics = list(Topic.objects.all())
    if not topics:
        seed_demo_data()
        topics = list(Topic.objects.all())
        
    profiles = list(SocialProfile.objects.all())
    target_topic = random.choice(topics)
    
    surge_templates = [
        ("Breaking update just dropped regarding {topic}. Emergency press conference scheduled!", 'anxiety', 'warning'),
        ("Tested the latest patch for {topic}. The performance improvements are astronomical! 🔥🚀 #Innovation", 'excitement', 'info'),
        ("This is completely unacceptable! Community coalition calling for an immediate boycott on {topic}. 🚫", 'opposition', 'critical'),
        ("Wait, did anyone actually read section 4 of the new {topic} whitepaper? Mind-blowing implications.", 'curiosity', 'info'),
        ("Yeah right, another PR announcement about {topic}... totally believable this time 🙄", 'sarcasm', 'warning')
    ]
    
    created_posts = []
    now = timezone.now()
    
    # Select 3 profiles to interact
    actors = random.sample(profiles, min(3, len(profiles)))
    root_post = None
    
    for idx, actor in enumerate(actors):
        tmpl, expected_emo, sev = surge_templates[idx % len(surge_templates)]
        content = tmpl.format(topic=target_topic.title)
        
        # Add mention of previous actor to build graph edges
        if root_post and idx > 0:
            content = f"@{root_post.author.username} {content}"
            p_type = 'reply'
            p_ext = root_post.external_id
        else:
            p_type = 'post'
            p_ext = None
            
        post = ingest_raw_post(
            platform=actor.platform,
            external_id=f"live_{uuid.uuid4().hex[:10]}",
            username=actor.username,
            display_name=actor.display_name,
            bio=actor.bio,
            content=content,
            post_type=p_type,
            parent_external_id=p_ext,
            created_at=now,
            metrics={
                'likes': random.randint(40, 200),
                'reposts': random.randint(15, 60),
                'comments': random.randint(5, 30),
                'views': random.randint(800, 3000)
            },
            topic_title=target_topic.title
        )
        if not root_post:
            root_post = post
        created_posts.append({
            'id': post.id,
            'author': post.author.username,
            'content': post.content,
            'sentiment': post.sentiment_label,
            'emotion': post.emotion_label,
            'platform': post.platform
        })

    # Recalculate graph and trend velocity
    compute_network_metrics()
    update_trend_velocities()

    return {
        'status': 'surge_simulated',
        'topic': target_topic.title,
        'posts_generated': created_posts,
        'timestamp': now.isoformat()
    }
