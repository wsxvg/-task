#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博API数据解析器 V6.2 (词库融合最终版)

核心改进:
- 【词库融合】智能评分算法的关键词库，已完整吸收您最初脚本中经过实战检验的海量关键词，
  极大提升了对口语化、多样化上新帖的识别能力。
- 【分数校准】对融合后的新词库进行了分数微调，确保评分体系的平衡。
- 保留了 V6.1 的所有高级算法逻辑和 V6.0 的所有核心功能。
"""

import json
import re
import requests
import base64
import hashlib
import os
import pytz
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
import sys
import time
import random

try:
    from PIL import Image
except ImportError:
    print("⚠️ 警告：未安装 Pillow 库，图片压缩功能将不可用。请运行: pip install Pillow")
    Image = None

# --- 全局配置 ---
MAX_WORKERS = 10 
GROUP_ID = '5159683220312291'
PAGE_LIMIT = 20
DEFAULT_SUB_COOKIE = "_2A25FuSErDeRhGeFJ7FoY8SfEyzuIHXVmtzzjrDV8PUJbkNAbLXf1kW1NfwLa8SVCbwqd6jJPgosBsh5OwDjzk6vD"
PROXIES_SETTING = {"http": None, "https": None}

# ---------------------------------------------------------------------------
# 全新的、基于您标注数据训练的智能评分检测器 (V3)
# ---------------------------------------------------------------------------
class SmartLaunchDetector:
    def __init__(self):
        # 时间信号
        self.STRONG_TIME_KEYWORDS = {'今晚': 30, '明晚': 30, '今晚八点': 35, '今晚8点': 35, '今晚7点': 35, '今晚七点': 35, '明天': 25, '后天': 25, '本周': 20, '本周末': 25, '月底': 20, '准时': 10, '稍后': 15, '即刻': 20, '立即': 20}
        self.TIME_PATTERNS = {r'\d{1,2}[:：]\d{2}': 35, r'[0-9一二三四五六七八九十]+点': 30, r'\d{1,2}月\d{1,2}日': 30, r'\d{1,2}号': 25, r'周[一二三四五六日]': 25}
        
        # 【核心升级】动作信号 - 完整吸收了您最初脚本中的海量关键词
        self.ACTION_KEYWORDS = {
            '上架': 30, '发售': 30, '释放': 25, '补货': 25, '现货': 25, '开启购买': 35, 
            '开售': 30, '补出': 20, '上新': 20, '预售': 25, '开启': 20, '会员先购': 30,
            '非会员释放': 25, 'VIP先购': 30, '先购': 25, '开放购买': 25, '已开售': 20,
            '已上架': 20, '现货上架': 30, '上新通知': 25, '新款预告': 15, '新品上市': 20,
            '新款上线': 20, '新品首发': 25, '首批': 15, '第一批': 15, '更新了': 10,
            '带来了': 10, '带给大家': 10, '上🆕': 20, '🆕': 15
        }
        
        # 负面信号
        self.NEGATIVE_KEYWORDS = {'进度': -40, '打样': -40, '调整': -30, '修改': -30, '确认': -30, '开发': -40, '研究': -40, '还在': -20, '还在改': -40, '还在调': -40, '面料': -10, '辅料': -10, '刺绣': -10, '样品': -20, '样衣': -20, '色卡': -20, '计划': -50, '预计': -30, '准备': -20, '快了': -20, '即将': -20, '近期': -30, '延迟':-60, '取消':-60, '停止':-60}
        self.POLLING_KEYWORDS = {'点点': -50, '要不要': -60, '怎么样': -50, '觉得': -40, '喜欢吗': -50}
        self.VETO_KEYWORDS = ['抽奖', '转发此微博']
        self.SCORE_THRESHOLD = 50

    def check(self, text: str) -> bool:
        if not text: return False
        for word in self.VETO_KEYWORDS:
            if word in text and not any(action in text for action in self.ACTION_KEYWORDS): return False
        time_score = self._calculate_score(text, self.STRONG_TIME_KEYWORDS)
        pattern_time_score = self._calculate_pattern_score(text, self.TIME_PATTERNS)
        action_score = self._calculate_score(text, self.ACTION_KEYWORDS, title_bonus=10)
        negative_score = sum(v for w, v in self.NEGATIVE_KEYWORDS.items() if w in text)
        polling_score = sum(v for w, v in self.POLLING_KEYWORDS.items() if w in text)
        final_time_score = max(time_score, pattern_time_score)
        if final_time_score == 0 or action_score == 0: return False
        total_score = final_time_score + action_score + negative_score + polling_score
        return total_score >= self.SCORE_THRESHOLD

    def _calculate_score(self, text: str, keywords: Dict[str, int], title_bonus: int = 0) -> int:
        max_score = 0; title_area = text[:35]
        for word, value in keywords.items():
            if word in text:
                score = value
                if title_bonus > 0 and word in title_area: score += title_bonus
                if score > max_score: max_score = score
        return max_score

    def _calculate_pattern_score(self, text: str, patterns: Dict[str, int]) -> int:
        max_score = 0
        for pattern, value in patterns.items():
            if re.search(pattern, text):
                if value > max_score: max_score = value
        return max_score

# ---------------------------------------------------------------------------
# 核心数据解析与推送类 (此类及其方法无需修改)
# ---------------------------------------------------------------------------
class WeiboDataParser:
    def __init__(self, sub_cookie: str, webhook_url: Optional[str] = None):
        self.sub_cookie = sub_cookie; self.webhook_url = webhook_url; self.launch_detector = SmartLaunchDetector()
    def _fetch_full_text(self, post_id: str) -> Optional[Dict[str, Any]]:
        time.sleep(random.uniform(0.3, 0.8)); url = "https://weibo.com/ajax/statuses/longtext"; params = {'id': post_id}
        headers = {'accept': 'application/json, text/plain, */*','x-requested-with': 'XMLHttpRequest','user-agent': 'Mozilla/5.0','referer': f'https://weibo.com/mygroups?gid={GROUP_ID}'}
        cookies = {'SUB': self.sub_cookie}
        try:
            response = requests.get(url, params=params, headers=headers, cookies=cookies, timeout=10, proxies=PROXIES_SETTING)
            if response.status_code == 200:
                data = response.json()
                if data.get('ok') == 1 and 'longTextContent' in data.get('data', {}):
                    full_text = data['data']['longTextContent']; clean_text = re.sub(r'<br\s*/?>', '\n', full_text); clean_text = re.sub(r'<.*?>', '', clean_text)
                    return {'post_id': post_id, 'full_text': clean_text.strip()}
        except Exception: pass
        return {'post_id': post_id, 'full_text': None}
    def _resolve_short_link(self, short_link: str) -> Optional[str]:
        try:
            response = requests.head(short_link, allow_redirects=True, timeout=10, proxies=PROXIES_SETTING); return response.url
        except Exception: return None
    def _extract_and_resolve_links(self, text: str) -> List[str]:
        short_links = re.findall(r'https?://t\.cn/\w+', text)
        if not short_links: return []
        real_links = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_link = {executor.submit(self._resolve_short_link, link): link for link in set(short_links)}
            for future in as_completed(future_to_link):
                real_link = future.result()
                if real_link: real_links.append(real_link)
        return real_links
    def parse_and_enrich(self, statuses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        long_text_posts = [post for post in statuses if post.get('isLongText')]
        full_texts = {}
        if long_text_posts:
            print(f"⚡ 检测到 {len(long_text_posts)} 条长微博，开始并行获取全文...")
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_id = {executor.submit(self._fetch_full_text, post['idstr']): post['idstr'] for post in long_text_posts}
                for future in as_completed(future_to_id):
                    result = future.result()
                    if result and result.get('full_text'): full_texts[result['post_id']] = result['full_text']
        parsed_statuses = []
        for status in statuses:
            if status.get('readtimetype') == 'adMblog': continue
            post_id = status.get('idstr')
            if post_id in full_texts: status['text_raw'] = full_texts[post_id]
            parsed_status = self._parse_single_status(status)
            if parsed_status:
                parsed_status['real_links'] = self._extract_and_resolve_links(parsed_status['text_raw'])
                parsed_statuses.append(parsed_status)
        return parsed_statuses
    def filter_launch_posts(self, parsed_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [post for post in parsed_data if self.launch_detector.check(post.get('text_raw', ''))]
    def _parse_single_status(self, s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            is_retweet = 'retweeted_status' in s; o_s = s['retweeted_status'] if is_retweet else s
            b = self._parse_basic_info(s, is_retweet); u = self._parse_user_info(s.get('user', {})); i = self._parse_interaction_data(s); m = self._parse_media_content(o_s)
            o_u = self._parse_user_info(o_s.get('user', {})) if is_retweet else None
            if is_retweet: b['original_text_raw'] = o_s.get('text_raw', '')
            return {**b, 'user': u, 'interaction': i, 'media': m, 'original_user': o_u}
        except Exception: return None
    def _parse_basic_info(self, s, r): return {'id': s.get('idstr', s.get('id')), 'text_raw': s.get('text_raw', ''), 'created_at': self._parse_time(s.get('created_at')), 'source': self._clean_source(s.get('source', '')), 'is_retweet': r}
    def _parse_user_info(self, u): return {'screen_name': u.get('screen_name', ''), 'user_id': u.get('idstr', u.get('id', ''))}
    def _parse_interaction_data(self, s): return {'reposts_count': s.get('reposts_count', 0), 'comments_count': s.get('comments_count', 0), 'attitudes_count': s.get('attitudes_count', 0)}
    def _parse_media_content(self, status: Dict[str, Any]) -> Dict[str, Any]:
        media = {'images': [], 'videos': []}
        pic_infos = status.get('pic_infos', {})
        for pic_id in status.get('pic_ids', []):
            if pic_id in pic_infos:
                for size in ['large', 'original', 'bmiddle']:
                    if size in pic_infos[pic_id] and 'url' in pic_infos[pic_id][size]: media['images'].append({'url': pic_infos[pic_id][size]['url']}); break
        if 'page_info' in status and status['page_info'].get('type') == 'video':
            page_pic_url = status['page_info'].get('page_pic', {}).get('url')
            if page_pic_url: media['images'].append({'url': page_pic_url})
        return media
    def _parse_time(self, t):
        try:
            if t: return datetime.strptime(t, "%a %b %d %H:%M:%S %z %Y").strftime("%Y-%m-%d %H:%M:%S")
        except Exception: pass
        return t or ""
    def _clean_source(self, s): return re.sub(r'<[^>]+>', '', s).strip()
    def download_and_convert_image(self, image_url: str) -> Optional[Dict[str, str]]:
        if not Image: return None
        try:
            headers = {'Referer': 'https://weibo.com/','User-Agent': 'Mozilla/5.0'}
            response = requests.get(image_url, headers=headers, timeout=10, proxies=PROXIES_SETTING)
            if response.status_code != 200: return None
            image_data = response.content
            if len(image_data) > 2 * 1024 * 1024:
                img = Image.open(BytesIO(image_data)); img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                buffer = BytesIO(); img.save(buffer, format='JPEG', quality=85); image_data = buffer.getvalue()
            return {'base64': base64.b64encode(image_data).decode('utf-8'), 'md5': hashlib.md5(image_data).hexdigest()}
        except Exception: return None
    def send_wechat_text(self, text: str) -> bool:
        if not self.webhook_url: return False
        try:
            data = {"msgtype": "text", "text": {"content": text}}; response = requests.post(self.webhook_url, json=data, timeout=10, proxies=PROXIES_SETTING)
            return response.json().get('errcode') == 0
        except Exception: return False
    def send_wechat_image(self, image_info: Dict[str, str]) -> bool:
        if not self.webhook_url: return False
        try:
            data = {"msgtype": "image", "image": {"base64": image_info['base64'], "md5": image_info['md5']}}
            response = requests.post(self.webhook_url, json=data, timeout=20, proxies=PROXIES_SETTING)
            return response.json().get('errcode') == 0
        except Exception: return False

def fetch_one_page_of_posts(sub_cookie: str, max_id: Optional[str] = None) -> Dict[str, Any]:
    url="https://weibo.com/ajax/feed/groupstimeline";params={'list_id':GROUP_ID,'count':'50'}
    if max_id: params['max_id']=max_id
    headers = {
        'accept': 'application/json, text/plain, */*','accept-language': 'zh-CN,zh;q=0.9','client-version': 'v2.47.106',
        'referer': f'https://weibo.com/mygroups?gid={GROUP_ID}','x-requested-with': 'XMLHttpRequest',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0',
    }
    cookies={'SUB':sub_cookie}
    try:
        response=requests.get(url,params=params,headers=headers,cookies=cookies,timeout=15, proxies=PROXIES_SETTING)
        return response.json()
    except Exception as e:
        print(f"   - 网络请求异常: {e}")
        return {}

def fetch_weibo_data(sub_cookie: str, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
    all_window_statuses=[];max_id=None
    for page in range(PAGE_LIMIT):
        if page == 0: print(f"🚀 正在抓取微博列表 (第 {page+1} 页)...")
        data = fetch_one_page_of_posts(sub_cookie, max_id)
        statuses=data.get('statuses',[])
        if page == 0 and not statuses: return []
        if not statuses:break
        found_old_post=False
        for status in statuses:
            try:
                post_time=datetime.strptime(status['created_at'],"%a %b %d %H:%M:%S %z %Y")
                if start_time<=post_time<=end_time:all_window_statuses.append(status)
                elif post_time<start_time:found_old_post=True
            except(ValueError,KeyError):continue
        if found_old_post:print("ℹ️ 帖子时间已早于窗口，停止翻页。");break
        max_id=data.get('max_id_str')
        if not max_id or max_id=="0":break
        time.sleep(1)
    return all_window_statuses

def format_launch_notification(launch_info: Dict[str, Any]) -> str:
    user_name = launch_info['user']['screen_name']
    content = re.sub(r'https?://t\.cn/\w+', '', launch_info['text_raw'])
    content = re.sub(r'\s+', ' ', content).strip()
    if len(content) > 400: content = content[:400] + "..."
    message = f"🛍️【上新预告】{user_name}\n\n💬 {content}"
    real_links = launch_info.get('real_links', [])
    if real_links:
        message += "\n\n🔗 直达链接:"
        for i, link in enumerate(real_links): message += f"\n{i+1}. {link}"
    return message

def send_launch_notifications(parser: WeiboDataParser, launch_posts: List[Dict[str, Any]]):
    print(f"\n📨 开始推送 {len(launch_posts)} 条上新预告到企业微信...")
    for post in launch_posts:
        text_content = format_launch_notification(post)
        if parser.send_wechat_text(text_content):
            print(f"   ✅ 文本推送成功: {post['user']['screen_name']}")
            time.sleep(1)
            images = post.get('media', {}).get('images', [])
            for i, image in enumerate(images[:2]):
                image_info = parser.download_and_convert_image(image['url'])
                if image_info and parser.send_wechat_image(image_info): print(f"      - 图片 {i+1} 发送成功")
                else: print(f"      - 图片 {i+1} 发送失败")
                time.sleep(0.5)
        else:
            print(f"   ❌ 文本推送失败: {post['user']['screen_name']}")
        time.sleep(2)

def check_cookie_status(sub_cookie: str, current_statuses: List[Dict[str, Any]]) -> bool:
    if current_statuses: return True
    print("⚠️ 当前时间窗口未抓取到任何帖子，启动Cookie有效性二次验证...")
    beijing_tz=pytz.timezone('Asia/Shanghai'); now_beijing=datetime.now(beijing_tz); current_hour=now_beijing.hour
    if 11<=current_hour<15: end_time=(now_beijing-timedelta(days=1)).replace(hour=22,minute=0,second=0); start_time=end_time-timedelta(hours=4)
    elif 15<=current_hour<17: end_time=now_beijing.replace(hour=12,minute=0,second=0); start_time=(now_beijing-timedelta(days=1)).replace(hour=22,minute=0,second=0)
    else: end_time=now_beijing-timedelta(hours=6); start_time=now_beijing-timedelta(hours=12)
    print(f"   - 正在回溯检查上一时间段...")
    previous_statuses = fetch_weibo_data(sub_cookie, start_time, end_time)
    if previous_statuses:
        print("   ✅ 在上一时间段找到数据，判定Cookie有效，当前时段确实无新帖。")
        return True
    else:
        print("   ❌ 当前及上一时间段均未找到任何数据，判定Cookie或请求头已失效！")
        return False

def execute_monitoring(sub_cookie: str, webhook_url: Optional[str] = None, enable_push: bool = True):
    beijing_tz=pytz.timezone('Asia/Shanghai');now_beijing=datetime.now(beijing_tz);current_hour=now_beijing.hour
    if 11<=current_hour<15:start_time=(now_beijing-timedelta(days=1)).replace(hour=22,minute=0,second=0);end_time=now_beijing.replace(hour=12,minute=0,second=0);window_desc="昨天22:00 → 今天12:00"
    elif 15<=current_hour<17:start_time=now_beijing.replace(hour=12,minute=0,second=0);end_time=now_beijing.replace(hour=16,minute=0,second=0);window_desc="今天12:00 → 今天16:00"
    elif 17<=current_hour<18:start_time=now_beijing.replace(hour=16,minute=0,second=0);end_time=now_beijing.replace(hour=18,minute=0,second=0);window_desc="今天16:00 → 今天18:00"
    elif 18<=current_hour<21:start_time=now_beijing.replace(hour=18,minute=0,second=0);end_time=now_beijing.replace(hour=19,minute=0,second=0);window_desc="今天18:00 → 今天19:00"
    elif current_hour>=21 or current_hour<2:
        if current_hour>=21:start_time=now_beijing.replace(hour=19,minute=0,second=0);end_time=now_beijing.replace(hour=22,minute=0,second=0)
        else:start_time=(now_beijing-timedelta(days=1)).replace(hour=19,minute=0,second=0);end_time=(now_beijing-timedelta(days=1)).replace(hour=22,minute=0,second=0)
        window_desc="今天19:00 → 今天22:00"
    else:start_time=now_beijing-timedelta(hours=6);end_time=now_beijing;window_desc="最近6小时"
    print(f"📅 设定抓取时间窗口: {window_desc}")
    
    raw_statuses = fetch_weibo_data(sub_cookie, start_time, end_time)
    if not check_cookie_status(sub_cookie, raw_statuses):
        if enable_push:
            parser = WeiboDataParser(sub_cookie, webhook_url=webhook_url)
            error_message = "⚠️ 微博监控失败\n\n原因: Cookie或请求头可能已失效\n连续两个时间段未抓取到任何数据，请及时更新。"; print("📨 正在发送失效通知...")
            if parser.send_wechat_text(error_message): print("✅ 失效通知发送成功。")
            else: print("❌ 失效通知发送失败。")
        return
    
    if not raw_statuses: print("🏁 本次未抓取到任何符合时间窗口的微博，程序结束。"); return
    
    print(f"\n📊 抓取完成，共获得 {len(raw_statuses)} 条原始微博待处理。")
    parser = WeiboDataParser(sub_cookie, webhook_url=webhook_url)
    parsed_data = parser.parse_and_enrich(raw_statuses)
    print(f"🧠 已完成数据解析和全文/链接获取，得到 {len(parsed_data)} 条有效帖子。")
    print("🔍 开始使用智能评分算法进行上新帖识别...")
    launch_posts = parser.filter_launch_posts(parsed_data)

    if launch_posts:
        print(f"✅ 识别成功！共找到 {len(launch_posts)} 条上新帖。")
        for i,post in enumerate(launch_posts): print(f"   {i+1}. {post['user']['screen_name']}: {post['text_raw'][:50]}...")
        if enable_push: send_launch_notifications(parser,launch_posts)
        else: print("🚫 推送功能已禁用(--no-push)。")
    else: print("ℹ️ 本次运行未识别到任何上新帖。")
    print("\n🏁 所有任务执行完毕。")

if __name__ == "__main__":
    webhook_url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=5a9b8214-4fae-4bbd-a147-9ecfe5eb6d90"
    enable_push = True
    
    if '--no-push' in sys.argv: enable_push = False
    if '--webhook' in sys.argv:
        try:
            webhook_index = sys.argv.index('--webhook') + 1; webhook_url = sys.argv[webhook_index]
        except (ValueError, IndexError):
            print("❌ 错误: --webhook 参数后需要提供一个URL。"); sys.exit(1)
    env_webhook = os.getenv('WECHAT_WEBHOOK_URL')
    if env_webhook: webhook_url = env_webhook
    
    sub_cookie = os.getenv('WEIBO_SUB_COOKIE')
    if sub_cookie:
        print("✅ 成功从环境变量加载 Cookie (GitHub Actions 模式)。")
    else:
        sub_cookie = DEFAULT_SUB_COOKIE
        print("⚠️ 未找到环境变量，使用代码中内置的默认备用 Cookie。")

    beijing_tz = pytz.timezone('Asia/Shanghai'); start_time = datetime.now(beijing_tz)
    print(f"🚀 程序启动于: {start_time.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
    
    target_times = [
        {'hour': 12, 'minute': 0, 'name': '中午'}, {'hour': 16, 'minute': 0, 'name': '下午'},
        {'hour': 18, 'minute': 0, 'name': '傍晚'}, {'hour': 19, 'minute': 0, 'name': '黄金时段'},
        {'hour': 22, 'minute': 0, 'name': '夜间'}
    ]
    
    next_target = None
    for target in sorted(target_times, key=lambda x: (x['hour'], x['minute'])):
        target_dt = start_time.replace(hour=target['hour'], minute=target['minute'], second=0, microsecond=0)
        if target_dt > start_time and (target_dt - start_time).total_seconds() <= 45 * 60:
            next_target = target; next_target['target_time'] = target_dt
            break

    is_in_window_now = False
    for target in target_times:
        if start_time.hour == target['hour'] and abs(start_time.minute - target['minute']) <= 15:
            is_in_window_now = True; break
            
    if next_target:
        wait_seconds = (next_target['target_time'] - start_time).total_seconds()
        print(f"⏰ 检测到下一个执行点: {next_target['target_time'].strftime('%H:%M')} ({next_target['name']})")
        print(f"⏳ 需要等待 {int(wait_seconds)} 秒...")
        time.sleep(wait_seconds)
        print(f"✅ 等待完成！实际开始时间: {datetime.now(beijing_tz).strftime('%Y-%m-%d %H:%M:%S')}")
        execute_monitoring(sub_cookie, webhook_url, enable_push)
    elif is_in_window_now:
        print(f"✅ 当前已在执行窗口内，立即开始...")
        execute_monitoring(sub_cookie, webhook_url, enable_push)
    else:
        print("🏁 当前不在任何预设的执行窗口或等待期内，程序正常退出。")
