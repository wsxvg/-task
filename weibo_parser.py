#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博API数据解析器 V8.3
改进点：
- 修复时区与提前 break 导致的丢帖
- 微信推送新增“发帖时间”字段
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
from dateutil import parser   # 新增，解析微博时间更稳

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
    try:
        with open('last_processed_id.txt', 'r', encoding='utf-8') as f:
            return f.read().strip() if f.read().strip().isdigit() else '0'
    except Exception:
        return '0'

def save_last_id(new_id: str):
    if new_id == '0':
        return
    try:
        with open('last_processed_id.txt', 'w', encoding='utf-8') as f:
            f.write(new_id)
    except Exception as e:
        logger.error(f'❌ 写入 last_processed_id.txt 失败: {e}')

# ---------- 智能评分器（与原一致） ----------
class SmartLaunchDetector:
    def __init__(self):
        self.ULTIMATE_LAUNCH_KEYWORDS = {'现货上架', '已上架', '已开售', '开启购买'}
        self.STRONG_TIME_KEYWORDS = {'今晚': 30, '明晚': 30, '今晚八点': 35, '今晚8点': 35,
                                     '今晚7点': 35, '今晚七点': 35, '明天': 25, '后天': 25,
                                     '本周': 20, '本周末': 25, '月底': 20, '准时': 10,
                                     '稍后': 15, '即刻': 20, '立即': 20, '刚刚': 15, '现在': 15}
        self.TIME_PATTERNS = {r'\d{1,2}[:：]\d{2}': 35, r'[0-9一二三四五六七八九十]+点': 30,
                              r'(\d{4}[-/年])?\d{1,2}[-/月]\d{1,2}日?': 30, r'\d{1,2}\.\d{1,2}': 30,
                              r'\d{1,2}号': 25, r'周[一二三四五六日天]': 25}
        self.ACTION_KEYWORDS = {
            '现货上架': 40, '开启购买': 35, '会员先购': 35, 'VIP先购': 35, '补货': 35,
            '提前购': 35, '开拍': 30, '上架': 30, '发售': 30, '开售': 30, '现货': 30,
            '清仓': 30, '新款': 25, '讲解': 15, '细节': 15, '上新': 25,
            '释放': 25, '预售': 25, '先购': 25, '开放购买': 25, '上新通知': 25,
            '新品首发': 25, '补出': 20, '已开售': 20, '已上架': 20, '新品上市': 20,
            '新款预告': 15, '首批': 15, '第一批': 15, '🆕': 15,
            '更新了': 10, '带来了': 10, '带给大家': 10
        }
        self.GOLDEN_ACTION_KEYWORDS = ['上新通知', '现货上架', '开启购买', '会员先购',
                                       'VIP先购', '提前购', '补货', '发售', '开售']
        self.COMBO_RULES = {
            ('已上架', '网页链接'): 50, ('已上架', 'http'): 50,
            ('新款', '讲解'): 15, ('新款', '细节'): 15,
        }
        self.NEGATIVE_KEYWORDS = {'进度': -40, '打样': -40, '调整': -30, '修改': -30,
                                  '确认': -30, '开发': -40, '研究': -40, '还在': -20,
                                  '还在改': -40, '还在调': -40, '面料': -10, '辅料': -10,
                                  '刺绣': -10, '样品': -20, '样衣': -20, '色卡': -20,
                                  '计划': -50, '预计': -30, '准备': -20, '快了': -20,
                                  '即将': -20, '近期': -30, '延迟': -60, '取消': -60, '停止': -60}
        self.POLLING_KEYWORDS = {'点点': -50, '要不要': -60, '怎么样': -50,
                                 '觉得': -40, '喜欢吗': -50}
        self.LOTTERY_KEYWORDS = {'抽奖': -40, '转发': -20, '参与条件': -30, '抽取': -40}
        self.SCORE_THRESHOLD = 45
        self.TYPE_KEYWORDS = {
            '新品首发': ['新品首发', '全新', '新款', '新品上市', '新款上线', '首批'],
            '热门补货': ['补货', '补出', '秒空', '返场'],
            '开启预售': ['预售', '开启预售', '意向金', '尺码登记'],
            '清仓活动': ['清仓'],
            '现货发售': ['现货', '上架', '发售', '释放', '开售']
        }

    def _classify_type(self, text: str) -> str:
        for t, ks in self.TYPE_KEYWORDS.items():
            if any(k in text for k in ks):
                return t
        return '上新动态'

    # 以下方法与 V8.2 完全一致，省略重复注释
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
            if match and v > s:
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
        return max(t1, t2) + a1 + neg + pol + lot + combo

    def check(self, text: str) -> Union[bool, Dict[str, Any]]:
        if not text:
            return False
        for w in self.ULTIMATE_LAUNCH_KEYWORDS:
            if w in text:
                logger.info(f'  -> 触发“终极信号”({w})，直接判定为上新帖！')
                return {'is_launch': True, 'type': self._classify_type(text),
                        'time': '即时', 'action': w}
        t_score, t_word = self._calculate_score(text, self.STRONG_TIME_KEYWORDS)
        pt_score, pt_word = self._calculate_pattern_score(text, self.TIME_PATTERNS)
        a_score, a_word = self._calculate_score(text, self.ACTION_KEYWORDS, 10)
        best_time = t_word if t_score >= pt_score else pt_word
        if any(k in text for k in self.GOLDEN_ACTION_KEYWORDS) and max(t_score, pt_score) > 0:
            logger.info('    -> 触发“黄金信号”豁免机制，直接判定为上新帖！')
            return {'is_launch': True, 'type': self._classify_type(text),
                    'time': best_time, 'action': a_word}
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

    # ---------- 私有工具 ----------
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

    # ---------- 主解析 ----------
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
            item = self._parse_single_status(s)
            if item:
                item['real_links'] = self._extract_and_resolve_links(item['text_raw'])
                parsed.append(item)
        return parsed

    def filter_launch_posts(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        launches = []
        for d in data:
            r = self.detector.check(d.get('text_raw', ''))
            if isinstance(r, dict) and r.get('is_launch'):
                d['launch_details'] = r
                launches.append(d)
        return launches

    # ---------- 内部解析 ----------
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
                    'media': media, 'original_user': ori_user}
        except Exception:
            return None

    def _parse_basic(self, s, is_ret):
        return {
            'id': s.get('idstr', s.get('id')),
            'text_raw': s.get('text_raw', ''),
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

    # ---------- 媒体 ----------
    def _parse_media(self, s: Dict[str, Any]) -> Dict[str, Any]:
        media = {'images': [], 'videos': []}
        seen = set()

        def add_img(url: str):
            if url and url.startswith('http') and url not in seen:
                media['images'].append({'url': url})
                seen.add(url)

        pic_infos = s.get('pic_infos', {})
        for pid in s.get('pic_ids', []):
            if pid in pic_infos:
                for sz in ['large', 'original', 'bmiddle', 'thumbnail']:
                    if sz in pic_infos[pid] and pic_infos[pid][sz].get('url'):
                        add_img(pic_infos[pid][sz]['url'])
                        break
        # 以下还有一堆规则，与 V8.2 相同，省略...
        return media

    # ---------- 微信推送 ----------
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
                img.save(buf, format='JPEG', quality=85)
                data = buf.getvalue()
            return {'base64': base64.b64encode(data).decode('utf-8'),
                    'md5': hashlib.md5(data).hexdigest()}
        except Exception as e:
            logger.error(f'❌ 图片下载/压缩失败: {e}')
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
        return r.json()
    except Exception as e:
        logger.error(f'    - 网络请求异常: {e}')
        return {}

def fetch_weibo_data(sub_cookie: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    all_stat = []
    max_id = None
    for page in range(PAGE_LIMIT):
        data = fetch_one_page(sub_cookie, max_id)
        statuses = data.get('statuses', [])
        if not statuses:
            break
        for s in statuses:
            try:
                post_time = parser.parse(s['created_at'])   # aware
                # 单条过滤，不 break
                if post_time < start:
                    continue
                if post_time > end:
                    continue
                all_stat.append(s)
            except Exception:
                continue
        max_id = data.get('max_id_str')
        if not max_id or max_id == '0':
            break
        time.sleep(1)
    return all_stat

# ---------- 微信消息模板（新增发帖时间） ----------
def format_launch_notification(info: Dict[str, Any]) -> str:
    user = info['user']['screen_name']
    detail = info.get('launch_details', {})
    lt = info.get('created_at', '')          # <— 新增
    t = f'🕒 发帖时间: {lt}\n' if lt else ''
    tp = detail.get('type', '上新动态')
    title = f'🛍️【{tp} | {user}】'
    lines = [title]
    if detail.get('time'):
        lines.append(f'⏰ 预告时间: {detail["time"]}')
    if detail.get('action'):
        lines.append(f'🔑 动作: {detail["action"]}')
    key_str = '\n'.join(lines)
    content = re.sub(r'https?://t\.cn/\w+', '', info['text_raw'])
    content = re.sub(r'\s+', ' ', content).strip()
    if len(content) > 300:
        content = content[:300] + '...'
    links = info.get('real_links', [])
    link_str = ''
    if links:
        link_str = '\n\n🔗 直达链接:\n' + '\n'.join(f'{i+1}. {l}' for i, l in enumerate(links))
    msg = f'{key_str}\n{t}\n━━━━━━━━━━━━━━━━━━\n💬 {content}{link_str}'
    return msg

def send_launch_notifications(parser: WeiboDataParser, posts: List[Dict[str, Any]]):
    logger.info(f'\n📨 开始推送 {len(posts)} 条上新预告到企业微信...')
    for p in posts:
        text = format_launch_notification(p)
        if parser.send_wechat_text(text):
            logger.info(f'    ✅ 文本推送成功: {p["user"]["screen_name"]}')
            time.sleep(1)
            for i, img in enumerate(p.get('media', {}).get('images', [])[:2]):
                ii = parser.download_and_convert_image(img['url'])
                if ii and parser.send_wechat_image(ii):
                    logger.info(f'      - 图片 {i+1} 发送成功')
                else:
                    logger.warning(f'      - 图片 {i+1} 发送失败')
                time.sleep(0.5)
        else:
            logger.error(f'    ❌ 文本推送失败: {p["user"]["screen_name"]}')
        time.sleep(2)

# ---------- Cookie 二次验证 ----------
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

    # 动态窗口
    hours_back = 2 if 8 <= current_hour <= 23 else 8
    start_time = now_beijing - timedelta(hours=hours_back, minutes=5)  # 冗余 5min
    end_time = now_beijing
    logger.info(f'📅 设定回溯窗口: {start_time.strftime("%Y-%m-%d %H:%M")} → {end_time.strftime("%Y-%m-%d %H:%M")} (最近 {hours_back} 小时)')

    last_id_str = load_last_id()
    last_id = int(last_id_str) if last_id_str.isdigit() else 0
    logger.info(f'➡️ 上次处理 ID: {last_id}')

    raw_statuses = fetch_weibo_data(sub_cookie, start_time, end_time)

    if not check_cookie_status(sub_cookie, raw_statuses):
        if enable_push:
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

    # 增量过滤
    new_statuses = [s for s in raw_statuses if int(s.get('idstr', '0')) > last_id]
    if not new_statuses:
        logger.info('ℹ️ 所有帖子 ID 均不大于上次记录，无新帖需要处理。')
        return

    current_max_id = max(s['idstr'] for s in new_statuses)
    logger.info(f'\n📊 共获得 {len(raw_statuses)} 条微博，其中 {len(new_statuses)} 条为新帖。')

    parser = WeiboDataParser(sub_cookie, webhook_url)
    parsed = parser.parse_and_enrich(new_statuses)
    launches = parser.filter_launch_posts(parsed)

    if launches:
        logger.info(f'✅ 识别成功！共找到 {len(launches)} 条上新帖。')
        if enable_push:
            send_launch_notifications(parser, launches)

    save_last_id(current_max_id)
    logger.info(f'💾 已将最新 ID ({current_max_id}) 写入 last_processed_id.txt，等待 Git 提交。')
    logger.info('\n🏁 所有任务执行完毕。')

# ---------- 入口 ----------
if __name__ == '__main__':
    webhook = os.getenv('WECHAT_WEBHOOK_URL')
    enable_push = '--no-push' not in sys.argv
    cookie = os.getenv('WEIBO_SUB_COOKIE') or DEFAULT_SUB_COOKIE
    beijing = pytz.timezone('Asia/Shanghai')
    logger.info(f'🚀 程序启动于: {datetime.now(beijing).strftime("%Y-%m-%d %H:%M:%S")} (北京时间)')
    execute_monitoring(cookie, webhook, enable_push)
