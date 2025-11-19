# -*- coding: utf-8 -*-
"""
微博自动扫码登录助手 V1.0
"""
import requests
import time
import base64
import hashlib
import re
import os
import sys

# 从环境变量读取 Webhook
WEBHOOK_URL = os.getenv('WECHAT_WEBHOOK_URL')

class WeiboQRLogin:
    def __init__(self):
        self.session = requests.Session()
        # 模拟浏览器环境，防风控
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
            "Referer": "https://passport.weibo.com/sso/signin",
            "Origin": "https://passport.weibo.com"
        }
        # 1. 初始化 Session (获取基础 CSRF Token)
        try:
            self.session.get("https://passport.weibo.com/sso/signin", headers=self.headers, timeout=10)
        except Exception:
            pass

    def send_wechat_msg(self, msg_type, data):
        """发送企业微信通知"""
        if not WEBHOOK_URL:
            print("⚠️ 未配置 WECHAT_WEBHOOK_URL，无法发送二维码")
            return False
        try:
            payload = {"msgtype": msg_type, msg_type: data}
            requests.post(WEBHOOK_URL, json=payload, timeout=10)
            return True
        except Exception as e:
            print(f"❌ 微信推送失败: {e}")
            return False

    def get_new_qr(self):
        """获取二维码"""
        ts = int(time.time() * 1000)
        url = f"https://passport.weibo.com/sso/v2/qrcode/image?size=180&_={ts}"
        try:
            r = self.session.get(url, headers=self.headers, timeout=10)
            data = r.json()
            if data.get('retcode') == 20000000:
                return data['data']['qrid'], data['data']['image']
        except Exception as e:
            print(f"❌ 获取二维码 API 错误: {e}")
        return None, None

    def check_qr_status(self, qrid):
        """
        轮询状态
        返回: (状态码, 数据)
        1: 成功, 2: 过期, 0: 等待
        """
        check_url = "https://passport.weibo.com/sso/v2/qrcode/check"
        params = {
            "entry": "sso",
            "qrid": qrid,
            "callback": "onSuccess", 
            "ts": int(time.time() * 1000)
        }
        try:
            r = self.session.get(check_url, params=params, headers=self.headers, timeout=10)
            resp = r.json()
            retcode = resp.get('retcode')
            
            if retcode == 20000000:
                return 1, resp['data']['alt'] # 成功，返回跳转URL
            elif retcode == 50114002:
                return 2, "Expired"
            else:
                return 0, "Waiting"
        except Exception:
            return 0, "Error"

    def login_and_get_sub(self, redirect_url):
        """访问跳转链接提取 SUB"""
        try:
            # 访问跨域登录 URL，自动 Set-Cookie
            r = self.session.get(redirect_url, headers=self.headers, allow_redirects=True, timeout=10)
            
            # 1. 优先从 CookieJar 获取
            sub = self.session.cookies.get('SUB', domain='.weibo.com') or \
                  self.session.cookies.get('SUB', domain='.sina.com.cn') or \
                  self.session.cookies.get('SUB')

            # 2. 兜底：从响应文本或 Header 提取
            if not sub:
                # 你的抓包显示 Set-Cookie: SUB=...
                import re
                match = re.search(r'SUB=([^;&"\']+)', str(r.headers) + r.text)
                if match:
                    sub = match.group(1)
            
            return sub
        except Exception as e:
            print(f"❌ 提取 SUB 失败: {e}")
            return None

    def run_login_process(self):
        """执行完整的登录流程"""
        print("🔄 启动自动登录流程...")
        max_duration = 900  # 最多运行 15 分钟
        start_time = time.time()
        
        while time.time() - start_time < max_duration:
            # 1. 获取二维码
            qrid, img_url = self.get_new_qr()
            if not qrid:
                time.sleep(5)
                continue
            
            print(f"📤 获取二维码成功 (ID: {qrid})，正在发送...")
            
            # 2. 下载并发送图片
            try:
                img_data = self.session.get(img_url).content
                b64_data = base64.b64encode(img_data).decode('utf-8')
                md5_val = hashlib.md5(img_data).hexdigest()
                
                self.send_wechat_msg("image", {"base64": b64_data, "md5": md5_val})
                self.send_wechat_msg("text", {"content": "⚠️ 微博 Cookie 已失效！\n请在 1-2 分钟内扫描二维码。\n(若过期脚本会自动刷新发新的)"})
            except Exception as e:
                print(f"❌ 发送图片失败: {e}")
            
            # 3. 轮询检查
            qr_is_valid = True
            while qr_is_valid and (time.time() - start_time < max_duration):
                status, data = self.check_qr_status(qrid)
                
                if status == 1:
                    print("✅ 扫码成功！正在获取最终 Cookie...")
                    final_sub = self.login_and_get_sub(data)
                    if final_sub:
                        return final_sub
                    else:
                        qr_is_valid = False # 失败重试
                        
                elif status == 2:
                    print("⚠️ 二维码已过期，正在刷新...")
                    qr_is_valid = False # 跳出内循环，获取新码
                    
                else:
                    time.sleep(2) # 继续等待
                    
        print("⏰ 登录超时，退出。")
        return None
