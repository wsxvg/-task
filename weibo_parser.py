#!/usr/-bin/env python3
# -*- coding: utf-8 -*-
"""
微博API数据解析器 V8.2 (GitHub Actions 最终版 - 完整代码)

核心改进:
- 彻底移除了 time.sleep 内部等待机制。
- 集成 last_processed_id.txt 文件的读写，实现无状态增量抓取。
- 优化评分逻辑：提高“新款”权重，引入“新款+讲解/细节”组合规则，确保捕捉到高价值预告。
"""

import json
import re
import requests
import base64
import hashlib
import os
import pytz
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
import sys
import time
import random
import logging

# 配置日志记录
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

try:
    from PIL import Image
except ImportError:
    logger.warning("⚠️ 警告：未安装 Pillow 库，图片压缩功能将不可用。请运行: pip install Pillow")
    Image = None

# --- 全局配置 ---
MAX_WORKERS = 10 
GROUP_ID = '5159683220312291' # 请确保这是您的实际分组 ID
PAGE_LIMIT = 20 
DEFAULT_SUB_COOKIE = "请替换为您的默认 SUB Cookie" 
PROXIES_SETTING = {"http": None, "https": None}
# ---------------------------------------------------------------------------


# --- 增量更新辅助函数 ---

def load_last_id() -> str:
    """从本地文件加载上次处理的最大微博 ID"""
    try:
        with open('last_processed_id.txt', 'r', encoding='utf-8') as f:
            content = f.read().strip()
            return content if content and content.isdigit() else '0'
    except FileNotFoundError:
        logger.info("ℹ️ last_processed_id.txt 文件不存在，进行首次全量抓取。")
        return '0'
    except Exception:
        logger.warning("⚠️ 警告：读取 last_processed_id.txt 文件出错，将使用 '0' 进行全量抓取。")
        return '0'

def save_last_id(new_id: str):
    """将本次处理的最大微博 ID 写入文件"""
    if new_id == '0': return
    try:
        with open('last_processed_id.txt', 'w', encoding='utf-8') as f:
            f.write(new_id)
    except Exception as e:
        logger.error(f"❌ 错误：无法写入 last_processed_id.txt 文件，本次增量状态无法保存。{e}")


