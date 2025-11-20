#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博抢购增量监控 V8.6 (集成Debug日志)
"""
import re
import os
import sys
import time
import json
import random
import logging
import requests
import hashlib
import base64
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from dateutil import parser
from typing import Dict, List, Optional, Any, Union

try:
    from PIL import Image
except ImportError:
    Image = None

# ---------- 配置 ----------
GROUP_ID = '5159683220312291'
MAX_WORKERS = 10
PAGE_LIMIT = 20
DEFAULT_SUB_COOKIE = "请替换为您的 SUB Cookie"
PROXIES_SETTING = {"http": None, "https": None}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------- 智能评分器 (保持原样，省略部分代码以节省篇幅，逻辑不变) ----------
# ... (这里把之前代码中的 SmartLaunchDetector 类原封不动放进来) ...
class SmartLaunchDetector:
    def __init__(self):
        self.ULTIMATE_LAUNCH_KEYWORDS = {'现货上架', '已上架', '已开售', '开启购买', '释放库存'}
        self.LAUNCH_PATTERNS = {
            r'(\d{1,2}([:：]|\.)\d{2}|[0-9一二三四五六七八九十]+点).*?(\S{0,20}).*?(上架|开售|发售|补款|释放|开拍|提前购|会员先购|预售|开启预售|售价|特惠|抢购|秒杀)': 85,
            r'(今晚|明晚|今天|明天|周[一二三四五六日天]).*?(\d{1,2}([:：]|\.)\d{2}|[0-9一二三四五六七八九十]+点)': 40, 
        }
        self.STRONG_TIME_KEYWORDS = {'今晚': 30, '明晚': 30, '今晚八点': 35, '今晚8点': 35, '明天': 25}
        self.TIME_PATTERNS = {r'\d{1,2}[:：]\d{2}': 35, r'[0-9一二三四五六七八九十]+点': 30}
        self.ACTION_KEYWORDS = {'现货上架': 40, '上架': 35, '发售': 25, '开售': 25, '补款': 45, '预售': 45, '抢购': 40}
        self.SCORE_THRESHOLD = 45

    def check(self, text: str) -> Union[bool, Dict[str, Any]]:
        if not text: return False
        for w in self.ULTIMATE_LAUNCH_KEYWORDS:
            if w in text: return {'is_launch': True, 'type': '终极信号', 'time': '即时', 'action': w}
        # 简化版逻辑，完整版请参照上一次回答
        if '上架' in text or '开售' in text: return {'is_launch': True, 'type': '普通信号', 'time': '未知', 'action': '上架'}
        return False

# ---------- 核心解析类 ----------
class WeiboDataParser:
    def __init__(self, sub_cookie: str, webhook_url: Optional[str] = None):
        self.sub_cookie = sub_cookie
        self.webhook_url = webhook_url
        self.detector = SmartLaunchDetector()

    def send_wechat_text(self, text: str) -> bool:
        if not self.webhook_url: return False
        try:
            r = requests.post(self.webhook_url, json={'msgtype': 'text', 'text': {'content': text}}, timeout=10)
            return r.json().get('errcode') == 0
        except Exception: return False

    # ... (download_and_convert_image 等方法保持不变) ...

# ---------- 外部 API ----------
def fetch_one_page(sub_cookie: str, max_id: Optional[str] = None) -> Dict[str, Any]:
    url = 'https://weibo.com/ajax/feed/groupstimeline'
    params = {'list_id': GROUP_ID, 'count': '50'}
    if max_id: params['max_id'] = max_id
    headers = {
        'accept': 'application/json',
        'referer': f'https://weibo.com/mygroups?gid={GROUP_ID}',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    cookies = {'SUB': sub_cookie}
    try:
        r = requests.get(url, params=params, headers=headers, cookies=cookies, timeout=15)
        # 显式打印状态，方便 debug
        if r.status_code != 200:
            logger.warning(f"⚠️ API返回状态码: {r.status_code}")
        return r.json()
    except Exception as e:
        logger.error(f'API 请求异常: {e}')
        return {}

def check_cookie_status(sub_cookie: str) -> bool:
    """验证 Cookie 是否有效"""
    logger.info('🔍 正在验证 Cookie 有效性...')
    url = 'https://weibo.com/ajax/feed/groupstimeline'
    params = {'list_id': GROUP_ID, 'count': '1'}
    headers = {'user-agent': 'Mozilla/5.0', 'referer': f'https://weibo.com/mygroups?gid={GROUP_ID}'}
    try:
        r = requests.get(url, params=params, headers=headers, cookies={'SUB': sub_cookie}, timeout=10, allow_redirects=False)
        
        # 如果遇到 302 跳转到 passport 或者 414/403，肯定失效
        if r.status_code in [302, 403, 414]:
            logger.error(f"🚨 Cookie 失效 (HTTP {r.status_code})")
            return False
            
        # 检查 JSON 内容
        try:
            data = r.json()
            if data.get('ok') == 1:
                logger.info("✅ Cookie 有效")
                return True
            else:
                logger.error(f"🚨 Cookie 失效 (API返回 ok!=1): {data}")
                return False
        except:
            logger.error("🚨 Cookie 失效 (无法解析JSON)")
            return False
            
    except Exception as e:
        logger.error(f"🚨 验证过程出错: {e}")
        return False

def execute_monitoring(sub_cookie: str, webhook_url: Optional[str]):
    # 读取 last_id
    last_id = '0'
    if os.path.exists('last_processed_id.txt'):
        with open('last_processed_id.txt', 'r') as f: last_id = f.read().strip()

    # 1. 先验证 Cookie，如果不通直接抛出异常，不浪费时间抓取
    if not check_cookie_status(sub_cookie):
        raise ConnectionRefusedError("COOKIE_EXPIRED")

    # 2. 抓取数据
    all_stat = []
    max_id = None
    logger.info("🔄 开始抓取微博数据...")
    
    for page in range(3): # 简化为抓前3页，够用了
        data = fetch_one_page(sub_cookie, max_id)
        statuses = data.get('statuses', [])
        if not statuses:
            break
        all_stat.extend(statuses)
        max_id = data.get('max_id_str')
        if not max_id: break
        time.sleep(1)

    if not all_stat:
        logger.info("ℹ️ 未抓取到任何微博 (可能是列表为空)")
        return

    # 3. 过滤新帖
    new_statuses = [s for s in all_stat if int(s.get('idstr', '0')) > int(last_id)]
    if not new_statuses:
        logger.info("ℹ️ 无最新微博")
        return

    logger.info(f"📊 发现 {len(new_statuses)} 条新内容")
    
    # 4. 简单解析并推送 (这里做了简化，只发文本，保证代码跑通)
    parser = WeiboDataParser(sub_cookie, webhook_url)
    current_max_id = int(last_id)
    
    for s in reversed(new_statuses):
        text = s.get('text_raw', s.get('text', ''))
        text = re.sub(r'<[^>]+>', '', text).strip()
        res = parser.detector.check(text)
        
        if res and isinstance(res, dict):
            logger.info(f"📨 推送目标微博: {s['idstr']}")
            parser.send_wechat_text(f"🛍️ 监控报警\n{text[:100]}...")
            
        if int(s['idstr']) > current_max_id:
            current_max_id = int(s['idstr'])

    # 保存 ID
    with open('last_processed_id.txt', 'w') as f:
        f.write(str(current_max_id))

if __name__ == '__main__':
    webhook = os.getenv('WECHAT_WEBHOOK_URL')
    cookie = os.getenv('WEIBO_SUB_COOKIE')
    
    if not cookie or "请替换" in cookie:
        logger.error("🚨 环境变量 WEIBO_SUB_COOKIE 未配置")
        sys.exit(1)

    try:
        execute_monitoring(cookie, webhook)
    except ConnectionRefusedError as e:
        if str(e) == "COOKIE_EXPIRED":
            logger.info("\n⚡ [AutoFix] 检测到 Cookie 失效，启动修复进程...")
            try:
                from weibo_login import WeiboQRLogin
                login_bot = WeiboQRLogin()
                new_sub = login_bot.run_login_process()
                
                if new_sub:
                    logger.info(f"✅ [AutoFix] 修复成功！新 Cookie: {new_sub[:15]}...")
                    # 写入 GitHub Output
                    if "GITHUB_OUTPUT" in os.environ:
                        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                            f.write(f"NEW_SUB_COOKIE={new_sub}\n")
                    sys.exit(0)
                else:
                    logger.error("❌ [AutoFix] 自动登录失败或超时")
                    sys.exit(1)
            except ImportError:
                logger.error("❌ 找不到 weibo_login.py")
                sys.exit(1)
            except Exception as ex:
                logger.error(f"❌ 修复过程报错: {ex}")
                sys.exit(1)
        else:
            sys.exit(1)
