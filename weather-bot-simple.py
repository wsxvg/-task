#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单的天气预报企业微信机器人
一个文件搞定所有功能
"""

import re
import json
import requests
from datetime import datetime, date
import calendar
import os
import time
import random
from lunardate import LunarDate

# 企业微信机器人地址 - 改成你的
WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=5a9b8214-4fae-4bbd-a147-9ecfe5eb6d90"

# 城市配置
CITIES = {
    "卧龙": "http://forecast.weather.com.cn/town/weather1dn/101210408001.shtml",
    "常大": "http://forecast.weather.com.cn/town/weather1dn/101191104007.shtml", 
    "苏科大": "http://forecast.weather.com.cn/town/weather1dn/101190401006.shtml",
    "吴村": "http://forecast.weather.com.cn/town/weather1dn/101210203004.shtml"
}

def get_weather_emoji(weather_text):
    """获取天气emoji"""
    weather_emojis = {
        "晴": "☀️", "多云": "⛅", "阴": "☁️", "小雨": "🌦️", "中雨": "🌧️", 
        "大雨": "⛈️", "雷阵雨": "⚡", "雪": "❄️", "雾": "🌫️", "霾": "😷"
    }
    for weather, emoji in weather_emojis.items():
        if weather in weather_text:
            return emoji
    return "🌤️"

def analyze_rain_periods(forecast_1h_data):
    """分析24小时预报中的下雨时段"""
    if not forecast_1h_data:
        return []
    
    rain_periods = []
    current_period = None
    
    for i, hour_data in enumerate(forecast_1h_data):
        weather = hour_data.get("weather", "")
        time_str = hour_data.get("time", "")
        
        # 检查是否是雨天
        is_rain = any(rain_word in weather for rain_word in ["雨", "雷阵", "阵雨", "小雨", "中雨", "大雨", "暴雨"])
        
        if is_rain:
            if current_period is None:
                # 开始新的下雨时段
                current_period = {
                    "start_time": time_str,
                    "start_hour": i,
                    "weather_types": [weather]
                }
            else:
                # 继续当前下雨时段
                current_period["weather_types"].append(weather)
        else:
            if current_period is not None:
                # 结束当前下雨时段
                current_period["end_time"] = forecast_1h_data[i-1].get("time", "")
                current_period["end_hour"] = i - 1
                
                # 计算时段描述
                if current_period["start_hour"] == current_period["end_hour"]:
                    current_period["duration"] = f"{current_period['start_time']}点"
                else:
                    current_period["duration"] = f"{current_period['start_time']}点-{current_period['end_time']}点"
                
                # 获取主要天气类型
                weather_types = current_period["weather_types"]
                weather_count = {}
                for w in weather_types:
                    weather_count[w] = weather_count.get(w, 0) + 1
                if weather_count:
                    # 获取出现次数最多的天气类型
                    max_count = max(weather_count.values())
                    for weather, count in weather_count.items():
                        if count == max_count:
                            current_period["main_weather"] = weather
                            break
                else:
                    current_period["main_weather"] = "雨"
                
                rain_periods.append(current_period)
                current_period = None
    
    # 处理最后一个时段
    if current_period is not None:
        current_period["end_time"] = forecast_1h_data[-1].get("time", "")
        current_period["end_hour"] = len(forecast_1h_data) - 1
        
        if current_period["start_hour"] == current_period["end_hour"]:
            current_period["duration"] = f"{current_period['start_time']}点"
        else:
            current_period["duration"] = f"{current_period['start_time']}点-{current_period['end_time']}点"
        
        weather_types = current_period["weather_types"]
        weather_count = {}
        for w in weather_types:
            weather_count[w] = weather_count.get(w, 0) + 1
        if weather_count:
            # 获取出现次数最多的天气类型
            max_count = max(weather_count.values())
            for weather, count in weather_count.items():
                if count == max_count:
                    current_period["main_weather"] = weather
                    break
        else:
            current_period["main_weather"] = "雨"
        
        rain_periods.append(current_period)
    
    return rain_periods

def get_city_weather(city_name, url):
    """获取单个城市天气（带重试机制）"""
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            print(f"📍 获取{city_name}天气数据...（第{attempt + 1}次尝试）")
            
            # 更真实的请求头
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Referer': 'http://www.weather.com.cn/'
            }
            
            # 添加随机延迟，避免请求过快
            if attempt > 0:
                delay = random.uniform(1, 3)  # 1-3秒随机延迟
                print(f"⏳ 等待 {delay:.1f} 秒后重试...")
                time.sleep(delay)
            
            response = requests.get(url, headers=headers, timeout=15)
            response.encoding = 'utf-8'
            
            if response.status_code != 200:
                print(f"⚠️ {city_name}: HTTP状态码 {response.status_code}")
                continue
                
            html = response.text
            
            # 提取天气数据
            weather_data = {"city": city_name, "rain_periods": []}
            
            # 调试: 查看是否包含forecast_default
            if 'forecast_default' in html:
                print(f"🔍 {city_name}: 找到forecast_default关键字")
            else:
                print(f"⚠️ {city_name}: 未找到forecast_default关键字")
            
            # 提取当前天气数据
            forecast_default_pattern = r'var\s+forecast_default\s*=\s*(\{.*?\});'
            match = re.search(forecast_default_pattern, html, re.DOTALL)
            if match:
                print(f"✅ {city_name}: 正则匹配成功")
                try:
                    data = json.loads(match.group(1))
                    print(f"📊 {city_name}: JSON解析成功, 数据: {data}")
                    # 直接使用正确的字段名
                    weather_data["temp"] = data.get("temp", "N/A")
                    weather_data["weather"] = data.get("weather", "N/A")
                    weather_data["humidity"] = data.get("humidity", "N/A")
                    weather_data["wind"] = data.get("wind", "N/A")
                    weather_data["maxTemp"] = data.get("maxTemp", "N/A")
                    weather_data["minTemp"] = data.get("minTemp", "N/A")
                except json.JSONDecodeError as e:
                    print(f"❌ {city_name}: JSON解析失败: {e}")
                    print(f"❌ 匹配内容: {match.group(1)[:200]}...")
            else:
                print(f"❌ {city_name}: 正则匹配失败")
                # 尝试查找forecast_default的位置
                pos = html.find('forecast_default')
                if pos >= 0:
                    print(f"🔍 找到forecast_default在位置: {pos}")
                    # 显示周围的内容
                    context = html[max(0, pos-50):pos+200]
                    print(f"🔍 周围内容: {context}")
            
            # 提取生活指数信息
            life_indices = {}
            
            # 更精确地提取生活指数数据
            # 查找包含生活指数的div区域
            life_pattern = r'<div class="lv"[^>]*>(.*?)</div>'
            life_match = re.search(life_pattern, html, re.DOTALL)
            
            if life_match:
                life_content = life_match.group(1)
                # 提取每个指数项
                index_pattern = r'<dt>\s*<em>([^<]+)</em>.*?</dt>\s*<dd>([^<]+)</dd>'
                indices = re.findall(index_pattern, life_content, re.DOTALL)
                
                # 根据顺序分配指数（通常顺序是：紫外线、感冒、穿衣、洗车、运动、空气污染扩散）
                if len(indices) >= 1:  # 紫外线
                    weather_data["uv_level"] = indices[0][0].strip()
                    weather_data["uv_advice"] = indices[0][1].strip()
                    print(f"🔍 {city_name}: 紫外线 - 等级: {indices[0][0].strip()}, 建议: {indices[0][1].strip()}")
                    
                if len(indices) >= 2:  # 感冒
                    weather_data["cold_level"] = indices[1][0].strip()
                    weather_data["cold_advice"] = indices[1][1].strip()
                    print(f"🔍 {city_name}: 感冒 - 等级: {indices[1][0].strip()}, 建议: {indices[1][1].strip()}")
                    
                if len(indices) >= 3:  # 穿衣
                    weather_data["clothing_level"] = indices[2][0].strip()
                    weather_data["clothing_advice"] = indices[2][1].strip()
                    print(f"🔍 {city_name}: 穿衣 - 等级: {indices[2][0].strip()}, 建议: {indices[2][1].strip()}")
                    
                print(f"🔍 {city_name}: 提取到{len(indices)}个生活指数")
                # 打印所有提取到的指数，帮助调试
                for i, (level, advice) in enumerate(indices):
                    print(f"🔍 {city_name}: 指数{i+1}: {level.strip()} - {advice.strip()}")
            else:
                print(f"⚠️ {city_name}: 未找到生活指数区域")
            
            # 提取24小时预报数据
            forecast_1h_pattern = r'var\s+forecast_1h\s*=\s*(\[.*?\]);'
            match = re.search(forecast_1h_pattern, html, re.DOTALL)
            if match:
                try:
                    forecast_1h = json.loads(match.group(1))
                    if forecast_1h:
                        # 从24小时预报中提取温度数据
                        temps = []
                        for item in forecast_1h:
                            if item.get("temp") and str(item.get("temp")).replace('.', '').replace('-', '').isdigit():
                                temps.append(float(item.get("temp")))
                        
                        if temps:
                            weather_data["temp_max_1h"] = max(temps)
                            weather_data["temp_min_1h"] = min(temps)
                            print(f"🌡️ {city_name}: 温度范围 {min(temps)}-{max(temps)}°C")
                        
                        # 分析雨天时段
                        rain_periods = analyze_rain_periods(forecast_1h)
                        weather_data["rain_periods"] = rain_periods
                        if rain_periods:
                            print(f"🌧️ {city_name}: 发现{len(rain_periods)}个下雨时段")
                            for period in rain_periods:
                                print(f"   - {period['duration']}: {period['main_weather']}")
                except json.JSONDecodeError as e:
                    print(f"❌ {city_name}: forecast_1h JSON解析失败: {e}")
            
            print(f"✅ {city_name}数据获取成功")
            print(f"🌡️ 调试信息: 温度={weather_data.get('temp')}, 天气={weather_data.get('weather')}, 湿度={weather_data.get('humidity')}")
            return weather_data
            
        except requests.exceptions.RequestException as e:
            print(f"⚠️ {city_name}: 网络请求异常 - {str(e)}")
            if attempt == max_retries - 1:
                print(f"❌ {city_name}: 所有重试都失败")
            continue
        except json.JSONDecodeError as e:
            print(f"⚠️ {city_name}: JSON解析失败 - {str(e)}")
            if attempt == max_retries - 1:
                print(f"❌ {city_name}: 数据格式错误")
            continue
        except Exception as e:
            print(f"⚠️ {city_name}: 未知错误 - {str(e)}")
            if attempt == max_retries - 1:
                print(f"❌ {city_name}: 最终失败")
            continue
    
    # 所有重试都失败
    print(f"❌ {city_name}数据获取失败：已经重试 {max_retries} 次")
    return None

def get_lunar_date():
    """获取农历日期信息（使用专业库）"""
    try:
        # 获取今天的阳历日期
        today = date.today()
        
        # 转换为农历日期
        lunar = LunarDate.fromSolarDate(today.year, today.month, today.day)
        
        # 格式化农历日期
        lunar_months = [
            "正月", "二月", "三月", "四月", "五月", "六月",
            "七月", "八月", "九月", "十月", "十一月", "十二月"
        ]
        
        lunar_days = [
            "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
            "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
            "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十"
        ]
        
        month_name = lunar_months[lunar.month - 1]
        day_name = lunar_days[lunar.day - 1]
        
        return f"农历{month_name}{day_name}"
        
    except Exception as e:
        # 如果农历库出错，返回简单的备用格式
        print(f"⚠️ 农历计算失败: {e}")
        return "农历七月廿一"

def get_solar_term():
    """获取当前节气"""
    now = datetime.now()
    month = now.month
    day = now.day
    
    # 简化的节气对照表
    solar_terms = {
        (8, 7): "立秋", (8, 23): "处暑",
        (9, 7): "白露", (9, 23): "秋分",
        (10, 8): "寒露", (10, 23): "霜降",
        (11, 7): "立冬", (11, 22): "小雪",
        (12, 7): "大雪", (12, 22): "冬至"
    }
    
    # 找到最接近的节气
    current_term = "处暑"  # 默认值
    for (m, d), term in solar_terms.items():
        if month == m and day >= d:
            current_term = term
        elif month > m:
            current_term = term
    
    return current_term

def analyze_overall_weather(all_weather):
    """分析整体天气状况"""
    if not all_weather:
        return "天气数据不足"
    
    weather_types = []
    temps = []
    
    for city, data in all_weather.items():
        if data and data.get("weather") != "N/A":
            weather_types.append(data.get("weather"))
        if data and data.get("temp") != "N/A":
            try:
                temps.append(float(data.get("temp")))
            except:
                pass
    
    if not weather_types:
        return "多云转晴"
    
    # 分析主要天气类型
    weather_count = {}
    for weather in weather_types:
        weather_count[weather] = weather_count.get(weather, 0) + 1
    
    main_weather = "多云"  # 默认值
    if weather_count:
        # 获取出现次数最多的天气类型
        max_count = max(weather_count.values())
        for weather, count in weather_count.items():
            if count == max_count:
                main_weather = weather
                break
    
    # 分析温度
    if temps:
        avg_temp = sum(temps) / len(temps)
        if avg_temp >= 35:
            return f"{main_weather}·炎热"
        elif avg_temp >= 30:
            return f"{main_weather}·温暖"
        elif avg_temp >= 25:
            return f"{main_weather}·舒适"
        elif avg_temp >= 20:
            return f"{main_weather}·凉爽"
        else:
            return f"{main_weather}·寒冷"
    
    return main_weather

def calculate_days_together():
    """计算在一起的天数"""
    start_date = date(2023, 8, 2)  # 2023年8月2日
    today = date.today()
    days = (today - start_date).days
    return days

def calculate_birthday_countdown():
    """计算农历生日倒计时"""
    try:
        today = date.today()
        current_year = today.year
        
        # 农历9月11（女朋友生日）
        # 农历腊月二十（你的生日）
        
        # 尝试计算今年的农历生日对应的阳历日期
        try:
            # 女朋友农历生日：九月十一
            girlfriend_lunar = LunarDate(current_year, 9, 11)
            girlfriend_solar = girlfriend_lunar.toSolarDate()
            girlfriend_birthday = date(girlfriend_solar.year, girlfriend_solar.month, girlfriend_solar.day)
            
            # 你的农历生日：腊月二十（腊月是12月）
            my_lunar = LunarDate(current_year, 12, 20)
            my_solar = my_lunar.toSolarDate()
            my_birthday = date(my_solar.year, my_solar.month, my_solar.day)
            
        except Exception as e:
            # 如果今年的农历转换失败，尝试明年
            print(f"⚠️ 今年农历转换失败: {e}，尝试明年")
            girlfriend_birthday = date(current_year, 10, 25)
            my_birthday = date(current_year + 1, 1, 30)
        
        # 如果今年的生日已过，计算明年的
        if girlfriend_birthday <= today:
            try:
                girlfriend_lunar = LunarDate(current_year + 1, 9, 11)
                girlfriend_solar = girlfriend_lunar.toSolarDate()
                girlfriend_birthday = date(girlfriend_solar.year, girlfriend_solar.month, girlfriend_solar.day)
            except:
                girlfriend_birthday = date(current_year + 1, 10, 25)
        
        if my_birthday <= today:
            try:
                my_lunar = LunarDate(current_year + 1, 12, 20)
                my_solar = my_lunar.toSolarDate()
                my_birthday = date(my_solar.year, my_solar.month, my_solar.day)
            except:
                my_birthday = date(current_year + 1, 1, 30)
        
        girlfriend_days = (girlfriend_birthday - today).days
        my_days = (my_birthday - today).days
        
        return girlfriend_days, my_days
        
    except Exception as e:
        # 如果所有农历计算都失败，返回备用值
        print(f"⚠️ 生日计算失败: {e}，使用备用值")
        return 62, 159

def get_sunscreen_advice(uv_level):
    """根据紫外线强度给出防晒建议"""
    if not uv_level or uv_level == "N/A":
        return "防晒：建议做好基础防扒"
    
    uv_level_str = str(uv_level).lower()
    
    if "弱" in uv_level_str or "较弱" in uv_level_str:
        return "防晒：无需特别防护，可选择SPF15以上防晒霜"
    elif "中" in uv_level_str or "适中" in uv_level_str:
        return "防晒：建议使用SPF20-30防晒霜，配戴太阳帽"
    elif "强" in uv_level_str or "较强" in uv_level_str:
        return "防晒：必须使用SPF30+防晒霜，配戴太阳镜和帽子"
    elif "极强" in uv_level_str or "很强" in uv_level_str:
        return "防晒：必须使用SPF50+防晒霜，避免10-16点外出"
    else:
        return f"防晒：{uv_level}，建议做好防护措施"

def get_personalized_tip(temp_max, temp_min, temp_diff, city_name):
    """根据城市温度给出个性化小贴士"""
    try:
        max_temp = float(temp_max) if temp_max != "N/A" else 30
        diff = int(temp_diff) if temp_diff else 5
    except:
        max_temp = 30
        diff = 5
    
    if diff >= 10:
        return f"温差{diff}°C,备好外套"
    elif max_temp >= 35:
        return "高温区域,避免午后外出"
    elif diff >= 8:
        return f"温差{diff}°C,防晒+补水重要"
    elif max_temp >= 30:
        return "防晒补水,清爽出行"
    else:
        return "温度适宜,注意防晒"

def get_clothing_advice(temp, weather):
    """根据温度和天气给出穿衣建议"""
    try:
        temp_num = float(temp) if temp != "N/A" else 25
    except:
        temp_num = 25
    
    if temp_num >= 30:
        return "预计今日天气温暖，早晚较凉，请穿可以保暖的春秋衣、建议着休闲装、西装、夹克衫、薄毛衣等，既舒适，又时尚。"
    elif temp_num >= 25:
        return "天气温和，建议穿短袖、薄外套等舒适服装。"
    elif temp_num >= 20:
        return "建议穿长袖衬衫、薄外套，注意早晚温差。"
    else:
        return "天气较凉，建议穿厚外套、毛衣等保暖衣物。"



def format_weather_message(all_weather):
    """格式化天气消息为美化的融合风格"""
    if not all_weather:
        return "❌ 天气数据获取失败"
    
    now = datetime.now()
    current_date = now.strftime("%Y年%m月%d日")
    weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekday_names[now.weekday()]
    lunar_date = get_lunar_date()
    solar_term = get_solar_term()
    overall_weather = analyze_overall_weather(all_weather)
    
    # 计算真实数据
    days_together = calculate_days_together()
    girlfriend_days, my_days = calculate_birthday_countdown()
    
    # 构建美化的融合消息
    content = "🌈 每日天气小报 ( ◕ ω ◕ )\n\n"
    content += f"📍 {current_date} {weekday} 🌸\n"
    content += f"🌙 {lunar_date} · {solar_term}时节\n"
    content += f"🌤️ 整体天气：{overall_weather}\n\n"
    
    # 添加城市天气信息
    content += "🌡️ 今日温度监测站\n\n"
    
    # 用于统计和分析的列表
    city_data_list = []
    city_emojis = ["（｡◕‗◕｡）", "（´▽｀）", "ヽ（°〇°）ﾉ", "（⌒‗⌒）"]
    
    city_count = 0
    for city, data in all_weather.items():
        if not data:
            continue
        
        temp = data.get("temp", "N/A")
        weather = data.get("weather", "N/A")
        
        # 温度范围
        temp_max = data.get("temp_max_1h") or data.get("maxTemp", "N/A")
        temp_min = data.get("temp_min_1h") or data.get("minTemp", "N/A")
        
        if temp_max != "N/A" and temp_min != "N/A":
            try:
                temp_range = f"{int(temp_min)}~{int(temp_max)}°C"
                temp_diff = int(temp_max) - int(temp_min)
            except (ValueError, TypeError):
                temp_range = "25~30°C"
                temp_diff = 5
        else:
            temp_range = "25~30°C"
            temp_diff = 5
        
        # 存储城市数据用于后面的分析
        city_data_list.append({
            'name': city,
            'temp_max': temp_max,
            'temp_min': temp_min,
            'temp_diff': temp_diff,
            'data': data
        })
        
        content += "──────────────────\n"
        
        emoji = city_emojis[city_count % len(city_emojis)]
        city_short = city.replace("新市镇", "").replace("嘉泽镇", "").replace("街道", "").replace("杭垄镇", "")
        
        content += f"🏠 {city_short} {emoji}\n"
        content += f"☀️ 天气：{weather} 🌡️ 气温：{temp_range}\n"
        
        # 穿衣建议 - 使用实际数据
        if data.get("clothing_level"):
            clothing_text = f"{data.get('clothing_level', '')}"
        else:
            # 如果没有网站数据，根据温度动态生成
            if temp_max != "N/A":
                try:
                    max_temp = float(temp_max)
                    if max_temp >= 35:
                        clothing_text = "薄透气装"
                    elif max_temp >= 30:
                        clothing_text = "清爽夏装"
                    elif max_temp >= 25:
                        clothing_text = "舒适装"
                    elif max_temp >= 20:
                        clothing_text = "轻薄外套"
                    else:
                        clothing_text = "保暖装"
                except:
                    clothing_text = "舒适装"
            else:
                clothing_text = "舒适装"
        
        # 紫外线建议 - 使用实际获取的数据
        if data.get("uv_level"):
            uv_text = f"{data.get('uv_level', '')}"
        else:
            # 如果没有网站数据，根据天气和温度估算
            if weather == "晴" and temp_max != "N/A":
                try:
                    max_temp = float(temp_max)
                    if max_temp >= 35:
                        uv_text = "强"
                    elif max_temp >= 30:
                        uv_text = "中等"
                    else:
                        uv_text = "较弱"
                except:
                    uv_text = "中等"
            elif weather in ["多云", "阴"]:
                uv_text = "较弱"
            elif weather in ["雨", "小雨", "中雨"]:
                uv_text = "最弱"
            else:
                uv_text = "中等"
            
        content += f"👕 穿衣：{clothing_text} ☀️ 紫外：{uv_text}\n"
        
        # 感冒建议 - 使用实际数据
        if data.get("cold_level"):
            cold_text = f"{data.get('cold_level', '')}"
        else:
            # 如果没有网站数据，根据温差和天气估算
            if temp_diff >= 10:
                cold_text = "易发"
            elif temp_diff >= 8:
                cold_text = "较易发"
            elif weather in ["雨", "小雨"]:
                cold_text = "较易发"
            else:
                cold_text = "少发"
        content += f"🤧 感冒：{cold_text} - 注意保暖\n"
        
        # 个性化小贴士
        tip = get_personalized_tip(temp_max, temp_min, temp_diff, city_short)
        content += f"💡 小贴士：{tip}\n\n"
        
        city_count += 1
    

    
    # 情侣专区
    content += "～～～～～💕甜蜜时光轴💕～～～～～\n\n"
    content += f"❤️ 我们已经相恋第 {days_together} 天啦！（｡◕‗◕｡）\n"
    content += f"💖 今天依然是深爱彼此的美好一天\n\n"
    content += f"🎂 距离我的生日：{my_days} 天 ✨\n"
    content += f"🎁 距离宝贝生日：{girlfriend_days} 天 ♡（˃͘ ੫ ˂͘ ☶ ）\n\n"
    
    content += "～～～～～💝爱情小屋💝～～～～～\n\n"
    
    # 综合出行建议
    content += "（ •̀ ω •́ ）✧ 综合出行建议\n\n"
    
    # 分析数据
    if city_data_list:
        # 找最舒适的城市（最低最高温均衡）
        most_comfortable = min(city_data_list, key=lambda x: abs((x['temp_max'] + x['temp_min'])/2 - 25) if x['temp_max'] != 'N/A' else float('inf'))
        # 找最热的城市
        hottest = max(city_data_list, key=lambda x: x['temp_max'] if x['temp_max'] != 'N/A' else 0)
        # 找温差最大的城市
        max_diff = max(city_data_list, key=lambda x: x['temp_diff'])
        
        content += f"🌟 今日最舒适：{most_comfortable['name'].replace('新市镇', '').replace('嘉泽镇', '').replace('街道', '').replace('杭垄镇', '')}（温度适中）\n"
        content += f"⚠️ 今日最炎热：{hottest['name'].replace('新市镇', '').replace('嘉泽镇', '').replace('街道', '').replace('杭垄镇', '')}（最高{hottest['temp_max']}°C）\n"
        content += f"🧥 温差最大：{max_diff['name'].replace('新市镇', '').replace('嘉泽镇', '').replace('街道', '').replace('杭垄镇', '')}（相差{max_diff['temp_diff']}°C）\n"

    # 紫外线分析
    uv_levels = [data['data'].get('uv_level', '中等') for data in city_data_list if data['data'].get('uv_level')]
    if uv_levels:
        strong_cities = [data['name'].replace('新市镇', '').replace('嘉泽镇', '').replace('街道', '').replace('杭垄镇', '') 
                        for data in city_data_list 
                        if data['data'].get('uv_level') and ('强' in data['data'].get('uv_level') or '较强' in data['data'].get('uv_level'))]
        weak_cities = [data['name'].replace('新市镇', '').replace('嘉泽镇', '').replace('街道', '').replace('杭垄镇', '') 
                      for data in city_data_list 
                      if data['data'].get('uv_level') and ('弱' in data['data'].get('uv_level') or '最弱' in data['data'].get('uv_level'))]
        
        if strong_cities:
            content += f"☀️ 防晒重点：{','.join(strong_cities)}紫外线较强\n"
        elif weak_cities:
            content += f"☀️ 防晒提醒：今日紫外线较弱，可适度防护\n"
        else:
            content += f"☀️ 防晒提醒：注意防晒措施\n"
    else:
        content += f"☀️ 防晒提醒：注意防晒措施\n"

    # 雨天带伞提醒 - 新增功能
    rain_alerts = []
    for city_data in city_data_list:
        city_name = city_data['name'].replace('新市镇', '').replace('嘉泽镇', '').replace('街道', '').replace('杭垄镇', '')
        rain_periods = city_data['data'].get('rain_periods', [])
        
        if rain_periods:
            for period in rain_periods:
                rain_alerts.append({
                    'city': city_name,
                    'time': period['duration'],
                    'weather': period['main_weather']
                })
    
    if rain_alerts:
        content += "☔ 带伞提醒：\n"
        
        # 按城市分组显示雨天信息
        city_rain_info = {}
        for alert in rain_alerts:
            city = alert['city']
            if city not in city_rain_info:
                city_rain_info[city] = []
            city_rain_info[city].append(f"{alert['time']}({alert['weather']})")
        
        for city, rain_times in city_rain_info.items():
            rain_time_str = '、'.join(rain_times)
            content += f"   🌧️ {city}：{rain_time_str}\n"
        
        content += "   记得带伞哦！ ☂️(◕‿◕)♡\n"
    else:
        content += "☔ 今日无降雨预报，可放心出行 ☀️\n"

    content += "\n"
    content += "💌 爱你每一天 （◕‗◕）♡"
    
    return content

def send_message(content):
    """发送企业微信纯文本消息"""
    try:
        data = {
            "msgtype": "text",
            "text": {
                "content": content
            }
        }
        
        response = requests.post(WEBHOOK_URL, json=data, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            if result.get("errcode") == 0:
                print("✅ 消息发送成功")
                return True
            else:
                print(f"❌ 消息发送失败: {result.get('errmsg')}")
        else:
            print(f"❌ HTTP请求失败: {response.status_code}")
        return False
        
    except Exception as e:
        print(f"❌ 发送消息异常: {e}")
        return False

def send_test_message():
    """发送测试消息"""
    test_content = f"""🤖 天气机器人测试

