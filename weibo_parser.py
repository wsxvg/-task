#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博抢购增量监控 V8.5 (集成自动登录修复 - 完整版)
"""
import json
import re
import os
import sys
import time
import random
import logging
import hashlib
import base64
import pytz
import requests
from io import BytesIO
from typing import Dict, List, Optional, Any, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from dateutil import parser

try:
    from PIL import Image
except ImportError:
    Image = None

# ---------- 全局配置 ----------
# ⚠️ 你的分组 ID
GROUP_ID = '5159683220312291'
MAX_WORKERS = 10
PAGE_LIMIT = 20
DEFAULT_SUB_COOKIE = "请替换为您的 SUB Cookie"
PROXIES_SETTING = {"http": None, "https": None}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------- 增量 ID 辅助 ----------
def load_last_id() -> str:
    """从 last_processed_id.txt 读取上次处理的 ID"""
    try:
        with open('last_processed_id.txt', 'r', encoding='utf-8') as f:
            content = f.read().strip()
            return content if content.isdigit() else '0'
    except Exception:
        return '0'

def save_last_id(new_id: str):
    """将最新的 ID 写入文件"""
    if new_id == '0':
        return
    try:
        with open('last_processed_id.txt', 'w', encoding='utf-8') as f:
            f.write(new_id)
    except Exception as e:
        logger.error(f'❌ 写入 last_processed_id.txt 失败: {e}')

# ---------- 智能评分器 (V8.3.5) ----------
class SmartLaunchDetector:
    def __init__(self):
        # 终极信号，直接判定
        self.ULTIMATE_LAUNCH_KEYWORDS = {'现货上架', '已上架', '已开售', '开启购买', '释放库存'}
        
        # 鲁棒性增强: 高分组合模式
        self.LAUNCH_PATTERNS = {
            r'(\d{1,2}([:：]|\.)\d{2}|[0-9一二三四五六七八九十]+点).*?(\S{0,20}).*?(上架|开售|发售|补款|释放|开拍|提前购|会员先购|预售|开启预售|售价|特惠|抢购|秒杀)': 85,
            r'(今晚|明晚|今天|明天|周[一二三四五六日天]).*?(\d{1,2}([:：]|\.)\d{2}|[0-9一二三四五六七八九十]+点)': 40, 
        }
        
        # 时间关键词
        self.STRONG_TIME_KEYWORDS = {'今晚': 30, '明晚': 30, '今晚八点': 35, '今晚8点': 35,
                                     '今晚7点': 35, '今晚七点': 35, '明天': 25, '后天': 25,
                                     '本周': 20, '本周末': 25, '月底': 20, '准时': 10,
                                     '稍后': 15, '即刻': 20, '立即': 20, '刚刚': 15, '现在': 15}
        # 时间正则模式
        self.TIME_PATTERNS = {r'\d{1,2}[:：]\d{2}': 35, r'[0-9一二三四五六七八九十]+点': 30,
                              r'(\d{4}[-/年])?\d{1,2}[-/月]\d{1,2}日?': 30, r'\d{1,2}\.\d{1,2}': 30,
                              r'\d{1,2}号': 25, r'周[一二三四五六日天]': 25}
        
        # 动作关键词
        self.ACTION_KEYWORDS = {
            '现货上架': 40, '会员先购': 35, 'VIP先购': 35, '补货': 35, '提前购': 35, '开拍': 30, 
            '上架': 35, '发售': 25, '开售': 25, '现货': 30,
            '补款': 45, '付尾款': 45, '开启购买': 50, '开启预售': 50,
            '预售': 45, '第二批预售': 45,
            '特惠': 45, '售价': 45, '抢购': 40, '秒杀': 40, '开团': 35, '预定': 35,
            '清仓': 30, '新款': 25, '讲解': 15, '细节': 15, '上新': 25,
            '释放': 35, '先购': 25, '开放购买': 25, '上新通知': 25,
            '新品首发': 25, '已开售': 20, '已上架': 20,
            '更新了': 10, '带来了': 10, '带给大家': 10
        }
        
        # 黄金信号关键词
        self.GOLDEN_ACTION_KEYWORDS = ['上新通知', '现货上架', '开启购买', '会员先购',
                                             'VIP先购', '提前购', '补货', '发售', '开售', 
                                             '补款', '付尾款', '释放', '上架',
                                             '预售', '开启预售', '特惠', '售价', '抢购']
        self.COMBO_RULES = {
            ('已上架', '网页链接'): 50, ('已上架', 'http'): 50,
            ('新款', '讲解'): 15, ('新款', '细节'): 15,
        }
        
        # 负面关键词
        self.NEGATIVE_KEYWORDS = {'进度': -40, '打样': -40, '调整': -30, '修改': -30,
                                     '确认': -30, '开发': -40, '研究': -40, '还在': -20,
                                     '还在改': -40, '还在调': -40, '面料': -10, '辅料': -10,
                                     '刺绣': -10, '样品': -20, '样衣': -20, '色卡': -20,
                                     '计划': -50, '预计': -15, '准备': -20, '快了': -20,
                                     '即将': -20, '近期': -30, '延迟': -60, '取消': -60, '停止': -60,
                                     '避雷': -35, '科普': -20, '测评': -20, '教别人做': -40, '对比': -20}
        self.POLLING_KEYWORDS = {'点点': -50, '要不要': -60, '怎么样': -50,
                                     '觉得': -40, '喜欢吗': -50}
        
        self.LOTTERY_KEYWORDS = {}
        self.SCORE_THRESHOLD = 45
        
        # 分类关键词
        self.TYPE_KEYWORDS = {
            '新品首发': ['新品首发', '全新', '新款', '新品上市', '新款上线', '首批'],
            '热门补货': ['补货', '补出', '秒空', '返场'],
            '开启预售': ['预售', '开启预售', '意向金', '尺码登记', '补款', '付尾款'], 
            '清仓活动': ['清仓'],
            '现货发售': ['现货', '上架', '发售', '释放', '开售']
        }
    
    def _classify_type(self, text: str) -> str:
        for t, ks in self.TYPE_KEYWORDS.items():
            if any(k in text for k in ks):
                return t
        if '特惠' in text or '售价' in text or '抢购' in text:
            return '优惠活动'
        return '上新动态'

    def _calculate_score(self, text: str, kw: Dict[str, int], bonus: int = 0) -> (int, str):
        s, w = 0, ''
        for k, v in kw.items():
            if k in text:
                tmp = v + (bonus if k in text[:35] else 0)
                if tmp > s:
                    s, w = tmp, k
        return s, w

    def _calculate_pattern_score(self, text: str, pt: Dict[str, int]) -> (int, str):
        s, m = 0, ''
        for p, v in pt.items():
            match = re.search(p, text)
            if match:
                if v > s:
                    s, m = v, match.group(0)
        return s, m

    def _calculate_total_score(self, text: str) -> int:
        t1, _ = self._calculate_score(text, self.STRONG_TIME_KEYWORDS)
        t2, _ = self._calculate_pattern_score(text, self.TIME_PATTERNS)
        a1, _ = self._calculate_score(text, self.ACTION_KEYWORDS, 10)
        neg = sum(v for k, v in self.NEGATIVE_KEYWORDS.items() if k in text)
        pol = sum(v for k, v in self.POLLING_KEYWORDS.items() if k in text)
        lot = sum(v for k, v in self.LOTTERY_KEYWORDS.items() if k in text) 
        
        combo = 0
        for (w1, w2), v in self.COMBO_RULES.items():
            if w1 in text and w2 in text:
                combo += v
        
        combo_score_total, _ = self._calculate_pattern_score(text, self.LAUNCH_PATTERNS)
        
        return max(t1, t2, combo_score_total) + a1 + neg + pol + lot + combo

    def check(self, text: str) -> Union[bool, Dict[str, Any]]:
        if not text:
            return False
        
        # 1. 终极信号检测
        for w in self.ULTIMATE_LAUNCH_KEYWORDS:
            if w in text:
                return {'is_launch': True, 'type': self._classify_type(text),
                        'time': '即时', 'action': w}
        
        # 2. 高分组合模式检测
        combo_score, combo_match = self._calculate_pattern_score(text, self.LAUNCH_PATTERNS)
        if combo_score >= self.SCORE_THRESHOLD:
            return {'is_launch': True, 'type': self._classify_type(text),
                    'time': combo_match, 'action': '组合判断'}
            
        # 计算得分
        t_score, t_word = self._calculate_score(text, self.STRONG_TIME_KEYWORDS)
        pt_score, pt_word = self._calculate_pattern_score(text, self.TIME_PATTERNS)
        a_score, a_word = self._calculate_score(text, self.ACTION_KEYWORDS, 10)
        best_time = t_word if t_score >= pt_score else pt_word
        
        # 3. 黄金信号检测 (动作+时间)
        is_golden_action = any(k in text for k in self.GOLDEN_ACTION_KEYWORDS)
        if is_golden_action and max(t_score, pt_score) > 0:
            return {'is_launch': True, 'type': self._classify_type(text),
                    'time': best_time, 'action': a_word}
        
        # 4. 阈值检测
        total = self._calculate_total_score(text)
        if total >= self.SCORE_THRESHOLD:
            return {'is_launch': True, 'type': self._classify_type(text),
                    'time': best_time, 'action': a_word}
        
        return False

# ---------- 微博解析核心 ----------
class WeiboDataParser:
    def __init__(self, sub_cookie: str, webhook_url: Optional[str] = None):
        self.sub_cookie = sub_cookie
        self.webhook_url = webhook_url
        self.detector = SmartLaunchDetector()

    def _fetch_full_text(self, post_id: str) -> Optional[Dict[str, Any]]:
        time.sleep(random.uniform(0.3, 0.8))
        url = 'https://weibo.com/ajax/statuses/longtext'
        params = {'id': post_id}
        headers = {
            'accept': 'application/json, text/plain, */*',
            'x-requested-with': 'XMLHttpRequest',
            'user-agent': 'Mozilla/5.0',
            'referer': f'https://weibo.com/mygroups?gid={GROUP_ID}'
        }
        cookies = {'SUB': self.sub_cookie}
        try:
            r = requests.get(url, params=params, headers=headers, cookies=cookies,
                             timeout=10, proxies=PROXIES_SETTING)
            if r.ok and r.json().get('ok') == 1:
                long_text = r.json()['data']['longTextContent']
                long_text = re.sub(r'<br\s*/?>', '\n', long_text)
                long_text = re.sub(r'<.*?>', '', long_text)
                return {'post_id': post_id, 'full_text': long_text.strip()}
        except Exception:
            pass
        return {'post_id': post_id, 'full_text': None}

    def _resolve_short_link(self, short: str) -> Optional[str]:
        try:
            return requests.head(short, allow_redirects=True, timeout=10,
                                 proxies=PROXIES_SETTING).url
        except Exception:
            return None

    def _extract_and_resolve_links(self, text: str) -> List[str]:
        shorts = re.findall(r'https?://t\.cn/\w+', text)
        if not shorts:
            return []
        reals = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
            f2s = {exe.submit(self._resolve_short_link, s): s for s in set(shorts)}
            for f in as_completed(f2s):
                res = f.result()
                if res:
                    reals.append(res)
        return reals

    def parse_and_enrich(self, statuses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        long_posts = [s for s in statuses if s.get('isLongText')]
        long_map = {}
        if long_posts:
            logger.info(f'⚡ 检测到 {len(long_posts)} 条长微博，并行获取全文...')
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
                f2id = {exe.submit(self._fetch_full_text, s['idstr']): s['idstr']
                        for s in long_posts}
                for f in as_completed(f2id):
                    res = f.result()
                    if res and res.get('full_text'):
                        long_map[res['post_id']] = res['full_text']
        parsed = []
        for s in statuses:
            if s.get('readtimetype') == 'adMblog':
                continue
            post_id = s['idstr']
            
            if post_id in long_map:
                s['text_raw'] = long_map[post_id]
            
            if 'text_raw' not in s:
                s['text_raw'] = s.get('text', '')
                s['text_raw'] = re.sub(r'<[^>]+>', '', s['text_raw']).strip()

            item = self._parse_single_status(s)
            
            if item:
                check_result = self.detector.check(item['text_raw'])
                if isinstance(check_result, dict) and check_result.get('is_launch'):
                    item['launch_details'] = check_result
                
                item['real_links'] = self._extract_and_resolve_links(item['text_raw'])
                parsed.append(item)
        return parsed

    def filter_launch_posts(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [d for d in data if d.get('launch_details')]

    def _parse_single_status(self, s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            is_ret = 'retweeted_status' in s
            obj = s['retweeted_status'] if is_ret else s
            basic = self._parse_basic(s, is_ret)
            user = self._parse_user(s.get('user', {}))
            inter = self._parse_interaction(s)
            
            media = self._parse_media(obj)
            
            ori_user = self._parse_user(obj.get('user', {})) if is_ret else None
            if is_ret:
                basic['original_text_raw'] = obj.get('text_raw', '')
                
            return {**basic, 'user': user, 'interaction': inter,
                    'media': media, 'original_user': ori_user, 'text_raw': basic['text_raw']}
        except Exception as e:
            logger.error(f"解析单条微博失败: {e}")
            return None

    def _parse_basic(self, s, is_ret):
        text_raw = s.get('text_raw', '')
        if not text_raw:
            text_raw = s.get('text', '')
            text_raw = re.sub(r'<[^>]+>', '', text_raw).strip()

        return {
            'id': s.get('idstr', s.get('id')),
            'text_raw': text_raw,
            'created_at': self._parse_time(s.get('created_at')),
            'source': re.sub(r'<[^>]+>', '', s.get('source', '')).strip(),
            'is_retweet': is_ret
        }

    def _parse_user(self, u):
        return {
            'screen_name': u.get('screen_name', ''),
            'user_id': u.get('idstr', u.get('id', ''))
        }

    def _parse_interaction(self, s):
        return {
            'reposts_count': s.get('reposts_count', 0),
            'comments_count': s.get('comments_count', 0),
            'attitudes_count': s.get('attitudes_count', 0)
        }

    def _parse_time(self, t: str) -> str:
        try:
            return parser.parse(t).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return t or ''

    def _parse_media(self, s: Dict[str, Any]) -> Dict[str, Any]:
        media = {'images': [], 'videos': []}
        seen_urls = set()

        def add_img(url: str):
            if url and url.startswith('http') and url not in seen_urls:
                if 'thumb180' in url and 'large' not in url:
                    url = url.replace('thumb180', 'large')
                url = re.sub(r'orj\d{2,4}', 'orj1080', url) 
                media['images'].append({'url': url})
                seen_urls.add(url)
        
        pic_infos = s.get('pic_infos', {})
        for pid in s.get('pic_ids', []):
            if pid in pic_infos:
                found_url = None
                for sz in ['large', 'original', 'bmiddle', 'thumbnail']:
                    if sz in pic_infos[pid] and pic_infos[pid][sz].get('url'):
                        found_url = pic_infos[pid][sz]['url']
                        break
                if found_url:
                    add_img(found_url)

        page_info = s.get('page_info', {})
        cover_url = None
        if page_info.get('page_pic'):
            if isinstance(page_info['page_pic'], dict) and page_info['page_pic'].get('url'):
                cover_url = page_info['page_pic']['url']
            elif isinstance(page_info['page_pic'], str):
                cover_url = page_info['page_pic']
        if cover_url:
            add_img(cover_url)

        if page_info.get('type') == 'video':
            video_url = page_info.get('media_info', {}).get('mp4_720p_mp4') or \
                        page_info.get('media_info', {}).get('mp4_hd_url') or \
                        page_info.get('media_info', {}).get('stream_url')
            if video_url:
                media['videos'].append({'url': video_url, 'cover_url': cover_url})
                
        if not media['images'] and s.get('bmiddle_pic'):
            add_img(s['bmiddle_pic'])
            
        return media
    
    def download_and_convert_image(self, url: str) -> Optional[Dict[str, str]]:
        if not Image:
            return None
        try:
            headers = {
                "Referer": "https://weibo.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
            }
            r = requests.get(url, headers=headers, timeout=10, proxies=PROXIES_SETTING)
            if r.status_code != 200:
                return None
            
            data = r.content
            if len(data) > 2 * 1024 * 1024:
                img = Image.open(BytesIO(data))
                img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.save(buf, format='JPEG', quality=85)
                data = buf.getvalue()
                
            return {'base64': base64.b64encode(data).decode('utf-8'),
                    'md5': hashlib.md5(data).hexdigest()}
        except Exception as e:
            logger.error(f'❌ 图片处理失败: {e}')
            return None

    def send_wechat_text(self, text: str) -> bool:
        if not self.webhook_url:
            return False
        try:
            r = requests.post(self.webhook_url,
                             json={'msgtype': 'text', 'text': {'content': text}},
                             timeout=10, proxies=PROXIES_SETTING)
            return r.json().get('errcode') == 0
        except Exception as e:
            logger.error(f'❌ 文本推送异常: {e}')
            return False

    def send_wechat_image(self, img: Dict[str, str]) -> bool:
        if not self.webhook_url:
            return False
        try:
            r = requests.post(self.webhook_url,
                             json={'msgtype': 'image', 'image': img},
                             timeout=20, proxies=PROXIES_SETTING)
            return r.json().get('errcode') == 0
        except Exception as e:
            logger.error(f'❌ 图片推送异常: {e}')
            return False

# ---------- 外部 API ----------
def fetch_one_page(sub_cookie: str, max_id: Optional[str] = None) -> Dict[str, Any]:
    url = 'https://weibo.com/ajax/feed/groupstimeline'
    params = {'list_id': GROUP_ID, 'count': '50'}
    if max_id:
        params['max_id'] = max_id
    headers = {
        'accept': 'application/json, text/plain, */*',
        'referer': f'https://weibo.com/mygroups?gid={GROUP_ID}',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    cookies = {'SUB': sub_cookie}
    try:
        r = requests.get(url, params=params, headers=headers, cookies=cookies,
                         timeout=15, proxies=PROXIES_SETTING)
        return r.json()
    except Exception as e:
        logger.error(f'    - API 请求失败: {e}')
        return {}

def fetch_weibo_data(sub_cookie: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    all_stat = []
    max_id = None
    for page in range(PAGE_LIMIT):
        logger.info(f'🔄 正在抓取第 {page+1} 页数据...')
        data = fetch_one_page(sub_cookie, max_id)
        
        # 关键：判断 cookie 是否失效
        if page == 0 and data.get('ok') != 1:
             # 微博API如果未登录，通常会返回 ok!=1 或者 http code 非 200
             # 这里返回空，交给上层去 check_cookie_status 进一步验证
             logger.warning('⚠️ API 返回非 OK 状态，疑似 Cookie 问题。')
             break

        statuses = data.get('statuses', [])
        if not statuses:
            break
            
        newly_fetched_count = 0
        for s in statuses:
            try:
                if s.get('readtimetype') == 'adMblog': continue
                post_time = parser.parse(s['created_at'])
                if post_time < start:
                    logger.info(f'🔍 时间过早，停止分页。')
                    return all_stat
                if post_time <= end:
                    all_stat.append(s)
                    newly_fetched_count += 1
            except Exception:
                continue
                
        max_id = data.get('max_id_str')
        if not max_id or max_id == '0' or newly_fetched_count == 0:
            break
        time.sleep(random.uniform(1.0, 2.0))
        
    return all_stat

def format_launch_notification(info: Dict[str, Any]) -> str:
    user = info['user']['screen_name']
    detail = info.get('launch_details', {})
    lt = info.get('created_at', '')
    
    raw_time = detail.get('time', '')
    detail_time = raw_time
    
    if raw_time and detail.get('action') == '组合判断':
        clean_time = re.sub(r'(上架|开售|发售|补款|释放|开拍|提前购|会员先购|预售|开启预售|售价|特惠|抢购|秒杀|\s*[:：,.。，]\s*)$', '', raw_time)
        detail_time = clean_time.strip()
    elif raw_time and len(raw_time) <= 6:
        idx = info['text_raw'].find(raw_time)
        if idx != -1:
            context = info['text_raw'][max(0, idx - 8):min(len(info['text_raw']), idx + len(raw_time) + 8)]
            match = re.search(r'(今晚|明晚|明天|今天|周[一二三四五六日天]).*?(\d{1,2}([:：]\d{2}|\.\d{2}|点)|[0-9一二三四五六七八九十]+点)', context)
            if match:
                detail_time = match.group(0).strip()
    
    t = f'🕒 发帖时间: {lt}\n' if lt else ''
    tp = detail.get('type', '上新动态')
    title = f'🛍️【{tp} | {user}】'
    lines = [title]
    if detail_time: lines.append(f'⏰ 预告时间: {detail_time}')
    if detail.get('action') and detail.get('action') != '组合判断': lines.append(f'🔑 动作: {detail["action"]}')
        
    content = re.sub(r'https?://t\.cn/\w+', '', info['text_raw'])
    content = re.sub(r'\s+', ' ', content).strip()
    if len(content) > 300: content = content[:300] + '...'
    
    links = info.get('real_links', [])
    link_str = ''
    if links:
        link_str = '\n\n🔗 直达链接:\n' + '\n'.join(f'{i+1}. {l}' for i, l in enumerate(links[:3]))
    
    video_str = ''
    if info.get('media', {}).get('videos', []):
        video_str = f'\n\n🎥 **[含视频]**'
            
    msg = f'{'\n'.join(lines)}\n{t}\n━━━━━━━━━━━━━━━━━━\n💬 {content}{video_str}{link_str}'
    return msg

def send_launch_notifications(parser_obj: WeiboDataParser, posts: List[Dict[str, Any]]):
    posts.reverse()
    logger.info(f'📨 推送 {len(posts)} 条上新预告...')
    for p in posts:
        text = format_launch_notification(p)
        if parser_obj.send_wechat_text(text):
            logger.info(f'    ✅ 推送: {p["user"]["screen_name"]}')
            time.sleep(1)
            images_to_send = p.get('media', {}).get('images', [])
            for i, img in enumerate(images_to_send[:2]):
                ii = parser_obj.download_and_convert_image(img['url'])
                if ii and parser_obj.send_wechat_image(ii):
                    logger.info(f'      - 图片 {i+1} ok')
                time.sleep(0.5)
        time.sleep(2)

def check_cookie_status(sub_cookie: str, curr: List[Dict[str, Any]]) -> bool:
    if curr: return True
    logger.warning('⚠️ 列表为空，验证 Cookie 有效性...')
    # 简单请求测试
    url = 'https://weibo.com/ajax/feed/groupstimeline'
    params = {'list_id': GROUP_ID, 'count': '1'}
    headers = {'user-agent': 'Mozilla/5.0', 'referer': f'https://weibo.com/mygroups?gid={GROUP_ID}'}
    try:
        r = requests.get(url, params=params, headers=headers, cookies={'SUB': sub_cookie}, timeout=10)
        # 414 或 403 或 passport 跳转代表失效
        if r.status_code != 200 or 'passport' in r.url:
            return False
        # 进一步检查内容
        if r.json().get('ok') != 1:
            return False
        return True
    except Exception:
        return False

def execute_monitoring(sub_cookie: str, webhook_url: Optional[str] = None, enable_push: bool = True):
    beijing = pytz.timezone('Asia/Shanghai')
    now_beijing = datetime.now(beijing)
    
    # 简单回溯策略：如果 8-23点 回溯1小时，否则4小时
    hours_back = 1 if 8 <= now_beijing.hour <= 23 else 4
    start_time = now_beijing - timedelta(hours=hours_back, minutes=5)
    end_time = now_beijing

    last_id = int(load_last_id())
    logger.info(f'➡️ Last ID: {last_id}')

    raw_statuses = fetch_weibo_data(sub_cookie, start_time, end_time)

    # 如果没抓到数据，必须检查是否是 Cookie 失效
    if not raw_statuses:
        if not check_cookie_status(sub_cookie, raw_statuses):
            logger.error("🚨 Cookie 已失效，抛出异常以触发登录流程。")
            raise ConnectionRefusedError("COOKIE_EXPIRED")
        logger.info('🏁 无数据。')
        return

    new_statuses = [s for s in raw_statuses if int(s.get('idstr', '0')) > last_id]
    if not new_statuses:
        logger.info('ℹ️ 无新帖。')
        return

    current_max_id = max(int(s['idstr']) for s in new_statuses)
    logger.info(f'📊 新增 {len(new_statuses)} 条。')

    parser_obj = WeiboDataParser(sub_cookie, webhook_url)
    parsed = parser_obj.parse_and_enrich(new_statuses)
    launches = parser_obj.filter_launch_posts(parsed)

    if launches and enable_push and webhook_url:
        send_launch_notifications(parser_obj, launches)

    save_last_id(str(current_max_id))
    logger.info(f'💾 更新 ID: {current_max_id}')

if __name__ == '__main__':
    webhook = os.getenv('WECHAT_WEBHOOK_URL')
    enable_push = '--no-push' not in sys.argv
    cookie = os.getenv('WEIBO_SUB_COOKIE')
    
    if not cookie or cookie == DEFAULT_SUB_COOKIE:
        logger.error('🚨 未配置 WEIBO_SUB_COOKIE')
        sys.exit(1)

    try:
        execute_monitoring(cookie, webhook, enable_push)
    except ConnectionRefusedError as e:
        if str(e) == "COOKIE_EXPIRED":
            logger.info("\n⚡ [AutoFix] 启动自动修复流程...")
            try:
                from weibo_login import WeiboQRLogin
                login_bot = WeiboQRLogin()
                # 运行登录逻辑
                new_sub = login_bot.run_login_process()
                
                if new_sub:
                    logger.info("✅ 登录成功！正在导出新 Cookie...")
                    # 写入 GITHUB_OUTPUT
                    if "GITHUB_OUTPUT" in os.environ:
                        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                            f.write(f"NEW_SUB_COOKIE={new_sub}\n")
                    sys.exit(0) # 成功退出
                else:
                    logger.error("❌ 自动登录失败或超时。")
                    sys.exit(1)
            except ImportError:
                logger.error("❌ 找不到 weibo_login.py，无法修复。")
                sys.exit(1)
            except Exception as login_err:
                logger.error(f"❌ 登录出错: {login_err}")
                sys.exit(1)
        else:
            sys.exit(1)
    except Exception as e:
        logger.error(f"❌ 运行错误: {e}")
        sys.exit(1)
