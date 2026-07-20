# -*- coding: utf-8 -*-
"""
Comment Classification & Analysis Engine
Classifies individual comments into 7 categories and computes purchase intent scores.

Supports: English + Spanish comments
"""
import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "..", "configs", "comment_classification_config.json")


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def classify_single_comment(comment_text, config=None):
    """Classify a single comment into one of 7 categories.
    Returns: (category_key, category_name_en, category_name_zh, matched_signals)
    """
    if config is None:
        config = load_config()

    text_lower = (" " + comment_text.lower() + " ").replace("\n", " ").replace("\r", " ")
    categories = config.get("categories", {})

    best_category = "other"
    best_name_en = "Other"
    best_name_zh = "其他"
    best_signal_count = 0
    best_signals = []
    best_weight = 0

    for cat_key, cat_info in categories.items():
        if cat_key == "other":
            continue

        # Support both new "signals" field and old "signals_en"/"signals_es" fields
        signals = cat_info.get("signals", [])
        if not signals:
            signals_es = cat_info.get("signals_es", [])
            signals_en = cat_info.get("signals_en", [])
            signals = signals_es + signals_en

        matched = []
        for sig in signals:
            if sig.lower() in text_lower:
                matched.append(sig)

        if matched:
            weight = cat_info.get("weight", 0)
            if len(matched) > best_signal_count or (len(matched) == best_signal_count and weight > best_weight):
                best_category = cat_key
                best_name_en = cat_info.get("name_en", cat_key)
                best_name_zh = cat_info.get("name_zh", cat_key)
                best_signal_count = len(matched)
                best_signals = matched
                best_weight = weight

    return (best_category, best_name_en, best_name_zh, best_signals)


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

        classified.append({
            "text": text[:200],
            "likes": likes,
            "category": cat_key,
            "category_name": name_en,
            "category_name_zh": name_zh
        })

    n = len(comments)
    avg_score = total_weighted_score / n if n > 0 else 0

    interpretation = scoring.get("interpretation", {})
    if avg_score >= interpretation.get("high", {}).get("min_score", 2.0):
        intent_label = interpretation["high"].get("label_zh", interpretation["high"]["label"])
    elif avg_score >= interpretation.get("medium", {}).get("min_score", 1.0):
        intent_label = interpretation["medium"].get("label_zh", interpretation["medium"]["label"])
    else:
        intent_label = interpretation["low"].get("label_zh", interpretation["low"]["label"])

    # Get top 3 comments per category
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
    ]

    print("=" * 70)
    print("Comment Classification Test")
    print("=" * 70)

    for c in test_comments:
        cat_key, name_en, name_zh, signals = classify_single_comment(c["text"], config)
        print(f"\nComment: \"{c['text'][:60]}...\"")
        print(f"  Category: {cat_key} ({name_en} / {name_zh})")
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
        print(f"  {v['name_en']}: {v['count']} ({v['pct']}%)")
