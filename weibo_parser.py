# weibo_parser.py (完整代码)

import os
import json
import time
import requests
import logging
import threading
from datetime import datetime, timedelta, timezone

# --- 配置常量 ---
# 从环境变量读取配置
WEIBO_UID = os.environ.get('WEIBO_UID', '7711480947')
WECHAT_WEBHOOK_URL = os.environ.get('WECHAT_WEBHOOK_URL')
# 确保上次处理ID文件路径正确
LAST_PROCESSED_ID_FILE = 'last_processed_id.txt'

# 微博 API 配置
PAGE_LIMIT = 20  # 最大抓取页数，每页约50条，总计约1000条
STATUS_COUNT = 50 # 微博每页返回数量

# 动态回溯窗口配置
# 默认高频监控（每10分钟运行一次）：回溯最近 2 小时
HIGH_FREQ_WINDOW_HOURS = 2
# 低频监控（如夜间）：回溯最近 8 小时 (您在 log 中没有体现，但作为通用配置保留)
LOW_FREQ_WINDOW_HOURS = 8 
LOW_FREQ_START_HOUR = 23 # 23点
LOW_FREQ_END_HOUR = 7    # 7点

# 日志配置
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

# --- 辅助函数 ---

def load_last_processed_id():
    """从文件中加载上次处理的最新微博ID"""
    try:
        with open(LAST_PROCESSED_ID_FILE, 'r') as f:
            content = f.read().strip()
            if content:
                return int(content)
    except FileNotFoundError:
        pass
    except ValueError:
        logger.warning("⚠️ last_processed_id.txt 内容格式不正确，将从 0 开始处理。")
    return 0

def save_last_processed_id(new_id):
    """将最新的微博ID写入文件"""
    with open(LAST_PROCESSED_ID_FILE, 'w') as f:
        f.write(str(new_id))

def get_current_time_range():
    """
    根据当前时间判断采用高频（2小时）还是低频（8小时）回溯窗口。
    并返回时间窗口的起点和终点。
    """
    tz = timezone(timedelta(hours=8)) # 北京时间
    now = datetime.now(tz)
    
    # 检查是否处于低频时间段 (例如 23:00 到次日 07:00)
    if LOW_FREQ_START_HOUR <= now.hour or now.hour < LOW_FREQ_END_HOUR:
        window_hours = LOW_FREQ_WINDOW_HOURS
        window_type = "低频监控"
    else:
        window_hours = HIGH_FREQ_WINDOW_HOURS
        window_type = "高频监控"
        
    start_time = now - timedelta(hours=window_hours)
    
    logger.info(f"📅 设定动态回溯窗口: {start_time.strftime('%Y-%m-%d %H:%M')} → {now.strftime('%Y-%m-%d %H:%M')} (最近 {window_hours} 小时 ({window_type}))")
    return start_time, now

def fetch_weibo_data(uid, start_time):
    """
    获取指定用户ID的微博列表。
    **重要改进：移除了基于时间的翻页终止，仅依赖 PAGE_LIMIT。**
    """
    base_url = f"https://weibo.com/ajax/statuses/mymblog?uid={uid}&feature=0&page="
    raw_statuses = []
    max_id_str = ""
    
    logger.info("🚀 正在抓取微博列表...")

    for page in range(PAGE_LIMIT):
        url = base_url + str(page + 1)
        if max_id_str:
            url += f"&max_id={max_id_str}"
            
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ 抓取第 {page+1} 页失败: {e}")
            break

        if data.get('ok') != 1:
            logger.error(f"❌ 抓取第 {page+1} 页失败，API 返回错误: {data}")
            break

        statuses = data.get('statuses', [])
        if not statuses:
            logger.info(f"ℹ️ 第 {page+1} 页没有更多帖子。")
            break

        raw_statuses.extend(statuses)
        
        # --- 关键修改：移除时间判断和 break ---
        # 原有的时间判断被移除，以确保在 API 限制页数内抓取到所有 ID 新的帖子
        # ------------------------------------
        
        max_id_str = data.get('max_id', '')
        if not max_id_str or max_id_str == '0':
            logger.info(f"ℹ️ max_id 耗尽，在第 {page+1} 页停止抓取。")
            break

        time.sleep(1) # 礼貌延迟，防止封禁
        
    # 对所有抓取到的帖子按ID降序排序，确保最新的ID排在前面
    raw_statuses.sort(key=lambda x: int(x.get('idstr', '0')), reverse=True)
    
    return raw_statuses