🕐 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
✅ 状态: 正常运行

这是一条测试消息，确认机器人正常工作"""
    
    data = {
        "msgtype": "text",
        "text": {
            "content": test_content
        }
    }
    
    try:
        response = requests.post(WEBHOOK_URL, json=data, timeout=10)
        if response.status_code == 200 and response.json().get("errcode") == 0:
            print("✅ 测试消息发送成功")
            return True
    except:
        pass
    print("❌ 测试消息发送失败")
    return False

def main():
    """主函数"""
    import sys
    from datetime import datetime
    import pytz
    
    print("🤖 天气预报机器人启动...")
    
    # 检查命令行参数
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        send_test_message()
        return
    
    # 获取北京时间
    beijing_tz = pytz.timezone('Asia/Shanghai')
    start_time = datetime.now(beijing_tz)
    print(f"🚀 程序运行开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
    
    # 检查是否到达北京时间早上7点
    target_hour = 7  # 目标时间：早上7点
    current_hour = start_time.hour
    current_minute = start_time.minute
    
    if current_hour < target_hour:
        # 计算需要等待的时间
        target_time = start_time.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        wait_seconds = int((target_time - start_time).total_seconds())
        
        print(f"⏰ 当前时间为 {current_hour:02d}:{current_minute:02d}，尚未到达北京时间早上{target_hour}点")
        print(f"⏳ 将等待 {wait_seconds} 秒（{wait_seconds//60} 分钟 {wait_seconds%60} 秒）后开始发送天气预报")
        print(f"📋 预计发送时间: {target_time.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
        
        # 等待到目标时间
        time.sleep(wait_seconds)
        
        # 重新获取当前时间
        actual_start_time = datetime.now(beijing_tz)
        print(f"✅ 等待完成！实际开始时间: {actual_start_time.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
    elif current_hour >= target_hour:
        print(f"✅ 当前时间为 {current_hour:02d}:{current_minute:02d}，已过北京时间早上{target_hour}点，立即发送天气预报")
    
    print(f"📊 开始获取 {len(CITIES)} 个城市的天气数据...")
    
    # 获取所有城市天气
    all_weather = {}
    for i, (city, url) in enumerate(CITIES.items()):
        weather_data = get_city_weather(city, url)
        if weather_data:
            all_weather[city] = weather_data
        
        # 在请求之间添加间隔，避免请求过快
        if i < len(CITIES) - 1:  # 不是最后一个城市
            delay = random.uniform(2, 4)  # 2-4秒随机延迟
            print(f"⏳ 等待 {delay:.1f} 秒后继续...")
            time.sleep(delay)
    
    if not all_weather:
        print("❌ 没有获取到任何天气数据")
        return
    
    print(f"✅ 成功获取 {len(all_weather)} 个城市的天气数据")
    
    # 格式化并发送消息
    message = format_weather_message(all_weather)
    print("📤 发送天气预报...")
    
    success = send_message(message)
    if success:
        print("🎉 天气预报发送完成!")
    else:
        print("😫 天气预报发送失败!")

if __name__ == "__main__":
    main()