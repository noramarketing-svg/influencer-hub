# -*- coding: utf-8 -*-
"""
Apify 数据抓取模块
用于从 Instagram/TikTok 抓取达人视频数据（标题、发布日期、链接、评论数）

支持的 Actor:
- Instagram: apify/instagram-profile-scraper (或 apify/instagram-hashtag-scraper)
- TikTok: apify/tiktok-profile-scraper (或 apify/tiktok-hashtag-scraper)

需要 Apify API Token (在 https://console.apify.com/settings/integrations 获取)
"""
import json
import os
import time
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "..", "cache")

# Apify API base URL
APIFY_BASE = "https://api.apify.com/v2"

# Default Actor IDs (已验证可用)
ACTOR_IG_PROFILE = "Y5mzw9TLFReI0d6gQ"  # sones/instagram-posts-scraper-lowcost (已验证 @letsdodizz 24条)
ACTOR_IG_HASHTAG = "Y5mzw9TLFReI0d6gQ"  # 同上
ACTOR_TK_PROFILE = "GdWCkxBtKWOsKjdch"  # clockworks/tiktok-scraper
ACTOR_TK_HASHTAG = "clockworks/tiktok-hashtag-scraper"

# API Key (可在运行时覆盖)
DEFAULT_API_KEY = os.environ.get("APIFY_API_KEY", "apify_api_lNbEszC31JbjeynU2JiEVpEE4WA0JO2IyFt4")


def _call_apify_actor(actor_id, input_data, api_token, timeout=180):
    """
    Call an Apify Actor and wait for completion.
    Returns the dataset items.
    """
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}

    # 1. Start the actor run
    run_url = f"{APIFY_BASE}/acts/{actor_id}/runs"
    run_resp = requests.post(run_url, headers=headers, json=input_data, timeout=30)

    if run_resp.status_code != 201:
        raise Exception(f"Apify run failed: HTTP {run_resp.status_code} - {run_resp.text[:300]}")

    run_data = run_resp.json()
    run_id = run_data.get("data", {}).get("id", "")
    if not run_id:
        raise Exception(f"No run ID in response: {json.dumps(run_data, indent=2)[:500]}")

    print(f"[apify] Run started: {run_id}")

    # 2. Poll for completion
    status_url = f"{APIFY_BASE}/acts/{actor_id}/runs/{run_id}"
    start_time = time.time()

    while time.time() - start_time < timeout:
        status_resp = requests.get(status_url, headers=headers, timeout=15)
        if status_resp.status_code != 200:
            raise Exception(f"Apify status check failed: HTTP {status_resp.status_code}")

        status_data = status_resp.json()
        status = status_data.get("data", {}).get("status", "UNKNOWN")
        print(f"[apify] Status: {status} ({int(time.time() - start_time)}s elapsed)")

        if status == "SUCCEEDED":
            break
        elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise Exception(f"Apify run {status}: {json.dumps(status_data, indent=2)[:500]}")

        time.sleep(5)
    else:
        raise Exception(f"Apify run timed out after {timeout}s")

    # 3. Fetch dataset items
    dataset_id = status_data.get("data", {}).get("defaultDatasetId", "")
    if not dataset_id:
        raise Exception(f"No dataset ID in run data")

    items_url = f"{APIFY_BASE}/datasets/{dataset_id}/items"
    items_resp = requests.get(items_url, headers=headers, timeout=30)
    if items_resp.status_code != 200:
        raise Exception(f"Apify dataset fetch failed: HTTP {items_resp.status_code}")

    items = items_resp.json()
    print(f"[apify] Fetched {len(items)} items from dataset {dataset_id}")
    return items


