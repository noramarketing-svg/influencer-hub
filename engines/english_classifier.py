# -*- coding: utf-8 -*-
"""
English Topic Classification Engine
Based on the Spanish 5-level pipeline logic, adapted for English tech content.

Level 1: Brand Filter (3C accessories vs other brands + context signals)
Level 1.5: Semantic Pattern Recognition (promotion narrative structure)
Level 2: Category Keyword Rules
Level 3: LLM Fallback (optional)
"""
import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "..", "configs", "english_category_config.json")


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def match_keyword_word_boundary(title_lower, kw):
    """Word boundary matching for brand names and short keywords"""
    kw_lower = kw.lower().strip()
    if not kw_lower:
        return False
    if kw_lower.startswith('#'):
        return kw_lower in title_lower
    if ' ' in kw_lower or '-' in kw_lower:
        return kw_lower in title_lower
    pattern = r'(?<![a-z])' + re.escape(kw_lower) + r'(?![a-z])'
    if re.search(pattern, title_lower):
        return True
    at_pattern = r'@' + re.escape(kw_lower)
    if re.search(at_pattern, title_lower):
        return True
    return False


def match_keyword_substring(title_lower, kw):
    return kw.lower().strip() in title_lower


def match_sponsor_marker(title_lower, marker):
    """Word boundary matching for sponsor markers like 'ad' to avoid substring false positives"""
    marker_lower = marker.lower().strip()
    if not marker_lower:
        return False
    # For short markers like "ad", use word boundary
    if len(marker_lower) <= 3:
        pattern = r'(?<![a-z])' + re.escape(marker_lower) + r'(?![a-z])'
        return bool(re.search(pattern, title_lower))
    return marker_lower in title_lower


def count_signal_matches(title_lower, signals):
    count = 0
    matched = []
    for sig in signals:
        if match_keyword_substring(title_lower, sig):
            count += 1
            matched.append(sig)
    return count, matched


def count_sponsor_marker_matches(title_lower, markers):
    """Count sponsor markers using word boundary matching for short markers"""
    count = 0
    matched = []
    for marker in markers:
        if match_sponsor_marker(title_lower, marker):
            count += 1
            matched.append(marker)
    return count, matched


def is_apple_ecosystem(title_lower, config):
    """Check if title contains strong Apple ecosystem signals"""
    apple_keywords = config.get("apple_ecosystem_keywords", [])
    matched = []
    for kw in apple_keywords:
        if match_keyword_word_boundary(title_lower, kw):
            matched.append(kw)
    return matched