# ---------------------------------------------------------------------------
# 智能评分检测器 (已优化“新款讲解”组合)
# ---------------------------------------------------------------------------
class SmartLaunchDetector:
    def __init__(self):
        # 终极上新关键词：最高优先级
        self.ULTIMATE_LAUNCH_KEYWORDS = {'现货上架', '已上架', '已开售', '开启购买'}

        # --- 时间关键词 ---
        self.STRONG_TIME_KEYWORDS = {'今晚': 30, '明晚': 30, '今晚八点': 35, '今晚8点': 35, '今晚7点': 35, '今晚七点': 35, '明天': 25, '后天': 25, '本周': 20, '本周末': 25, '月底': 20, '准时': 10, '稍后': 15, '即刻': 20, '立即': 20, '刚刚': 15, '现在': 15}
        self.TIME_PATTERNS = {r'\d{1,2}[:：]\d{2}': 35, r'[0-9一二三四五六七八九十]+点': 30, r'(\d{4}[-/年])?\d{1,2}[-/月]\d{1,2}日?': 30, r'\d{1,2}\.\d{1,2}': 30, r'\d{1,2}号': 25, r'周[一二三四五六日天]': 25}

        # --- 行动关键词 (已优化“新款”和“讲解/细节”权重) ---
        self.ACTION_KEYWORDS = {
            '现货上架': 40, '开启购买': 35, '会员先购': 35, 'VIP先购': 35, '补货': 35, '提前购': 35, '开拍': 30,
            '上架': 30, '发售': 30, '开售': 30, '现货': 30, '清仓': 30,
            
            '新款': 25,       # 【优化】权重提升
            '讲解': 15,       # 【新增】讲解
            '细节': 15,       # 【新增】细节
            '上新': 25,
            
            '释放': 25, '预售': 25, '先购': 25, '开放购买': 25, '上新通知': 25, '新品首发': 25,
            '补出': 20, '已开售': 20, '已上架': 20, '新品上市': 20,
            '新款预告': 15, '首批': 15, '第一批': 15, '🆕': 15,
            '更新了': 10, '带来了': 10, '带给大家': 10
        }
        self.GOLDEN_ACTION_KEYWORDS = ['上新通知', '现货上架', '开启购买', '会员先购', 'VIP先购', '提前购', '补货', '发售', '开售']
        
        # --- 组合规则 (已新增新款相关的组合) ---
        self.COMBO_RULES = {
            ('已上架', '网页链接'): 50,
            ('已上架', 'http'): 50,
            
            # 【优化】新增组合规则，保证新款讲解帖被捕捉
            ('新款', '讲解'): 15, 
            ('新款', '细节'): 15,
        }
        
        # --- 负面/排除关键词 ---
        self.NEGATIVE_KEYWORDS = {'进度': -40, '打样': -40, '调整': -30, '修改': -30, '确认': -30, '开发': -40, '研究': -40, '还在': -20, '还在改': -40, '还在调': -40, '面料': -10, '辅料': -10, '刺绣': -10, '样品': -20, '样衣': -20, '色卡': -20, '计划': -50, '预计': -30, '准备': -20, '快了': -20, '即将': -20, '近期': -30, '延迟':-60, '取消':-60, '停止':-60}
        self.POLLING_KEYWORDS = {'点点': -50, '要不要': -60, '怎么样': -50, '觉得': -40, '喜欢吗': -50}
        self.LOTTERY_KEYWORDS = {'抽奖': -40, '转发': -20, '参与条件': -30, '抽取':-40}
        self.SCORE_THRESHOLD = 45 # 保持阈值不变
        
        self.TYPE_KEYWORDS = {'新品首发': ['新品首发', '全新', '新款', '新品上市', '新款上线', '首批'],'热门补货': ['补货', '补出', '秒空', '返场'],'开启预售': ['预售', '开启预售', '意向金', '尺码登记'],'清仓活动': ['清仓'],'现货发售': ['现货', '上架', '发售', '释放', '开售']}


    def _classify_type(self, text: str) -> str:
        for type_name, keywords in self.TYPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text: return type_name
        return "上新动态"
    
    def _calculate_score(self, text: str, keywords: Dict[str, int], title_bonus: int = 0) -> (int, str):
        max_score = 0; best_word = ""; title_area = text[:35]
        for word, value in keywords.items():
            if word in text:
                score = value
                if title_bonus > 0 and word in title_area: score += title_bonus
                if score > max_score: max_score = score; best_word = word
        return max_score, best_word
        
    def _calculate_pattern_score(self, text: str, patterns: Dict[str, int]) -> (int, str):
        max_score = 0; best_match = ""
        for pattern, value in patterns.items():
            match = re.search(pattern, text)
            if match:
                if value > max_score: max_score = value; best_match = match.group(0)
        return max_score, best_match

    def _calculate_total_score(self, text: str) -> int:
        time_score, _ = self._calculate_score(text, self.STRONG_TIME_KEYWORDS)
        pattern_time_score, _ = self._calculate_pattern_score(text, self.TIME_PATTERNS)
        action_score, _ = self._calculate_score(text, self.ACTION_KEYWORDS, title_bonus=10)
        final_time_score = max(time_score, pattern_time_score)
        
        negative_score = sum(v for w, v in self.NEGATIVE_KEYWORDS.items() if w in text)
        polling_score = sum(v for w, v in self.POLLING_KEYWORDS.items() if w in text)
        lottery_score = sum(v for w, v in self.LOTTERY_KEYWORDS.items() if w in text)
        
        combo_score = 0
        for (word1, word2), score in self.COMBO_RULES.items():
            if word1 in text and word2 in text: combo_score += score
            
        return final_time_score + action_score + negative_score + polling_score + lottery_score + combo_score
    
    def check(self, text: str) -> Union[bool, Dict[str, Any]]:
        if not text: return False
        for word in self.ULTIMATE_LAUNCH_KEYWORDS:
            if word in text:
                logger.info(f"  -> 触发“终极信号”({word})，直接判定为上新帖！")
                return {"is_launch": True, "type": self._classify_type(text), "time": "即时", "action": word}

        time_score, best_time_word = self._calculate_score(text, self.STRONG_TIME_KEYWORDS)
        pattern_time_score, best_pattern_time = self._calculate_pattern_score(text, self.TIME_PATTERNS)
        action_score, best_action_word = self._calculate_score(text, self.ACTION_KEYWORDS, title_bonus=10)
        final_time_score = max(time_score, pattern_time_score)
        final_best_time = best_time_word if time_score >= pattern_time_score else best_pattern_time

        has_golden_action = any(word in text for word in self.GOLDEN_ACTION_KEYWORDS)
        if has_golden_action and final_time_score > 0:
            logger.info("    -> 触发“黄金信号”豁免机制，直接判定为上新帖！")
            return {"is_launch": True, "type": self._classify_type(text), "time": final_best_time, "action": best_action_word}
        
        total_score = self._calculate_total_score(text)
        
        if total_score >= self.SCORE_THRESHOLD:
            return {"is_launch": True, "type": self._classify_type(text), "time": final_best_time, "action": best_action_word}
        
        return False


