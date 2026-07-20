# -*- coding: utf-8 -*-
"""
达人分析系统前端 - Flask 自包含版本
参考旧版排版，支持任意达人输入
数据流: Apify(标题) + SocialCrawl(评论) → english_classifier + comment_analyzer → 前端
"""
import json, os, re, sys, time, hashlib, threading
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "engines"))

from english_classifier import classify_title_en, load_config as load_en_config
from spanish_classifier import classify_title_es, load_config as load_es_config
from comment_analyzer import classify_single_comment, analyze_video_comments, analyze_account_comments, load_config as load_comment_config
from socialcrawl_fetcher import fetch_ig_comments_sc, fetch_tiktok_comments_sc, get_top_valid_comments, fetch_comments_for_video

CAT_COLORS = {
    "3C配件品牌赞助/种草": "#F8CBAD",
    "Apple/iOS生态": "#DDEBF7",
    "其他品牌产品种草": "#FCE4D6",
    "科技资讯/教程技巧": "#D9E1F2",
    "AI工具/生活观点": "#E2EFDA",
    "赞助广告内容": "#F4CCCC",
    "其他": "#F2F2F2",
}

COMMENT_CAT_NAMES = {
    "purchase_inquiry": "产品咨询",
    "purchase_intent": "购买意向",
    "product_discussion": "产品讨论",
    "positive_feedback": "正向反馈",
    "negative_feedback": "负向反馈",
    "social_engagement": "社交互动",
    "other": "其他",
}

COMMENT_CAT_CSS = {
    "purchase_inquiry": "c-inquiry",
    "purchase_intent": "c-intent",
    "product_discussion": "c-discuss",
    "positive_feedback": "c-positive",
    "negative_feedback": "c-negative",
    "social_engagement": "c-social",
    "other": "",
}

SPONSORED_CATEGORIES = ["3C配件品牌赞助/种草", "其他品牌产品种草", "赞助广告内容"]

# ============================================================
# 达人资源总库 — 索引 + 增量更新
# ============================================================
CACHE_DIR = os.path.join(BASE_DIR, "cache")
INDEX_PATH = os.path.join(CACHE_DIR, "influencer_index.json")

def load_influencer_index():
    """加载达人资源总库索引"""
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"instagram": {}, "tiktok": {}}