def level1_brand_filter(title, config):
    """Level 1: Brand-based classification with context signals"""
    title_lower = (" " + title.lower() + " ").replace("\n", " ").replace("\r", " ")
    title_stripped = title.strip().lower()
    all_keywords = []

    # 0. Apple Ecosystem Pre-filter: If title contains Apple product keywords, classify as Apple/iOS Ecosystem
    # This prevents Apple content from being misclassified as "Other Brand" due to brand name matching
    apple_matched = is_apple_ecosystem(title_lower, config)
    if apple_matched:
        # Check if there's an explicit sponsor marker + another brand that suggests non-Apple sponsorship
        explicit_markers = config.get("explicit_sponsor_markers", []) + config.get("explicit_sponsor_markers_word_boundary", [])
        sponsor_count, sponsor_matched = count_sponsor_marker_matches(title_lower, explicit_markers)
        
        # Check if a non-Apple brand is the main subject (appears before Apple in the title, NOT just an @mention)
        # Also check brands_3c which may contain brands like GoPro, Insta360
        all_other_brands = {}
        all_other_brands.update(config.get("brands_other", {}))
        all_other_brands.update(config.get("brands_3c_accessories", {}))
        
        non_apple_brand = None
        for brand_name, brand_info in all_other_brands.items():
            if brand_name not in ("apple", "dji") and match_keyword_word_boundary(title_lower, brand_name):
                brand_pos = title_lower.find(" " + brand_name)
                if brand_pos < 0:
                    brand_pos = title_lower.find(brand_name)
                # Find position of any Apple keyword
                apple_pos = min([title_lower.find(kw) for kw in apple_matched if title_lower.find(kw) >= 0] + [99999])
                # Only treat as non-Apple main subject if brand appears BEFORE Apple keywords
                # AND the brand is not just an @mention (check if there's content before the brand)
                if brand_pos >= 0 and brand_pos < apple_pos:
                    # Check if it's just an @mention at the end (no product context after brand)
                    context_after = title_lower[brand_pos + len(brand_name):brand_pos + len(brand_name) + 30]
                    # If brand is just an @mention and Apple keywords are the main subject, skip
                    is_at_mention = title_lower.startswith('@' + brand_name, brand_pos - 1)
                    if not is_at_mention or sponsor_count >= 1:
                        non_apple_brand = brand_name
                        break
        
        # If there's a sponsor marker AND a non-Apple brand is the main subject
        if sponsor_count >= 1 and non_apple_brand:
            all_keywords.extend(sponsor_matched)
            all_keywords.extend(apple_matched[:3])
            return (non_apple_brand, "other", "Other Brand Product Review",
                    all_keywords, "Rule: Explicit sponsor + non-Apple brand as main subject")
        
        # If a non-Apple brand is the main subject but no sponsor marker, it's a comparison/review (Tech News)
        # Only trigger this if the brand is truly the main subject (not just @mention)
        if non_apple_brand and not sponsor_count:
            all_keywords.extend(apple_matched[:3])
            all_keywords.append(non_apple_brand)
            return (non_apple_brand, "other", "科技资讯/教程技巧",
                    all_keywords, "Rule: Non-Apple brand as main subject + Apple comparison")
        
        # Otherwise, Apple ecosystem takes priority
        all_keywords.extend(apple_matched[:5])
        return ("apple", "apple", "Apple/iOS生态", all_keywords, "Rule: Apple ecosystem keyword match")

    # 1. Explicit sponsor markers
    explicit_markers = config.get("explicit_sponsor_markers", []) + config.get("explicit_sponsor_markers_word_boundary", [])
    sponsor_count, sponsor_matched = count_sponsor_marker_matches(title_lower, explicit_markers)
    if sponsor_count >= 1:
        all_keywords.extend(sponsor_matched)
        brands_3c = config.get("brands_3c_accessories", {})
        for brand_name, brand_info in brands_3c.items():
            if match_keyword_word_boundary(title_lower, brand_name):
                all_keywords.append(brand_name)
                return (brand_name, "3c", "3C Accessories Brand Sponsor/Review",
                        all_keywords, "Rule: Explicit sponsor marker + 3C brand")
        brands_other = config.get("brands_other", {})
        for brand_name, brand_info in brands_other.items():
            if match_keyword_word_boundary(title_lower, brand_name):
                all_keywords.append(brand_name)
                return (brand_name, "other", "Other Brand Product Review",
                        all_keywords, "Rule: Explicit sponsor marker + other brand")
        return (None, "unknown", "Sponsored Content",
                all_keywords, "Rule: Explicit sponsor marker")

    # 2. Brand name identification + context
    brands_3c = config.get("brands_3c_accessories", {})
    for brand_name, brand_info in brands_3c.items():
        if match_keyword_word_boundary(title_lower, brand_name):
            all_keywords.append(brand_name)
            for kw in brand_info.get("keywords", []):
                if match_keyword_substring(title_lower, kw):
                    all_keywords.append(kw)

            review_signals = config.get("review_signals", [])
            news_signals = config.get("news_signals", [])
            sponsor_signals = config.get("sponsor_signals", [])

            review_count, review_matched = count_signal_matches(title_lower, review_signals)
            news_count, news_matched = count_signal_matches(title_lower, news_signals)
            sponsor_count, sponsor_matched = count_signal_matches(title_lower, sponsor_signals)

            all_keywords.extend(review_matched)
            all_keywords.extend(news_matched)
            all_keywords.extend(sponsor_matched)

            if review_count > 0 or sponsor_count > 0:
                basis = "Rule: 3C brand + review signal" if review_count > 0 else "Rule: 3C brand + sponsor signal"
                return (brand_name, "3c", "3C Accessories Brand Sponsor/Review", all_keywords, basis)
            elif news_count > 0:
                return (brand_name, "3c", "Tech News & Tutorials", all_keywords, "Rule: 3C brand + news signal")
            else:
                return (brand_name, "3c", "3C Accessories Brand Sponsor/Review", all_keywords, "Rule: 3C brand (default review)")

    brands_other = config.get("brands_other", {})
    # Skip platform brands (Google/Facebook/Instagram/YouTube are not "products to review")
    platform_brands = {"google", "facebook", "instagram", "youtube", "whatsapp", "tiktok", "twitter", "snapchat"}
    for brand_name, brand_info in brands_other.items():
        if brand_name in platform_brands:
            continue
        if match_keyword_word_boundary(title_lower, brand_name):
            all_keywords.append(brand_name)
            for kw in brand_info.get("keywords", []):
                if match_keyword_substring(title_lower, kw):
                    all_keywords.append(kw)

            review_signals = config.get("review_signals", [])
            news_signals = config.get("news_signals", [])
            sponsor_signals = config.get("sponsor_signals", [])
            tutorial_patterns = config.get("tutorial_patterns", [])

            review_count, review_matched = count_signal_matches(title_lower, review_signals)
            news_count, news_matched = count_signal_matches(title_lower, news_signals)
            sponsor_count, sponsor_matched = count_signal_matches(title_lower, sponsor_signals)
            tutorial_count, tutorial_matched = count_signal_matches(title_lower, tutorial_patterns)

            all_keywords.extend(review_matched)
            all_keywords.extend(news_matched)
            all_keywords.extend(sponsor_matched)
            all_keywords.extend(tutorial_matched)

            if tutorial_count >= 2:
                return (brand_name, "other", "Tech News & Tutorials", all_keywords, "Rule: Other brand + tutorial pattern")
            elif review_count > 0 or sponsor_count > 0:
                basis = "Rule: Other brand + review signal" if review_count > 0 else "Rule: Other brand + sponsor signal"
                return (brand_name, "other", "Other Brand Product Review", all_keywords, basis)
            elif news_count > 0:
                return (brand_name, "other", "Tech News & Tutorials", all_keywords, "Rule: Other brand + news signal")
            else:
                return (brand_name, "other", "Other Brand Product Review", all_keywords, "Rule: Other brand (default review)")

    # 3. Sponsor behavior signals (no brand)
    sponsor_signals = config.get("sponsor_signals", [])
    sponsor_count, sponsor_matched = count_signal_matches(title_lower, sponsor_signals)
    if sponsor_count >= 2:
        category_keywords = config.get("category_keywords", {})
        strong_match = False
        for cat_name, keywords in category_keywords.items():
            for kw in keywords:
                if match_keyword_word_boundary(title_lower, kw):
                    strong_match = True
                    break
            if strong_match:
                break
        if not strong_match:
            all_keywords.extend(sponsor_matched)
            return (None, "unknown", "Sponsored Content", all_keywords, "Rule: Multiple sponsor signals")

    return (None, None, None, all_keywords, None)