# ---------------------------------------------------------------------------
# WeiboDataParser - 核心解析类
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
            logger.info(f"⚡ 检测到 {len(long_text_posts)} 条长微博，开始并行获取全文...")
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
        launch_posts = []
        for post in parsed_data:
            check_result = self.launch_detector.check(post.get('text_raw', ''))
            if isinstance(check_result, dict) and check_result.get("is_launch"):
                post['launch_details'] = check_result
                launch_posts.append(post)
        return launch_posts
        
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
        media = {'images': [], 'videos': []}; found_urls = set()
        def add_image(url):
            if url and isinstance(url, str) and url.startswith('http') and url not in found_urls: media['images'].append({'url': url}); found_urls.add(url)
        pic_infos = status.get('pic_infos', {})
        for pic_id in status.get('pic_ids', []):
            if pic_id in pic_infos:
                for size in ['large', 'original', 'bmiddle', 'thumbnail']:
                    if size in pic_infos[pic_id] and pic_infos[pic_id][size].get('url'): add_image(pic_infos[pic_id][size]['url']); break
        if 'page_info' in status and isinstance(status['page_info'], dict):
            page_info = status['page_info']
            if page_info.get('page_pic') and isinstance(page_info['page_pic'], dict): add_image(page_info['page_pic'].get('url'))
            elif isinstance(page_info.get('page_pic'), str): add_image(page_info['page_pic'])
            if page_info.get('media_info', {}).get('big_pic_info', {}).get('url'): add_image(page_info['media_info']['big_pic_info']['pic_big']['url'])
        if 'mix_media_info' in status and isinstance(status.get('mix_media_info'), dict):
            for item in status['mix_media_info'].get('items', []):
                if item.get('type') == 'pic' and isinstance(item.get('data'), dict):
                    for size in ['large', 'original', 'bmiddle', 'thumbnail']:
                        if size in item['data'] and item['data'][size].get('url'): add_image(item['data'][size]['url']); break
                elif item.get('type') == 'video' and isinstance(item.get('data'), dict):
                    if item['data'].get('page_pic'): add_image(item['data']['page_pic'])
        possible_keys = ['thumbnail_pic', 'bmiddle_pic', 'original_pic', 'pic', 'cover_image_url']
        for key in possible_keys:
            if key in status and isinstance(status[key], str) and (status[key].endswith('.jpg') or status[key].endswith('.png')): add_image(status[key])
        if 'retweeted_status' in status:
            retweeted_media = self._parse_media_content(status['retweeted_status'])
            for img in retweeted_media['images']: add_image(img['url'])
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
        except Exception as e: 
            logger.error(f"❌ 图片下载或转换失败: {image_url}. 错误: {e}")
            return None
            
    def send_wechat_text(self, text: str) -> bool:
        if not self.webhook_url: return False
        try:
            data = {"msgtype": "text", "text": {"content": text}}; response = requests.post(self.webhook_url, json=data, timeout=10, proxies=PROXIES_SETTING)
            if response.json().get('errcode') == 0: return True
            logger.error(f"❌ 文本推送失败。WeChat API 返回: {response.text}")
            return False
        except Exception as e: 
            logger.error(f"❌ 文本推送请求异常: {e}")
            return False
            
    def send_wechat_image(self, image_info: Dict[str, str]) -> bool:
        if not self.webhook_url: return False
        try:
            data = {"msgtype": "image", "image": {"base64": image_info['base64'], "md5": image_info['md5']}}
            response = requests.post(self.webhook_url, json=data, timeout=20, proxies=PROXIES_SETTING)
            if response.json().get('errcode') == 0: return True
            logger.error(f"❌ 图片推送失败。WeChat API 返回: {response.text}")
            return False
        except Exception as e: 
            logger.error(f"❌ 图片推送请求异常: {e}")
            return False

# ---------------------------------------------------------------------------
# 外部辅助函数
# ---------------------------------------------------------------------------

