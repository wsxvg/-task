#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博 API 数据解析器 V8.5 (集成自动登录修复版)
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

# ---------- 配置区 ----------
# ⚠️ 重要：请确保这里是你的分组 ID
GROUP_ID = '5159683220312291' 
MAX_WORKERS = 10
DEFAULT_SUB_COOKIE = "请配置Secrets"
PROXIES_SETTING = {"http": None, "https": None}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ... [中间的 SmartLaunchDetector 和 WeiboDataParser 类保持不变，为了节省篇幅省略] ...
# ... [请保留你之前发给我的代码中 SmartLaunchDetector 到 format_launch_notification 的所有内容] ...
# ... [如果你不会拼接，请告诉我，我再发完整的长代码，但核心逻辑就在下面] ...

# 粘贴提示：
# 这里请保留原本的 load_last_id, save_last_id, SmartLaunchDetector 类, 
# WeiboDataParser 类, 以及 fetch_weibo_data 等函数。
# 只需要替换下面的 check_cookie_status 和 execute_monitoring 以及 if __name__ == ...

# ---------- 辅助函数：ID 处理 ----------
def load_last_id() -> str:
    try:
        with open('last_processed_id.txt', 'r', encoding='utf-8') as f:
            content = f.read().strip()
            return content if content.isdigit() else '0'
    except Exception:
        return '0'

def save_last_id(new_id: str):
    if new_id == '0': return
    try:
        with open('last_processed_id.txt', 'w', encoding='utf-8') as f:
            f.write(new_id)
    except Exception as e:
        logger.error(f'❌ 写入 ID 失败: {e}')

# ---------- 核心修改：Cookie 检查 ----------
def check_cookie_status(sub_cookie: str, curr: List[Dict[str, Any]]) -> bool:
    """检查 Cookie 是否有效"""
    if curr:
        return True
    logger.warning('⚠️ 当前窗口无数据，进行 Cookie 二次验证...')
    # 回溯检查 12 小时前的数据
    beijing = pytz.timezone('Asia/Shanghai')
    now = datetime.now(beijing)
    # 这里为了避免循环引用，简单重新实现一次 fetch logic 或者假设外部传入
    # 简化逻辑：如果返回空，且 API 状态码不对，则认为失效
    # 但由于 fetch_weibo_data 已经处理了 API 错误返回空列表的情况
    # 我们通过模拟一次简单的 API 请求来测试
    url = 'https://weibo.com/ajax/feed/groupstimeline'
    params = {'list_id': GROUP_ID, 'count': '1'}
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'referer': f'https://weibo.com/mygroups?gid={GROUP_ID}'
    }
    try:
        r = requests.get(url, params=params, headers=headers, cookies={'SUB': sub_cookie}, timeout=10)
        if r.status_code == 414 or 'passport.weibo.com' in r.url or r.json().get('ok') != 1:
            return False # 确实失效了
        return True # 只是单纯没贴
    except Exception:
        return False # 网络错或解析错，视为失效

# ---------- 主监控逻辑 ----------
def execute_monitoring(sub_cookie: str, webhook_url: Optional[str] = None, enable_push: bool = True):
    beijing = pytz.timezone('Asia/Shanghai')
    now_beijing = datetime.now(beijing)
    
    # 简单的回溯策略
    start_time = now_beijing - timedelta(hours=1)
    end_time = now_beijing

    last_id = int(load_last_id())
    logger.info(f'➡️ 上次 ID: {last_id}')

    # 引入 fetch_weibo_data (需确保该函数在上方已定义)
    # 注意：为了代码完整性，请确保 fetch_weibo_data 在此函数之前定义
    from weibo_parser import fetch_weibo_data # 自身引用，或者直接把 fetch_weibo_data 放在同一个文件里
    
    # 这里直接调用你在原代码里定义的 fetch_weibo_data
    # 假设它在同一个文件中：
    raw_statuses = fetch_weibo_data(sub_cookie, start_time, end_time)

    # 关键修改：如果抓取为空，检测 Cookie
    if not raw_statuses:
        if not check_cookie_status(sub_cookie, raw_statuses):
            logger.error("🚨 判定 Cookie 已失效！")
            # 抛出特定异常，供主程序捕获并启动登录
            raise ConnectionRefusedError("COOKIE_EXPIRED")
        
        logger.info('🏁 无新帖。')
        return

    # 过滤新帖
    new_statuses = [s for s in raw_statuses if int(s.get('idstr', '0')) > last_id]
    if not new_statuses:
        logger.info('ℹ️ 无 ID 更新。')
        return

    current_max_id = max(int(s['idstr']) for s in new_statuses)
    
    # 解析与推送
    parser_obj = WeiboDataParser(sub_cookie, webhook_url) # 需确保类已定义
    parsed = parser_obj.parse_and_enrich(new_statuses)
    launches = parser_obj.filter_launch_posts(parsed)
    
    if launches and enable_push and webhook_url:
        from weibo_parser import send_launch_notifications # 自身引用
        send_launch_notifications(parser_obj, launches)

    save_last_id(str(current_max_id))
    logger.info(f'💾 更新 ID: {current_max_id}')

# ---------- 程序入口 (整合登录) ----------
if __name__ == '__main__':
    # 确保导入依赖函数 (假设所有类和函数都在同一个文件里，这里直接运行)
    # 如果你把 fetch_weibo_data 等分开了，请记得 import
    
    webhook = os.getenv('WECHAT_WEBHOOK_URL')
    enable_push = '--no-push' not in sys.argv
    cookie = os.getenv('WEIBO_SUB_COOKIE')
    
    if not cookie or cookie == DEFAULT_SUB_COOKIE:
        logger.error('🚨 未配置 WEIBO_SUB_COOKIE')
        sys.exit(1)

    try:
        # 运行监控
        execute_monitoring(cookie, webhook, enable_push)
        
    except ConnectionRefusedError as e:
        # 捕获到 Cookie 失效异常
        if str(e) == "COOKIE_EXPIRED":
            logger.info("\n⚡ 启动自动修复流程...")
            try:
                # 动态调用登录模块
                from weibo_login import WeiboQRLogin
                login_bot = WeiboQRLogin()
                new_sub = login_bot.run_login_process()
                
                if new_sub:
                    logger.info("✅ 登录成功！正在导出新 Cookie...")
                    # 将新 Cookie 写入 GitHub Output
                    if "GITHUB_OUTPUT" in os.environ:
                        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                            f.write(f"NEW_SUB_COOKIE={new_sub}\n")
                    sys.exit(0) # 正常退出，交给 YAML 更新 Secret
                else:
                    logger.error("❌ 自动登录失败或超时。")
                    sys.exit(1) # 失败退出
            except ImportError:
                logger.error("❌ 缺少 weibo_login.py，无法修复。")
                sys.exit(1)
            except Exception as login_err:
                logger.error(f"❌ 登录过程出错: {login_err}")
                sys.exit(1)
        else:
            logger.error(f"❌ 发生未处理错误: {e}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"❌ 运行出错: {e}")
        sys.exit(1)
