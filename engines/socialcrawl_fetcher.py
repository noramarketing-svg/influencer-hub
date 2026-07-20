# -*- coding: utf-8 -*-
"""
评论抓取模块 — 主用 ScrapeCreators（性价比最优）
- Instagram: ScrapeCreators /v2/instagram/post/comments（cursor翻页，本地按赞排序）
- TikTok: ScrapeCreators /v1/tiktok/video/comments（cursor翻页，本地按赞排序）
- 备用: SocialCrawl（sort=top原生排序，但贵5倍）

策略：每视频至少翻2页，确保有效评论≥20条
"""
import json
import os
import re
import time
import hashlib
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "..", "cache")
COMMENT_CACHE_PATH = os.path.join(CACHE_DIR, "socialcrawl_comments.json")

# API Config
SOCIALCRAWL_API_KEY = os.environ.get(
    "SOCIALCRAWL_API_KEY",
    "sc_gPSfSMPuVBeP6Bp8IQEDoZ3fXTEQrTmEneNE8mupZSc"
)
SCRAPECREATORS_API_KEY = os.environ.get(
    "SCRAPECREATORS_API_KEY",
    "Yun1op59ONdU7wVDnbFKkZ5jWIG2"
)

# ScrapeCreators endpoints
SC_IG_COMMENTS = "https://api.scrapecreators.com/v2/instagram/post/comments"
SC_TK_COMMENTS = "https://api.scrapecreators.com/v1/tiktok/video/comments"

# SocialCrawl endpoints (备用)
SOCIALCRAWL_IG_ENDPOINT = "https://www.socialcrawl.dev/v1/instagram/post/comments"
SOCIALCRAWL_TK_ENDPOINT = "https://www.socialcrawl.dev/v1/tiktok/post/comments"