# Category name mapping for Chinese display
CATEGORY_NAME_MAP = {
    "3C Accessories Brand Sponsor/Review": "3C配件品牌赞助/种草",
    "Apple/iOS Ecosystem": "Apple/iOS生态",
    "Other Brand Product Review": "其他品牌产品种草",
    "Tech News & Tutorials": "科技资讯/教程技巧",
    "AI Tools & Tech Lifestyle": "AI工具/生活观点",
    "Sponsored Content": "赞助广告内容",
    "Other": "其他",
}


def get_category_name(category_en):
    """Get Chinese category name from English name"""
    return CATEGORY_NAME_MAP.get(category_en, category_en)


def level1_5_promotion_patterns(title, config):
    """Level 1.5: Semantic pattern recognition for promotion narrative"""
    title_lower = (" " + title.lower() + " ").replace("\n", " ").replace("\r", " ")
    promotion_patterns = config.get("promotion_patterns", {})
    tutorial_patterns = config.get("tutorial_patterns", [])

    matched_patterns = []
    promotion_score = 0
    tutorial_score = 0

    problem_solution = promotion_patterns.get("problem_solution", [])
    ps_count, ps_matched = count_signal_matches(title_lower, problem_solution)
    if ps_count >= 1:
        promotion_score += ps_count
        matched_patterns.extend(ps_matched)

    feature_list = promotion_patterns.get("feature_list", [])
    fl_count, fl_matched = count_signal_matches(title_lower, feature_list)
    if fl_count >= 2:
        promotion_score += fl_count
        matched_patterns.extend(fl_matched)

    purchase_intent = promotion_patterns.get("purchase_intent", [])
    pi_count, pi_matched = count_signal_matches(title_lower, purchase_intent)
    if pi_count >= 1:
        promotion_score += pi_count * 2
        matched_patterns.extend(pi_matched)

    personal_review = promotion_patterns.get("personal_review", [])
    pr_count, pr_matched = count_signal_matches(title_lower, personal_review)
    if pr_count >= 1:
        promotion_score += pr_count
        matched_patterns.extend(pr_matched)

    recommendation_list = promotion_patterns.get("recommendation_list", [])
    rl_count, rl_matched = count_signal_matches(title_lower, recommendation_list)
    if rl_count >= 1:
        promotion_score += rl_count
        matched_patterns.extend(rl_matched)

    tl_count, tl_matched = count_signal_matches(title_lower, tutorial_patterns)
    if tl_count >= 1:
        tutorial_score += tl_count

    if promotion_score >= 4 and promotion_score > tutorial_score:
        brands_3c = config.get("brands_3c_accessories", {})
        for brand_name in brands_3c.keys():
            if match_keyword_word_boundary(title_lower, brand_name):
                return ("3C Accessories Brand Sponsor/Review", matched_patterns[:8], "Rule: Promotion narrative + 3C brand")

        apple_keywords = config.get("category_keywords", {}).get("Apple/iOS Ecosystem", [])
        apple_matched = []
        for kw in apple_keywords:
            if match_keyword_word_boundary(title_lower, kw):
                apple_matched.append(kw)
        if len(apple_matched) >= 2:
            matched_patterns.extend(apple_matched[:5])
            return ("Apple/iOS Ecosystem", matched_patterns[:8], "Rule: Promotion narrative + Apple content")

        return ("Other Brand Product Review", matched_patterns[:8], "Rule: Promotion narrative pattern")

    if tutorial_score >= 2 and tutorial_score > promotion_score:
        return ("Tech News & Tutorials", tl_matched[:5], "Rule: Tutorial pattern")

    return (None, [], None)


