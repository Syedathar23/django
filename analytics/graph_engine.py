import networkx as nx
from typing import Dict, List, Any
from django.db.models import Count
from analytics.models import SocialProfile, Interaction, SocialPost


def compute_network_metrics() -> Dict[str, Any]:
    """
    Constructs a directed interaction network from all interactions,
    calculates PageRank, Betweenness Centrality, In/Out degree, and
    performs community detection. Updates profile models with metrics.
    """
    G = nx.DiGraph()
    
    # 1. Add all profiles as nodes
    profiles = SocialProfile.objects.all()
    if not profiles.exists():
        return {'node_count': 0, 'edge_count': 0, 'communities': 0}
        
    for p in profiles:
        G.add_node(
            p.id,
            username=p.username,
            platform=p.platform,
            interest=p.primary_interest,
            followers=p.followers_count
        )
        
    # 2. Add edges from Interaction records
    interactions = Interaction.objects.select_related('source_profile', 'target_profile')
    for inter in interactions:
        s_id = inter.source_profile_id
        t_id = inter.target_profile_id
        if G.has_edge(s_id, t_id):
            G[s_id][t_id]['weight'] += inter.weight
        else:
            G.add_edge(s_id, t_id, weight=inter.weight, type=inter.interaction_type)
            
    # Also link child posts (replies / quotes) if not already explicitly in Interaction
    child_posts = SocialPost.objects.filter(parent_post__isnull=False).select_related('author', 'parent_post__author')
    for cp in child_posts:
        s_id = cp.author_id
        t_id = cp.parent_post.author_id
        if s_id != t_id:
            if G.has_edge(s_id, t_id):
                G[s_id][t_id]['weight'] += 1.5
            else:
                G.add_edge(s_id, t_id, weight=1.5, type=cp.post_type)
                
    if len(G.nodes) == 0:
        return {'node_count': 0, 'edge_count': 0, 'communities': 0}

    # 3. Compute PageRank (authority metric)
    try:
        pagerank_scores = nx.pagerank(G, alpha=0.85, weight='weight', max_iter=200)
    except Exception:
        pagerank_scores = {n: 1.0 / len(G.nodes) for n in G.nodes}
        
    # 4. Compute Betweenness Centrality (information bridge metric)
    try:
        betweenness = nx.betweenness_centrality(G, weight='weight', normalized=True)
    except Exception:
        betweenness = {n: 0.0 for n in G.nodes}

    # 5. Community Detection (using greedy modularity on undirected projection)
    undirected_G = G.to_undirected()
    try:
        communities_generator = nx.community.greedy_modularity_communities(undirected_G, weight='weight')
        community_map = {}
        for comm_id, comm_nodes in enumerate(communities_generator):
            for node_id in comm_nodes:
                community_map[node_id] = comm_id
    except Exception:
        community_map = {n: 0 for n in G.nodes}

    # 6. Normalize and update database
    max_pr = max(pagerank_scores.values()) if pagerank_scores else 1.0
    if max_pr == 0:
        max_pr = 1.0

    updates = []
    for p in profiles:
        node_id = p.id
        if node_id in G:
            norm_pr = round((pagerank_scores.get(node_id, 0.0) / max_pr) * 100, 2)
            norm_bc = round(betweenness.get(node_id, 0.0) * 100, 3)
            in_deg = G.in_degree(node_id)
            out_deg = G.out_degree(node_id)
            comm = community_map.get(node_id, 0)
            
            p.influence_score = norm_pr
            p.betweenness_score = norm_bc
            p.in_degree = in_deg
            p.out_degree = out_deg
            p.community_id = comm
            updates.append(p)
            
    if updates:
        SocialProfile.objects.bulk_update(
            updates,
            ['influence_score', 'betweenness_score', 'in_degree', 'out_degree', 'community_id']
        )
        
    num_communities = len(set(community_map.values()))
    return {
        'node_count': G.number_of_nodes(),
        'edge_count': G.number_of_edges(),
        'communities': num_communities,
        'density': round(nx.density(G), 4)
    }


