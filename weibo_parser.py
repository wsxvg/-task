#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博API数据解析器 V8.3.4 (终极增强版 - 移除抽奖负分并按时间正序推送，增强图片防盗链)
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
from dateutil import parser  # 确保解析微博时间更稳

try:
    from PIL import Image
except ImportError:
    Image = None

# ---------- 全局配置 ----------
MAX_WORKERS = 10
GROUP_ID = '5159683220312291'      # ← 你的分组 ID
PAGE_LIMIT = 20
DEFAULT_SUB_COOKIE = "请替换为您的 SUB Cookie"
PROXIES_SETTING = {"http": None, "https": None}
# ------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------- 增量 ID 辅助 ----------
def load_last_id() -> str:
    """从 last_processed_id.txt 读取上次处理的 ID，如果文件不存在或内容非法则返回 '0'。"""
    try:
        with open('last_processed_id.txt', 'r', encoding='utf-8') as f:
            content = f.read().strip()
            return content if content.isdigit() else '0'
    except Exception:
        return '0'

def save_last_id(new_id: str):
    """将最新的 ID 写入文件。"""
    if new_id == '0':
        return
    try:
        with open('last_processed_id.txt', 'w', encoding='utf-8') as f:
            f.write(new_id)
    except Exception as e:
        logger.error(f'❌ 写入 last_processed_id.txt 失败: {e}')

