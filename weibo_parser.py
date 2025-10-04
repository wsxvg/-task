#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博API数据解析器 V8.3.1 (高频运行版 - 增强组合模式识别与时间提取)
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

# ---------- 增量 ID 辅助（修复逻辑） ----------
def load_last_id() -> str:
    """从 last_processed_id.txt 读取上次处理的 ID，如果文件不存在或内容非法则返回 '0'。"""
    try:
        # 修复点：只读取一次并strip
        with open('last_processed_id.txt', 'r', encoding='utf-8') as f:
            content = f.read().strip()
            # 确保内容是数字
            return content if content.isdigit() else '0'
    except Exception:
        # 如果文件不存在，或者读取失败，则返回 '0'
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

# ---------- 智能评分器 (已优化关键词和调试输出) ----------
class SmartLaunchDetector:
    def __init__(self):
        # 关键词定义...
        # 🚀 优化 1: 终极信号 - 添加'释放库存'，直接识别最高优先级
        self.ULTIMATE_LAUNCH_KEYWORDS = {'现货上架', '已上架', '已开售', '开启购买', '释放库存'}
        
        # 🚀 优化 5: 新增高分组合模式，解决识别被负分淹没和时间提取不完整的问题
        self.LAUNCH_PATTERNS = {
            # 模式 A: (今/明/后/周/日期) + [任意字符] + (精确时间点: 8点/20:00) + [任意字符] + (动作词)
            r'(今|明|后|本周|下周|周[一二三四五六日天]|\d{1,2}[./]\d{1,2}|\d{1,2}号).*?(\d{1,2}([:：]|\.)\d{2}|[0-9一二三四五六七八九十]+点).*?(上架|开售|发售|补款|释放|开拍|提前购|会员先购)': 80,
        }
        
        self.STRONG_TIME_KEYWORDS = {'今晚': 30, '明晚': 30, '今晚八点': 35, '今晚8点': 35,
                                     '今晚7点': 35, '今晚七点': 35, '明天': 25, '后天': 25,
                                     '本周': 20, '本周末': 25, '月底': 20, '准时': 10,
                                     '稍后': 15, '即刻': 20, '立即': 20, '刚刚': 15, '现在': 15}
        self.TIME_PATTERNS = {r'\d{1,2}[:：]\d{2}': 35, r'[0-9一二三四五六七八九十]+点': 30,
                              r'(\d{4}[-/年])?\d{1,2}[-/月]\d{1,2}日?': 30, r'\d{1,2}\.\d{1,2}': 30,
                              r'\d{1,2}号': 25, r'周[一二三四五六日天]': 25}
        
        # 🚀 优化 2: 调整 '上架' 和 '释放' 关键词分值，同时提升 '上架' 至 35 分
        self.ACTION_KEYWORDS = {
            '现货上架': 40, '开启购买': 35, '会员先购': 35, 'VIP先购': 35, '补货': 35,
            '提前购': 35, '开拍': 30, 
            '上架': 35,  # <<< 调整: 从 30 提升到 35
            '发售': 30, '开售': 30, '现货': 30,
            
            '补款': 40,          
            '付尾款': 40,          
            '补定金': 35,          
            
            '清仓': 30, '新款': 25, '讲解': 15, '细节': 15, '上新': 25,
            '释放': 35, # ← 分值从 25 提升到 35
            '预售': 25, '先购': 25, '开放购买': 25, '上新通知': 25,
            '新品首发': 25, '补出': 20, '已开售': 20, '已上架': 20, '新品上市': 20,
            '新款预告': 15, '首批': 15, '第一批': 15, '🆕': 15,
            '更新了': 10, '带来了': 10, '带给大家': 10
        }
        
        # 🚀 优化 3: 黄金信号 - 添加 '释放' 和 '上架'
        self.GOLDEN_ACTION_KEYWORDS = ['上新通知', '现货上架', '开启购买', '会员先购',
                                             'VIP先购', '提前购', '补货', '发售', '开售', 
                                             '补款', '付尾款', '释放', '上架'] # <<< 调整: 添加 '上架'
        self.COMBO_RULES = {
            ('已上架', '网页链接'): 50, ('已上架', 'http'): 50,
            ('新款', '讲解'): 15, ('新款', '细节'): 15,
        }
        
        # 🚀 优化 4: 调整 '预计' 负分，使其不完全抵消有效信号
        self.NEGATIVE_KEYWORDS = {'进度': -40, '打样': -40, '调整': -30, '修改': -30,
                                     '确认': -30, '开发': -40, '研究': -40, '还在': -20,
                                     '还在改': -40, '还在调': -40, '面料': -10, '辅料': -10,
                                     '刺绣': -10, '样品': -20, '样衣': -20, '色卡': -20,
                                     '计划': -50, 
                                     '预计': -15, # <<< 调整: 从 -30 降低到 -15
                                     '准备': -20, '快了': -20,
                                     '即将': -20, '近期': -30, '延迟': -60, '取消': -60, '停止': -60}
        self.POLLING_KEYWORDS = {'点点': -50, '要不要': -60, '怎么样': -50,
                                     '觉得': -40, '喜欢吗': -50}
        self.LOTTERY_KEYWORDS = {'抽奖': -40, '转发': -20, '参与条件': -30, '抽取': -40}
        self.SCORE_THRESHOLD = 45
        
        # 🚀 优化分类：将补款归类到开启预售
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
                # 修复点：确保取的是最大得分的关键词
                if tmp > s:
                    s, w = tmp, k
        return s, w

    def _calculate_pattern_score(self, text: str, pt: Dict[str, int]) -> (int, str):
        s, m = 0, ''
        for p, v in pt.items():
            match = re.search(p, text)
            if match:
                # 修复点：确保取的是最大得分的模式
                if v > s:
                    s, m = v, match.group(0)
        return s, m

    def _calculate_total_score(self, text: str) -> int:
        # 注意: LAUNCH_PATTERNS 的分值也会计算到 t1 或 t2 中，如果启用了组合模式，这里的分数会很高
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
        
        # 组合模式的分数直接体现在 max(t1, t2) 中（因为 LAUNCH_PATTERNS 会被 check() 优先处理，
        # 如果走到这里，只计算常规的 ACTION_KEYWORDS 和 TIME_PATTERNS）
        
        # 将 LAUNCH_PATTERNS 分数单独计算并计入，以确保它能在阈值检测中发挥作用，
        # 即使它在 check() 中没有被单独处理，也能通过总分。
        combo_score_total, _ = self._calculate_pattern_score(text, self.LAUNCH_PATTERNS)
        
        # 如果 combo_score_total 很高 (如 80)，则直接将其计入总分
        return max(t1, t2, combo_score_total) + a1 + neg + pol + lot + combo

    def check(self, text: str) -> Union[bool, Dict[str, Any]]:
        if not text:
            return False
        
        # 1. 终极信号检测
        for w in self.ULTIMATE_LAUNCH_KEYWORDS:
            if w in text:
                logger.info(f'  -> 触发“终极信号”({w})，直接判定为上新帖！')
                logger.info(f'  [匹配内容]：{text[:150]}...')
                return {'is_launch': True, 'type': self._classify_type(text),
                        'time': '即时', 'action': w}
        
        # 0. 【新增】高分组合模式检测 (赋予最高优先级)
        combo_score, combo_match = self._calculate_pattern_score(text, self.LAUNCH_PATTERNS)
        if combo_score >= self.SCORE_THRESHOLD:
            logger.info(f'  -> 触发“高分组合模式” ({combo_match})，得分 {combo_score}，直接判定为上新帖！')
            # 使用整个匹配内容作为提取的时间
            return {'is_launch': True, 'type': self._classify_type(text),
                    'time': combo_match, 'action': '组合判断'}
            
        # 计算得分
        t_score, t_word = self._calculate_score(text, self.STRONG_TIME_KEYWORDS)
        pt_score, pt_word = self._calculate_pattern_score(text, self.TIME_PATTERNS)
        a_score, a_word = self._calculate_score(text, self.ACTION_KEYWORDS, 10)
        best_time = t_word if t_score >= pt_score else pt_word
        total = self._calculate_total_score(text)
        
        # 2. 黄金信号检测 (动作+时间)
        is_golden_action = any(k in text for k in self.GOLDEN_ACTION_KEYWORDS)
        if is_golden_action and max(t_score, pt_score) > 0:
            logger.info('    -> 触发“黄金信号”豁免机制，直接判定为上新帖！')
            logger.info(f'    [匹配动作]：{a_word} | [匹配时间]：{best_time} | [总分]：{total}')
            logger.info(f'    [匹配内容]：{text[:150]}...')
            return {'is_launch': True, 'type': self._classify_type(text),
                    'time': best_time, 'action': a_word}
        
        # 3. 阈值检测
        if total >= self.SCORE_THRESHOLD:
            logger.info(f'    -> 命中阈值！总分：{total} (阈值 {self.SCORE_THRESHOLD})')
            logger.info(f'    [匹配动作]：{a_word} | [匹配时间]：{best_time}')
            logger.info(f'    [匹配内容]：{text[:150]}...')
            return {'is_launch': True, 'type': self._classify_type(text),
                    'time': best_time, 'action': a_word}
        
        return False

