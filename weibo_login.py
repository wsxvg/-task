# -*- coding: utf-8 -*-
"""
微博自动扫码登录助手 V2.1 (Debug增强版)
基于抓包数据修复: entry=miniblog, alt 换票机制
"""
import requests
import time
import base64
import hashlib
import json
import os
import sys
from datetime import datetime

# 从环境变量读取 Webhook
WEBHOOK_URL = os.getenv('WECHAT_WEBHOOK_URL')

def log(msg):
    """格式化日志输出，方便在 Github Action 查看"""
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)

class WeiboQRLogin:
    def __init__(self):
        self.session = requests.Session()
        # 模拟 Chrome 142 (基于你的抓包)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
            "Referer": "https://passport.weibo.com/sso/signin?entry=miniblog&source=miniblog&disp=popup&url=https%3A%2F%2Fweibo.com%2Fnewlogin%3Ftabtype%3Dweibo%26gid%3D102803%26openLoginLayer%3D0%26url%3Dhttps%253A%252F%252Fweibo.com%252F&from=weibopro",
            "Origin": "https://passport.weibo.com",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Sec-Ch-Ua": '"Chromium";v="142", "Microsoft Edge";v="142", "Not_A Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors"
        }
        
        # 初始化 Session
        try:
            log("🔌 [Init] 正在初始化会话...")
            init_url = "https://passport.weibo.com/sso/signin"
            params = {
                "entry": "miniblog", "source": "miniblog", "disp": "popup",
                "url": "https://weibo.com/newlogin?tabtype=weibo&gid=102803&openLoginLayer=0&url=https%3A%2F%2Fweibo.com%2F",
                "from": "weibopro"
            }
            r = self.session.get(init_url, params=params, headers=self.headers, timeout=15)
            log(f"✅ [Init] 初始化完成, Status: {r.status_code}")
        except Exception as e:
            log(f"❌ [Init] 初始化失败: {e}")

    def send_wechat_msg(self, msg_type, data):
        """发送企业微信通知"""
        if not WEBHOOK_URL:
            log("⚠️ 未配置 Webhook，跳过推送")
            return False
        try:
            payload = {"msgtype": msg_type, msg_type: data}
            r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
            if r.status_code == 200:
                return True
            else:
                log(f"⚠️ 微信推送返回非200: {r.text}")
                return False
        except Exception as e:
            log(f"❌ 微信推送异常: {e}")
            return False

    def get_new_qr(self):
        """获取二维码"""
        ts = int(time.time() * 1000)
        url = "https://passport.weibo.com/sso/v2/qrcode/image"
        params = {"entry": "miniblog", "size": "180", "_": ts}
        try:
            r = self.session.get(url, params=params, headers=self.headers, timeout=10)
            data = r.json()
            if data.get('retcode') == 20000000:
                qrid = data['data']['qrid']
                log(f"📸 [QR] 获取二维码成功 ID: {qrid[:10]}...")
                return qrid, data['data']['image']
            else:
                log(f"❌ [QR] API返回错误: {data}")
        except Exception as e:
            log(f"❌ [QR] 请求异常: {e}")
        return None, None

    def check_qr_status(self, qrid):
        """轮询二维码状态"""
        check_url = "https://passport.weibo.com/sso/v2/qrcode/check"
        params = {
            "entry": "miniblog", "source": "miniblog", "qrid": qrid,
            "disp": "popup", "ver": "20250520", "callback": "onSuccess",
            "ts": int(time.time() * 1000)
        }
        try:
            r = self.session.get(check_url, params=params, headers=self.headers, timeout=10)
            content = r.text
            # 处理 JSONP 或 JSON
            if '(' in content:
                json_str = content[content.find('(')+1 : content.rfind(')')]
                resp = json.loads(json_str)
            else:
                resp = r.json()

            retcode = resp.get('retcode')
            
            if retcode == 20000000:
                log("✅ [Check] 扫码成功！用户已确认。")
                return 1, resp['data']['alt'] 
            elif retcode == 50114002:
                return 2, "Expired" # 过期
            else:
                # 状态 50114001 = 等待扫码
                # 状态 50114004 = 已扫码但未确认
                # log(f"⏳ [Check] 等待中... Code: {retcode}") 
                return 0, "Waiting"
        except Exception as e:
            log(f"⚠️ [Check] 轮询出错: {e}")
            return 0, "Error"

    def login_with_alt(self, alt):
        """使用 ALT 换取 SUB"""
        log(f"🔑 [Login] 正在使用 ALT 票据换取 SUB (ALT: {alt[:10]}***)...")
        url = "https://passport.weibo.com/sso/v2/login"
        params = {
            "entry": "miniblog", "source": "miniblog", "type": "3",
            "alt": alt, 
            "url": "https://passport.weibo.com/sso/v2/pmproxy?url=https%3A%2F%2Fweibo.com%2Fnewlogin%3Ftabtype%3Dweibo%26gid%3D102803%26openLoginLayer%3D0%26url%3Dhttps%253A%252F%252Fweibo.com%252F",
            "disp": "popup", "rid": f"019{int(time.time()*1000)}", "ver": "20250520"
        }
        
        try:
            r = self.session.get(url, params=params, headers=self.headers, allow_redirects=True, timeout=15)
            
            # 优先从 CookieJar 查找 _2A 开头的 Cookie
            sub = None
            for cookie in self.session.cookies:
                if cookie.name == 'SUB' and cookie.value.startswith('_2A'):
                    sub = cookie.value
                    if '.weibo.com' in cookie.domain:
                        log("🎉 [Login] 在 .weibo.com 域下找到目标 Cookie！")
                        return sub
            
            # 兜底：检查响应头
            if not sub and 'SUB' in r.cookies:
                sub = r.cookies['SUB']
                if sub.startswith('_2A'):
                    log("🎉 [Login] 在响应头中找到目标 Cookie！")
                    return sub

            log("❌ [Login] 请求成功但未找到符合格式的 SUB Cookie")
            # 打印调试信息
            log(f"    Cookies: {self.session.cookies.get_dict()}")
            return None
        except Exception as e:
            log(f"❌ [Login] 换取 SUB 异常: {e}")
            return None

    def run_login_process(self):
        log("🚀 [Bot] 启动自动登录修复流程...")
        max_duration = 850 # 14分钟
        start_time = time.time()
        
        while time.time() - start_time < max_duration:
            qrid, img_url = self.get_new_qr()
            if not qrid:
                time.sleep(5); continue
            
            # 下载并推送图片
            try:
                img_data = self.session.get(img_url, headers=self.headers).content
                b64_data = base64.b64encode(img_data).decode('utf-8')
                md5_val = hashlib.md5(img_data).hexdigest()
                
                log("📤 [Bot] 推送二维码到微信...")
                self.send_wechat_msg("image", {"base64": b64_data, "md5": md5_val})
                self.send_wechat_msg("text", {"content": "⚠️ 监控 Cookie 已失效！\n请打开微博APP扫码。\n(Actions 日志可查看实时状态)"})
            except Exception as e:
                log(f"❌ [Bot] 推送图片失败: {e}")

            # 轮询
            qr_is_valid = True
            log("⏳ [Bot] 等待用户扫码...", )
            
            while qr_is_valid and (time.time() - start_time < max_duration):
                status, data = self.check_qr_status(qrid)
                
                if status == 1:
                    # 成功
                    final_sub = self.login_with_alt(data)
                    if final_sub:
                        return final_sub
                    else:
                        qr_is_valid = False # 换取失败，重新获取二维码
                elif status == 2:
                    log("⚠️ [Bot] 二维码已过期，正在刷新...")
                    qr_is_valid = False
                else:
                    time.sleep(2)
        
        log("⏰ [Bot] 登录流程超时。")
        return None

if __name__ == "__main__":
    # 本地测试用
    bot = WeiboQRLogin()
    bot.run_login_process()
