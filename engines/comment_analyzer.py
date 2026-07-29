# -*- coding: utf-8 -*-
"""
Comment Classification & Analysis Engine
v2.0 — 新4类体系：content_engagement(内容互动) -> purchase_intent(购买意向) -> product_interaction(产品互动) -> other(其他)
判定顺序即优先级。每类先查 exclude_signals，命中排除词则跳过。购买意向带子类型。
"""
import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "..", "configs", "comment_classification_config.json")

# Module-level cache for precompiled regex patterns
_compiled_patterns_cache = {}
_config_cache = None


def load_config():
    global _config_cache
    if _config_cache is None:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            _config_cache = json.load(f)
    return _config_cache


def _norm(text):
    """Normalize text: lowercase, wrap in spaces for boundary matching."""
    return " " + (text or "").lower().strip().replace("\n", " ").replace("\r", " ") + " "


def _hit(text_norm, signals):
    """Check if any signal word (substring) appears in normalized text.
    Returns the matched signal string, or None."""
    for s in signals:
        if s.lower() in text_norm:
            return s
    return None


def _hit_patterns(text_norm, cat_key):
    """Check precompiled regex patterns for a category. Returns matched pattern string or None."""
    patterns = _get_compiled_patterns(cat_key)
    for p in patterns:
        if p.search(text_norm):
            return p.pattern
    return None


def _get_compiled_patterns(cat_key):
    """Lazily compile and cache regex patterns for a category."""
    global _compiled_patterns_cache
    if cat_key not in _compiled_patterns_cache:
        config = load_config()
        cat_info = config.get("categories", {}).get(cat_key, {})
        raw_patterns = cat_info.get("signal_patterns", [])
        _compiled_patterns_cache[cat_key] = [re.compile(p, re.IGNORECASE) for p in raw_patterns]
    return _compiled_patterns_cache[cat_key]


def _get_subtype(text_norm, config):
    """Determine purchase_intent subtype. Returns (subtype_key, subtype_zh) or (None, None)."""
    pi = config.get("categories", {}).get("purchase_intent", {})
    subs = pi.get("subtypes", {})
    for key in ("price_inquiry", "purchase_willingness", "channel_link", "discount_code"):
        info = subs.get(key)
        if info and _hit(text_norm, info.get("signals", [])):
            return key, info["label_zh"]
    return None, None


def classify_single_comment(comment_text, config=None):
    """Classify a single comment into one of 4 categories (priority-based).
    
    Args:
        comment_text: raw comment text
        config: optional pre-loaded config dict
    
    Returns:
        (category_key, name_en, name_zh, matched_signals)
        - category_key: "content_engagement" | "purchase_intent" | "product_interaction" | "other"
        - matched_signals: list of matched signal strings (for transparency)
    """
    if config is None:
        config = load_config()

    t = _norm(comment_text)
    categories = config.get("categories", {})
    order = config.get("classification_order", ["content_engagement", "purchase_intent", "product_interaction", "other"])
    normalized_key = re.sub(r"\s+", " ", (comment_text or "").strip().lower())
    override = config.get("calibration_overrides", {}).get(normalized_key)
    if override:
        cat_key = override.get("category", "other")
        info = categories.get(cat_key, categories.get("other", {}))
        return (cat_key, info.get("name_en", cat_key), info.get("name_zh", cat_key), ["人工校准"])

    for cat_key in order:
        cat_info = categories.get(cat_key)
        if not cat_info:
            continue

        # Skip excluded categories
        excludes = cat_info.get("exclude_signals", [])
        if excludes and _hit(t, excludes):
            continue

        # Check signal words
        signals = cat_info.get("signals", [])
        matched = _hit(t, signals)

        # For content_engagement, also check regex patterns
        if not matched and cat_key == "content_engagement":
            matched = _hit_patterns(t, cat_key)

        if matched:
            # Weak signal guard: if CE matched via a "weak" signal (emoji/bro)
            # but the comment also has PI signals, defer to PI instead
            if cat_key == "content_engagement":
                weak_signals = cat_info.get("weak_signals", [])
                if matched in weak_signals:
                    pi_info = categories.get("purchase_intent", {})
                    if pi_info and _hit(t, pi_info.get("signals", [])):
                        continue  # Skip CE, let PI win

            name_en = cat_info.get("name_en", cat_key)
            name_zh = cat_info.get("name_zh", cat_info.get("label_zh", cat_key))
            return (cat_key, name_en, name_zh, [matched])

    # Fallback: "other"
    other_info = categories.get("other", {})
    name_en = other_info.get("name_en", "Other")
    name_zh = other_info.get("name_zh", "其他")
    other_signals = _hit(t, other_info.get("signals", []))
    return ("other", name_en, name_zh, [other_signals] if other_signals else [])


