# -*- coding: utf-8 -*-
"""
Spanish Topic Classification Engine
Based on the existing 西语 IG 选题分类主脚本, adapted as a reusable module.

5 categories (same as English):
- 3C配件品牌赞助/种草
- Apple/iOS生态
- 其他品牌产品种草
- 科技资讯/教程技巧
- AI工具/生活观点

Level 1: Brand Filter (3C accessories vs other brands + context signals)
Level 1.5: Semantic Pattern Recognition (promotion narrative structure)
Level 2: Category Keyword Rules
Level 3: LLM Fallback (optional)
"""
import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "..", "configs", "spanish_category_config.json")


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def match_keyword_word_boundary(title_lower, kw):
    """Word boundary matching for brand names and short keywords (Spanish-aware)"""
    kw_lower = kw.lower().strip()
    if not kw_lower:
        return False
    if kw_lower.startswith('#'):
        return kw_lower in title_lower
    if ' ' in kw_lower or '-' in kw_lower:
        return kw_lower in title_lower
    # Spanish-aware word boundary: a-z + accented chars
    pattern = r'(?<![a-záéíóúñü])' + re.escape(kw_lower) + r'(?![a-záéíóúñü])'
    if re.search(pattern, title_lower):
        return True
    at_pattern = r'@' + re.escape(kw_lower)
    if re.search(at_pattern, title_lower):
        return True
    return False


def match_keyword_substring(title_lower, kw):
    return kw.lower().strip() in title_lower


def count_signal_matches(title_lower, signals):
    count = 0
    matched = []
    for sig in signals:
        if match_keyword_substring(title_lower, sig):
            count += 1
            matched.append(sig)
    return count, matched


# ============================================================
# Level 1: Brand Filter
# ============================================================
def level1_brand_filter(title, config):
    title_lower = (" " + title.lower() + " ").replace("\n", " ").replace("\r", " ")
    title_stripped = title.strip().lower()

    all_keywords = []

    # --- 1.1 Explicit sponsor markers (highest priority) ---
    explicit_markers = config.get("explicit_sponsor_markers", [])
    for marker in explicit_markers:
        if match_keyword_substring(title_lower, marker):
            all_keywords.append(marker)
            brands_3c = config.get("brands_3c_accessories", {})
            for brand_name, brand_info in brands_3c.items():
                if match_keyword_word_boundary(title_lower, brand_name):
                    all_keywords.append(brand_name)
                    return (brand_name, "3c", "3C配件品牌赞助/种草",
                            all_keywords, "规则:显式赞助标记+3C品牌")
            brands_other = config.get("brands_other", {})
            for brand_name, brand_info in brands_other.items():
                if match_keyword_word_boundary(title_lower, brand_name):
                    all_keywords.append(brand_name)
                    return (brand_name, "other", "其他品牌产品种草",
                            all_keywords, "规则:显式赞助标记+其他品牌")
            return (None, "unknown", "赞助广告内容",
                    all_keywords, "规则:显式赞助标记")

    # --- 1.2 "publi" prefix ---
    if config.get("publi_prefix", False):
        if title_stripped.startswith("publi"):
            brands_3c = config.get("brands_3c_accessories", {})
            for brand_name, brand_info in brands_3c.items():
                if match_keyword_word_boundary(title_lower, brand_name):
                    all_keywords.append(brand_name)
                    return (brand_name, "3c", "3C配件品牌赞助/种草",
                            all_keywords, "规则:Publi前缀+3C品牌")
            brands_other = config.get("brands_other", {})
            for brand_name, brand_info in brands_other.items():
                if match_keyword_word_boundary(title_lower, brand_name):
                    all_keywords.append(brand_name)
                    return (brand_name, "other", "其他品牌产品种草",
                            all_keywords, "规则:Publi前缀+其他品牌")
            return (None, "unknown", "赞助广告内容",
                    all_keywords, "规则:Publi前缀")

    # --- 1.3 3C accessory brands ---
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
                basis = "规则:3C品牌+种草信号" if review_count > 0 else "规则:3C品牌+赞助行为"
                return (brand_name, "3c", "3C配件品牌赞助/种草", all_keywords, basis)
            elif news_count > 0:
                return (brand_name, "3c", "科技资讯/教程技巧", all_keywords, "规则:3C品牌+新闻信号")
            else:
                return (brand_name, "3c", "3C配件品牌赞助/种草", all_keywords, "规则:3C品牌命中(默认种草)")

    # --- 1.4 Other brands ---
    brands_other = config.get("brands_other", {})
    for brand_name, brand_info in brands_other.items():
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
                return (brand_name, "other", "科技资讯/教程技巧", all_keywords, "规则:其他品牌+教程模式")
            elif review_count > 0 or sponsor_count > 0:
                basis = "规则:其他品牌+种草信号" if review_count > 0 else "规则:其他品牌+赞助行为"
                return (brand_name, "other", "其他品牌产品种草", all_keywords, basis)
            elif news_count > 0:
                return (brand_name, "other", "科技资讯/教程技巧", all_keywords, "规则:其他品牌+新闻信号")
            else:
                return (brand_name, "other", "其他品牌产品种草", all_keywords, "规则:其他品牌命中(默认种草)")

    # --- 1.5 No brand, check sponsor signals ---
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
            return (None, "unknown", "赞助广告内容", all_keywords, "规则:多条赞助行为信号")

    return (None, None, None, all_keywords, None)


