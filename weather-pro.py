#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
爱意天气机器人 V4.3 - 最终完整版
* 所有功能和逻辑优化已应用。
* 使用提供的示例默认值进行配置。
"""

import requests
import json
import time
import random
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 请确保已安装此库: pip install pytz
import pytz

# --- 核心配置区 ---
# 使用您之前在对话中提供的示例值
WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=5a9b8214-4fae-4bbd-a147-9ecfe5eb6d90"
API_KEY = "8ddce2378f984da6b24e1310c226e500"

# API 接口域名（和风天气默认）
API_HOST = "p55pwcrrq3.re.qweatherapi.com"

# 城市配置：经纬度是机器人获取天气的核心依据
CITIES = {
    "卧龙": {"lon": "120.27294", "lat": "30.61604"},
    "吴村": {"lon": "119.33204", "lat": "30.57856"},
    "常大": {"lon": "119.82476", "lat": "31.70889"},
    "苏科大": {"lon": "120.57377", "lat": "31.24879"}
}

# --- 常量定义 ---
TEMP_DIFF_LARGE = 12  # 大温差阈值
UV_STRONG = 7       # 强紫外线阈值
RAIN_POP_REMINDER = 70 # 降水概率提醒阈值 (%)
AQI_MODERATE = 100     # 良好  
HUMIDITY_VERY_DRY = 30    # 很干燥阈值
HUMIDITY_COMFORTABLE_MAX = 60  # 舒适湿度上限
PM25_VERY_UNHEALTHY = 150  # PM2.5重度污染阈值

API_TIMEOUT = 15    # API超时时间
API_RETRY_COUNT = 3  # API重试次数
MAX_WORKERS = 4    # 最大并发线程数

INDICES_FLU = 9       # 感冒指数
INDICES_COMF = 8    # 舒适度指数
INDICES_DRSG = 3    # 穿衣指数

GREETINGS = ["宝贝的专属天气小报", "今日份的贴心天气", "你的专属天气预报员"]
CLOSINGS = {
    "爱你的每一天！": "🥰",
    "希望我的预报能给你带来一天的好心情！": "💖",
    "照顾好自己呀，比心！": "💕"
}
WEATHER_ICONS = {"晴": "☀️", "多云": "⛅", "阴": "☁️", "雨": "🌧️", "雷": "⛈️", "雪": "❄️"}

# --- API数据获取模块 ---
def get_api_data_with_retry(endpoint, params, retry_count=API_RETRY_COUNT):
    """带重试机制的API数据获取"""
    api_url = f"https://{API_HOST}{endpoint}"
    headers = {"X-QW-Api-Key": API_KEY}
    
    for attempt in range(retry_count):
        try:
            response = requests.get(api_url, params=params, headers=headers, timeout=API_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            if not isinstance(data, dict):
                raise ValueError(f"API返回数据格式错误: {type(data)}")
                
            if data.get("code") == "200":
                return data
            else:
                print(f"  > ⚠️ API返回错误 (attempt {attempt + 1}): {data.get('code')} for {endpoint}")
                if attempt == retry_count - 1:
                    return None
                    
        except requests.exceptions.RequestException as e:
            print(f"  > ❌ 网络请求错误 (attempt {attempt + 1}): {e} for {endpoint}")
        except ValueError as e:
            print(f"  > ❌ 数据格式错误 (attempt {attempt + 1}): {e}")
        except Exception as e:
            print(f"  > ❌ 未知错误 (attempt {attempt + 1}): {e}")
            
        if attempt < retry_count - 1:
            time.sleep(1 + attempt * 0.5)
            
    return None

def get_city_weather_from_api(city_name, coords):
    """单个城市天气数据获取（增强版）"""
    print(f"📍 开始获取「{city_name}」的天气数据...")
    base_params = {"location": f"{coords['lon']},{coords['lat']}", "lang": "zh"}
    
    api_calls = [
        ("/v7/weather/7d", base_params),       # 七天预报
        ("/v7/weather/24h", base_params),     # 24小时预报
        ("/v7/weather/now", base_params),     # 实时天气
        ("/v7/air/now", base_params),         # 实时空气质量
        ("/v7/warning/now", base_params),      # 预警信息
        ("/v7/indices/1d", {**base_params, "type": f"{INDICES_FLU},{INDICES_COMF},{INDICES_DRSG}"})  # 生活指数
    ]
    
    results = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_to_api = {
            executor.submit(get_api_data_with_retry, endpoint, params): endpoint
            for endpoint, params in api_calls
        }
        
        for future in as_completed(future_to_api):
            endpoint = future_to_api[future]
            try:
                result = future.result()
                results[endpoint] = result
            except Exception as e:
                results[endpoint] = None
    
    weather_7d = results.get("/v7/weather/7d")
    weather_24h = results.get("/v7/weather/24h")
    weather_now = results.get("/v7/weather/now")
    air_quality = results.get("/v7/air/now")
    warning_data = results.get("/v7/warning/now")
    indices_data = results.get("/v7/indices/1d")

    if not weather_7d or not weather_7d.get('daily'):
        print(f"  > ❌ 「{city_name}」核心天气数据获取失败。")
        return None

    today_forecast = weather_7d['daily'][0]
    
    # 获取实时和逐小时数据
    current_hour_temp = None
    weather_trend = None
    
    if weather_24h and weather_24h.get('hourly'):
        current_hour_temp, weather_trend = analyze_hourly_weather_changes(weather_24h['hourly'])
    
    if not weather_trend:
        text_day = today_forecast.get('textDay', '未知')
        text_night = today_forecast.get('textNight', '未知')
        weather_trend = text_day if text_day == text_night else f"{text_day}转{text_night}"
    
    current_temp = current_hour_temp
    feels_like = None
    
    if not current_temp and weather_now and weather_now.get('now'):
        current_temp = float(weather_now['now'].get('temp')) if weather_now['now'].get('temp') else None
        feels_like = float(weather_now['now'].get('feelsLike')) if weather_now['now'].get('feelsLike') else None
    
    # 提取关键字段
    wind_info = {
        'windDir': today_forecast.get('windDirDay', 'N/A'),
        'windScale': today_forecast.get('windScaleDay', 'N/A'),
    }
    
    precipitation = today_forecast.get('precip', '0.0')
    pop = today_forecast.get('pop', '0')
    
    air_data = None
    if air_quality and air_quality.get('now'):
        air_info = air_quality['now']
        air_data = {
            'aqi': int(air_info.get('aqi', 0)),
            'pm25': int(air_info.get('pm2p5', 0)),
            'category': air_info.get('category', '未知'),
        }

    indices_info = {}
    if indices_data and indices_data.get('daily'):
        for index in indices_data['daily']:
            index_type = int(index.get('type', 0))
            if index_type == INDICES_FLU:
                indices_info['flu'] = {'level': int(index.get('level', 0)), 'category': index.get('category', ''), 'text': index.get('text', '')}
            elif index_type == INDICES_COMF:
                indices_info['comfort'] = {'level': int(index.get('level', 0)), 'category': index.get('category', ''), 'text': index.get('text', '')}
            elif index_type == INDICES_DRSG:
                indices_info['dressing'] = {'level': int(index.get('level', 0)), 'category': index.get('category', ''), 'text': index.get('text', '')}
    
    print(f"  > ✅ 「{city_name}」天气数据获取成功")
    
    return {
        "name": city_name,
        "tempMax": float(today_forecast.get('tempMax', 0.0)),
        "tempMin": float(today_forecast.get('tempMin', 0.0)),
        "currentTemp": float(current_temp) if current_temp else None,
        "feelsLike": float(feels_like) if feels_like else None,
        "textDay": weather_trend,
        "humidity": int(today_forecast.get('humidity', 0)),
        "uvIndex": int(today_forecast.get('uvIndex', 0)),
        "windInfo": wind_info,
        "precipitation": float(precipitation) if precipitation != 'N/A' else 0.0,
        "pop": int(pop) if pop != 'N/A' else 0,
        "warnings": warning_data.get('warning', []) if warning_data else [],
        "hourly_data": weather_24h.get('hourly', []) if weather_24h else [],
        "air_quality": air_data,
        "indices": indices_info,
    }

def get_all_cities_weather_concurrent():
    """并发获取所有城市的天气数据"""
    print(f"🚀 开始并发获取 {len(CITIES)} 个城市的天气数据...")
    start_time = time.time()
    
    all_weather_data = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_city = {
            executor.submit(get_city_weather_from_api, city_name, coords): city_name
            for city_name, coords in CITIES.items()
        }
        
        for future in as_completed(future_to_city):
            city_name = future_to_city[future]
            try:
                weather_data = future.result()
                if weather_data:
                    all_weather_data.append(weather_data)
                else:
                    print(f"  > ⚠️ 「{city_name}」天气数据获取失败，跳过")
            except Exception as e:
                print(f"  > ❌ 「{city_name}」处理异常: {e}")
    
    end_time = time.time()
    elapsed_time = end_time - start_time
    print(f"⚡ 并发获取完成！总用时: {elapsed_time:.2f}秒，成功获取 {len(all_weather_data)}/{len(CITIES)} 个城市数据")
    
    return all_weather_data

# --- Webhook 发送模块 ---
def send_message_to_webhook(message):
    """
    向微信企业号 Webhook 发送纯文本消息
    （增强容错和日志记录）
    """
    if not WEBHOOK_URL or not API_KEY:
        print("  > ❌ 配置缺失，跳过消息发送。")
        return False

    payload = {
        "msgtype": "text",
        "text": {
            "content": message
        }
    }

    try:
        response = requests.post(WEBHOOK_URL, json=payload, timeout=API_TIMEOUT)
        response.raise_for_status()
        
        result = response.json()
        
        if result.get("errcode") == 0:
            print("  > ✅ 微信 Webhook 消息发送成功！")
            return True
        else:
            print(f"  > ❌ 微信 Webhook 发送失败。错误码: {result.get('errcode')}, 消息: {result.get('errmsg')}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"  > ❌ Webhook 请求异常: {e}")
        return False
    except Exception as e:
        print(f"  > ❌ Webhook 发送时发生未知错误: {e}")
        return False

# --- 辅助分析模块 ---
def analyze_hourly_weather_changes(hourly_data):
    """分析逐小时天气变化趋势"""
    if not hourly_data:
        return None, None
    
    current_time = datetime.now()
    current_hour_temp = None
    
    for hour_data in hourly_data:
        hour_time_str = hour_data['fxTime']
        try:
            hour = int(hour_time_str[11:13])
            current_hour = current_time.hour
            
            if hour == current_hour or (hour == current_hour + 1 and current_time.minute > 45):
                current_hour_temp = float(hour_data['temp'])
                break
        except:
            continue
    
    if current_hour_temp is None and hourly_data:
        current_hour_temp = float(hourly_data[0]['temp'])
    
    weather_changes = []
    prev_weather = None
    
    for hour_data in hourly_data[:12]:
        current_weather = hour_data['text']
        if not prev_weather or prev_weather != current_weather:
            if not weather_changes or weather_changes[-1] != current_weather:
                 weather_changes.append(current_weather)
        prev_weather = current_weather
    
    weather_trend = '转'.join(weather_changes) if weather_changes else '未知'
    
    return current_hour_temp, weather_trend

def analyze_meaningful_rain(hourly_data):
    """智能分析有意义的降雨时段（只提醒白天和接下来的降雨）"""
    if not hourly_data:
        return False, ""
    
    meaningful_rain_periods = []
    next_12h_rain = False
    daytime_rain = False
    
    for i, hour_data in enumerate(hourly_data[:12]):
        if "雨" in hour_data['text'] or "雪" in hour_data['text']:
            try:
                hour_time_str = hour_data['fxTime']
                hour = int(hour_time_str[11:13])
                
                if 6 <= hour <= 20:
                    daytime_rain = True
                    if i <= 6:
                        next_12h_rain = True
                        time_desc = f"上午{hour}点" if 6 <= hour < 12 else f"下午{hour-12 if hour>12 else 12}点" if 12 <= hour < 18 else f"傍晚{hour-12}点"
                        rain_intensity = [w for w in ["大雨", "中雨", "小雨"] if w in hour_data['text']]
                        meaningful_rain_periods.append({'time': time_desc, 'intensity': rain_intensity[0] if rain_intensity else "雨", 'hour': hour})
                elif i <= 3:
                    next_12h_rain = True
            except:
                continue
    
    rain_time = ""
    if meaningful_rain_periods:
        earliest = min(meaningful_rain_periods, key=lambda x: x['hour'])
        rain_time = f"（预计{earliest['time']}左右有{earliest['intensity']}）"
    elif next_12h_rain:
        rain_time = "（接下来有雨）"
    
    should_remind = daytime_rain or next_12h_rain
    return should_remind, rain_time

def get_feels_like_alert(current_temp, feels_like, temp_min, temp_max):
    """根据体感温度与实际温度的差异生成提醒信息 (V4.3 优化点)"""
    if feels_like is None:
        return ""
    
    if current_temp is None:
        current_temp = (temp_min + temp_max) / 2
        
    feels_diff = abs(feels_like - current_temp)
    
    if feels_diff >= 3:
        diff_desc = "更热" if feels_like > current_temp else "更冷"
        return f"🔥 实际体感{feels_like:.0f}°C，比气温{diff_desc}！"
    return ""

def get_flu_index_desc(level, category):
    """根据感冒指数等级返回描述和emoji"""
    flu_emoji_map = {1: "🙏", 2: "🤧", 3: "🤒", 4: "🤢"}
    flu_desc_map = {1: "不易感冒", 2: "较易感冒", 3: "易感冒", 4: "极易感冒"}
    emoji = flu_emoji_map.get(level, "🤧")
    desc = flu_desc_map.get(level, category)
    return emoji, desc

def get_dressing_index_desc(level, category):
    """根据穿衣指数等级返回描述、emoji和具体建议 (V4.3 优化点)"""
    dressing_emoji_map = {1: "🩱", 2: "🩲", 3: "👕", 4: "👔", 5: "🧥", 6: "🧣", 7: "🧤", 8: "🧤"}
    emoji = dressing_emoji_map.get(level, "👕")
    
    detailed_advice_map = {
        1: "夏装短袖、背心即可，注意防晒。", 
        2: "薄短袖、裙子等，尽量穿透气衣物。",
        3: "T恤、衬衫、薄长裙等休闲夏装。",
        4: "衬衫、薄外套、休闲服、牛仔裤。",
        5: "风衣、夹克、薄毛衣，早晚加外套。",
        6: "毛衣、厚外套、西服套装、呢大衣。",
        7: "羽绒服、厚棉衣、冬大衣等保暖衣物。",
        8: "极寒天气，需穿厚羽绒服和保暖配饰（围巾、手套）。"
    }
    
    advice = detailed_advice_map.get(level, category)
    return emoji, category, advice

# --- 消息生成模块 ---
def create_summary_board(all_weather):
    """【V4.3 优化】创建简洁美观的天气看板"""
    board = []
    board.append(f"🌈 {random.choice(GREETINGS)} - {datetime.now().strftime('%Y年%m月%d日')}")
    board.append("════════════════════")
    
    for city in all_weather:
        icon = next((emoji for weather, emoji in WEATHER_ICONS.items() if weather in city['textDay']), "💡")
        
        weather_desc = city['textDay']
        temp_display = f"{city['tempMin']:.0f}~{city['tempMax']:.0f}°C"
        
        feels_like_alert_text = get_feels_like_alert(
            city.get('currentTemp'), city.get('feelsLike'), city['tempMin'], city['tempMax']
        )
        
        if city.get('currentTemp'):
            temp_display = f"{city['currentTemp']:.0f}°C ({city['tempMin']:.0f}-{city['tempMax']:.0f}°C)"
        
        city_line = f"{icon} **{city['name']}** · {weather_desc} · {temp_display}"
        board.append(city_line)
        
        alerts = []
        if feels_like_alert_text:
            alerts.append(feels_like_alert_text)
        
        # 降雨提醒（V4.3 优化）
        should_remind_rain, rain_detail = analyze_meaningful_rain(city.get('hourly_data', []))
        rain_pop = city.get('pop', 0)
        
        if should_remind_rain or rain_pop >= RAIN_POP_REMINDER:
            if rain_detail:
                alerts.append(f"💧 有雨带伞{rain_detail}")
            else:
                alerts.append(f"💧 降水概率{rain_pop}%，带伞")
        
        # 空气质量提醒
        if city.get('air_quality'):
            air_data = city['air_quality']
            aqi = air_data.get('aqi', 0)
            pm25 = air_data.get('pm25', 0)
            
            if pm25 > PM25_VERY_UNHEALTHY:
                alerts.append(f"⚠️ PM2.5({pm25})重度污染")
            elif aqi > AQI_MODERATE:
                alerts.append(f"😷 空气不太好(AQI{aqi})")
        
        # 紫外线提醒
        if city['uvIndex'] >= UV_STRONG and "雨" not in city['textDay']:
            alerts.append("🧴 紫外线强要防晒")
        
        # 温差提醒
        temp_diff = city['tempMax'] - city['tempMin']
        if temp_diff >= TEMP_DIFF_LARGE:
            alerts.append(f"🌡️ 温差{temp_diff:.0f}°C")
        
        # 预警提醒
        if city['warnings']:
            alerts.append("🚨 气象预警")
        
        # 感冒指数提醒
        flu_info = city.get('indices', {}).get('flu')
        if flu_info and flu_info['level'] >= 2:
            flu_emoji, flu_desc = get_flu_index_desc(flu_info['level'], flu_info['category'])
            alerts.append(f"{flu_emoji} {flu_desc}")
        
        # ----------------- 输出提醒信息 -----------------
        if alerts:
            alert_line = f"  * {' | '.join([re.sub(r'[🔥⚠️🚨]', '', a) for a in alerts])}"
            board.append(alert_line)
        else:
            humidity = city['humidity']
            if humidity < HUMIDITY_VERY_DRY:
                comfort_info = "💨 很干燥，多喝水"
            elif humidity <= HUMIDITY_COMFORTABLE_MAX:
                comfort_info = "😌 湿度舒适"
            else:
                comfort_info = "🌊 比较潮湿"
            
            board.append(f"  * {comfort_info}")
        
        board.append("")
    
    board.append("════════════════════")
    return "\n".join(board)
    
def create_comprehensive_advice(all_weather):
    """【V4.3 优化】生成增强版的综合建议"""
    advice_parts = []
    
    # --- 今日重点关注 ---
    focus_points = []
    for city in all_weather:
        if city['warnings']:
            for warning in city['warnings']:
                focus_points.append(f"🚨 「{city['name']}」{warning['type']}预警：{warning['title']}")
    
    if focus_points:
        advice_parts.append("🎯 **今日重点关注**")
        for point in focus_points[:4]:
            advice_parts.append(f"- {point}")
        advice_parts.append("")
    
    # --- 贴心小提示 ---
    tips_section = ["💡 **贴心小提示**"]
    
    # 穿衣情况 (V4.3 优化：具体服装建议)
    dressing_tips = []
    final_advice = None
    for city in all_weather:
        dressing_info = city.get('indices', {}).get('dressing')
        if dressing_info:
            emoji, category, advice = get_dressing_index_desc(dressing_info['level'], dressing_info['category'])
            dressing_tips.append(f"「{city['name']}」({category})")
            if final_advice is None:
                final_advice = advice
            
    if dressing_tips:
        tips_section.append(f"👚 **穿衣提醒：** 今天各地主要以{'、'.join([c.split('(')[1].replace(')','') for c in dressing_tips])}为主。")
        if final_advice:
            tips_section.append(f"  👉 建议穿着：{final_advice}")
    
    # 降雨提醒
    meaningful_rainy_cities = []
    for city in all_weather:
        should_remind, rain_time = analyze_meaningful_rain(city.get('hourly_data', []))
        rain_pop = city.get('pop', 0)
        
        if should_remind or rain_pop >= RAIN_POP_REMINDER:
            if rain_time:
                meaningful_rainy_cities.append(f"「{city['name']}」{rain_time}")
            else:
                meaningful_rainy_cities.append(f"「{city['name']}」(概率{rain_pop}%)")
    
    if meaningful_rainy_cities:
        tips_section.append(f"☔️ **降雨提醒：** {'、'.join(meaningful_rainy_cities)}，记得带伞哦！")
    
    # 体感温度警示
    feels_like_alerts = []
    for city in all_weather:
        alert = get_feels_like_alert(
            city.get('currentTemp'), city.get('feelsLike'), city['tempMin'], city['tempMax']
        )
        if alert:
            alert_text = alert.split(' ', 1)[1] 
            feels_like_alerts.append(f"「{city['name']}」{alert_text}")

    if feels_like_alerts:
        tips_section.append(f"🥵 **体感警示：** {'，'.join(feels_like_alerts)}")
    
    # 组合最终建议
    if len(tips_section) > 1:
        advice_parts.extend(tips_section)
    
    advice_parts.append("")
    advice_parts.append(f"最后，{random.choice(list(CLOSINGS.keys()))} {random.choice(list(CLOSINGS.values()))}")
    
    return "\n".join(advice_parts)

# --- 主程序执行模块 ---
def main():
    """主程序入口"""
    print("=======================================")
    print("🤖 爱意天气机器人 V4.3 启动")
    print("=======================================")
    
    # 检查配置是否已填充
    if WEBHOOK_URL.endswith("5a9b8214-4fae-4bbd-a147-9ecfe5eb6d90") and API_KEY == "8ddce2378f984da6b24e1310c226e500":
        print("⚠️ 警告：正在使用示例默认配置，请确认这些值是您想使用的！")

    all_weather = get_all_cities_weather_concurrent()
    
    if not all_weather:
        final_message = "❌ 抱歉，核心天气数据获取失败，今天的小报暂停发送。"
    else:
        summary_board = create_summary_board(all_weather)
        comprehensive_advice = create_comprehensive_advice(all_weather)
        final_message = f"{summary_board}\n{comprehensive_advice}"
        
    print("\n--- 最终消息内容 ---")
    print(final_message)
    print("------------------\n")
    
    send_message_to_webhook(final_message)
    
    print("\n=======================================")
    print("✅ 机器人运行结束")
    print("=======================================")


if __name__ == "__main__":
    main()