def save_influencer_index(index):
    """保存达人资源总库索引"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

def update_index(username, platform, videos):
    """更新达人索引"""
    index = load_influencer_index()
    pf = platform.lower()
    if pf not in index:
        index[pf] = {}
    
    dates = [v.get("发布日期", "") for v in videos if v.get("发布日期")]
    dates.sort()
    index[pf][username] = {
        "last_fetch": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "video_count": len(videos),
        "newest_video_date": dates[-1] if dates else "",
        "oldest_video_date": dates[0] if dates else "",
    }
    save_influencer_index(index)

def merge_videos(cached, new):
    """按视频链接去重合并，新数据覆盖旧数据"""
    merged = {v.get("视频链接", v.get("url", "")): v for v in cached}
    for v in new:
        key = v.get("视频链接", v.get("url", ""))
        if key:
            merged[key] = v  # 新数据覆盖
    result = list(merged.values())
    result.sort(key=lambda v: v.get("发布日期", ""), reverse=True)
    return result

def needs_apify_refresh(username, platform, days):
    """
    判断是否需要 Apify 补抓
    返回: (need_refresh, cached_videos, message)
    """
    cached = load_cached_videos(username, platform)
    if not cached:
        return (True, [], "无历史缓存，需全量抓取")
    
    dates = [v.get("发布日期", "") for v in cached if v.get("发布日期")]
    if not dates:
        return (True, cached, "缓存数据无日期，需重新抓取")
    
    newest = max(dates)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    if newest >= cutoff:
        # 缓存覆盖了请求的天数范围
        filtered = [v for v in cached if v.get("发布日期", "") >= cutoff]
        return (False, filtered, f"✅ 缓存最新（最新视频 {newest}），共 {len(filtered)} 条")
    else:
        return (True, cached, f"⚠️ 缓存数据截止 {newest}，需补充最新视频")

def load_cached_videos(username, platform):
    cache_dir = os.path.join(CACHE_DIR, platform.lower())
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"{username}_videos.json")
    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_cached_videos(username, platform, videos):
    cache_dir = os.path.join(CACHE_DIR, platform.lower())
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"{username}_videos.json")
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)
    update_index(username, platform, videos)


def extract_username(text, platform):
    text = text.strip()
    if "tiktok.com" in text:
        m = re.search(r'@([a-zA-Z0-9_.]+)', text)
        return m.group(1) if m else text.split("@")[-1].split("/")[0].split("?")[0]
    if "instagram.com" in text:
        m = re.search(r'instagram\.com/([a-zA-Z0-9_.]+)', text)
        return m.group(1) if m else text.split("/")[-1].split("?")[0]
    return text.replace("@", "").split("/")[0].split("?")[0].strip()


@app.route("/")
def index():
    """Serve the main page"""
    html_path = os.path.join(BASE_DIR, "templates", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Template not found</h1>"


@app.route("/report")
def api_report():
    """Serve the API evaluation report"""
    html_path = os.path.join(BASE_DIR, "templates", "api-report.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Report not found</h1>"


# ============================================================
# API: Analyze influencer — 标题抓取 + 分类
# ============================================================
@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """Analyze an influencer - 增量更新 + 分类"""
    data = request.get_json()
    input_text = data.get("input", "")
    platform = data.get("platform", "TikTok")
    days = int(data.get("days", 30))
    language = data.get("language", "en")

    username = extract_username(input_text, platform)
    if not username:
        return jsonify({"error": "无法解析达人ID"}), 400

    # Select classifier
    if language == "es":
        es_config = load_es_config()
        classify_fn = lambda t: classify_title_es(t, es_config)
    else:
        en_config = load_en_config()
        classify_fn = lambda t: classify_title_en(t, en_config)

    # 增量更新判断
    need_refresh, cached, status_msg = needs_apify_refresh(username, platform, days)

    if not need_refresh:
        # 缓存最新，直接用
        results = []
        for v in cached:
            title = v.get("标题", v.get("title", ""))
            brand, keywords, category, basis = classify_fn(title)
            results.append({
                "发布日期": v.get("发布日期", v.get("date", "")),
                "达人ID": username,
                "平台": platform,
                "标题": title,
                "视频链接": v.get("视频链接", v.get("url", "")),
                "评论数": int(v.get("评论数", v.get("comments_count", 0)) or 0),
                "分类": category,
                "命中品牌": brand or "",
                "命中关键词": keywords or "",
                "分类依据": basis,
            })
        return jsonify({
            "username": username, "platform": platform, "language": language,
            "videos": results, "source": "cache", "status": status_msg
        })

    # 需要补抓 — 返回状态提示，前端触发 Apify
    if cached:
        return jsonify({
            "username": username, "platform": platform, "language": language,
            "videos": [],
            "source": "stale",
            "need_apify": True,
            "cached_count": len(cached),
            "message": status_msg + "，请点击下方按钮抓取最新数据。"
        })
    else:
        return jsonify({
            "username": username, "platform": platform, "language": language,
            "videos": [],
            "source": "none",
            "need_apify": True,
            "message": "未找到缓存数据。请点击下方按钮通过 Apify 抓取标题数据。"
        })


# ============================================================
# API: Single video comments (备用)
# ============================================================
@app.route("/api/comments", methods=["POST"])
def api_comments():
    """Fetch real comments from SocialCrawl for a single video"""
    data = request.get_json()
    video_url = data.get("url", "")
    platform = data.get("platform", "")

    try:
        if "instagram" in platform.lower():
            comments = fetch_ig_comments(video_url)
        elif "tiktok" in platform.lower():
            comments = fetch_tiktok_comments(video_url)
        else:
            return jsonify({"error": "Unknown platform"}), 400

        valid = get_top_valid_comments(comments, 15)
        comment_config = load_comment_config()

        results = []
        for c in valid:
            cat_key, name_en, name_zh, signals = classify_single_comment(c["text"], comment_config)
            results.append({
                "text": c["text"][:200],
                "likes": c["likes"],
                "username": c.get("username", ""),
                "category": name_zh,
                "category_key": cat_key,
                "matched_signals": signals,
            })

        return jsonify({"comments": results, "total": len(comments)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# API: Batch comment analysis — 两个 Part
# ============================================================
@app.route("/api/comments/analysis", methods=["POST"])
def api_comments_analysis():
    """
    一次性获取两个 Part 的评论分析：
    - Part 1: TOP-3 评论数最高的视频
    - Part 2: TOP-3 赞助/种草分类中评论数最高的视频

    Input: {videos: [{...}], platform: "Instagram|TikTok"}
    Output: {part1_top3_hot: {...}, part2_top3_sponsored: {...}}
    """
    data = request.get_json()
    videos = data.get("videos", [])
    platform = data.get("platform", "Instagram")

    if not videos:
        return jsonify({"error": "No videos provided"}), 400

    comment_config = load_comment_config()

    # --- Part 1: TOP-3 by comment count ---
    sorted_by_comments = sorted(videos, key=lambda v: int(v.get("评论数", 0) or 0), reverse=True)
    top3_hot = sorted_by_comments[:3]

    # --- Part 2: TOP-3 sponsored/review by comment count ---
    sponsored_videos = [v for v in videos if v.get("分类", "") in SPONSORED_CATEGORIES]
    # If < 3 sponsored videos, fill with next-highest comment count non-sponsored
    if len(sponsored_videos) < 3:
        non_sponsored = [v for v in sorted_by_comments if v not in sponsored_videos]
        needed = 3 - len(sponsored_videos)
        sponsored_videos.extend(non_sponsored[:needed])
    top3_sponsored = sorted(sponsored_videos, key=lambda v: int(v.get("评论数", 0) or 0), reverse=True)[:3]

    # --- Fetch & classify comments for each video ---
    def analyze_video_group(video_group, group_label):
        """Fetch comments + classify for a group of videos"""
        video_results = []
        all_classified = []

        for idx, v in enumerate(video_group):
            url = v.get("视频链接", "")
            title = v.get("标题", "")
            native_comment_count = int(v.get("评论数", 0) or 0)

            # Fetch comments from SocialCrawl
            comments_raw = []
            fetch_error = None
            if url:
                try:
                    comments_raw = fetch_comments_for_video(url, platform)
                except Exception as e:
                    fetch_error = str(e)

            valid_comments = get_top_valid_comments(comments_raw, 30)

            # Classify each comment
            classified = []
            for c in valid_comments:
                cat_key, name_en, name_zh, signals = classify_single_comment(c["text"], comment_config)
                classified.append({
                    "text": c["text"][:200],
                    "likes": c["likes"],
                    "username": c.get("username", "N/A"),
                    "category": name_zh,
                    "category_key": cat_key,
                    "matched_signals": signals,
                })
                all_classified.append({
                    "text": c["text"][:200],
                    "likes": c["likes"],
                    "username": c.get("username", "N/A"),
                    "category": name_zh,
                    "category_key": cat_key,
                    "matched_signals": signals,
                })

            # Distribution for this video
            dist = defaultdict(lambda: {"count": 0, "pct": 0})
            for c in classified:
                dist[c["category_key"]]["count"] += 1
            total_c = len(classified)
            for k in dist:
                dist[k]["pct"] = round(dist[k]["count"] / total_c * 100, 1) if total_c > 0 else 0

            video_results.append({
                "title": title[:120],
                "url": url,
                "native_comment_count": native_comment_count,
                "fetched_comment_count": len(classified),
                "total_raw_comments": len(comments_raw),
                "distribution": dict(dist),
                "classified_comments": classified,
                "fetch_error": fetch_error,
            })

        # Aggregate distribution across all videos in group
        agg_dist = defaultdict(lambda: {"count": 0, "name_zh": "", "pct": 0})
        for c in all_classified:
            cat_key = c["category_key"]
            agg_dist[cat_key]["count"] += 1
            agg_dist[cat_key]["name_zh"] = c["category"]

        total_all = len(all_classified)
        for k in agg_dist:
            agg_dist[k]["pct"] = round(agg_dist[k]["count"] / total_all * 100, 1) if total_all > 0 else 0

        # Purchase intent scoring
        intent_score, intent_label = _calc_purchase_intent(all_classified, comment_config)

        return {
            "videos": video_results,
            "aggregate_distribution": dict(agg_dist),
            "total_classified_comments": total_all,
            "purchase_intent_score": intent_score,
            "purchase_intent_label": intent_label,
        }

    part1 = analyze_video_group(top3_hot, "hot")
    part2 = analyze_video_group(top3_sponsored, "sponsored")

    return jsonify({
        "part1_top3_hot": part1,
        "part2_top3_sponsored": part2,
        "platform": platform,
    })


def _calc_purchase_intent(classified_comments, config):
    """Calculate purchase intent score from classified comments"""
    scoring = config.get("purchase_intent_scoring", {})
    weights = scoring.get("weights", {})
    interpretation = scoring.get("interpretation", {})

    if not classified_comments:
        return 0, "低购买意向"

    total_weighted = sum(weights.get(c["category_key"], 0) for c in classified_comments)
    avg_score = total_weighted / len(classified_comments)

    if avg_score >= interpretation.get("high", {}).get("min_score", 2.0):
        label = interpretation["high"].get("label_zh", "高购买意向")
    elif avg_score >= interpretation.get("medium", {}).get("min_score", 1.0):
        label = interpretation["medium"].get("label_zh", "中购买意向")
    else:
        label = interpretation["low"].get("label_zh", "低购买意向")

    return round(avg_score, 2), label


# ============================================================
# API: Apify fetch (placeholder — needs user's API key)
# ============================================================
@app.route("/api/apify/fetch", methods=["POST"])
def api_apify_fetch():
    """Trigger Apify scraping for a given influencer"""
    data = request.get_json()
    username = data.get("username", "")
    platform = data.get("platform", "Instagram")
    days = int(data.get("days", 30))
    api_key = data.get("api_key", "")
    language = data.get("language", "en")

    if not api_key:
        return jsonify({"error": "请提供 Apify API Key"}), 400

    # Try importing Apify fetcher
    try:
        from apify_fetcher import fetch_instagram_videos, fetch_tiktok_videos
    except ImportError:
        return jsonify({
            "error": "Apify 集成模块未就绪",
            "message": "apify_fetcher.py 尚未创建，请提供 Apify API Key 和 Actor 信息"
        }), 500

    try:
        if "instagram" in platform.lower():
            videos = fetch_instagram_videos(username, api_key, days)
        else:
            videos = fetch_tiktok_videos(username, api_key, days)

        # 增量合并：与旧缓存去重，新数据覆盖旧数据
        old_cached = load_cached_videos(username, platform)
        merged = merge_videos(old_cached, videos)
        save_cached_videos(username, platform, merged)

        # Select classifier
        if language == "es":
            es_config = load_es_config()
            classify_fn = lambda t: classify_title_es(t, es_config)
        else:
            en_config = load_en_config()
            classify_fn = lambda t: classify_title_en(t, en_config)

        # Classify
        results = []
        for v in merged:
            title = v.get("标题", v.get("title", ""))
            brand, keywords, category, basis = classify_fn(title)
            results.append({
                "发布日期": v.get("发布日期", v.get("date", "")),
                "达人ID": username,
                "平台": platform,
                "标题": title,
                "视频链接": v.get("视频链接", v.get("url", "")),
                "评论数": int(v.get("评论数", v.get("comments_count", 0)) or 0),
                "分类": category,
                "命中品牌": brand or "",
                "命中关键词": keywords or "",
                "分类依据": basis,
            })

        new_count = len(videos)
        total_count = len(merged)
        return jsonify({
            "username": username, "platform": platform,
            "videos": results, "source": "apify",
            "message": f"Apify 抓取完成: 新增 {new_count} 条，总计 {total_count} 条（已去重合并）"
        })
    except Exception as e:
        return jsonify({"error": f"Apify 抓取失败: {str(e)}"}), 500


# ============================================================
# API: Upload videos manually (bypass Apify)
# ============================================================
@app.route("/api/upload/videos", methods=["POST"])
def api_upload_videos():
    """Upload video list manually (JSON format)"""
    data = request.get_json()
    username = data.get("username", "")
    platform = data.get("platform", "Instagram")
    videos = data.get("videos", [])
    language = data.get("language", "en")

    if not videos:
        return jsonify({"error": "No videos provided"}), 400

    # Save to cache
    normalized = []
    for v in videos:
        normalized.append({
            "标题": v.get("title", v.get("标题", "")),
            "发布日期": v.get("date", v.get("发布日期", "")),
            "视频链接": v.get("url", v.get("视频链接", "")),
            "评论数": v.get("comments_count", v.get("评论数", 0)),
        })
    save_cached_videos(username, platform, normalized)

    # Select classifier
    if language == "es":
        es_config = load_es_config()
        classify_fn = lambda t: classify_title_es(t, es_config)
    else:
        en_config = load_en_config()
        classify_fn = lambda t: classify_title_en(t, en_config)

    # Classify
    results = []
    for v in normalized:
        title = v.get("标题", "")
        brand, keywords, category, basis = classify_title_en(title, en_config)
        results.append({
            "发布日期": v.get("发布日期", ""),
            "达人ID": username,
            "平台": platform,
            "标题": title,
            "视频链接": v.get("视频链接", ""),
            "评论数": int(v.get("评论数", 0) or 0),
            "分类": category,
            "命中品牌": brand or "",
            "命中关键词": keywords or "",
            "分类依据": basis,
        })

    return jsonify({
        "username": username, "platform": platform,
        "videos": results, "source": "upload",
        "message": f"已导入 {len(results)} 条视频"
    })


# ============================================================
# API: Download Excel — Sheet-1 视频明细 + Sheet-2 评论明细
# ============================================================
@app.route("/api/download/excel", methods=["POST"])
def api_download_excel():
    """Generate multi-sheet Excel: Sheet-1 videos + Sheet-2 comments"""
    data = request.get_json()
    videos = data.get("videos", [])
    comments_data = data.get("comments", {})
    username = data.get("username", "unknown")
    platform = data.get("platform", "")

    if not videos:
        return jsonify({"error": "No video data"}), 400

    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()

    # --- Sheet 1: 视频明细 ---
    ws1 = wb.active
    ws1.title = "视频明细"

    headers1 = ['发布日期','达人ID','平台','标题','视频链接','评论数','分类','命中品牌','命中关键词','分类依据']
    ws1.append(headers1)

    # Header style
    header_fill = PatternFill(start_color="1e293b", end_color="1e293b", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    for cell in ws1[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Data rows
    for v in videos:
        title = v.get('标题', '')
        if len(title) > 120:
            title = title[:120] + "..."
        row = [
            v.get('发布日期', ''),
            v.get('达人ID', username),
            v.get('平台', platform),
            title,
            v.get('视频链接', ''),
            v.get('评论数', 0),
            v.get('分类', ''),
            v.get('命中品牌', ''),
            v.get('命中关键词', ''),
            v.get('分类依据', ''),
        ]
        ws1.append(row)

    # Auto-width for Sheet 1
    for col_idx, col in enumerate(ws1.columns, 1):
        max_len = 0
        col_letter = get_column_letter(col_idx)
        for cell in col:
            try:
                val_len = len(str(cell.value)) if cell.value else 0
                if val_len > max_len:
                    max_len = min(val_len, 60)
            except:
                pass
        ws1.column_dimensions[col_letter].width = min(max_len + 4, 50)

    # Freeze header
    ws1.freeze_panes = "A2"

    # --- Sheet 2: 评论明细 ---
    ws2 = wb.create_sheet(title="评论明细")

    headers2 = ['达人ID','平台','视频标题','视频链接','评论用户名','评论内容','点赞数','评论分类','匹配信号词','抓取来源']
    ws2.append(headers2)

    for cell in ws2[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Build video title/url lookup
    video_lookup = {}
    for v in videos:
        url = v.get('视频链接', '')
        video_lookup[url] = {
            'title': v.get('标题', '')[:120] + ("..." if len(v.get('标题', '')) > 120 else ""),
            'url': url,
        }

    # Populate comments from both parts
    part1 = comments_data.get("part1_top3_hot", {})
    part2 = comments_data.get("part2_top3_sponsored", {})

    for part_name, part in [("Part1-热门", part1), ("Part2-赞助", part2)]:
        if not part or not part.get("videos"):
            continue
        for v in part["videos"]:
            url = v.get("url", "")
            v_info = video_lookup.get(url, {"title": v.get("title", ""), "url": url})
            for c in v.get("classified_comments", []):
                signals = c.get("matched_signals", [])
                signals_str = ", ".join(signals) if isinstance(signals, list) else str(signals)
                row = [
                    username,
                    platform,
                    v_info["title"],
                    v_info["url"],
                    c.get("username", "N/A"),
                    c.get("text", ""),
                    c.get("likes", 0),
                    c.get("category", ""),
                    signals_str,
                    part_name,
                ]
                ws2.append(row)

    # Auto-width for Sheet 2
    for col_idx, col in enumerate(ws2.columns, 1):
        max_len = 0
        col_letter = get_column_letter(col_idx)
        for cell in col:
            try:
                val_len = len(str(cell.value)) if cell.value else 0
                if val_len > max_len:
                    max_len = min(val_len, 80)
            except:
                pass
        ws2.column_dimensions[col_letter].width = min(max_len + 4, 60)

    # Wrap text for comment content column (column F = 6)
    for row in ws2.iter_rows(min_row=2, max_row=ws2.max_row, min_col=6, max_col=6):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    ws2.freeze_panes = "A2"

    # Save to bytes
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    from flask import send_file
    filename = f"达人分析_{username}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )


# ============================================================
# API: Batch analyze — 批量分析多达人
# ============================================================
@app.route("/api/batch/analyze", methods=["POST"])
def api_batch_analyze():
    """批量分析达人：依次检查缓存，返回哪些有缓存、哪些需要 Apify"""
    data = request.get_json()
    inputs = data.get("inputs", [])
    platform = data.get("platform", "Instagram")
    days = int(data.get("days", 30))
    language = data.get("language", "en")

    if not inputs:
        return jsonify({"error": "请提供达人列表"}), 400

    results = []
    need_apify = []
    cached_results = []

    for inp in inputs:
        username = extract_username(inp.strip(), platform)
        if not username:
            continue

        need_refresh, cached, msg = needs_apify_refresh(username, platform, days)

        if not need_refresh and cached:
            cached_results.append({
                "username": username,
                "video_count": len(cached),
                "status": "cached",
            })
        else:
            need_apify.append({
                "username": username,
                "cached_count": len(cached) if cached else 0,
                "status": "need_apify",
            })

    return jsonify({
        "cached": cached_results,
        "need_apify": need_apify,
        "total": len(inputs),
        "cached_count": len(cached_results),
        "need_apify_count": len(need_apify),
        "platform": platform,
        "language": language,
        "days": days,
    })


@app.route("/api/batch/fetch", methods=["POST"])
def api_batch_fetch():
    """批量 Apify 抓取 + 分类"""
    data = request.get_json()
    usernames = data.get("usernames", [])
    platform = data.get("platform", "Instagram")
    days = int(data.get("days", 30))
    language = data.get("language", "en")
    api_key = data.get("api_key", "apify_api_lNbEszC31JbjeynU2JiEVpEE4WA0JO2IyFt4")

    if not usernames:
        return jsonify({"error": "请提供需要抓取的达人列表"}), 400

    from apify_fetcher import fetch_instagram_videos, fetch_tiktok_videos

    # Select classifier
    if language == "es":
        es_config = load_es_config()
        classify_fn = lambda t: classify_title_es(t, es_config)
    else:
        en_config = load_en_config()
        classify_fn = lambda t: classify_title_en(t, en_config)

    all_results = []
    for i, username in enumerate(usernames):
        try:
            if "instagram" in platform.lower():
                videos = fetch_instagram_videos(username, api_key, days)
            else:
                videos = fetch_tiktok_videos(username, api_key, days)

            old_cached = load_cached_videos(username, platform)
            merged = merge_videos(old_cached, videos)
            save_cached_videos(username, platform, merged)

            classified = []
            for v in merged:
                title = v.get("标题", v.get("title", ""))
                brand, keywords, category, basis = classify_fn(title)
                classified.append({
                    "发布日期": v.get("发布日期", v.get("date", "")),
                    "达人ID": username,
                    "平台": platform,
                    "标题": title,
                    "视频链接": v.get("视频链接", v.get("url", "")),
                    "评论数": int(v.get("评论数", v.get("comments_count", 0)) or 0),
                    "分类": category,
                    "命中品牌": brand or "",
                    "命中关键词": keywords or "",
                    "分类依据": basis,
                })

            all_results.append({
                "username": username,
                "videos": classified,
                "new_count": len(videos),
                "total_count": len(merged),
                "status": "ok",
            })
        except Exception as e:
            all_results.append({
                "username": username,
                "videos": [],
                "status": "error",
                "error": str(e),
            })

    return jsonify({
        "results": all_results,
        "platform": platform,
        "language": language,
        "total_done": len([r for r in all_results if r["status"] == "ok"]),
        "total_error": len([r for r in all_results if r["status"] == "error"]),
    })


if __name__ == "__main__":
    os.makedirs(os.path.join(BASE_DIR, "templates"), exist_ok=True)
    app.run(host="0.0.0.0", port=8504, debug=False)