def level2_category_keywords(title, config):
    """Level 2: Category keyword rules"""
    title_lower = (" " + title.lower() + " ").replace("\n", " ").replace("\r", " ")
    category_keywords = config.get("category_keywords", {})
    priority_order = ["Apple/iOS Ecosystem", "AI Tools & Tech Lifestyle", "Tech News & Tutorials"]

    for cat_name in priority_order:
        keywords = category_keywords.get(cat_name, [])
        matched = []
        for kw in keywords:
            if match_keyword_word_boundary(title_lower, kw):
                matched.append(kw)
        if matched:
            return (cat_name, matched, "Rule: Keyword match")

    return (None, [], None)


def classify_title_en(title, config=None):
    """Main classification function for English titles.
    Returns: (brand, keywords_str, category, basis)
    """
    if config is None:
        config = load_config()

    if not title or not title.strip():
        return ("", "", "其他", "Empty title")

    # Level 1: Brand filter
    brand, brand_group, category, keywords, basis = level1_brand_filter(title, config)
    if category:
        if category == "Sponsored Content":
            category = "Other Brand Product Review"
            basis = basis.replace("Sponsored Content", "Other Brand Review")
        seen = set()
        unique_kw = []
        for k in keywords:
            if k.lower() not in seen:
                seen.add(k.lower())
                unique_kw.append(k)
        category_zh = get_category_name(category)
        return (brand or "", ", ".join(unique_kw[:10]), category_zh, basis)

    # Level 1.5: Semantic patterns
    category, keywords, basis = level1_5_promotion_patterns(title, config)
    if category:
        category_zh = get_category_name(category)
        return ("", ", ".join(keywords[:10]), category_zh, basis)

    # Level 2: Category keywords
    category, keywords, basis = level2_category_keywords(title, config)
    if category:
        category_zh = get_category_name(category)
        return ("", ", ".join(keywords[:10]), category_zh, basis)

    return ("", "", "其他", "No rule matched")


if __name__ == "__main__":
    config = load_config()
    test_titles = [
        "I tested the new Anker Prime 20,000mAh power bank - here's my honest review",
        "iOS 27 beta 3 is here - every new feature explained",
        "ChatGPT vs Gemini - which AI tool is actually better?",
        "How to clean install macOS 27 on your MacBook",
        "This $300 Samsung phone is actually amazing - Galaxy A56 review",
        "I built a startup in 30 days using only AI tools",
        "The problem with every phone case on Amazon... until I found this",
        "Unboxing the new iPhone 17 Pro Max - first impressions",
    ]

    print("=" * 70)
    print("English Topic Classification - Test Results")
    print("=" * 70)
    for title in test_titles:
        brand, keywords, category, basis = classify_title_en(title, config)
        print(f"\nTitle: {title[:80]}...")
        print(f"  Category: {category}")
        print(f"  Brand: {brand}")
        print(f"  Keywords: {keywords}")
        print(f"  Basis: {basis}")