def fetch_long_text(mid):
    """获取长微博的完整文本"""
    url = f"https://weibo.com/ajax/statuses/longtext?id={mid}"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data.get('ok') == 1 and data.get('data'):
            return data['data'].get('longTextContent', '')
    except requests.exceptions.RequestException:
        pass
    return None

def process_long_texts(statuses):
    """并行获取长微博全文"""
    long_text_statuses = [s for s in statuses if s.get('isLongText')]
    if not long_text_statuses:
        return
        
    logger.info(f"⚡ 检测到 {len(long_text_statuses)} 条长微博，开始并行获取全文...")
    
    def worker(status):
        full_text = fetch_long_text(status.get('mid'))
        if full_text:
            status['text_raw'] = full_text
            status['text'] = full_text # 更新 text 字段
            
    threads = []
    for status in long_text_statuses:
        t = threading.Thread(target=worker, args=(status,))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    logger.info("✅ 长微博全文获取完毕。")


def intelligent_score(status):
    """
    智能评分算法：基于关键词、图片数量和长文本的权重判断是否为上新帖。
    （此函数为示例，您可以根据实际需求调整逻辑）
    """
    score = 0
    reason = []
    
    text = status.get('text_raw', status.get('text', '')).lower()
    
    # 权重 1: 强上新信号
    strong_keywords = ['已上架', '今晚八点', '明天开拍', '开定', '预告', '上新链接']
    if any(k in text for k in strong_keywords):
        score += 100
        reason.append("终极信号(已上架)")
        
    # 权重 2: 次级信号
    secondary_keywords = ['定金', '全款', '抽奖', '转发送', '预览', '制作进度']
    if any(k in text for k in secondary_keywords):
        score += 30
        reason.append("次级信号(关键词)")
        
    # 权重 3: 长文本/多图（暗示内容丰富）
    if status.get('isLongText'):
        score += 15
        reason.append("长文本")
        
    if status.get('pic_num', 0) >= 3:
        score += 10
        reason.append("多图")
        
    if score >= 100:
        return score, "终极信号(已上架)"
    elif score >= 50:
        return score, "高权重预告"
    elif score >= 30:
        return score, "中等权重信息"
    
    return score, "普通内容"

def push_wechat_text_card(status):
    """
    **改进的推送函数：使用纯文本卡片格式，包含时间、热度、链接等信息。**
    """
    if not WECHAT_WEBHOOK_URL:
        logger.warning("⚠️ 未配置企业微信 Webhook URL，跳过推送。")
        return False

    # 1. 提取和格式化关键信息
    try:
        # 格式化发帖时间
        raw_time_str = status['created_at']
        dt_obj = datetime.strptime(raw_time_str, "%a %b %d %H:%M:%S %z %Y")
        post_time = dt_obj.strftime("%Y-%m-%d %H:%M:%S")

        # 智能评分结果
        score, tag = intelligent_score(status)
        
        # 截取摘要 (防止文本过长)
        text_raw = status.get('text_raw', status.get('text', ''))
        summary = text_raw.replace('\n', ' ').strip()[:100] + '...'
        
        # 互动数据
        reposts = status.get('reposts_count', 0)
        comments = status.get('comments_count', 0)
        attitudes = status.get('attitudes_count', 0)
        
        # 微博链接
        user_id = status['user']['idstr']
        mid = status['mid']
        weibo_url = f"https://weibo.com/{user_id}/{mid}"
        
        # 2. 构建纯文本消息
        message_title = f"【🔥 微博上新/重要通知】"
        message_text = f"""
*** {message_title} ***
- 账号: {status['user']['screen_name']}
- 时间: {post_time}
- 识别: {tag} (评分: {score})
- 摘要: {summary}
- 热度: 👍 {attitudes} | 💬 {comments} | 🔃 {reposts}
- 链接: {weibo_url}
"""

        payload = {
            "msgtype": "text",
            "text": {
                "content": message_text
            }
        }

        # 3. 发送请求
        response = requests.post(WECHAT_WEBHOOK_URL, json=payload, timeout=5)
        response.raise_for_status()
        result = response.json()
        
        if result.get('errmsg') == 'ok':
            logger.info(f"    ✅ 文本推送成功: {status['user']['screen_name']}")
            return True
        else:
            logger.error(f"    ❌ 文本推送失败: {result}")
            return False

    except Exception as e:
        logger.error(f"推送消息时发生错误: {e}")
        return False