def fetch_instagram_videos(username, api_token, days=30, actor_id=None):
    """
    Fetch Instagram videos for a profile.

    Args:
        username: Instagram username (without @)
        api_token: Apify API token
        days: Look back period
        actor_id: Override the default actor

    Returns:
        List of dicts: [{标题, 发布日期, 视频链接, 评论数}, ...]
    """
    if actor_id is None:
        actor_id = ACTOR_IG_PROFILE

    input_data = {
        "usernames": [username],
        "resultsLimit": 50,
        "maxPosts": 50,
    }

    items = _call_apify_actor(actor_id, input_data, api_token)

    videos = []
    cutoff_date = None
    if days > 0:
        from datetime import datetime, timedelta
        cutoff_date = datetime.now() - timedelta(days=days)

    for item in items:
        # Normalize fields from sones/instagram-posts-scraper-lowcost output
        # caption is a dict: {"pk": "...", "text": "..."}
        caption_data = item.get("caption", {})
        title = caption_data.get("text", "") if isinstance(caption_data, dict) else str(caption_data or "")

        # Date: taken_at is unix timestamp
        date_str = item.get("taken_at", item.get("timestamp", "")) or ""
        date_formatted = ""
        if date_str:
            try:
                from datetime import datetime as dt
                if isinstance(date_str, (int, float)):
                    d = dt.fromtimestamp(date_str)
                else:
                    d = dt.fromisoformat(str(date_str).replace("Z", "+00:00"))
                date_formatted = d.strftime("%Y-%m-%d")
                # Apply cutoff
                if cutoff_date and d.replace(tzinfo=None) < cutoff_date:
                    continue
            except:
                date_formatted = str(date_str)[:10]

        # URL: use post_url field
        url = item.get("post_url", item.get("url", item.get("shortcode", "")))
        if url and not url.startswith("http"):
            url = f"https://www.instagram.com/p/{url}/"

        # Comment count: comment_count field
        comments_count = item.get("comment_count", item.get("commentsCount", 0)) or 0

        videos.append({
            "标题": title,
            "发布日期": date_formatted,
            "视频链接": url,
            "评论数": int(comments_count),
        })

    print(f"[apify] IG: {len(videos)} videos from @{username} (last {days} days)")
    return videos


def fetch_tiktok_videos(username, api_token, days=30, actor_id=None):
    """
    Fetch TikTok videos for a profile.

    Args:
        username: TikTok username (without @)
        api_token: Apify API token
        days: Look back period
        actor_id: Override the default actor

    Returns:
        List of dicts: [{标题, 发布日期, 视频链接, 评论数}, ...]
    """
    if actor_id is None:
        actor_id = ACTOR_TK_PROFILE

    input_data = {
        "profiles": [username],
        "resultsPerPage": 50,
        "maxPosts": 50,
    }

    items = _call_apify_actor(actor_id, input_data, api_token)

    videos = []
    cutoff_date = None
    if days > 0:
        from datetime import datetime, timedelta
        cutoff_date = datetime.now() - timedelta(days=days)

    for item in items:
        title = item.get("text", item.get("desc", item.get("title", ""))) or ""
        date_str = item.get("createTime", item.get("create_time", "")) or ""

        date_formatted = ""
        if date_str:
            try:
                from datetime import datetime as dt
                if isinstance(date_str, (int, float)):
                    d = dt.fromtimestamp(date_str)
                else:
                    d = dt.fromisoformat(date_str.replace("Z", "+00:00"))
                date_formatted = d.strftime("%Y-%m-%d")
                if cutoff_date and d.replace(tzinfo=None) < cutoff_date:
                    continue
            except:
                date_formatted = str(date_str)[:10]

        video_id = item.get("id", item.get("video_id", ""))
        url = item.get("webVideoUrl", item.get("url", ""))
        if not url and video_id:
            url = f"https://www.tiktok.com/@{username}/video/{video_id}"

        comments_count = item.get("commentCount", item.get("comments_count", 0)) or 0

        videos.append({
            "标题": title,
            "发布日期": date_formatted,
            "视频链接": url,
            "评论数": int(comments_count),
        })

    print(f"[apify] TK: {len(videos)} videos from @{username} (last {days} days)")
    return videos


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python apify_fetcher.py <platform:ig|tk> <username> <api_token> [days]")
        sys.exit(1)

    platform = sys.argv[1]
    username = sys.argv[2]
    api_token = sys.argv[3]
    days = int(sys.argv[4]) if len(sys.argv) > 4 else 30

    if platform.lower() in ("ig", "instagram"):
        videos = fetch_instagram_videos(username, api_token, days)
    else:
        videos = fetch_tiktok_videos(username, api_token, days)

    print(f"\nFetched {len(videos)} videos:")
    for v in videos[:5]:
        print(f"  [{v['发布日期']}] {v['标题'][:80]}...")
        print(f"    {v['视频链接']} | comments={v['评论数']}")