def fetch_one_page_of_posts(sub_cookie: str, max_id: Optional[str] = None) -> Dict[str, Any]:
    url="https://weibo.com/ajax/feed/groupstimeline";params={'list_id':GROUP_ID,'count':'50'}
    if max_id: params['max_id']=max_id
    headers = {'accept': 'application/json, text/plain, */*','accept-language': 'zh-CN,zh;q=0.9','client-version': 'v2.47.106','referer': f'https://weibo.com/mygroups?gid={GROUP_ID}','x-requested-with': 'XMLHttpRequest','user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0'}
    cookies={'SUB':sub_cookie}
    try:
        response=requests.get(url,params=params,headers=headers,cookies=cookies,timeout=15, proxies=PROXIES_SETTING)
        return response.json()
    except Exception as e:
        logger.error(f"    - 网络请求异常: {e}")
        return {}

def fetch_weibo_data(sub_cookie: str, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
    all_fetched_statuses = []; max_id = None
    logger.info("🚀 正在抓取微博列表...")
    for page in range(PAGE_LIMIT):
        data = fetch_one_page_of_posts(sub_cookie, max_id)
        statuses = data.get('statuses', [])
        if page == 0 and not statuses: return []
        if not statuses: break
        all_fetched_statuses.extend(statuses)
        
        try:
            last_post_time = datetime.strptime(statuses[-1]['created_at'], "%a %b %d %H:%M:%S %z %Y")
            if last_post_time < start_time.astimezone(last_post_time.tzinfo):
                logger.info(f"ℹ️ 第 {page+1} 页帖子已早于时间窗口起点，提前停止抓取。")
                break
        except (ValueError, KeyError): pass
        
        max_id = data.get('max_id_str')
        if not max_id or max_id == "0": break
        time.sleep(1)
        
    final_statuses = []
    for status in all_fetched_statuses:
        try:
            post_time = datetime.strptime(status['created_at'], "%a %b %d %H:%M:%S %z %Y").astimezone(start_time.tzinfo)
            if start_time <= post_time <= end_time:
                final_statuses.append(status)
        except (ValueError, KeyError): continue
        
    return final_statuses

def format_launch_notification(launch_info: Dict[str, Any]) -> str:
    user_name = launch_info['user']['screen_name']; details = launch_info.get('launch_details', {})
    launch_type = details.get('type', '上新动态'); title = f"🛍️【{launch_type} | {user_name}】"
    key_info = []
    if details.get('time'): key_info.append(f"🕒 时间: {details['time']}")
    if details.get('action'): key_info.append(f"🔑 动作: {details['action']}")
    key_info_str = "\n".join(key_info)
    content = re.sub(r'https?://t\.cn/\w+', '', launch_info['text_raw']); content = re.sub(r'\s+', ' ', content).strip()
    if len(content) > 300: content = content[:300] + "..."
    real_links = launch_info.get('real_links', []); links_str = ""
    if real_links:
        links_str += "\n\n🔗 直达链接:"
        for i, link in enumerate(real_links): links_str += f"\n{i+1}. {link}"
    message = f"{title}\n\n{key_info_str}"
    if key_info_str: message += "\n- - - - - - - - - - - - - - -"
    message += f"\n💬 {content}{links_str}"
    return message

def send_launch_notifications(parser: WeiboDataParser, launch_posts: List[Dict[str, Any]]):
    logger.info(f"\n📨 开始推送 {len(launch_posts)} 条上新预告到企业微信...")
    for post in launch_posts:
        text_content = format_launch_notification(post)
        if parser.send_wechat_text(text_content):
            logger.info(f"    ✅ 文本推送成功: {post['user']['screen_name']}")
            time.sleep(1) 
            images = post.get('media', {}).get('images', [])
            for i, image in enumerate(images[:2]):
                image_info = parser.download_and_convert_image(image['url'])
                if image_info and parser.send_wechat_image(image_info): logger.info(f"      - 图片 {i+1} 发送成功")
                else: logger.warning(f"      - 图片 {i+1} 发送失败")
                time.sleep(0.5) 
        else:
            logger.error(f"    ❌ 文本推送失败: {post['user']['screen_name']}")
        time.sleep(2) 

def check_cookie_status(sub_cookie: str, current_statuses: List[Dict[str, Any]]) -> bool:
    if current_statuses: return True
    logger.warning("⚠️ 当前时间窗口未抓取到任何帖子，启动 Cookie 有效性二次验证...")
    
    beijing_tz=pytz.timezone('Asia/Shanghai'); now_beijing=datetime.now(beijing_tz)
    end_time = now_beijing - timedelta(hours=1)
    start_time = now_beijing - timedelta(hours=12)
    
    logger.info(f"    - 正在回溯检查上一时间段 ({start_time.strftime('%H:%M')} → {end_time.strftime('%H:%M')})...")
    
    global PAGE_LIMIT 
    original_page_limit = PAGE_LIMIT
    PAGE_LIMIT = 5 
    previous_statuses = fetch_weibo_data(sub_cookie, start_time, end_time)
    PAGE_LIMIT = original_page_limit
    
    if previous_statuses:
        logger.info("    ✅ 在上一时间段找到数据，判定 Cookie 有效，当前时段确实无新帖。")
        return True
    else:
        logger.error("    ❌ 当前及上一时间段均未找到任何数据，判定 Cookie 或请求头已失效！")
        return False

# ---------------------------------------------------------------------------
# 主执行逻辑 (execute_monitoring 函数)
# ---------------------------------------------------------------------------

def execute_monitoring(sub_cookie: str, webhook_url: Optional[str] = None, enable_push: bool = True):
    beijing_tz=pytz.timezone('Asia/Shanghai'); now_beijing=datetime.now(beijing_tz); current_hour=now_beijing.hour
    
    # 【动态时间窗口】
    hours_to_back = 2 if 8 <= current_hour <= 23 else 8
    window_desc = f"最近 {hours_to_back} 小时 ({'高频监控' if 8 <= current_hour <= 23 else '夜间补偿'})"
        
    start_time = now_beijing - timedelta(hours=hours_to_back)
    end_time = now_beijing
    
    start_time = start_time.replace(second=0, microsecond=0); end_time = end_time.replace(second=0, microsecond=0)
    logger.info(f"📅 设定动态回溯窗口: {start_time.strftime('%Y-%m-%d %H:%M')} → {end_time.strftime('%Y-%m-%d %H:%M')} ({window_desc})")
    
    # 【增量更新】1. 加载上次处理的 ID
    last_processed_id_str = load_last_id()
    try:
        last_processed_id = int(last_processed_id_str)
    except ValueError:
        last_processed_id = 0
        
    logger.info(f"➡️ 上次处理的最新 ID: {last_processed_id}")
    
    raw_statuses = fetch_weibo_data(sub_cookie, start_time, end_time)
    
    if not check_cookie_status(sub_cookie, raw_statuses):
        if enable_push:
            parser = WeiboDataParser(sub_cookie, webhook_url=webhook_url)
            error_message = "⚠️ 微博监控失败\n\n原因: Cookie或请求头可能已失效\n连续两个时间段未抓取到任何数据，请及时更新。"; 
            if parser.send_wechat_text(error_message): logger.info("✅ 失效通知发送成功。")
        sys.exit(1)
    
    if not raw_statuses: logger.info("🏁 本次未抓取到任何符合时间窗口的微博，程序结束。"); return
    
    # 【增量更新】2. 过滤掉 ID 小于或等于上次处理 ID 的帖子
    new_raw_statuses = [
        s for s in raw_statuses 
        if int(s.get('idstr', '0')) > last_processed_id
    ]
    
    if not new_raw_statuses:
        logger.info("ℹ️ 抓取到的所有帖子 ID 都小于等于上次的记录，没有新帖需要处理。"); return
        
    logger.info(f"\n📊 抓取完成，共获得 {len(raw_statuses)} 条原始微博。其中 {len(new_raw_statuses)} 条为新帖待处理。")
    
    # 找出本次所有新帖中的最大 ID，用于保存
    current_max_id_str = max(s['idstr'] for s in new_raw_statuses)
    
    parser = WeiboDataParser(sub_cookie, webhook_url=webhook_url)
    parsed_data = parser.parse_and_enrich(new_raw_statuses) 
    
    logger.info("🔍 开始使用智能评分算法进行上新帖识别...")
    launch_posts = parser.filter_launch_posts(parsed_data)

    if launch_posts:
        logger.info(f"✅ 识别成功！共找到 {len(launch_posts)} 条上新帖。")
        if enable_push: send_launch_notifications(parser,launch_posts)
        
    # 【增量更新】3. 保存本次处理的最大 ID，覆盖旧文件
    save_last_id(current_max_id_str)
    logger.info(f"💾 已将本次最新 ID ({current_max_id_str}) 写入 last_processed_id.txt，待 Git 提交。")

    logger.info("\n🏁 所有任务执行完毕。")

if __name__ == "__main__":
    webhook_url = os.getenv('WECHAT_WEBHOOK_URL')
    enable_push = '--no-push' not in sys.argv
    sub_cookie = os.getenv('WEIBO_SUB_COOKIE') or DEFAULT_SUB_COOKIE

    beijing_tz = pytz.timezone('Asia/Shanghai'); start_time = datetime.now(beijing_tz)
    logger.info(f"🚀 程序启动于: {start_time.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
    
    execute_monitoring(sub_cookie, webhook_url, enable_push)