def execute_monitoring():
    """主执行逻辑"""
    last_processed_id = load_last_processed_id()
    logger.info(f"➡️ 上次处理的最新 ID: {last_processed_id}")

    start_time, now = get_current_time_range()

    # 1. 抓取微博数据 (现在会抓取 PAGE_LIMIT 页，不受 start_time 限制)
    raw_statuses = fetch_weibo_data(WEIBO_UID, start_time)
    
    # 2. 筛选出 ID 比上次记录新的帖子 (核心防遗漏机制)
    # 排除ID为0或空的帖子，并确保 ID 必须大于上次记录
    new_raw_statuses = [
        s for s in raw_statuses 
        if s.get('idstr') and int(s['idstr']) > last_processed_id
    ]

    logger.info(f"\n📊 抓取完成，共获得 {len(raw_statuses)} 条原始微博。其中 {len(new_raw_statuses)} 条为新帖待处理。")

    if not new_raw_statuses:
        logger.info("ℹ️ 没有新的帖子需要处理。")
        return

    # 3. 处理长文本
    process_long_texts(new_raw_statuses)

    # 4. 智能评分与过滤
    logger.info("🔍 开始使用智能评分算法进行上新帖识别...")
    
    # 对新帖子列表进行评分和识别
    processed_new_posts = []
    for status in new_raw_statuses:
        score, tag = intelligent_score(status)
        if score > 0: # 假设任何评分大于 0 的都值得推送
            status['score'] = score
            status['tag'] = tag
            logger.info(f"  -> 识别到新帖 (ID: {status['idstr']}): 评分 {score}, 标签 {tag}")
            processed_new_posts.append(status)
            
    if not processed_new_posts:
        logger.info("✅ 筛选完成，没有找到符合推送标准的上新帖。")
        return

    logger.info(f"✅ 识别成功！共找到 {len(processed_new_posts)} 条上新帖。")

    # 5. 推送通知
    logger.info(f"\n📨 开始推送 {len(processed_new_posts)} 条上新预告到企业微信...")
    
    # 按 ID 降序排序，确保最新的先推送
    processed_new_posts.sort(key=lambda x: int(x.get('idstr', '0')), reverse=True)
    
    latest_id = last_processed_id
    
    for status in processed_new_posts:
        if push_wechat_text_card(status):
            # 只有成功推送后，才更新 latest_id
            current_id = int(status['idstr'])
            if current_id > latest_id:
                latest_id = current_id
        
        time.sleep(1) # 推送间隔

    # 6. 更新最新ID
    if latest_id > last_processed_id:
        save_last_processed_id(latest_id)
        logger.info(f"💾 已将本次最新 ID ({latest_id}) 写入 {LAST_PROCESSED_ID_FILE}，待 Git 提交。")
    else:
        logger.info("ℹ️ 最新 ID 未更新或无变化，无需提交。")

    logger.info("\n🏁 所有任务执行完毕。")

if __name__ == '__main__':
    # 设置北京时间时区
    os.environ['TZ'] = 'Asia/Shanghai'
    time.tzset()
    logger.info(f"🚀 程序启动于: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
    
    try:
        execute_monitoring()
    except Exception as e:
        logger.error(f"致命错误：{e}", exc_info=True)