# ---------- 微博解析核心 (已优化媒体解析) ----------
class WeiboDataParser:
    def __init__(self, sub_cookie: str, webhook_url: Optional[str] = None):
        self.sub_cookie = sub_cookie
        self.webhook_url = webhook_url
        self.detector = SmartLaunchDetector()

    # ---------- 私有工具 (与原代码一致) ----------
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

    # ---------- 主解析 (与原代码一致) ----------
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
            # 将获取到的全文注入到原始数据中
            if post_id in long_map:
                s['text_raw'] = long_map[post_id]
            
            # 如果不是长微博，使用原始短文本
            if 'text_raw' not in s:
                s['text_raw'] = s.get('text', '')
                s['text_raw'] = re.sub(r'<[^>]+>', '', s['text_raw']).strip()


            item = self._parse_single_status(s)
            
            if item:
                # 在这里对每个帖子进行初步的关键词检查，并嵌入结果
                check_result = self.detector.check(item['text_raw'])
                if isinstance(check_result, dict) and check_result.get('is_launch'):
                    item['launch_details'] = check_result
                
                item['real_links'] = self._extract_and_resolve_links(item['text_raw'])
                parsed.append(item)
        return parsed

    # 这里的 filter_launch_posts 现在只负责筛选已打上标记的帖子
    def filter_launch_posts(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [d for d in data if d.get('launch_details')]

    def _parse_single_status(self, s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            is_ret = 'retweeted_status' in s
            obj = s['retweeted_status'] if is_ret else s
            basic = self._parse_basic(s, is_ret)
            user = self._parse_user(s.get('user', {}))
            inter = self._parse_interaction(s)
            
            # 媒体解析使用原始帖子对象 (obj)
            media = self._parse_media(obj)
            
            ori_user = self._parse_user(obj.get('user', {})) if is_ret else None
            if is_ret:
                basic['original_text_raw'] = obj.get('text_raw', '')
                
            # 由于 text_raw 可能在 parse_and_enrich 中被填充，这里使用它作为最终的 text_raw
            return {**basic, 'user': user, 'interaction': inter,
                    'media': media, 'original_user': ori_user, 'text_raw': basic['text_raw']}
        except Exception as e:
            logger.error(f"解析单条微博失败 (ID: {s.get('idstr', 'N/A')}): {e}", exc_info=True)
            return None

    def _parse_basic(self, s, is_ret):
        # 确保这里获取的是纯净的文本
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

    
    # ------------------------------------------------
    # 媒体解析 (已优化，支持视频封面图)
    # ------------------------------------------------
    def _parse_media(self, s: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析微博中的图片和视频媒体。
        - 增加了对视频帖封面图的解析 (page_info)。
        - 增加了对视频 URL 的解析。
        """
        media = {'images': [], 'videos': []}
        seen_urls = set() # 用来去重，防止重复添加图片

        def add_img(url: str):
            """将图片 URL 添加到列表中并去重"""
            # 确保 URL 是一个可用的图片链接，并且没有被添加过
            if url and url.startswith('http') and url not in seen_urls:
                # 简单处理：将 'thumb180' 缩略图替换为 'large' 大图
                if 'thumb180' in url and 'large' not in url:
                    url = url.replace('thumb180', 'large')
                
                media['images'].append({'url': url})
                seen_urls.add(url)
        
        # --- 1. 处理常规多图帖子 (pic_ids) ---
        pic_infos = s.get('pic_infos', {})
        for pid in s.get('pic_ids', []):
            if pid in pic_infos:
                # 优先寻找最大的图
                found_url = None
                for sz in ['large', 'original', 'bmiddle', 'thumbnail']:
                    if sz in pic_infos[pid] and pic_infos[pid][sz].get('url'):
                        found_url = pic_infos[pid][sz]['url']
                        break
                if found_url:
                    add_img(found_url)

        # --- 2. 处理视频帖封面和视频 URL (page_info) ---
        page_info = s.get('page_info', {})
        if page_info.get('type') == 'video':
            cover_url = None
            if page_info.get('page_pic'):
                # 提取视频封面图
                cover_url = page_info['page_pic'].get('url')
                if cover_url:
                    add_img(cover_url)
            
            # 提取视频 URL 
            video_url = page_info.get('media_info', {}).get('mp4_720p_mp4')
            if not video_url:
                 video_url = page_info.get('media_info', {}).get('mp4_hd_url')
            if not video_url:
                 video_url = page_info.get('media_info', {}).get('stream_url')
            if not video_url:
                 # 尝试获取短视频的 URL，通常在 `playback_url`
                 video_url = page_info.get('media_info', {}).get('playback_url')


            if video_url:
                media['videos'].append({'url': video_url, 'cover_url': cover_url})


        # --- 3. 处理单图/短视频的备用封面 (big_pic_info) ---
        big_pic_info = s.get('big_pic_info', {})
        if big_pic_info.get('url'):
            add_img(big_pic_info['url'])

        # 额外的对 'bmiddle_pic' 的检查，通常用于转发帖
        if not media['images'] and s.get('bmiddle_pic'):
            add_img(s['bmiddle_pic'])
            
        return media
    # ------------------------------------------------
    
    # ---------- 微信推送 (与原代码一致) ----------
    def download_and_convert_image(self, url: str) -> Optional[Dict[str, str]]:
        if not Image:
            return None
        try:
            headers = {'Referer': 'https://weibo.com/', 'User-Agent': 'Mozilla/5.0'}
            r = requests.get(url, headers=headers, timeout=10, proxies=PROXIES_SETTING)
            if r.status_code != 200:
                return None
            data = r.content
            if len(data) > 2 * 1024 * 1024:
                img = Image.open(BytesIO(data))
                img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                buf = BytesIO()
                # 尝试保存为 JPEG，减小尺寸
                img.save(buf, format='JPEG', quality=85)
                data = buf.getvalue()
            return {'base64': base64.b64encode(data).decode('utf-8'),
                    'md5': hashlib.md5(data).hexdigest()}
        except Exception as e:
            logger.error(f'❌ 图片下载/压缩失败 (URL: {url[:80]}...): {e}')
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

# ---------- 外部 API (与原代码一致) ----------
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
        r.raise_for_status() # 检查 HTTP 错误
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
        
        # 验证 Cookie 是否失效，如果第一页数据为空且没有错误信息，可能是 Cookie 失效
        if page == 0 and not statuses and data.get('ok') != 1:
            logger.error('🚨 第一页数据为空且API返回非OK状态，请检查 Cookie 或网络。')
            break
            
        if not statuses:
            break
            
        newly_fetched_count = 0
        
        for s in statuses:
            try:
                # 过滤掉广告
                if s.get('readtimetype') == 'adMblog':
                    continue
                    
                post_time = parser.parse(s['created_at'])
                # 检查时间窗口
                if post_time < start:
                    # 如果帖子时间早于窗口开始时间，则后面的帖子通常也早于此时间，可以停止分页
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
        
        # 防止请求过于频繁
        time.sleep(random.uniform(1.0, 2.0))
        
    logger.info(f'🏁 数据抓取完毕，共获得 {len(all_stat)} 条微博。')
    return all_stat

def format_launch_notification(info: Dict[str, Any]) -> str:
    user = info['user']['screen_name']
    detail = info.get('launch_details', {})
    lt = info.get('created_at', '')
    
    # 【优化部分开始】优化预告时间提取
    raw_time = detail.get('time', '')
    detail_time = raw_time
    
    # 如果是通过组合模式匹配到的
    if raw_time and detail.get('action') == '组合判断':
        # 清理动作词（上架|开售|发售|...）和可能的标点、空格
        action_words_pattern = r'(上架|开售|发售|补款|释放|开拍|提前购|会员先购|\s*[:：,.。，]\s*)$'
        clean_time = re.sub(action_words_pattern, '', raw_time)
        detail_time = clean_time.strip()
    
    # 如果是单个时间词/模式（如 明晚, 20:00），尝试回溯文本提取上下文
    elif raw_time and len(raw_time) <= 6: # 长度限制，只对较短的匹配词进行上下文扩展
        idx = info['text_raw'].find(raw_time)
        if idx != -1:
            # 向前和向后扩展一些字符，以获取上下文 (例如 12个字符)
            start_idx = max(0, idx - 8)
            end_idx = min(len(info['text_raw']), idx + len(raw_time) + 8)
            context = info['text_raw'][start_idx:end_idx]
            
            # 使用正则提取 "今晚/明晚 + 时间" 模式
            match = re.search(r'(今晚|明晚|明天|今天|周[一二三四五六日天]).*?(\d{1,2}([:：]|\.)\d{2}|[0-9一二三四五六七八九十]+点)', context)
            if match:
                detail_time = match.group(0).strip()
    # 【优化部分结束】
    
    t = f'🕒 发帖时间: {lt}\n' if lt else ''
    tp = detail.get('type', '上新动态')
    title = f'🛍️【{tp} | {user}】'
    lines = [title]
    
    if detail_time: # 使用经过美化或增强的 detail_time
        lines.append(f'⏰ 预告时间: {detail_time}')
    
    if detail.get('action') and detail.get('action') != '组合判断': # 避免对“组合判断”显示动作
        lines.append(f'🔑 动作: {detail["action"]}')
        
    key_str = '\n'.join(lines)
    
    # 清理并截断内容
    content = re.sub(r'https?://t\.cn/\w+', '', info['text_raw'])
    content = re.sub(r'\s+', ' ', content).strip()
    if len(content) > 300:
        content = content[:300] + '...'
    
    # 链接
    links = info.get('real_links', [])
    link_str = ''
    if links:
        link_str = '\n\n🔗 直达链接:\n' + '\n'.join(f'{i+1}. {l}' for i, l in enumerate(links[:3])) # 最多只显示3个链接
    
    # 视频提示
    video_info = info.get('media', {}).get('videos', [])
    video_str = ''
    if video_info:
        # 只取第一个视频链接作为提示
        video_url = video_info[0]['url']
        video_str = f'\n\n🎥 **[含视频]**\n(请在浏览器打开链接查看视频)'
        if len(video_info) > 1:
            video_str += f' (共 {len(video_info)} 个视频)'
            
    # 组合最终消息
    msg = f'{key_str}\n{t}\n━━━━━━━━━━━━━━━━━━\n💬 {content}{video_str}{link_str}'
    return msg

def send_launch_notifications(parser: WeiboDataParser, posts: List[Dict[str, Any]]):
    logger.info(f'\n📨 开始推送 {len(posts)} 条上新预告到企业微信...')
    for p in posts:
        # 1. 发送文本消息
        text = format_launch_notification(p)
        if parser.send_wechat_text(text):
            logger.info(f'    ✅ 文本推送成功: {p["user"]["screen_name"]} ({p["id"]})')
            time.sleep(1)
            
            # 2. 发送图片（最多前两张）
            images_to_send = p.get('media', {}).get('images', [])
            if not images_to_send and p.get('media', {}).get('videos'):
                # 如果没有图片，但有视频，则尝试发送视频封面图（已经包含在 images 列表里，但这里双重保险）
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
        time.sleep(2) # 帖子之间增加等待，避免被微信限流

def check_cookie_status(sub_cookie: str, curr: List[Dict[str, Any]]) -> bool:
    if curr:
        return True
    logger.warning('⚠️ 当前窗口未抓到任何帖子，启动 Cookie 二次验证...')
    beijing = pytz.timezone('Asia/Shanghai')
    now = datetime.now(beijing)
    # 检查前一个较长时段是否有数据
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

    # 动态窗口 (高频运行，窗口可小一点)
    hours_back = 1 if 8 <= current_hour <= 23 else 4
    start_time = now_beijing - timedelta(hours=hours_back, minutes=5)
    end_time = now_beijing
    logger.info(f'📅 设定回溯窗口: {start_time.strftime("%Y-%m-%d %H:%M")} → {end_time.strftime("%Y-%m-%d %H:%M")} (最近 {hours_back} 小时)')

    last_id_str = load_last_id()
    last_id = int(last_id_str) if last_id_str.isdigit() else 0
    logger.info(f'➡️ 上次处理 ID: {last_id}')  

    # 1. 抓取数据
    raw_statuses = fetch_weibo_data(sub_cookie, start_time, end_time)

    # 2. Cookie 状态检查
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

    # 3. 增量过滤
    # 确保只处理比上次记录 ID 大的新帖
    new_statuses = [s for s in raw_statuses if int(s.get('idstr', '0')) > last_id]
    
    if not new_statuses:
        logger.info('ℹ️ 所有帖子 ID 均不大于上次记录，无新帖需要处理。')
        return 

    current_max_id = max(int(s['idstr']) for s in new_statuses)
    current_max_id_str = str(current_max_id)
    
    logger.info(f'\n📊 共获得 {len(raw_statuses)} 条微博，其中 {len(new_statuses)} 条为新帖。')

    parser = WeiboDataParser(sub_cookie, webhook_url)
    
    # 4. 解析与关键词检查
    parsed = parser.parse_and_enrich(new_statuses)
    
    logger.info('🔍 开始调试打印所有新帖内容...')
    for p in parsed:
        is_launch = 'YES' if p.get('launch_details') else 'NO'
        
        # 提取关键信息用于打印
        user = p['user']['screen_name']
        text_preview = p['text_raw'][:150].replace('\n', ' ') + ('...' if len(p['text_raw']) > 150 else '')
        
        media_summary = f"[图:{len(p.get('media', {}).get('images', []))}] [视:{len(p.get('media', {}).get('videos', []))}]"
        
        logger.info(f'  [ID: {p["id"]}] [用户: {user}] [上新判定: {is_launch}] {media_summary}')
        logger.info(f'  [内容]: {text_preview}')
        
    logger.info('--- 调试打印结束 ---')

    # 5. 过滤和推送
    # 这里的 filter_launch_posts 只是筛选出在 parse_and_enrich 阶段已打上标记的帖子
    launches = parser.filter_launch_posts(parsed)

    if launches:
        logger.info(f'✅ 识别成功！共找到 {len(launches)} 条上新帖。')
        if enable_push and webhook_url:
            send_launch_notifications(parser, launches) 
        elif enable_push and not webhook_url:
            logger.warning('⚠️ 已识别到上新帖，但未配置 WECHAT_WEBHOOK_URL，跳过推送。')

    # 6. 更新 ID
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