def get_subtype_for_comment(comment_text, config=None):
    """Get purchase intent subtype for a comment (convenience wrapper).
    Returns (subtype_key, subtype_zh) or (None, None)."""
    if config is None:
        config = load_config()
    return _get_subtype(_norm(comment_text), config)


def should_llm_fallback(cat_key, config=None):
    """Check if LLM fallback should be triggered for this comment classification.
    Returns True if the comment was classified as 'other' and LLM fallback is enabled."""
    if config is None:
        config = load_config()
    llm_cfg = config.get("llm_fallback", {})
    if not llm_cfg.get("enabled", False):
        return False
    return cat_key == "other"


def analyze_video_comments(comments, config=None):
    """Analyze all comments for a single video.
    Input: list of dicts with at least {'text': '...', 'likes': N}
    Returns: dict with category distribution, top comments, and purchase intent score
    """
    if config is None:
        config = load_config()

    if not comments:
        return {
            "total_comments": 0,
            "distribution": {},
            "purchase_intent_score": 0,
            "purchase_intent_label": "Low Purchase Intent",
            "top_comments_by_category": {},
            "all_classified": []
        }

    categories = config.get("categories", {})
    scoring = config.get("purchase_intent_scoring", {})
    weights = scoring.get("weights", {})

    dist = {}
    classified = []
    total_weighted_score = 0

    for c in comments:
        text = c.get("text", "")
        likes = c.get("likes", 0)
        cat_key, name_en, name_zh, signals = classify_single_comment(text, config)

        # Get subtype for purchase_intent
        subtype_key, subtype_zh = None, None
        if cat_key == "purchase_intent":
            subtype_key, subtype_zh = _get_subtype(_norm(text), config)

        if cat_key not in dist:
            dist[cat_key] = {"count": 0, "name_en": name_en, "name_zh": name_zh, "comments": []}

        dist[cat_key]["count"] += 1
        dist[cat_key]["comments"].append({
            "text": text[:200],
            "likes": likes,
            "matched_signals": signals
        })

        weight = weights.get(cat_key, 0)
        total_weighted_score += weight

        entry = {
            "text": text[:200],
            "likes": likes,
            "category": cat_key,
            "category_name": name_en,
            "category_name_zh": name_zh
        }
        if subtype_key:
            entry["subtype"] = subtype_key
            entry["subtype_zh"] = subtype_zh

        classified.append(entry)

    n = len(comments)
    avg_score = total_weighted_score / n if n > 0 else 0

    interpretation = scoring.get("interpretation", {})
    if avg_score >= interpretation.get("high", {}).get("min_score", 2.0):
        intent_label = interpretation["high"].get("label_zh", interpretation["high"]["label"])
    elif avg_score >= interpretation.get("medium", {}).get("min_score", 1.0):
        intent_label = interpretation["medium"].get("label_zh", interpretation["medium"]["label"])
    else:
        intent_label = interpretation["low"].get("label_zh", interpretation["low"]["label"])

    # Get top 5 comments per category
    top_by_category = {}
    for cat_key, cat_data in dist.items():
        sorted_comments = sorted(cat_data["comments"], key=lambda x: x["likes"], reverse=True)
        top_by_category[cat_key] = {
            "name_en": cat_data["name_en"],
            "name_zh": cat_data["name_zh"],
            "count": cat_data["count"],
            "pct": round(cat_data["count"] / n * 100, 1) if n > 0 else 0,
            "top_comments": sorted_comments[:5]
        }

    return {
        "total_comments": n,
        "distribution": {
            k: {"name_en": v["name_en"], "name_zh": v["name_zh"], "count": v["count"],
                "pct": round(v["count"] / n * 100, 1) if n > 0 else 0}
            for k, v in dist.items()
        },
        "purchase_intent_score": round(avg_score, 2),
        "purchase_intent_label": intent_label,
        "top_comments_by_category": top_by_category,
        "all_classified": sorted(classified, key=lambda x: x["likes"], reverse=True)[:15]
    }


