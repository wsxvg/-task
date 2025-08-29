#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
爱意天气机器人 V4.1 - 最终纯净版 (严格纯文本)
"""

import requests
from datetime import datetime, date
import json
# 请确保已安装此库: pip install lunardate
from lunardate import LunarDate
import time
import random
# 请确保已安装此库: pip install pytz
import pytz
# 并发处理相关导入
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# --- 核心配置区 ---
WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=5a9b8214-4fae-4bbd-a147-9ecfe5eb6d90"
API_KEY = "8ddce2378f984da6b24e1310c226e500"
API_HOST = "p55pwcrrq3.re.qweatherapi.com"
CITIES = {
    "卧龙": {"lon": "120.27294", "lat": "30.61604"},
    "吴村": {"lon": "119.33204", "lat": "30.57856"},
    "常大": {"lon": "119.82476", "lat": "31.70889"},
    "苏科大": {"lon": "120.57377", "lat": "31.24879"}
}

# --- 常量定义 ---
# 温度阈值
TEMP_HOT = 30      # 高温阈值
TEMP_WARM = 28     # 温暖阈值
TEMP_COOL = 18     # 凉爽阈值
TEMP_COLD = 10     # 低温阈值
TEMP_VERY_HOT = 33 # 酷热阈值

# 其他阈值
UV_STRONG = 7      # 强紫外线阈值
TEMP_DIFF_LARGE = 12  # 大温差阈值
HUMIDITY_LOW = 40  # 低湿度阈值
HUMIDITY_VERY_DRY = 30   # 很干燥阈值
HUMIDITY_COMFORTABLE_MAX = 60  # 舒适湿度上限
HUMIDITY_SLIGHTLY_WET = 75     # 略微潮湿阈值
HUMIDITY_WET = 85      # 潮湿阈值

# 空气质量阈值 (AQI)
AQI_GOOD = 50          # 优秀
AQI_MODERATE = 100     # 良好  
AQI_UNHEALTHY_SENSITIVE = 150  # 轻度污染
AQI_UNHEALTHY = 200    # 中度污染
AQI_VERY_UNHEALTHY = 300  # 重度污染
# 300以上为严重污染

# PM2.5阈值 (μg/m³)
PM25_GOOD = 35         # 优秀
PM25_MODERATE = 75     # 良好
PM25_UNHEALTHY = 115   # 轻度污染
PM25_VERY_UNHEALTHY = 150  # 中度污染
# 150以上为重度污染

# 网络请求配置
API_TIMEOUT = 15   # API超时时间
API_RETRY_COUNT = 3  # API重试次数
MAX_WORKERS = 4    # 最大并发线程数

# 生活指数类型ID
INDICES_FLU = 9     # 感冒指数
INDICES_COMF = 8    # 舒适度指数
INDICES_DRSG = 3    # 穿衣指数

# --- 随机文案库 & 图标库 ---
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
            
            # 数据完整性验证
            if not isinstance(data, dict):
                raise ValueError(f"API返回数据格式错误: {type(data)}")
                
            if data.get("code") == "200":
                return data
            else:
                print(f"  > ⚠️ API返回错误 (attempt {attempt + 1}): {data.get('code')} for {endpoint}")
                if attempt == retry_count - 1:  # 最后一次尝试
                    return None
                    
        except requests.exceptions.Timeout:
            print(f"  > ⏱️ API请求超时 (attempt {attempt + 1}): {endpoint}")
        except requests.exceptions.RequestException as e:
            print(f"  > ❌ 网络请求错误 (attempt {attempt + 1}): {e} for {endpoint}")
        except ValueError as e:
            print(f"  > ❌ 数据格式错误 (attempt {attempt + 1}): {e}")
        except Exception as e:
            print(f"  > ❌ 未知错误 (attempt {attempt + 1}): {e}")
            
        # 重试前稍微等待
        if attempt < retry_count - 1:
            time.sleep(1 + attempt * 0.5)  # 递增等待时间
            
    return None

def get_api_data(endpoint, params):
    """保留原有接口兼容性"""
    return get_api_data_with_retry(endpoint, params)

def get_city_weather_from_api(city_name, coords):
    """单个城市天气数据获取（增强版）"""
    print(f"📍 开始获取「{city_name}」的天气数据...")
    base_params = {"location": f"{coords['lon']},{coords['lat']}", "lang": "zh"}
    
    # 并发获取多个API数据
    api_calls = [
        ("/v7/weather/7d", base_params),     # 七天预报
        ("/v7/weather/24h", base_params),    # 24小时预报
        ("/v7/weather/now", base_params),    # 实时天气
        ("/v7/air/now", base_params),        # 实时空气质量
        ("/v7/warning/now", base_params),     # 预警信息
        ("/v7/indices/1d", {**base_params, "type": f"{INDICES_FLU},{INDICES_COMF},{INDICES_DRSG}"})  # 生活指数
    ]
    
    results = {}
    with ThreadPoolExecutor(max_workers=6) as executor:  # 增加线程数到6
        # 提交所有API请求
        future_to_api = {
            executor.submit(get_api_data_with_retry, endpoint, params): endpoint 
            for endpoint, params in api_calls
        }
        
        # 收集结果
        for future in as_completed(future_to_api):
            endpoint = future_to_api[future]
            try:
                result = future.result()
                results[endpoint] = result
            except Exception as e:
                print(f"  > ⚠️ API调用异常 {endpoint}: {e}")
                results[endpoint] = None
    
    weather_7d = results.get("/v7/weather/7d")
    weather_24h = results.get("/v7/weather/24h")
    weather_now = results.get("/v7/weather/now")
    air_quality = results.get("/v7/air/now")
    warning_data = results.get("/v7/warning/now")
    indices_data = results.get("/v7/indices/1d")

    # 数据完整性验证
    if not weather_7d or not weather_7d.get('daily'):
        print(f"  > ❌ 「{city_name}」核心天气数据获取失败。")
        return None

    today_forecast = weather_7d['daily'][0]
    
    # 验证关键字段
    required_fields = ['tempMax', 'tempMin', 'textDay', 'textNight', 'humidity', 'uvIndex']
    for field in required_fields:
        if field not in today_forecast:
            print(f"  > ⚠️ 「{city_name}」缺少关键字段: {field}")
            return None
    
    # 天气描述优化：优先使用逐小时数据分析天气变化
    current_hour_temp = None
    weather_trend = None
    
    if weather_24h and weather_24h.get('hourly'):
        current_hour_temp, weather_trend = analyze_hourly_weather_changes(weather_24h['hourly'])
        print(f"  > 🕰️ 「{city_name}」逐小时分析: 当前{current_hour_temp}°C, 天气趋势: {weather_trend}")
    
    # 如果逐小时数据不可用，使用传统方法
    if not weather_trend:
        text_day = today_forecast['textDay']
        text_night = today_forecast['textNight']
        weather_trend = text_day if text_day == text_night else f"{text_day}转{text_night}"
    
    # 获取实时温度和体感温度（优先使用逐小时数据）
    current_temp = current_hour_temp  # 优先使用逐小时数据
    feels_like = None
    
    # 如果逐小时数据没有当前温度，尝试使用实时天气API
    if not current_temp and weather_now and weather_now.get('now'):
        current_temp = float(weather_now['now'].get('temp')) if weather_now['now'].get('temp') else None
        feels_like = float(weather_now['now'].get('feelsLike')) if weather_now['now'].get('feelsLike') else None
    
    # 获取风力信息
    wind_info = {}
    if 'windDirDay' in today_forecast and 'windScaleDay' in today_forecast:
        wind_info = {
            'windDir': today_forecast['windDirDay'],
            'windScale': today_forecast['windScaleDay'],
            'windSpeed': today_forecast.get('windSpeedDay', 'N/A')
        }
    
    # 获取降水信息
    precipitation = today_forecast.get('precip', '0.0')
    
    # 获取气压和能见度
    pressure = today_forecast.get('pressure', 'N/A')
    visibility = today_forecast.get('vis', 'N/A')
    
    # 获取空气质量信息
    air_data = None
    if air_quality and air_quality.get('now'):
        air_info = air_quality['now']
        air_data = {
            'aqi': int(air_info.get('aqi', 0)),
            'pm25': int(air_info.get('pm2p5', 0)),
            'pm10': int(air_info.get('pm10', 0)),
            'no2': int(air_info.get('no2', 0)),
            'so2': int(air_info.get('so2', 0)),
            'o3': int(air_info.get('o3', 0)),
            'co': float(air_info.get('co', 0)),
            'category': air_info.get('category', '未知'),
            'primary': air_info.get('primary', '')
        }

    
    # 获取生活指数信息
    indices_info = {}
    if indices_data and indices_data.get('daily'):
        for index in indices_data['daily']:
            index_type = int(index.get('type', 0))
            if index_type == INDICES_FLU:  # 感冒指数
                indices_info['flu'] = {
                    'level': int(index.get('level', 0)),
                    'category': index.get('category', ''),
                    'text': index.get('text', '')
                }
            elif index_type == INDICES_COMF:  # 舒适度指数
                indices_info['comfort'] = {
                    'level': int(index.get('level', 0)),
                    'category': index.get('category', ''),
                    'text': index.get('text', '')
                }
            elif index_type == INDICES_DRSG:  # 穿衣指数
                indices_info['dressing'] = {
                    'level': int(index.get('level', 0)),
                    'category': index.get('category', ''),
                    'text': index.get('text', '')
                }
        print(f"  > 📊 「{city_name}」生活指数获取成功")
    else:
        print(f"  > ⚠️ 「{city_name}」生活指数获取失败")
    
    print(f"  > ✅ 「{city_name}」天气数据获取成功")
    
    return {
        "name": city_name,
        "tempMax": float(today_forecast['tempMax']),
        "tempMin": float(today_forecast['tempMin']),
        "currentTemp": float(current_temp) if current_temp else None,
        "feelsLike": float(feels_like) if feels_like else None,  # 新增体感温度
        "textDay": weather_trend,  # 使用优化后的天气趋势分析
        "humidity": int(today_forecast['humidity']),
        "uvIndex": int(today_forecast['uvIndex']),
        "windInfo": wind_info,
        "precipitation": float(precipitation) if precipitation != 'N/A' else 0.0,
        "pressure": pressure,
        "visibility": visibility,
        "warnings": warning_data.get('warning', []) if warning_data else [],
        "hourly_data": weather_24h.get('hourly', []) if weather_24h else [],
        "air_quality": air_data,  # 空气质量数据
        "indices": indices_info,  # 新增生活指数数据
    }

def get_all_cities_weather_concurrent():
    """并发获取所有城市的天气数据"""
    print(f"🚀 开始并发获取 {len(CITIES)} 个城市的天气数据...")
    start_time = time.time()
    
    all_weather_data = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 提交所有城市的天气获取任务
        future_to_city = {
            executor.submit(get_city_weather_from_api, city_name, coords): city_name 
            for city_name, coords in CITIES.items()
        }
        
        # 收集结果
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

# --- 空气质量模块 ---
def get_aqi_level(aqi_value):
    """根据AQI值返回空气质量等级和颜色"""
    if aqi_value <= AQI_GOOD:
        return "优秀", "😊", "#00e400"
    elif aqi_value <= AQI_MODERATE:
        return "良好", "🙂", "#ffff00"
    elif aqi_value <= AQI_UNHEALTHY_SENSITIVE:
        return "轻度污染", "😐", "#ff7e00"
    elif aqi_value <= AQI_UNHEALTHY:
        return "中度污染", "😷", "#ff0000"
    elif aqi_value <= AQI_VERY_UNHEALTHY:
        return "重度污染", "🤢", "#8f3f97"
    else:
        return "严重污染", "☠️", "#7e0023"

def get_pm25_level(pm25_value):
    """根据PM2.5值返回空气质量描述"""
    if pm25_value <= PM25_GOOD:
        return "优秀", "🍃"
    elif pm25_value <= PM25_MODERATE:
        return "良好", "🌿"
    elif pm25_value <= PM25_UNHEALTHY:
        return "轻度污染", "😷"
    elif pm25_value <= PM25_VERY_UNHEALTHY:
        return "中度污染", "😭"
    else:
        return "重度污染", "☠️"

def generate_air_quality_advice(city_name, air_data):
    """根据空气质量数据生成个性化建议"""
    if not air_data:
        return ""
    
    aqi = air_data.get('aqi', 0)
    pm25 = air_data.get('pm25', 0)
    pm10 = air_data.get('pm10', 0)
    no2 = air_data.get('no2', 0)
    so2 = air_data.get('so2', 0)
    o3 = air_data.get('o3', 0)
    co = air_data.get('co', 0)
    
    aqi_level, aqi_emoji, _ = get_aqi_level(aqi)
    pm25_level, pm25_emoji = get_pm25_level(pm25)
    
    advice_parts = []
    
    # 基本状况描述
    advice_parts.append(f"🌭 {city_name}空气质量：AQI {aqi} {aqi_level}{aqi_emoji}")
    
    # 主要污染物提示
    pollutant_details = []
    if pm25 > PM25_GOOD:
        pollutant_details.append(f"PM2.5: {pm25}μg/m³ {pm25_emoji}")
    if pm10 > 50:  # PM10的一般阈值
        pollutant_details.append(f"PM10: {pm10}μg/m³")
    if no2 > 40:   # NO2的一般阈值
        pollutant_details.append(f"NO2: {no2}μg/m³")
    
    if pollutant_details:
        advice_parts.append(f"   主要污染物：{' | '.join(pollutant_details)}")
    
    # 健康建议
    health_advice = []
    if aqi <= AQI_GOOD:
        health_advice.append("空气质量优秀，适宜各类户外活动")
    elif aqi <= AQI_MODERATE:
        health_advice.append("空气质量良好，正常户外活动")
    elif aqi <= AQI_UNHEALTHY_SENSITIVE:
        health_advice.append("敏感人群应减少户外活动")
        if pm25 > PM25_MODERATE:
            health_advice.append("建议佩戴N95口罩")
    elif aqi <= AQI_UNHEALTHY:
        health_advice.append("建议减少户外活动，佩戴口罩")
        health_advice.append("儿童和老人应停止户外运动")
    elif aqi <= AQI_VERY_UNHEALTHY:
        health_advice.append("避免户外活动，紧闭门窗")
        health_advice.append("必须佩戴N95或更高级别口罩")
    else:
        health_advice.append("严重污染！停止所有户外活动")
        health_advice.append("紧闭门窗，使用空气净化器")
    
    if health_advice:
        advice_parts.append(f"   健康建议：{'，'.join(health_advice)}")
    
    # 特殊情况提醒
    special_tips = []
    if pm25 > PM25_VERY_UNHEALTHY:
        special_tips.append("高PM2.5，注意心血管健康")
    if o3 > 160:  # 臭氧高浓度阈值
        special_tips.append("臭氧浓度高，避免午后户外活动")
    if no2 > 80:  # 二氧化氮高浓度
        special_tips.append("二氧化氮浓度高，呼吸系统敏感者小心")
    
    if special_tips:
        advice_parts.append(f"   特别提醒：{'，'.join(special_tips)}")
    
    return "\n".join(advice_parts)

def get_city_air_quality_focus(city_name, air_data):
    """根据城市空气质量生成关注点（通俗版）"""
    if not air_data:
        return []
    
    focus_points = []
    aqi = air_data.get('aqi', 0)
    pm25 = air_data.get('pm25', 0)
    
    if aqi > AQI_MODERATE:
        if aqi <= AQI_UNHEALTHY_SENSITIVE:  # 100-150
            focus_points.append(f"😷 {city_name}空气不太好，敏感人群少外出")
        elif aqi <= AQI_UNHEALTHY:  # 150-200
            focus_points.append(f"😷 {city_name}空气较差，建议戴口罩")
        else:  # 200以上
            focus_points.append(f"😷 {city_name}空气很差，尽量少外出")
    
    if pm25 > PM25_VERY_UNHEALTHY:
        focus_points.append(f"⚠️ {city_name}空气重度污染，必须戴N95口罩")
    
    return focus_points

# --- 生活指数模块 ---
def get_flu_index_desc(level, category):
    """根据感冒指数等级返回描述和emoji"""
    flu_emoji_map = {1: "🙏", 2: "🤧", 3: "🤒", 4: "🤢"}
    flu_desc_map = {
        1: "不易感冒",
        2: "较易感冒", 
        3: "易感冒",
        4: "极易感冒"
    }
    emoji = flu_emoji_map.get(level, "🤧")
    desc = flu_desc_map.get(level, category)
    return emoji, desc

def get_comfort_index_desc(level, category):
    """根据舒适度指数等级返回描述和emoji"""
    comfort_emoji_map = {1: "😌", 2: "🙂", 3: "😐", 4: "😣", 5: "😫", 6: "😰", 7: "😵"}
    emoji = comfort_emoji_map.get(level, "🙂")
    return emoji, category

def get_dressing_index_desc(level, category):
    """根据穿衣指数等级返回描述和emoji"""
    dressing_emoji_map = {1: "🧥", 2: "🧥", 3: "🧺", 4: "👕", 5: "👔", 6: "👕", 7: "🩲"}
    emoji = dressing_emoji_map.get(level, "👕")
    return emoji, category

def get_health_indices_tips(city_weather):
    """生成健康指数相关的提醒"""
    tips = []
    city_name = city_weather['name']
    indices = city_weather.get('indices', {})
    
    # 感冒指数提醒
    flu_info = indices.get('flu')
    if flu_info and flu_info['level'] >= 3:  # 易感冒或极易感冒
        flu_emoji, flu_desc = get_flu_index_desc(flu_info['level'], flu_info['category'])
        tips.append(f"{flu_emoji} 「{city_name}」{flu_desc}，注意保暖")
    
    # 舒适度指数提醒
    comfort_info = indices.get('comfort')
    if comfort_info and comfort_info['level'] >= 4:  # 很不舒适及以上
        comfort_emoji, comfort_desc = get_comfort_index_desc(comfort_info['level'], comfort_info['category'])
        tips.append(f"{comfort_emoji} 「{city_name}」体感{comfort_desc}")
    
    return tips

# --- 美学与洞察模块 ---
def get_temp_emoji(temp):
    """根据温度返回对应的emoji"""
    if temp >= TEMP_VERY_HOT: return "🥵"
    elif temp >= TEMP_WARM: return "😊"
    elif temp >= TEMP_COOL: return "😌"
    else: return "🥶"

def analyze_hourly_weather_changes(hourly_data):
    """分析逐小时天气变化趋势"""
    if not hourly_data:
        return None, None
    
    from datetime import datetime
    current_time = datetime.now()
    
    # 找到当前时间最近的小时数据
    current_hour_temp = None
    current_hour_weather = None
    
    for hour_data in hourly_data:
        hour_time_str = hour_data['fxTime']
        # 解析时间格式: "2021-02-16T15:00+08:00"
        try:
            hour_time = datetime.fromisoformat(hour_time_str.replace('+08:00', ''))
            if abs((hour_time - current_time).total_seconds()) <= 3600:  # 1小时内
                current_hour_temp = float(hour_data['temp'])
                current_hour_weather = hour_data['text']
                break
        except:
            continue
    
    # 如果没找到当前小时，使用第一个数据
    if current_hour_temp is None and hourly_data:
        current_hour_temp = float(hourly_data[0]['temp'])
        current_hour_weather = hourly_data[0]['text']
    
    # 分析天气变化趋势
    weather_changes = []
    prev_weather = None
    
    for hour_data in hourly_data[:12]:  # 只分析前12小时
        current_weather = hour_data['text']
        if prev_weather and prev_weather != current_weather:
            weather_changes.append(current_weather)
        elif prev_weather is None:
            weather_changes.append(current_weather)
        prev_weather = current_weather
    
    # 生成天气变化描述
    if len(weather_changes) > 1:
        weather_trend = '转'.join(weather_changes)
    else:
        weather_trend = weather_changes[0] if weather_changes else '未知'
    
    return current_hour_temp, weather_trend

def analyze_meaningful_rain(hourly_data):
    """智能分析有意义的降雨时段（只提醒白天和接下来的降雨）"""
    if not hourly_data:
        return False, ""
    
    from datetime import datetime
    current_time = datetime.now()
    current_hour = current_time.hour
    
    meaningful_rain_periods = []
    next_12h_rain = False
    daytime_rain = False
    
    for i, hour_data in enumerate(hourly_data[:12]):  # 只分析未来12小时
        if "雨" in hour_data['text']:
            try:
                # 解析时间
                hour_time_str = hour_data['fxTime']
                hour = int(hour_time_str[11:13])
                
                # 只关注白天时段（6:00-20:00）或接下来几小时的降雨
                if 6 <= hour <= 20:  # 白天时段
                    daytime_rain = True
                    if i <= 6:  # 接下来6小时内
                        next_12h_rain = True
                        
                        if 6 <= hour < 12:
                            time_desc = f"上午{hour}点"
                        elif 12 <= hour < 18:
                            time_desc = f"下午{hour-12 if hour>12 else 12}点"
                        else:  # 18-20
                            time_desc = f"晚上{hour-12}点"
                        
                        # 检查降雨强度
                        rain_intensity = ""
                        if "大雨" in hour_data['text'] or "暴雨" in hour_data['text']:
                            rain_intensity = "大雨"
                        elif "中雨" in hour_data['text']:
                            rain_intensity = "中雨"
                        elif "小雨" in hour_data['text']:
                            rain_intensity = "小雨"
                        else:
                            rain_intensity = "雨"
                        
                        meaningful_rain_periods.append({
                            'time': time_desc,
                            'intensity': rain_intensity,
                            'hour': hour
                        })
                elif i <= 3:  # 即使是晚上，如果是接下来几小时内也要提醒
                    next_12h_rain = True
                    
            except Exception as e:
                print(f"  > ⚠️ 解析降雨时间失败: {e}")
                continue
    
    # 生成提醒信息
    if meaningful_rain_periods:
        if len(meaningful_rain_periods) == 1:
            period = meaningful_rain_periods[0]
            rain_time = f"（预计{period['time']}左右有{period['intensity']}）"
        else:
            # 多个时段，只显示最近的
            earliest = min(meaningful_rain_periods, key=lambda x: x['hour'])
            rain_time = f"（预计{earliest['time']}左右开始）"
    elif next_12h_rain:
        rain_time = "（今日有雨）"
    else:
        rain_time = ""
    
    # 只有白天有雨或接下来几小时有雨才返回true
    should_remind = daytime_rain or next_12h_rain
    return should_remind, rain_time

def get_first_rain_time_precise(hourly_data):
    """从逐小时数据中精确获取首次下雨时间"""
    if not hourly_data:
        return ""
    
    for hour_data in hourly_data:
        if "雨" in hour_data['text']:
            try:
                # 解析时间: "2021-02-16T15:00+08:00"
                time_str = hour_data['fxTime']
                hour = int(time_str[11:13])
                
                if 5 <= hour < 12:
                    time_desc = f"上午{hour}点"
                elif 12 <= hour < 18:
                    time_desc = f"下午{hour-12 if hour>12 else 12}点"
                elif 18 <= hour < 24:
                    time_desc = f"晚上{hour-12}点"
                else:
                    time_desc = "凌晨"
                
                return f"（预计从{time_desc}左右开始）"
            except:
                continue
    return ""

def get_first_rain_time(hourly_data):
    for hour_data in hourly_data:
        if "雨" in hour_data['text']:
            hour = int(hour_data['fxTime'][11:13])
            if 5 <= hour < 12: time_desc = f"上午{hour}点"
            elif 12 <= hour < 18: time_desc = f"下午{hour-12 if hour>12 else 12}点"
            elif 18 <= hour < 24: time_desc = f"晚上{hour-12}点"
            else: time_desc = "凌晨"
            return f"（预计从{time_desc}左右开始）"
    return ""

def create_summary_board(all_weather):
    """【V4.3 微信适配版】创建简洁美观的天气看板"""
    board = []
    board.append("🌈 今日天气一览")
    board.append("")
    
    for i, city in enumerate(all_weather):
        # 获取天气图标
        icon = next((emoji for weather, emoji in WEATHER_ICONS.items() if weather in city['textDay']), "💡")
        
        # 主要信息行
        weather_desc = city['textDay']
        temp_display = f"{city['tempMin']:.0f}~{city['tempMax']:.0f}°C"
        
        # 如果有实时温度，优先显示
        if city.get('currentTemp'):
            temp_display = f"{city['currentTemp']:.0f}°C ({city['tempMin']:.0f}-{city['tempMax']:.0f}°C)"
        
        # 如果有体感温度且与实际温度差异较大，显示体感温度
        feels_like_info = ""
        if city.get('feelsLike'):
            if city.get('currentTemp'):
                feels_diff = abs(float(city['feelsLike']) - float(city['currentTemp']))
                if feels_diff >= 3:  # 提高阈值到3度，减少过度敏感
                    feels_like_info = f"❕ 体感{city['feelsLike']:.0f}°C"
            else:
                # 即使没有实时温度，也可以显示体感温度
                temp_avg = (city['tempMin'] + city['tempMax']) / 2
                feels_diff = abs(float(city['feelsLike']) - temp_avg)
                if feels_diff >= 3:  # 提高阈值到3度
                    feels_like_info = f"❕ 体感{city['feelsLike']:.0f}°C"
        
        city_line = f"{icon} {city['name']} · {weather_desc} · {temp_display}{feels_like_info}"
        board.append(city_line)
        
        # 重要提醒（显示所有关键信息）
        alerts = []
        
        # 降雨提醒（智能分析）
        should_remind_rain, rain_detail = analyze_meaningful_rain(city.get('hourly_data', []))
        if should_remind_rain:
            if city.get('precipitation', 0) > 0 and rain_detail:
                alerts.append(f"💧 {city['precipitation']:.1f}mm降雨{rain_detail}")
            elif rain_detail:
                alerts.append(f"💧 有雨记得带伞{rain_detail}")
            else:
                alerts.append("💧 有雨记得带伞")
        
        # 空气质量提醒（用通俗语言）- 提高优先级
        if city.get('air_quality'):
            air_data = city['air_quality']
            aqi = air_data.get('aqi', 0)
            pm25 = air_data.get('pm25', 0)
            
            # PM2.5重度污染优先提醒
            if pm25 > PM25_VERY_UNHEALTHY:
                alerts.append(f"⚠️ PM2.5({pm25})重度污染，戴N95")
            elif aqi > AQI_MODERATE:  # 大于100才提醒污染
                if aqi <= AQI_UNHEALTHY_SENSITIVE:  # 100-150
                    alerts.append(f"😷 空气不太好(AQI{aqi})")
                elif aqi <= AQI_UNHEALTHY:  # 150-200
                    alerts.append(f"😷 空气较差(AQI{aqi})，戴口罩")
                else:  # 200以上
                    alerts.append(f"😷 空气很差(AQI{aqi})，少外出")
            elif aqi <= AQI_GOOD:  # 当空气质量优秀时，显示积极信息
                alerts.append(f"🌿 空气清新(AQI{aqi})")
        
        # 紫外线提醒
        if city['uvIndex'] >= UV_STRONG and "雨" not in city['textDay']:
            alerts.append("🧴 紫外线强要防晒")
        
        # 温差提醒
        temp_diff = city['tempMax'] - city['tempMin']
        if temp_diff >= TEMP_DIFF_LARGE:
            alerts.append(f"🌡️ 温差{temp_diff:.0f}°C注意增减衣")
        
        # 预警提醒
        if city['warnings']:
            alerts.append("⚠️ 有气象预警")
        
        # 风力提醒（安全优先）
        wind_info = city.get('windInfo', {})
        if wind_info.get('windScale'):
            wind_scale = wind_info['windScale']
            import re
            wind_nums = re.findall(r'\d+', wind_scale)
            if wind_nums:
                max_wind = max(int(num) for num in wind_nums)
                if max_wind >= 8:  # 8级及以上大风，严重安全隐患
                    alerts.append(f"⚠️ {wind_scale}大风，减少外出！")
                elif max_wind >= 6:  # 6-7级强风
                    alerts.append(f"🌪️ {wind_scale}强风，注意安全")
                elif max_wind >= 4:  # 4-5级风
                    alerts.append(f"🌬️ {wind_scale}风力")
        
        # 健康指数提醒（感冒指数、舒适度指数）
        indices = city.get('indices', {})
        
        # 感冒指数提醒 - 只在有风险时提醒
        flu_info = indices.get('flu')
        if flu_info and flu_info['level'] >= 2:  # 较易感冒及以上才提醒
            flu_emoji, flu_desc = get_flu_index_desc(flu_info['level'], flu_info['category'])
            alerts.append(f"{flu_emoji} {flu_desc}")
        
        # 舒适度指数提醒（简化逻辑，只在提醒很少时显示）
        comfort_info = indices.get('comfort')
        if comfort_info and len(alerts) <= 1:  # 只在提醒很少时显示舒适度
            comfort_emoji, comfort_desc = get_comfort_index_desc(comfort_info['level'], comfort_info['category'])
            if comfort_info['level'] == 1:  # 舒适
                alerts.append(f"{comfort_emoji} 体感舒适")
            elif comfort_info['level'] >= 5:  # 非常不舒适及以上才显示
                alerts.append(f"{comfort_emoji} 体感{comfort_desc}")
        
        # 显示提醒信息
        if alerts:
            # 根据提醒数量智能显示
            if len(alerts) <= 3:
                alert_line = f"   {' | '.join(alerts)}"
                board.append(alert_line)
            elif len(alerts) == 4:
                # 4个提醒：前2个 | 后2个
                first_line = f"   {' | '.join(alerts[:2])}"
                second_line = f"   {' | '.join(alerts[2:])}"
                board.append(first_line)
                board.append(second_line)
            else:
                # 5个及以上提醒：前3个 | 后面的
                first_line = f"   {' | '.join(alerts[:3])}"
                second_line = f"   {' | '.join(alerts[3:])}"
                board.append(first_line)
                board.append(second_line)
        else:
            # 如果没有特别提醒，显示舒适度信息
            humidity = city['humidity']
            if humidity < HUMIDITY_VERY_DRY:
                comfort_info = "💨 很干燥，多喝水"
            elif humidity < HUMIDITY_LOW:
                comfort_info = "🌵 有点干燥"
            elif humidity <= HUMIDITY_COMFORTABLE_MAX:
                comfort_info = "😌 湿度舒适"
            elif humidity <= HUMIDITY_SLIGHTLY_WET:
                comfort_info = "💧 略微潮湿"
            elif humidity <= HUMIDITY_WET:
                comfort_info = "🌊 比较潮湿"
            else:
                comfort_info = "🌧️ 很潮湿，注意除湿"
            
            board.append(f"   {comfort_info}")
        
        # 如果不是最后一个城市，添加空行分隔
        if i < len(all_weather) - 1:
            board.append("")
    
    board.append("")
    return "\n".join(board)
    
def create_comprehensive_advice(all_weather):
    """【V4.1 升级】生成增强版的综合建议"""
    advice_parts = []
    
    # 生成今日重点关注
    focus_points = []
    for city in all_weather:
        # 空气质量关注点
        if city.get('air_quality'):
            air_focus = get_city_air_quality_focus(city['name'], city['air_quality'])
            focus_points.extend(air_focus)
    
    if focus_points:
        advice_parts.append("🎯 ════ 今日重点关注 ════")
        for point in focus_points[:4]:  # 最多显示4个重点
            advice_parts.append(point)
        advice_parts.append("")  # 空行分隔
    
    # 生成贴心小提示
    tips_section = ["💡 ════ 贴心小提示 ════"]
    
    # 通用提示 - 降雨情况（使用智能分析，只提醒有意义的降雨）
    meaningful_rainy_cities = []
    for city in all_weather:
        should_remind, rain_time = analyze_meaningful_rain(city.get('hourly_data', []))
        if should_remind:
            if rain_time:
                meaningful_rainy_cities.append(f"「{city['name']}」{rain_time}")
            else:
                meaningful_rainy_cities.append(f"「{city['name']}」")
    
    if meaningful_rainy_cities:
        umbrella_emoji = "⛈️" if any("雷" in c['textDay'] for c in all_weather) else "☔️"
        tips_section.append(f"{umbrella_emoji} 降雨提醒：{'、'.join(meaningful_rainy_cities)} 记得带伞哦！")
    
    # 通用提示 - 饮水健康提醒
    hydration_cities = []
    for city in all_weather:
        current_temp = city.get('currentTemp', city['tempMax'])
        humidity = city['humidity']
        
        # 高温或低湿度需要多喝水
        if current_temp >= 30:  # 高温
            hydration_cities.append(f"「{city['name']}」高温{current_temp:.0f}°C")
        elif humidity <= 35:  # 低湿度干燥
            hydration_cities.append(f"「{city['name']}」空气干燥")
    
    if hydration_cities:
        tips_section.append(f"🥤 饮水提醒：{'、'.join(hydration_cities)}，记得多喝水补充水分")
    
    # 通用提示 - 防晒情况
    strong_uv_cities = [c['name'] for c in all_weather if c['uvIndex'] >= UV_STRONG and "雨" not in c['textDay']]
    if strong_uv_cities:
        tips_section.append(f"🧴 防晒提醒：「{'、'.join(strong_uv_cities)}」紫外线强，做好防护")
    
    # 通用提示 - 风力情况（安全优先）
    windy_cities = []
    strong_wind_cities = []
    dangerous_wind_cities = []
    
    for city in all_weather:
        wind_info = city.get('windInfo', {})
        if wind_info.get('windScale'):
            wind_scale = wind_info['windScale']
            # 提取数字，判断风力等级
            import re
            wind_nums = re.findall(r'\d+', wind_scale)
            if wind_nums:
                max_wind = max(int(num) for num in wind_nums)
                wind_dir = wind_info.get('windDir', '风')
                
                if max_wind >= 8:  # 8级及以上大风
                    dangerous_wind_cities.append(f"「{city['name']}」{wind_dir}{wind_scale}")
                elif max_wind >= 6:  # 6-7级强风
                    strong_wind_cities.append(f"「{city['name']}」{wind_dir}{wind_scale}")
                elif max_wind >= 4:  # 4-5级风
                    windy_cities.append(f"「{city['name']}」{wind_dir}{wind_scale}")
    
    # 按严重程度显示风力提醒
    if dangerous_wind_cities:
        tips_section.append(f"⚠️ 大风警告：{'、'.join(dangerous_wind_cities)}，建议减少外出，避免高空作业")
    elif strong_wind_cities:
        tips_section.append(f"🌪️ 强风提醒：{'、'.join(strong_wind_cities)}，注意风大保暖，小心高空坠物")
    elif windy_cities:
        tips_section.append(f"🌬️ 风力提醒：{'、'.join(windy_cities)}，注意风大保暖")
    
    # 通用提示 - 温差情况
    temp_diff_cities = []
    for city in all_weather:
        temp_diff = city['tempMax'] - city['tempMin']
        if temp_diff >= TEMP_DIFF_LARGE:
            temp_diff_cities.append(f"「{city['name']}」温差{temp_diff:.0f}°C")
    
    if temp_diff_cities:
        tips_section.append(f"🌡️ 温差提醒：{'、'.join(temp_diff_cities)}，注意及时增减衣物")
    
    # 通用提示 - 空气质量情况
    polluted_cities = []
    for city in all_weather:
        if city.get('air_quality'):
            air_data = city['air_quality']
            aqi = air_data.get('aqi', 0)
            if aqi > AQI_MODERATE:
                if aqi <= AQI_UNHEALTHY_SENSITIVE:  # 100-150
                    polluted_cities.append(f"「{city['name']}」空气不太好")
                elif aqi <= AQI_UNHEALTHY:  # 150-200
                    polluted_cities.append(f"「{city['name']}」空气较差")
                else:  # 200以上
                    polluted_cities.append(f"「{city['name']}」空气很差")
    
    if polluted_cities:
        tips_section.append(f"😷 空气质量提醒：{'、'.join(polluted_cities)}，建议戴口罩出行")
    
    # 特殊空气质量提示
    severe_pollution_cities = []
    for city in all_weather:
        if city.get('air_quality'):
            air_data = city['air_quality']
            pm25 = air_data.get('pm25', 0)
            if pm25 > PM25_VERY_UNHEALTHY:
                severe_pollution_cities.append(f"「{city['name']}」")
    
    if severe_pollution_cities:
        tips_section.append(f"⚠️ 重度污染警告：{'、'.join(severe_pollution_cities)}空气很差，必须戴N95口罩，避免户外活动")
    
    # 健康指数提醒（感冒指数、舒适度指数、穿衣指数）
    flu_risk_cities = []
    comfort_issue_cities = []
    dressing_tips = []
    
    for city in all_weather:
        indices = city.get('indices', {})
        print(f"  > 🔍 检查「{city['name']}」生活指数: {indices}")
        
        # 感冒指数检查 - 降低阈值，显示更多感冒提醒
        flu_info = indices.get('flu')
        if flu_info:
            print(f"    - 感冒指数: level={flu_info['level']}, category={flu_info['category']}")
            if flu_info['level'] >= 2:  # 从3降低到2，较易感冒也显示
                flu_emoji, flu_desc = get_flu_index_desc(flu_info['level'], flu_info['category'])
                flu_risk_cities.append(f"「{city['name']}」{flu_desc}")
        else:
            print(f"    - 「{city['name']}」未获取到感冒指数数据")
        
        # 舒适度指数检查
        comfort_info = indices.get('comfort')
        if comfort_info:
            print(f"    - 舒适度指数: level={comfort_info['level']}, category={comfort_info['category']}")
            if comfort_info['level'] >= 4:  # 很不舒适及以上
                comfort_emoji, comfort_desc = get_comfort_index_desc(comfort_info['level'], comfort_info['category'])
                # 添加具体原因说明
                reason = ""
                comfort_text = comfort_info.get('text', '')
                if "炎热" in comfort_text or "酷热" in comfort_text:
                    reason = "(天气炎热)"
                elif "寒冷" in comfort_text or "严寒" in comfort_text:
                    reason = "(天气寒冷)"
                elif "潮湿" in comfort_text:
                    reason = "(湿度过高)"
                elif "干燥" in comfort_text:
                    reason = "(空气干燥)"
                elif "风大" in comfort_text:
                    reason = "(风力较大)"
                comfort_issue_cities.append(f"「{city['name']}」{reason}")
        
        # 穿衣指数检查（扩大覆盖范围）
        dressing_info = indices.get('dressing')
        if dressing_info:
            print(f"    - 穿衣指数: level={dressing_info['level']}, category={dressing_info['category']}")
            level = dressing_info['level']
            category = dressing_info['category']
            dressing_text = dressing_info.get('text', '')
            
            # 根据穿衣指数等级和描述生成建议（扩大覆盖范围）
            if level >= 6:  # 炎热（6-7级）
                if "短衫" in dressing_text or "短裤" in dressing_text or "清凉" in dressing_text:
                    dressing_tips.append(f"「{city['name']}」建议清凉夏装")
            elif level <= 2:  # 寒冷（1-2级）
                if "棉衣" in dressing_text or "羽绒" in dressing_text or "厚重" in dressing_text:
                    dressing_tips.append(f"「{city['name']}」建议厚重保暖衣物")
            elif level == 3:  # 较冷（3级）
                if "外套" in dressing_text or "保暖" in dressing_text:
                    dressing_tips.append(f"「{city['name']}」建议加外套保暖")
            elif level == 4:  # 较舒适（4级）
                if "薄外套" in dressing_text or "长袜" in dressing_text:
                    dressing_tips.append(f"「{city['name']}」建议薄外套或长袜")
            elif level == 5:  # 适中（5级）
                if "薄" in dressing_text and ("裤" in dressing_text or "衫" in dressing_text):
                    dressing_tips.append(f"「{city['name']}」建议薄长袖衣物")
    
    if flu_risk_cities:
        tips_section.append(f"🤧 感冒风险提醒：{'、'.join(flu_risk_cities)}，注意保暖和休息")
        print(f"  > ✅ 添加感冒提醒: {flu_risk_cities}")
    else:
        print(f"  > ❌ 没有感冒风险城市需要提醒")
    
    if comfort_issue_cities:
        tips_section.append(f"😣 舒适度提醒：{'、'.join(comfort_issue_cities)}体感不舒适，建议适当调节活动")
    
    # 添加穿衣建议
    if dressing_tips:
        tips_section.append(f"👕 穿衣建议：{'、'.join(dressing_tips)}")
    
    advice_parts.extend(tips_section)
    
    # 添加详细的空气质量建议（如果有污染）
    air_advice_section = []
    for city in all_weather:
        if city.get('air_quality'):
            air_data = city['air_quality']
            aqi = air_data.get('aqi', 0)
            if aqi > AQI_GOOD:  # 只有当空气质量不是优秀时才显示详细建议
                air_advice = generate_air_quality_advice(city['name'], air_data)
                if air_advice:
                    air_advice_section.append(air_advice)
    
    if air_advice_section:
        advice_parts.append("")  # 空行分隔
        advice_parts.append("🌭 ════ 空气质量健康建议 ════")
        advice_parts.extend(air_advice_section)
    
    return "\n".join(advice_parts)

# --- 辅助与核心函数 ---
def get_lunar_date():
    try:
        lunar = LunarDate.fromSolarDate(date.today().year, date.today().month, date.today().day)
        lunar_months = ["正月", "二月", "三月", "四月", "五月", "六月", "七月", "八月", "九月", "十月", "冬月", "腊月"]
        lunar_days = ["初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十", "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十", "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十"]
        return f"农历 {lunar_months[lunar.month - 1]}{lunar_days[lunar.day - 1]}"
    except Exception as e:
        print(f"  > ⚠️ 农历计算失败: {e}"); return "农历 吉月吉日"
def calculate_days_together(): return (date.today() - date(2023, 8, 2)).days
def calculate_birthday_countdown(month, day):
    try:
        today, year = date.today(), date.today().year
        bday_solar = LunarDate(year, month, day).toSolarDate()
        bday = date(bday_solar.year, bday_solar.month, bday_solar.day)
        if bday <= today: bday = date(LunarDate(year + 1, month, day).toSolarDate().year, LunarDate(year + 1, month, day).toSolarDate().month, LunarDate(year + 1, month, day).toSolarDate().day)
        return (bday - today).days
    except: return 999

# --- 主流程 ---
def format_weather_message(all_weather):
    """【V4.1 升级】格式化天气消息 - 新的结构化布局"""
    now = datetime.now()
    
    # 消息头部
    content = "🌈 ════════════════════════════\n"
    content += f"   💌 {random.choice(GREETINGS)} 💌\n"
    content += "🌈 ════════════════════════════\n\n"
    content += f"📅 {now.strftime('%Y年%m月%d日')} {['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][now.weekday()]} | {get_lunar_date()}\n\n"
    
    # 城市天气总览
    content += f"{create_summary_board(all_weather)}\n\n"
    
    # 今日建议与提示
    content += f"{create_comprehensive_advice(all_weather)}\n\n"
    
    # 甜蜜时光轴
    content += "💕 ════ 甜蜜时光轴 ════\n"
    
    days_together = calculate_days_together()
    if days_together % 365 == 0 and days_together > 0:
        years = days_together // 365
        content += f"🎉 周年快乐，我的爱人！今天是我们相爱的第 {years} 年！\n💖 又一年春华秋实，感恩有你。\n"
    else:
        content += f"❤️ 我们已经相爱 {days_together} 天啦！\n"
        
    gf_bday_countdown = calculate_birthday_countdown(9, 11)
    if gf_bday_countdown == 0: content += "🎂 生日快乐，我最最最爱的宝贝！！！\n"
    elif gf_bday_countdown <= 7: content += f"🥳 生日周倒计时！距离宝贝生日只有 {gf_bday_countdown} 天！💖\n"
    elif gf_bday_countdown <= 30: content += f"🎁 距离宝贝生日还有 {gf_bday_countdown} 天！(可以开始悄悄期待礼物咯！)\n"
    else: content += f"🎁 距离宝贝生日还有 {gf_bday_countdown} 天 ♡\n"
        
    my_bday_countdown = calculate_birthday_countdown(12, 20)
    content += f"🎂 距离我的生日还有 {my_bday_countdown} 天 (也要记得给我准备惊喜哦！)\n"
    
    closing_text, closing_emoji = random.choice(list(CLOSINGS.items()))
    content += f"\n{closing_emoji} {closing_text}"
    return content

def send_message(content, message_type="天气消息"):
    """发送纯文字消息"""
    try:
        text_data = {"msgtype": "text", "text": {"content": content}}
        response = requests.post(WEBHOOK_URL, json=text_data, timeout=10)
        response_data = response.json()
        if response_data.get("errcode") == 0: 
            print(f"✅ {message_type}发送成功")
            return True
        else: 
            print(f"❌ {message_type}发送失败: {response_data.get('errmsg')} (errcode: {response_data.get('errcode')})")
            
    except Exception as e: 
        print(f"❌ 发送{message_type}异常: {e}")
    return False

def wait_until_target_time():
    beijing_tz = pytz.timezone('Asia/Shanghai')
    while True:
        now_beijing = datetime.now(beijing_tz)
        if now_beijing.hour == 7:
            print(f"✅ 已到达目标时间！当前北京时间: {now_beijing.strftime('%H:%M:%S')}")
            break
        elif now_beijing.hour > 7:
            print(f"⏰ 时间已过，立即执行！当前北京时间: {now_beijing.strftime('%H:%M:%S')}")
            break
        else:
            print(f"⏳ 当前北京时间: {now_beijing.strftime('%H:%M:%S')}，等待下一分钟...")
            time.sleep(60)

def generate_rain_alert_message(all_weather_data):
    """生成专门的降雨提醒消息"""
    rainy_alerts = []
    
    for city in all_weather_data:
        # 使用智能分析判断是否需要降雨提醒
        should_remind, rain_time = analyze_meaningful_rain(city.get('hourly_data', []))
        
        if should_remind:
            city_name = city['name']
            weather_desc = city['textDay']
            
            # 构建降雨强度描述
            rain_intensity = ""
            if "大雨" in weather_desc or "暴雨" in weather_desc:
                rain_intensity = "⛈️ 大雨"
            elif "中雨" in weather_desc:
                rain_intensity = "🌧️ 中雨"
            elif "小雨" in weather_desc:
                rain_intensity = "☔ 小雨"
            elif "雷" in weather_desc:
                rain_intensity = "⛈️ 雷雨"
            else:
                rain_intensity = "🌧️ 降雨"
            
            # 获取降雨量信息
            precipitation = city.get('precipitation', 0)
            precip_info = ""
            if precipitation > 0:
                if precipitation >= 25:  # 大雨
                    precip_info = f"（预计降雨量{precipitation:.1f}mm）"
                elif precipitation >= 10:  # 中雨
                    precip_info = f"（预计降雨量{precipitation:.1f}mm）"
                elif precipitation >= 2.5:  # 小到中雨
                    precip_info = f"（预计降雨量{precipitation:.1f}mm）"
            
            # 构建该城市的降雨提醒
            city_alert = f"{rain_intensity} 「{city_name}」{rain_time}{precip_info}"
            
            # 添加个性化温馨提醒
            if city_name in ["卧龙", "吴村"]:  # 家人所在地
                city_alert += " - 记得提醒家人带伞哦！"
            elif city_name in ["常大", "苏科大"]:  # 学校
                city_alert += " - 宝贝记得带伞！"
            
            rainy_alerts.append(city_alert)
    
    if not rainy_alerts:
        return None
    
    # 构建完整的降雨提醒消息
    rain_message_parts = [
        "☔ ══════════════════════════════",
        "   🌧️ 贴心降雨提醒 🌧️",
        "☔ ══════════════════════════════",
        ""
    ]
    
    rain_message_parts.extend(rainy_alerts)
    
    rain_message_parts.extend([
        "",
        "💡 温馨提示：",
        "• 📱 可以关注实时天气变化",
        "• 🌂 雨伞常备身边，有备无患",
        "• 👟 雨天路滑，注意安全",
        "• 🚗 开车的话记得打开雨刷器",
        "",
        "💕 爱你，愿你每天都平安快乐！"
    ])
    
    return "\n".join(rain_message_parts)

def main():
    wait_until_target_time()
    
    print(f"================== 爱意天气机器人 V4.3 任务开始 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ==================")
    
    # 使用并发方式获取所有城市的天气数据
    all_weather_data = get_all_cities_weather_concurrent()
    
    if not all_weather_data: 
        print("  > ❌ 所有城市天气数据获取失败，取消发送。"); 
        return
    elif len(all_weather_data) < len(CITIES):
        print(f"  > ⚠️ 仅获取到 {len(all_weather_data)}/{len(CITIES)} 个城市数据，继续发送")
    
    # 生成并发送主要天气消息
    main_message = format_weather_message(all_weather_data)
    print("✅ 天气预报文字内容生成完毕")
    
    print("📤 准备发送天气报告...")
    send_message(main_message, "天气报告")
    
    # 检查是否需要发送降雨提醒
    rain_alert = generate_rain_alert_message(all_weather_data)
    if rain_alert:
        print("🌧️ 检测到降雨情况，准备发送专门的降雨提醒...")
        time.sleep(2)  # 稍等2秒再发送第二条消息
        send_message(rain_alert, "降雨提醒")
    else:
        print("☀️ 今日无需要特别提醒的降雨情况")
    
    print(f"================== 本次任务圆满完成 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ==================\n")

if __name__ == "__main__":
    main()