# ---------- 智能评分器 (V8.3.4) ----------
class SmartLaunchDetector:
    def __init__(self):
        # 终极信号，直接判定
        self.ULTIMATE_LAUNCH_KEYWORDS = {'现货上架', '已上架', '已开售', '开启购买', '释放库存'}
        
        # 鲁棒性增强: 高分组合模式
        self.LAUNCH_PATTERNS = {
            r'(\d{1,2}([:：]|\.)\d{2}|[0-9一二三四五六七八九十]+点).*?(\S{0,20}).*?(上架|开售|发售|补款|释放|开拍|提前购|会员先购|预售|开启预售)': 85,
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
            '上架': 35,
            
            '发售': 25,      # 调整: 从 30 降到 25
            '开售': 25,      # 调整: 从 30 降到 25
            '现货': 30,
            
            '补款': 45,      
            '付尾款': 45,    
            '开启购买': 50, 
            '开启预售': 50,
            '预售': 45,      
            '第二批预售': 45,
            
            '清仓': 30, '新款': 25, '讲解': 15, '细节': 15, '上新': 25,
            '释放': 35, 
            '先购': 25, '开放购买': 25, '上新通知': 25,
            '新品首发': 25, '已开售': 20, '已上架': 20,
            '更新了': 10, '带来了': 10, '带给大家': 10
        }
        
        # 黄金信号关键词 (用于触发豁免)
        self.GOLDEN_ACTION_KEYWORDS = ['上新通知', '现货上架', '开启购买', '会员先购',
                                             'VIP先购', '提前购', '补货', '发售', '开售', 
                                             '补款', '付尾款', '释放', '上架',
                                             '预售', '开启预售']
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
                                     '避雷': -35,
                                     '科普': -20,
                                     '测评': -20,
                                     '教别人做': -40,
                                     '对比': -20}
        self.POLLING_KEYWORDS = {'点点': -50, '要不要': -60, '怎么样': -50,
                                     '觉得': -40, '喜欢吗': -50}
        
        # 🚨 关键修改: 删除抽奖负分
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
                logger.info(f'  -> 触发“终极信号”({w})，直接判定为上新帖！')
                return {'is_launch': True, 'type': self._classify_type(text),
                        'time': '即时', 'action': w}
        
        # 2. 高分组合模式检测 (赋予最高优先级)
        combo_score, combo_match = self._calculate_pattern_score(text, self.LAUNCH_PATTERNS)
        if combo_score >= self.SCORE_THRESHOLD:
            logger.info(f'  -> 触发“高分组合模式” ({combo_match})，得分 {combo_score}，直接判定为上新帖！')
            return {'is_launch': True, 'type': self._classify_type(text),
                    'time': combo_match, 'action': '组合判断'}
            
        # 计算得分（用于黄金信号和阈值）
        t_score, t_word = self._calculate_score(text, self.STRONG_TIME_KEYWORDS)
        pt_score, pt_word = self._calculate_pattern_score(text, self.TIME_PATTERNS)
        a_score, a_word = self._calculate_score(text, self.ACTION_KEYWORDS, 10)
        best_time = t_word if t_score >= pt_score else pt_word
        
        # 3. 黄金信号检测 (动作+时间) - 命中即豁免
        is_golden_action = any(k in text for k in self.GOLDEN_ACTION_KEYWORDS)
        if is_golden_action and max(t_score, pt_score) > 0:
            logger.info('    -> 触发“黄金信号”豁免机制，直接判定为上新帖！')
            return {'is_launch': True, 'type': self._classify_type(text),
                    'time': best_time, 'action': a_word}
        
        # 4. 阈值检测
        total = self._calculate_total_score(text)
        if total >= self.SCORE_THRESHOLD:
            logger.info(f'    -> 命中阈值！总分：{total} (阈值 {self.SCORE_THRESHOLD})')
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
                url = f.result()
                if url:
                    reals.append(url)
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
            logger.error(f"解析单条微博失败 (ID: {s.get('idstr', 'N/A')}): {e}", exc_info=True)
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
        if page_info.get('type') == 'video':
            cover_url = None
            if page_info.get('page_pic'):
                cover_url = page_info['page_pic'].get('url')
                if cover_url:
                    add_img(cover_url)
            
            video_url = page_info.get('media_info', {}).get('mp4_720p_mp4')
            if not video_url:
                video_url = page_info.get('media_info', {}).get('mp4_hd_url')
            if not video_url:
                video_url = page_info.get('media_info', {}).get('stream_url')
            if not video_url:
                video_url = page_info.get('media_info', {}).get('playback_url')


            if video_url:
                media['videos'].append({'url': video_url, 'cover_url': cover_url})

        big_pic_info = s.get('big_pic_info', {})
        if big_pic_info.get('url'):
            add_img(big_pic_info['url'])

        if not media['images'] and s.get('bmiddle_pic'):
            add_img(s['bmiddle_pic'])
            
        return media

    # ------------------------------------------------------------------
    # 💥 核心修改部分：增强图片下载的 Headers 以绕过防盗链
    # ------------------------------------------------------------------
    def download_and_convert_image(self, url: str) -> Optional[Dict[str, str]]:
        if not Image:
            return None
        try:
            # 完整复制 curl 命令中模拟浏览器行为的请求头
            # 关键是 Referer 和 User-Agent
            headers = {
                "Referer": "https://weibo.com/", # 核心防盗链绕过
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0", 
                "sec-ch-ua-platform": "\"Windows\"",
                "sec-ch-ua": "\"Microsoft Edge\";v=\"120\", \"Not?A_Brand\";v=\"8\", \"Chromium\";v=\"120\"",
                "sec-ch-ua-mobile": "?0",
            }
            
            r = requests.get(url, headers=headers, timeout=10, proxies=PROXIES_SETTING)
            
            if r.status_code != 200:
                if r.status_code == 403:
                     logger.warning(f'⚠️ 图片下载失败 (403 Forbidden)，可能是请求头仍被阻止。URL: {url[:80]}...')
                return None
            
            data = r.content
            
            # 图片压缩/处理逻辑不变
            if len(data) > 2 * 1024 * 1024:
                img = Image.open(BytesIO(data))
                img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.save(buf, format='JPEG', quality=85)
                data = buf.getvalue()
                
            return {'base64': base64.b64encode(data).decode('utf-8'),
                    'md5': hashlib.md5(data).hexdigest()}
            
        except Exception as e:
            logger.error(f'❌ 图片下载/压缩失败 (URL: {url[:80]}...): {e}')
            return None
    # ------------------------------------------------------------------


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
        'accept-language': 'zh-CN,zh;q=0.9',
        'client-version': 'v2.47.106',
        'referer': f'https://weibo.com/mygroups?gid={GROUP_ID}',
        'x-requested-with': 'XMLHttpRequest',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    cookies = {'SUB': sub_cookie}
    try:
        r = requests.get(url, params=params, headers=headers, cookies=cookies,
                         timeout=15, proxies=PROXIES_SETTING)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        logger.error(f'    - 网络请求或API错误: {e}')
        return {}
    except json.JSONDecodeError:
        logger.error('    - API返回的不是有效的 JSON，可能 Cookie 已失效或被屏蔽。')
        return {}