# ============================================================
# Level 1.5: Semantic Pattern Recognition
# ============================================================
def level1_5_promotion_patterns(title, config):
    title_lower = (" " + title.lower() + " ").replace("\n", " ").replace("\r", " ")

    promotion_patterns = config.get("promotion_patterns", {})
    tutorial_patterns = config.get("tutorial_patterns", [])

    matched_patterns = []
    promotion_score = 0
    tutorial_score = 0

    # 1. Problem → Solution
    problem_solution = promotion_patterns.get("problem_solution", [])
    ps_count, ps_matched = count_signal_matches(title_lower, problem_solution)
    if ps_count >= 1:
        promotion_score += ps_count
        matched_patterns.extend(ps_matched)

    # 2. Feature list (✔️/✅)
    feature_list = promotion_patterns.get("feature_list", [])
    fl_count, fl_matched = count_signal_matches(title_lower, feature_list)
    if fl_count >= 2:
        promotion_score += fl_count
        matched_patterns.extend(fl_matched)

    # 3. Purchase intent
    purchase_intent = promotion_patterns.get("purchase_intent", [])
    pi_count, pi_matched = count_signal_matches(title_lower, purchase_intent)
    if pi_count >= 1:
        promotion_score += pi_count * 2
        matched_patterns.extend(pi_matched)

    # 4. Personal review
    personal_review = promotion_patterns.get("personal_review", [])
    pr_count, pr_matched = count_signal_matches(title_lower, personal_review)
    if pr_count >= 1:
        promotion_score += pr_count
        matched_patterns.extend(pr_matched)

    # 5. Recommendation list
    recommendation_list = promotion_patterns.get("recommendation_list", [])
    rl_count, rl_matched = count_signal_matches(title_lower, recommendation_list)
    if rl_count >= 1:
        promotion_score += rl_count
        matched_patterns.extend(rl_matched)

    # 6. Tutorial pattern detection
    tl_count, tl_matched = count_signal_matches(title_lower, tutorial_patterns)
    if tl_count >= 1:
        tutorial_score += tl_count

    # Decision
    if promotion_score >= 3 and promotion_score > tutorial_score:
        # Check 3C brand
        brands_3c = config.get("brands_3c_accessories", {})
        for brand_name in brands_3c.keys():
            if match_keyword_word_boundary(title_lower, brand_name):
                return ("3C配件品牌赞助/种草", matched_patterns[:8], "规则:推广叙事模式+3C品牌")

        # Apple content priority check
        apple_keywords = config.get("category_keywords", {}).get("Apple/iOS生态", [])
        apple_matched = []
        for kw in apple_keywords:
            if match_keyword_word_boundary(title_lower, kw):
                apple_matched.append(kw)
        if len(apple_matched) >= 2:
            matched_patterns.extend(apple_matched[:5])
            return ("Apple/iOS生态", matched_patterns[:8], "规则:推广叙事模式+Apple内容")

        return ("其他品牌产品种草", matched_patterns[:8], "规则:推广叙事模式")

    if tutorial_score >= 2 and tutorial_score > promotion_score:
        return ("科技资讯/教程技巧", tl_matched[:5], "规则:教程模式")

    return (None, [], None)


# ============================================================
# Level 2: Category Keyword Rules
# ============================================================
def level2_category_keywords(title, config):
    title_lower = (" " + title.lower() + " ").replace("\n", " ").replace("\r", " ")

    category_keywords = config.get("category_keywords", {})
    priority_order = ["Apple/iOS生态", "AI工具/生活观点", "科技资讯/教程技巧"]

    for cat_name in priority_order:
        keywords = category_keywords.get(cat_name, [])
        matched = []
        for kw in keywords:
            if match_keyword_word_boundary(title_lower, kw):
                matched.append(kw)
        if matched:
            return (cat_name, matched, "规则:关键词匹配")

    return (None, [], None)


# ============================================================
# Level 3: LLM Fallback (stub)
# ============================================================
def level3_llm_fallback(title, config):
    llm_config = config.get("llm_config", {})
    if not llm_config.get("enabled", False):
        return (None, [], None)
    return (None, [], None)


# ============================================================
# Main classification function
# ============================================================
def classify_title_es(title, config=None):
    """
    Classify a Spanish title into one of 5 categories.

    Args:
        title: Spanish text title
        config: Optional pre-loaded config dict

    Returns:
        (brand, keywords_str, category, basis)
    """
    if not title or not title.strip():
        return ("", "", "其他", "")

    if config is None:
        config = load_config()

    # Level 1: Brand Filter
    brand, brand_group, category, keywords, basis = level1_brand_filter(title, config)
    if category:
        # Merge "赞助广告内容" into "其他品牌产品种草"
        if category == "赞助广告内容":
            category = "其他品牌产品种草"
            basis = basis.replace("赞助广告", "其他品牌种草")
        seen = set()
        unique_kw = []
        for k in keywords:
            if k.lower() not in seen:
                seen.add(k.lower())
                unique_kw.append(k)
        return (brand or "", ", ".join(unique_kw[:10]), category, basis)

    # Level 1.5: Semantic Pattern Recognition
    category, keywords, basis = level1_5_promotion_patterns(title, config)
    if category:
        return ("", ", ".join(keywords[:10]), category, basis)

    # Level 2: Category Keywords
    category, keywords, basis = level2_category_keywords(title, config)
    if category:
        return ("", ", ".join(keywords[:10]), category, basis)

    # Level 3: LLM Fallback
    category, keywords, basis = level3_llm_fallback(title, config)
    if category:
        return ("", ", ".join(keywords[:10]), category, basis)

    return ("", "", "其他", "未命中任何规则")
