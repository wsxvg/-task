# -*- coding: utf-8 -*-
"""
微博自动扫码登录助手 V2.2 (主动刷新版)
核心升级：
1. 增加 90秒 本地强制刷新机制，解决二维码过期不更新的问题
2. 优化轮询逻辑，防止网络波动导致脚本卡死
"""
import requests
import time
import base64
import hashlib
import json
import os
from datetime import datetime

# 从环境变量读取 Webhook
WEBHOOK_URL = os.getenv('WECHAT_WEBHOOK_URL')

# 🔧 配置：二维码最大存活时间 (秒)
# 经验值：微博二维码大概2-3分钟失效，我们设为90秒主动刷新，保证用户体验
QR_MAX_LIFETIME = 90 

def log(msg):
    """格式化日志输出"""
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)

class WeiboQRLogin:
    def __init__(self):
        self.session = requests.Session()
        # 模拟 Chrome 142
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
            log("🔌 [Init] 初始化会话...")
            init_url = "https://passport.weibo.com/sso/signin"
            params = {
                "entry": "miniblog", "source": "miniblog", "disp": "popup",
                "url": "https://weibo.com/newlogin?tabtype=weibo&gid=102803&openLoginLayer=0&url=https%3A%2F%2Fweibo.com%2F",
                "from": "weibopro"
            }
            self.session.get(init_url, params=params, headers=self.headers, timeout=15)
        except Exception as e:
            log(f"⚠️ [Init] 初始化网络波动: {e}")

    def send_wechat_msg(self, msg_type, data):
        """发送企业微信通知"""
        if not WEBHOOK_URL:
            return False
        try:
            payload = {"msgtype": msg_type, msg_type: data}
            requests.post(WEBHOOK_URL, json=payload, timeout=10)
            return True
        except Exception:
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
                return data['data']['qrid'], data['data']['image']
        except Exception as e:
            log(f"❌ [QR] 获取失败: {e}")
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
            if '(' in content:
                json_str = content[content.find('(')+1 : content.rfind(')')]
                resp = json.loads(json_str)
            else:
                resp = r.json()

            retcode = resp.get('retcode')
            
            if retcode == 20000000:
                return 1, resp['data']['alt'] # 成功
            elif retcode == 50114002:
                return 2, "Expired" # 明确过期
            else:
                return 0, "Waiting" # 等待中
        except Exception:
            return 0, "NetError" # 网络错误不中断，继续重试

    def login_with_alt(self, alt):
        """使用 ALT 换取 SUB"""
        log(f"🔑 [Login] 正在换取 SUB...")
        url = "https://passport.weibo.com/sso/v2/login"
        params = {
            "entry": "miniblog", "source": "miniblog", "type": "3",
            "alt": alt, 
            "url": "https://passport.weibo.com/sso/v2/pmproxy?url=https%3A%2F%2Fweibo.com%2Fnewlogin%3Ftabtype%3Dweibo%26gid%3D102803%26openLoginLayer%3D0%26url%3Dhttps%253A%252F%252Fweibo.com%252F",
            "disp": "popup", "rid": f"019{int(time.time()*1000)}", "ver": "20250520"
        }
        try:
            r = self.session.get(url, params=params, headers=self.headers, allow_redirects=True, timeout=15)
            
            # 1. 查 CookieJar
            for cookie in self.session.cookies:
                if cookie.name == 'SUB' and cookie.value.startswith('_2A'):
                    if '.weibo.com' in cookie.domain:
                        return cookie.value
            
            # 2. 查 Response Cookies
            if 'SUB' in r.cookies and r.cookies['SUB'].startswith('_2A'):
                return r.cookies['SUB']
                
            return None
        except Exception as e:
            log(f"❌ [Login] 换取异常: {e}")
            return None

    def run_login_process(self):
        log(f"🚀 [Bot] 启动修复流程 (超时上限: 14分钟)")
        log(f"⚙️ 设定二维码主动刷新间隔: {QR_MAX_LIFETIME}秒")
        
        max_duration = 850 
        start_time = time.time()
        
        while time.time() - start_time < max_duration:
            # 1. 获取新码
            qrid, img_url = self.get_new_qr()
            if not qrid:
                time.sleep(5); continue
            
            # 记录这个二维码的生成时间
            qr_start_time = time.time()
            
            # 推送
            try:
                img_data = self.session.get(img_url, headers=self.headers).content
                b64_data = base64.b64encode(img_data).decode('utf-8')
                md5_val = hashlib.md5(img_data).hexdigest()
                
                self.send_wechat_msg("image", {"base64": b64_data, "md5": md5_val})
                self.send_wechat_msg("text", {"content": f"⚠️ Cookie 已失效，请扫码！\n(二维码有效期 {QR_MAX_LIFETIME}秒)"})
                log(f"📤 [Bot] 二维码已推送 (ID: {qrid[:8]}...)")
            except Exception:
                pass

            # 2. 轮询 (加入主动超时判断)
            qr_is_valid = True
            while qr_is_valid and (time.time() - start_time < max_duration):
                # Check 1: 强制超时检查
                life_span = time.time() - qr_start_time
                if life_span > QR_MAX_LIFETIME:
                    log(f"♻️ [Bot] 二维码已存在 {int(life_span)}秒，主动废弃并刷新...")
                    qr_is_valid = False
                    # 发送一条提示让用户知道这个码废了
                    # self.send_wechat_msg("text", {"content": "🔄 二维码超时，正在获取新的..."})
                    continue # 跳出内层循环，触发外层循环重新获取

                # Check 2: API 状态检查
                status, data = self.check_qr_status(qrid)
                
                if status == 1: # 成功
                    log("✅ [Bot] 扫码成功！")
                    final_sub = self.login_with_alt(data)
                    if final_sub:
                        return final_sub
                    else:
                        qr_is_valid = False 
                elif status == 2: # 服务器明确说过期
                    log("⚠️ [Bot] 微博提示二维码已过期，刷新中...")
                    qr_is_valid = False
                else:
                    # Waiting or Error
                    time.sleep(2)
                    # 可以在这里打印心跳，但为了日志整洁先省略
        
        log("⏰ [Bot] 流程彻底超时，退出。")
        return None

if __name__ == "__main__":
    bot = WeiboQRLogin()
    # 本地测试打印结果
    sub = bot.run_login_process()
    if sub:
        print(f"✅ 最终获取的 SUB: {sub}")