def fetch_weibo_data(sub_cookie: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    all_stat = []
    max_id = None
    for page in range(PAGE_LIMIT):
        logger.info(f'🔄 正在抓取第 {page+1} 页数据...')
        data = fetch_one_page(sub_cookie, max_id)
        statuses = data.get('statuses', [])
        
        if page == 0 and not statuses and data.get('ok') != 1:
            logger.error('🚨 第一页数据为空且API返回非OK状态，请检查 Cookie 或网络。')
            break
            
        if not statuses:
            break
            
        newly_fetched_count = 0
        
        for s in statuses:
            try:
                if s.get('readtimetype') == 'adMblog':
                    continue
                    
                post_time = parser.parse(s['created_at'])
                if post_time < start:
                    logger.info(f'🔍 遇到时间过早的帖子 ({post_time.strftime("%H:%M:%S")})，停止分页。')
                    return all_stat
                
                if post_time <= end:
                    all_stat.append(s)
                    newly_fetched_count += 1

            except Exception as e:
                logger.warning(f'忽略无效时间戳帖子 (ID: {s.get("idstr", "N/A")}): {e}')
                continue
                
        logger.info(f'    - 本页抓取到 {len(statuses)} 条，{newly_fetched_count} 条在窗口内。')
                
        max_id = data.get('max_id_str')
        if not max_id or max_id == '0' or newly_fetched_count == 0:
            break
        
        time.sleep(random.uniform(1.0, 2.0))
        
    logger.info(f'🏁 数据抓取完毕，共获得 {len(all_stat)} 条微博。')
    return all_stat

# 格式化通知函数
def format_launch_notification(info: Dict[str, Any]) -> str:
    user = info['user']['screen_name']
    detail = info.get('launch_details', {})
    lt = info.get('created_at', '')
    
    # 优化预告时间提取
    raw_time = detail.get('time', '')
    detail_time = raw_time
    
    # 逻辑 1: 如果是通过组合模式匹配到的 (e.g., "明日晚八点上架")
    if raw_time and detail.get('action') == '组合判断':
        # 清理动作词和可能的标点
        action_words_pattern = r'(上架|开售|发售|补款|释放|开拍|提前购|会员先购|预售|开启预售|\s*[:：,.。，]\s*)$'
        clean_time = re.sub(action_words_pattern, '', raw_time)
        detail_time = clean_time.strip()
    
    # 逻辑 2: 如果是单个时间词/模式 (e.g., "明晚", "20:00")，尝试回溯文本提取上下文
    elif raw_time and len(raw_time) <= 6:
        idx = info['text_raw'].find(raw_time)
        if idx != -1:
            start_idx = max(0, idx - 8)
            end_idx = min(len(info['text_raw']), idx + len(raw_time) + 8)
            context = info['text_raw'][start_idx:end_idx]
            
            match = re.search(r'(今晚|明晚|明天|今天|周[一二三四五六日天]).*?(\d{1,2}([:：]|\.)\d{2}|[0-9一二三四五六七八九十]+点)', context)
            if match:
                detail_time = match.group(0).strip()
    
    t = f'🕒 发帖时间: {lt}\n' if lt else ''
    tp = detail.get('type', '上新动态')
    title = f'🛍️【{tp} | {user}】'
    lines = [title]
    
    if detail_time:
        lines.append(f'⏰ 预告时间: {detail_time}')
    
    if detail.get('action') and detail.get('action') != '组合判断':
        lines.append(f'🔑 动作: {detail["action"]}')
        
    key_str = '\n'.join(lines)
    
    content = re.sub(r'https?://t\.cn/\w+', '', info['text_raw'])
    content = re.sub(r'\s+', ' ', content).strip()
    if len(content) > 300:
        content = content[:300] + '...'
    
    links = info.get('real_links', [])
    link_str = ''
    if links:
        link_str = '\n\n🔗 直达链接:\n' + '\n'.join(f'{i+1}. {l}' for i, l in enumerate(links[:3]))
    
    video_info = info.get('media', {}).get('videos', [])
    video_str = ''
    if video_info:
        video_url = video_info[0]['url']
        video_str = f'\n\n🎥 **[含视频]**\n(请在浏览器打开链接查看视频)'
        if len(video_info) > 1:
            video_str += f' (共 {len(video_info)} 个视频)'
            
    msg = f'{key_str}\n{t}\n━━━━━━━━━━━━━━━━━━\n💬 {content}{video_str}{link_str}'
    return msg

def send_launch_notifications(parser: WeiboDataParser, posts: List[Dict[str, Any]]):
    
    # 🚨 关键修改: 将列表倒序，确保在微信中按时间正序输出（旧 -> 新）
    posts.reverse()
    
    logger.info(f'\n📨 开始推送 {len(posts)} 条上新预告到企业微信 (按时间正序)...')
    for p in posts:
        text = format_launch_notification(p)
        if parser.send_wechat_text(text):
            logger.info(f'    ✅ 文本推送成功: {p["user"]["screen_name"]} ({p["id"]})')
            time.sleep(1)
            
            images_to_send = p.get('media', {}).get('images', [])
            if not images_to_send and p.get('media', {}).get('videos'):
                cover_url = p['media']['videos'][0].get('cover_url')
                if cover_url:
                    images_to_send = [{'url': cover_url}]
            
            for i, img in enumerate(images_to_send[:2]):
                ii = parser.download_and_convert_image(img['url'])
                if ii and parser.send_wechat_image(ii):
                    logger.info(f'      - 图片 {i+1} 发送成功')
                else:
                    logger.warning(f'      - 图片 {i+1} 发送失败或下载失败')
                time.sleep(0.5)
        else:
            logger.error(f'    ❌ 文本推送失败: {p["user"]["screen_name"]} ({p["id"]})')
        time.sleep(2)

def check_cookie_status(sub_cookie: str, curr: List[Dict[str, Any]]) -> bool:
    if curr:
        return True
    logger.warning('⚠️ 当前窗口未抓到任何帖子，启动 Cookie 二次验证...')
    beijing = pytz.timezone('Asia/Shanghai')
    now = datetime.now(beijing)
    
    prev = fetch_weibo_data(sub_cookie,
                            now - timedelta(hours=12),
                            now - timedelta(hours=1))
    if prev:
        logger.info('    ✅ 上一时间段有数据，Cookie 有效，当前时段确实无新帖。')
        return True
    logger.error('    ❌ 连续两个时段无数据，判定 Cookie/请求头失效！')
    return False

# ---------- 主监控逻辑 ----------
def execute_monitoring(sub_cookie: str, webhook_url: Optional[str] = None, enable_push: bool = True):
    beijing = pytz.timezone('Asia/Shanghai')
    now_beijing = datetime.now(beijing)
    current_hour = now_beijing.hour

    hours_back = 1 if 8 <= current_hour <= 23 else 4
    start_time = now_beijing - timedelta(hours=hours_back, minutes=5)
    end_time = now_beijing
    logger.info(f'📅 设定回溯窗口: {start_time.strftime("%Y-%m-%d %H:%M")} → {end_time.strftime("%Y-%m-%d %H:%M")} (最近 {hours_back} 小时)')

    last_id_str = load_last_id()
    last_id = int(last_id_str) if last_id_str.isdigit() else 0
    logger.info(f'➡️ 上次处理 ID: {last_id}')  

    raw_statuses = fetch_weibo_data(sub_cookie, start_time, end_time)

    if not check_cookie_status(sub_cookie, raw_statuses):
        if enable_push and webhook_url:
            parser = WeiboDataParser(sub_cookie, webhook_url)
            err_msg = ('⚠️ 微博监控失败\n\n'
                       '原因: Cookie 或请求头可能已失效\n'
                       '连续两个时间段未抓取到任何数据，请及时更新。')
            if parser.send_wechat_text(err_msg):
                logger.info('✅ 失效通知发送成功。')
        sys.exit(1)

    if not raw_statuses:
        logger.info('🏁 本次未抓到任何符合窗口的微博，程序结束。')
        return

    new_statuses = [s for s in raw_statuses if int(s.get('idstr', '0')) > last_id]
    
    if not new_statuses:
        logger.info('ℹ️ 所有帖子 ID 均不大于上次记录，无新帖需要处理。')
        return  

    current_max_id = max(int(s['idstr']) for s in new_statuses)
    current_max_id_str = str(current_max_id)
    
    logger.info(f'\n📊 共获得 {len(raw_statuses)} 条微博，其中 {len(new_statuses)} 条为新帖。')

    parser = WeiboDataParser(sub_cookie, webhook_url)
    
    parsed = parser.parse_and_enrich(new_statuses)
    
    logger.info('🔍 开始调试打印所有新帖内容...')
    for p in parsed:
        is_launch = 'YES' if p.get('launch_details') else 'NO'
        user = p['user']['screen_name']
        text_preview = p['text_raw'][:150].replace('\n', ' ') + ('...' if len(p['text_raw']) > 150 else '')
        media_summary = f"[图:{len(p.get('media', {}).get('images', []))}] [视:{len(p.get('media', {}).get('videos', []))}]"
        
        # 调试输出详细得分
        if not p.get('launch_details'):
              total_score = parser.detector._calculate_total_score(p['text_raw'])
              logger.info(f'  [ID: {p["id"]}] [用户: {user}] [上新判定: NO] (总分: {total_score}) {media_summary}')
        else:
            time_word = p['launch_details'].get('time', 'N/A')
            action_word = p['launch_details'].get('action', 'N/A')
            logger.info(f'  [ID: {p["id"]}] [用户: {user}] [上新判定: YES] (时间: {time_word} / 动作: {action_word}) {media_summary}')
        
        logger.info(f'  [内容]: {text_preview}')
        
    logger.info('--- 调试打印结束 ---')

    launches = parser.filter_launch_posts(parsed)

    if launches:
        logger.info(f'✅ 识别成功！共找到 {len(launches)} 条上新帖。')
        if enable_push and webhook_url:
            send_launch_notifications(parser, launches) 
        elif enable_push and not webhook_url:
            logger.warning('⚠️ 已识别到上新帖，但未配置 WECHAT_WEBHOOK_URL，跳过推送。')

    save_last_id(current_max_id_str)
    logger.info(f'💾 已将最新 ID ({current_max_id_str}) 写入 last_processed_id.txt。')
    logger.info('\n🏁 所有任务执行完毕。')

# ---------- 入口 ----------
if __name__ == '__main__':
    webhook = os.getenv('WECHAT_WEBHOOK_URL')
    enable_push = '--no-push' not in sys.argv
    cookie = os.getenv('WEIBO_SUB_COOKIE') or DEFAULT_SUB_COOKIE
    beijing = pytz.timezone('Asia/Shanghai')
    logger.info(f'🚀 程序启动于: {datetime.now(beijing).strftime("%Y-%m-%d %H:%M:%S")} (北京时间)')
    
    if cookie == DEFAULT_SUB_COOKIE:
        logger.error('🚨 请修改 `DEFAULT_SUB_COOKIE` 或设置 `WEIBO_SUB_COOKIE` 环境变量！')
        sys.exit(1)
        
    execute_monitoring(cookie, webhook, enable_push)