def get_network_graph_data(limit_nodes: int = 80, community_filter: int = None) -> Dict[str, Any]:
    """
    Produces Vis-network compatible nodes and edges payload.
    Includes community clustering, influence ranking, and connection details.
    """
    profiles_qs = SocialProfile.objects.all().order_by('-influence_score')
    if community_filter is not None and community_filter >= 0:
        profiles_qs = profiles_qs.filter(community_id=community_filter)
        
    profiles = list(profiles_qs[:limit_nodes])
    profile_ids = {p.id for p in profiles}
    
    # Community Colors palette
    PALETTE = [
        {'bg': '#3b82f6', 'border': '#60a5fa'}, # Electric Blue
        {'bg': '#8b5cf6', 'border': '#a78bfa'}, # Purple
        {'bg': '#10b981', 'border': '#34d399'}, # Emerald
        {'bg': '#f59e0b', 'border': '#fbbf24'}, # Amber
        {'bg': '#ec4899', 'border': '#f472b6'}, # Pink
        {'bg': '#06b6d4', 'border': '#22d3ee'}, # Cyan
        {'bg': '#ef4444', 'border': '#f87171'}, # Crimson
    ]

    nodes = []
    for p in profiles:
        palette_idx = p.community_id % len(PALETTE)
        colors = PALETTE[palette_idx]
        
        # Node size scaled to influence score (min 14, max 45)
        node_size = max(14, int(14 + (p.influence_score / 100.0) * 32))
        
        nodes.append({
            'id': p.id,
            'label': f"@{p.username}",
            'title': f"""<b>@{p.username}</b> ({p.get_platform_display()})<br/>
                         Influence: <b>{p.influence_score}/100</b><br/>
                         Followers: {p.followers_count:,}<br/>
                         Interest: {p.primary_interest}<br/>
                         Community: #{p.community_id}""",
            'value': node_size,
            'group': p.community_id,
            'color': {
                'background': colors['bg'],
                'border': colors['border'],
                'highlight': {'background': '#ffffff', 'border': colors['bg']}
            },
            'font': {'color': '#f8fafc', 'size': 12, 'face': 'Inter'},
            'details': {
                'username': p.username,
                'display_name': p.display_name,
                'platform': p.platform,
                'followers': p.followers_count,
                'influence_score': p.influence_score,
                'betweenness': p.betweenness_score,
                'in_degree': p.in_degree,
                'out_degree': p.out_degree,
                'community_id': p.community_id,
                'interest': p.primary_interest,
                'country': p.estimated_country,
                'age_group': p.estimated_age_group
            }
        })
        
    # Gather edges where both source and target are in current node set
    interactions = Interaction.objects.filter(
        source_profile_id__in=profile_ids,
        target_profile_id__in=profile_ids
    ).select_related('source_profile', 'target_profile')[:300]
    
    edges = []
    edge_seen = set()
    for inter in interactions:
        key = (inter.source_profile_id, inter.target_profile_id, inter.interaction_type)
        if key in edge_seen:
            continue
        edge_seen.add(key)
        
        # Color based on type
        color_map = {
            'mention': '#38bdf8', # sky
            'repost': '#34d399',  # green
            'reply': '#a78bfa',   # purple
            'quote': '#f59e0b'    # amber
        }
        edge_color = color_map.get(inter.interaction_type, '#64748b')
        
        edges.append({
            'from': inter.source_profile_id,
            'to': inter.target_profile_id,
            'arrows': 'to',
            'label': inter.interaction_type,
            'font': {'size': 9, 'color': '#94a3b8', 'align': 'middle'},
            'color': {'color': edge_color, 'opacity': 0.6, 'highlight': '#ffffff'},
            'width': max(1, min(4, int(inter.weight))),
            'smooth': {'type': 'curvedCW', 'roundness': 0.15}
        })
        
    return {
        'nodes': nodes,
        'edges': edges,
        'stats': {
            'total_nodes': len(nodes),
            'total_edges': len(edges),
            'active_communities': len(set(n['group'] for n in nodes))
        }
    }


def get_propagation_cascade(post_id: int) -> Dict[str, Any]:
    """
    Traces how a specific viral post cascaded across downstream users
    (replies, reposts, quotes) to visualize the timeline and depth of spread.
    """
    try:
        seed_post = SocialPost.objects.select_related('author', 'topic').get(id=post_id)
    except SocialPost.DoesNotExist:
        return {'error': 'Post not found'}
        
    cascade_nodes = []
    cascade_edges = []
    
    # Level 0: Seed
    cascade_nodes.append({
        'id': f"post_{seed_post.id}",
        'label': f"@{seed_post.author.username} (Origin)",
        'level': 0,
        'timestamp': seed_post.created_at.strftime('%H:%M:%S'),
        'content': seed_post.content[:80],
        'sentiment': seed_post.sentiment_label,
        'emotion': seed_post.emotion_label,
        'shares': seed_post.reposts_count
    })
    
    # Query direct children
    children = SocialPost.objects.filter(parent_post=seed_post).select_related('author')
    for idx, cp in enumerate(children[:30]):
        cascade_nodes.append({
            'id': f"post_{cp.id}",
            'label': f"@{cp.author.username}",
            'level': 1,
            'timestamp': cp.created_at.strftime('%H:%M:%S'),
            'content': cp.content[:60],
            'type': cp.post_type,
            'sentiment': cp.sentiment_label,
            'emotion': cp.emotion_label
        })
        cascade_edges.append({
            'from': f"post_{seed_post.id}",
            'to': f"post_{cp.id}",
            'type': cp.post_type
        })
        
        # Query grandchildren for propagation depth
        grandchildren = SocialPost.objects.filter(parent_post=cp).select_related('author')[:5]
        for gcp in grandchildren:
            cascade_nodes.append({
                'id': f"post_{gcp.id}",
                'label': f"@{gcp.author.username}",
                'level': 2,
                'timestamp': gcp.created_at.strftime('%H:%M:%S'),
                'content': gcp.content[:60],
                'type': gcp.post_type,
                'sentiment': gcp.sentiment_label,
                'emotion': gcp.emotion_label
            })
            cascade_edges.append({
                'from': f"post_{cp.id}",
                'to': f"post_{gcp.id}",
                'type': gcp.post_type
            })

    return {
        'seed': {
            'id': seed_post.id,
            'author': seed_post.author.username,
            'content': seed_post.content,
            'created_at': seed_post.created_at.isoformat(),
            'total_shares': seed_post.reposts_count,
            'views': seed_post.views_count
        },
        'cascade_nodes': cascade_nodes,
        'cascade_edges': cascade_edges,
        'max_depth': 2 if any(n['level'] == 2 for n in cascade_nodes) else 1,
        'cascade_size': len(cascade_nodes)
    }