def load_comment_cache():
    """Load cached comment data"""
    if os.path.exists(COMMENT_CACHE_PATH):
        with open(COMMENT_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_comment_cache(data):
    """Save comment data to cache"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(COMMENT_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _video_key(video_url):
    """Generate cache key from video URL"""
    return hashlib.md5(video_url.encode()).hexdigest()[:12]


def remove_emojis(text):
    """Remove emoji characters from text"""
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE
    )
    return emoji_pattern.sub("", text)


def is_valid_comment(text):
    """Check if comment is valid (at least 2 alphanumeric chars after removing emoji and punctuation)"""
    if not text:
        return False
    cleaned = re.sub(r"[^\w\s]", "", remove_emojis(text), flags=re.UNICODE)
    alphanum = re.sub(r"[^a-zA-Z0-9]", "", cleaned)
    return len(alphanum) >= 2


# ============================================================
# ScrapeCreators — 主力抓取引擎
# ============================================================

def _scrapecreators_fetch(endpoint, video_url, target_valid=30, max_pages=10, max_retries=3, source_label="sc"):
    """
    通用 ScrapeCreators 翻页抓取函数

    Args:
        endpoint: API URL
        video_url: 视频链接
        target_valid: 目标有效评论数（达到即停止翻页）
        max_pages: 最多翻页数（安全上限，防止无限消耗 credits）
        source_label: 缓存来源标签

    Returns:
        List of comment dicts: [{text, likes, replies, username, timestamp, valid}, ...]
    """
    cache = load_comment_cache()
    key = _video_key(video_url)

    # Return cached if fresh
    if key in cache and cache[key].get("source") == source_label:
        cached_data = cache[key]
        if time.time() - cached_data.get("cached_at", 0) < 3600:
            print(f"[cache] Using cached comments for {key}")
            return cached_data.get("comments", [])

    headers = {"x-api-key": SCRAPECREATORS_API_KEY}
    all_comments = []
    cursor = None
    valid_count = 0
    should_stop = False

    for page in range(max_pages):
        if should_stop:
            break
        params = {"url": video_url}
        if cursor:
            params["cursor"] = cursor

        for attempt in range(max_retries):
            try:
                resp = requests.get(endpoint, headers=headers, params=params, timeout=30)

                if resp.status_code == 200:
                    data = resp.json()
                    credits_remaining = data.get("credits_remaining", "?")
                    items = data.get("comments", [])

                    for c in items:
                        text = c.get("text", "")
                        valid = is_valid_comment(text)
                        all_comments.append({
                            "text": text,
                            "likes": c.get("likes", c.get("digg_count", 0)),
                            "replies": c.get("reply_count", c.get("reply_comment_total", 0)),
                            "username": c.get("username", c.get("user", {}).get("username", "N/A")),
                            "timestamp": c.get("timestamp", c.get("create_time", "")),
                            "valid": valid,
                        })
                        if valid:
                            valid_count += 1

                    cursor = data.get("cursor")
                    # ScrapeCreators IG returns has_more=None even when cursor exists, 
                    # so use cursor presence as the "has more" indicator
                    has_more = bool(cursor)
                    print(f"[scrapecreators] Page {page+1}: {len(items)} comments, {valid_count} valid so far, credits_left={credits_remaining}")

                    # 有效评论数达标就停
                    if valid_count >= target_valid:
                        print(f"[scrapecreators] Reached target {target_valid} valid comments ({valid_count}), stopping at page {page+1}")
                        should_stop = True
                        break
                    if not has_more:
                        print(f"[scrapecreators] No more pages, stopping at page {page+1} (valid={valid_count}/{target_valid})")
                        should_stop = True
                        break
                    break  # Success, exit retry loop

                elif resp.status_code == 429:
                    print(f"[scrapecreators] Rate limited (attempt {attempt + 1})")
                    time.sleep(2 ** attempt)
                    continue
                else:
                    print(f"[scrapecreators] HTTP {resp.status_code}: {resp.text[:200]}")
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    break
            except requests.exceptions.Timeout:
                print(f"[scrapecreators] Timeout (attempt {attempt + 1})")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                break
            except Exception as e:
                print(f"[scrapecreators] Error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                break

        if not cursor or not has_more:
            break

    # Sort by likes descending
    all_comments.sort(key=lambda x: x["likes"], reverse=True)

    # Cache result
    cache[key] = {
        "source": source_label,
        "cached_at": time.time(),
        "comments": all_comments,
    }
    save_comment_cache(cache)

    print(f"[scrapecreators] Total: {len(all_comments)} comments, {valid_count} valid, pages={page+1}")
    return all_comments


def fetch_ig_comments_sc(video_url, target_valid=30, max_pages=10):
    """Fetch Instagram comments via ScrapeCreators (cursor pagination, stops when ≥target_valid valid comments)"""
    return _scrapecreators_fetch(
        SC_IG_COMMENTS, video_url,
        target_valid=target_valid, max_pages=max_pages,
        source_label="scrapecreators_ig"
    )


def fetch_tiktok_comments_sc(video_url, target_valid=30, max_pages=10):
    """Fetch TikTok comments via ScrapeCreators (cursor pagination, stops when ≥target_valid valid comments)"""
    return _scrapecreators_fetch(
        SC_TK_COMMENTS, video_url,
        target_valid=target_valid, max_pages=max_pages,
        source_label="scrapecreators_tk"
    )


# ============================================================
# SocialCrawl — 备用（sort=top原生排序，贵5倍）
# ============================================================

def fetch_ig_comments_socialcrawl(video_url, max_retries=3):
    """Fetch Instagram comments via SocialCrawl (sort=top, no pagination)"""
    cache = load_comment_cache()
    key = _video_key(video_url)

    if key in cache and cache[key].get("source") == "socialcrawl_ig":
        cached_data = cache[key]
        if time.time() - cached_data.get("cached_at", 0) < 3600:
            print(f"[cache] Using cached comments for {key}")
            return cached_data.get("comments", [])

    headers = {"x-api-key": SOCIALCRAWL_API_KEY}
    params = {"url": video_url, "sort": "top"}

    for attempt in range(max_retries):
        try:
            resp = requests.get(SOCIALCRAWL_IG_ENDPOINT, headers=headers, params=params, timeout=30)

            if resp.status_code == 200:
                data = resp.json()
                credits_used = data.get("credits_used", "?")
                credits_remaining = data.get("credits_remaining", "?")
                print(f"[socialcrawl] IG credits: used={credits_used}, remaining={credits_remaining}")

                d = data.get("data", {})
                items = d.get("items", []) if isinstance(d, dict) else d

                comments = []
                for it in items:
                    c = it.get("comment", it) if isinstance(it, dict) else it
                    text = c.get("text", "")
                    comments.append({
                        "text": text,
                        "likes": c.get("engagement", {}).get("likes", 0),
                        "replies": c.get("engagement", {}).get("replies", 0),
                        "username": c.get("author", {}).get("username", "N/A"),
                        "timestamp": c.get("published_at", ""),
                        "valid": is_valid_comment(text),
                    })

                comments.sort(key=lambda x: x["likes"], reverse=True)
                cache[key] = {"source": "socialcrawl_ig", "cached_at": time.time(), "comments": comments}
                save_comment_cache(cache)
                return comments

            elif resp.status_code == 402:
                print(f"[socialcrawl] No credits remaining")
                return []
            else:
                print(f"[socialcrawl] HTTP {resp.status_code}: {resp.text[:200]}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return []
        except Exception as e:
            print(f"[socialcrawl] Error: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return []
    return []


# ============================================================
# 路由：统一入口
# ============================================================

def fetch_comments_for_video(video_url, platform):
    """
    Fetch comments for a single video.
    Instagram → ScrapeCreators (cursor翻页，本地按赞排序)
    TikTok → ScrapeCreators (cursor翻页，本地按赞排序)
    """
    platform_lower = (platform or "").lower()

    if "instagram" in platform_lower or "ig" in platform_lower:
        return fetch_ig_comments_sc(video_url)
    elif "tiktok" in platform_lower or "tk" in platform_lower:
        return fetch_tiktok_comments_sc(video_url)
    else:
        print(f"[fetch] Unknown platform: {platform}")
        return []


def get_top_valid_comments(comments, top_n=30):
    """Get top N valid comments (default 30 to ensure enough for calibration)"""
    valid = [c for c in comments if c.get("valid", True)]
    return valid[:top_n]


# ============================================================
# 测试
# ============================================================
if __name__ == "__main__":
    # Test IG
    test_url = "https://www.instagram.com/p/Dawq5BwS0Pr/"
    print("=" * 60)
    print("Testing ScrapeCreators IG comment fetch...")
    print("=" * 60)

    comments = fetch_ig_comments_sc(test_url)
    if comments:
        valid = get_top_valid_comments(comments, 20)
        print(f"\nTotal: {len(comments)}, Valid: {len(valid)}")
        print(f"\nTop {len(valid)} valid comments:")
        for i, c in enumerate(valid, 1):
            print(f"  {i}. 👍{c['likes']} @{c['username']}: {c['text'][:80]}")
    else:
        print("No comments fetched")