def analyze_account_comments(video_analyses):
    """Aggregate comment analysis across all videos of an account.
    Input: list of video analysis dicts from analyze_video_comments()
    """
    if not video_analyses:
        return {
            "total_videos_analyzed": 0,
            "total_comments_analyzed": 0,
            "aggregate_distribution": {},
            "overall_purchase_intent_score": 0,
            "overall_purchase_intent_label": "Low Purchase Intent",
            "top_videos_by_intent": []
        }

    total_comments = sum(v.get("total_comments", 0) for v in video_analyses)
    total_weighted = 0
    all_dist = {}

    for v in video_analyses:
        total_weighted += v.get("purchase_intent_score", 0) * v.get("total_comments", 0)
        for cat_key, cat_data in v.get("distribution", {}).items():
            if cat_key not in all_dist:
                all_dist[cat_key] = {"name_en": cat_data["name_en"], "name_zh": cat_data["name_zh"], "count": 0}
            all_dist[cat_key]["count"] += cat_data["count"]

    avg_score = total_weighted / total_comments if total_comments > 0 else 0

    scoring = load_config().get("purchase_intent_scoring", {}).get("interpretation", {})
    if avg_score >= scoring.get("high", {}).get("min_score", 2.0):
        intent_label = scoring["high"].get("label_zh", scoring["high"]["label"])
    elif avg_score >= scoring.get("medium", {}).get("min_score", 1.0):
        intent_label = scoring["medium"].get("label_zh", scoring["medium"]["label"])
    else:
        intent_label = scoring["low"].get("label_zh", scoring["low"]["label"])

    for k in all_dist:
        all_dist[k]["pct"] = round(all_dist[k]["count"] / total_comments * 100, 1) if total_comments > 0 else 0

    sorted_videos = sorted(video_analyses, key=lambda x: x.get("purchase_intent_score", 0), reverse=True)

    return {
        "total_videos_analyzed": len(video_analyses),
        "total_comments_analyzed": total_comments,
        "aggregate_distribution": all_dist,
        "overall_purchase_intent_score": round(avg_score, 2),
        "overall_purchase_intent_label": intent_label,
        "top_videos_by_intent": sorted_videos[:5]
    }


if __name__ == "__main__":
    config = load_config()

    test_comments = [
        {"text": "How much does this cost? Where can I buy it?", "likes": 45},
        {"text": "Just ordered mine! Can't wait 🎉", "likes": 120},
        {"text": "Is this better than the Anker version?", "likes": 33},
        {"text": "Great review as always man! 🔥", "likes": 200},
        {"text": "Bought this last month, stopped working after 2 weeks", "likes": 89},
        {"text": "@john check this out!", "likes": 15},
        {"text": "What's the battery life like on this?", "likes": 67},
        {"text": "Take my money! 💰", "likes": 250},
        {"text": "Thanks for the detailed breakdown!", "likes": 55},
        {"text": "link please!!", "likes": 30},
        {"text": "I need this in my life", "likes": 78},
        {"text": "Does it work with iPhone 16?", "likes": 42},
        {"text": "if i get 1000 likes ill buy 10 of these", "likes": 5},
        {"text": "day 3 of asking for a pin", "likes": 12},
    ]

    print("=" * 70)
    print("Comment Classification Test (v2.0 — 4 categories)")
    print("=" * 70)

    for c in test_comments:
        cat_key, name_en, name_zh, signals = classify_single_comment(c["text"], config)
        subtype = ""
        if cat_key == "purchase_intent":
            st_key, st_zh = _get_subtype(_norm(c["text"]), config)
            subtype = f" [{st_zh}]" if st_key else ""
        print(f"\nComment: \"{c['text'][:60]}...\"")
        print(f"  Category: {cat_key} ({name_zh}){subtype}")
        print(f"  Signals: {signals}")

    print("\n" + "=" * 70)
    print("Video-Level Analysis")
    print("=" * 70)
    result = analyze_video_comments(test_comments, config)
    print(f"\nTotal: {result['total_comments']} comments")
    print(f"Purchase Intent Score: {result['purchase_intent_score']}")
    print(f"Purchase Intent Label: {result['purchase_intent_label']}")
    print(f"\nDistribution:")
    for k, v in result['distribution'].items():
        print(f"  {v['name_zh']}: {v['count']} ({v['pct']}%)")
    print(f"\nAll classified (top 5):")
    for c in result['all_classified'][:5]:
        sub = f" [{c.get('subtype_zh', '')}]" if c.get('subtype') else ""
        print(f"  [{c['category_name_zh']}{sub}] {c['text'][:60]} (👍{c['likes']})")
