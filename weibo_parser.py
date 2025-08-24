#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博API数据解析器
单文件版本 - 用于解析微博群组时间线API返回的JSON数据
支持企业微信机器人推送

使用方法：
1. 默认使用（自动推送到企业微信）：
   python weibo_parser.py

2. 只显示在控制台（不推送）：
   python weibo_parser.py --no-push

3. 使用自定义webhook URL：
   python weibo_parser.py --webhook "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY"

4. 通过环境变量覆盖：
   set WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY
   python weibo_parser.py

默认配置：
- 企业微信机器人已预设配置
- 自动推送今天的微博动态
- 支持纯文本 + 图片模式

功能特性：
- 智能分页抓取，只获取今天的动态
- 自动过滤广告/推广内容
- 支持企业微信机器人推送（纯文本 + 图片）
- 图片自动压缩（不超过2M）
- 频率限制保护（20条/分钟）

注意事项：
- 需要安装 Pillow 库：pip install Pillow
- 企业微信机器人已预设配置，可直接使用
- 图片转换为base64可能需要一些时间
"""

import json
import re
import requests
import base64
import hashlib
import os
import pytz
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

try:
    from PIL import Image
except ImportError:
    print("⚠️ 未安装 Pillow 库，图片功能将不可用")
    print("请运行：pip install Pillow")
    Image = None


class WeiboDataParser:
    """微博API数据解析器"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.parsed_data = []
        self.webhook_url = webhook_url
        
        # 上新/发售识别的关键词库
        self.time_triggers = {
            # 相对时间
            '今晚', '明晚', '明天', '后天', '今天',
            # 星期
            '周一', '周二', '周三', '周四', '周五', '周六', '周日',
            '星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日',
            # 时间点关键词
            '点', ':', '：'
        }
        
        self.action_triggers = {
            '上架', '发售', '预售', '释放', '开启', '开售', '补货', '现货',
            '上新通知', '新款预告', '准备就绪', '开启预售', '提前购',
            '会员先购', '非会员释放', 'VIP先购', 'vip先购', '先购',
            '售罄不补', '限购', '抽奖', '现货直接释放',
            '上新', '上新产品', '开放', '开放购买'
        }
        
    def is_product_launch_post(self, status: Dict[str, Any]) -> bool:
        """
        两阶段过滤：识别是否为上新/发售预告帖子
        
        阶段1: 必须包含时间类或动作类触发词
        阶段2: 进一步的语义验证和时间有效性检查
        """
        content = status.get('text_raw', '')
        
        if not content:
            return False
        
        # 阶段1: 检查时间类触发词
        has_time_trigger = self._has_time_trigger(content)
        
        # 阶段1: 检查动作类触发词
        has_action_trigger = self._has_action_trigger(content)
        
        # 必须至少包含一个时间类或动作类触发词
        if not (has_time_trigger or has_action_trigger):
            return False
        
        # 阶段2: 排除纯预告类内容（没有实际上新行为）
        if self._is_pure_preview(content):
            return False
        
        # 阶段2: 排除时间久远的内容
        if self._is_time_too_distant(content):
            return False
        
        return True
    
    def _has_time_trigger(self, content: str) -> bool:
        """检查是否包含时间类触发词"""
        # 检查直接关键词（排除误判）
        for trigger in self.time_triggers:
            if trigger in content:
                # 特殊处理：排除“点点赞”、“点击”等误判
                if trigger == '点' and ('点点赞' in content or '点击' in content or '点评' in content):
                    continue
                # 特殊处理：只有URL中的冒号不算时间触发词
                if trigger in [':', '：'] and ('http' in content or 't.cn' in content) and len([t for t in self.action_triggers if t in content]) == 0:
                    continue
                return True
        
        # 检查日期模式（包含“日”或“号”）
        import re
        # 匹配如: 27日, 25号, 8.24号, 8/24日
        date_pattern = r'\d+[.\/]?\d*[日号]'
        if re.search(date_pattern, content):
            return True
        
        # 匹配时间格式（如: 20:00, 19:30, 八点）
        time_pattern = r'\d+[:：]\d{2}|[一-九十百千万]+点'
        if re.search(time_pattern, content):
            return True
            
        return False
    
    def _has_action_trigger(self, content: str) -> bool:
        """检查是否包含动作类触发词"""
        for trigger in self.action_triggers:
            if trigger in content:
                return True
        return False
    
    def _is_pure_preview(self, content: str) -> bool:
        """检查是否为纯预告内容（没有实际上新行为）"""
        # 纯预告的特征：只有“预告”没有具体发售行为
        preview_only_patterns = [
            '新款预告',  # 单纯的新款预告
        ]
        
        for pattern in preview_only_patterns:
            if pattern in content:
                # 检查是否有实际的发售行为关键词
                action_keywords = ['上架', '发售', '释放', '开售', '补货', '开启', '现货']
                has_real_action = any(keyword in content for keyword in action_keywords)
                
                # 如果只有预告没有实际行为，则认为是纯预告
                if not has_real_action:
                    return True
        
        return False
    
    def _is_time_too_distant(self, content: str) -> bool:
        """检查时间是否过于久远（超过3天）"""
        import re
        
        # 检查远期时间的模式
        distant_patterns = [
            r'下个月',  # 下个月
            r'下月',    # 下月 
            r'下周',    # 下周
        ]
        
        for pattern in distant_patterns:
            if re.search(pattern, content):
                return True
        
        return False
    
    def extract_launch_info(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """
        提取上新帖子的结构化信息
        """
        content = status.get('text_raw', '')
        user_name = status.get('user', {}).get('screen_name', '')
        created_time = status.get('created_at', '')
        post_id = status.get('id', '')
        
        # 提取匹配的关键词
        matched_time_triggers = [t for t in self.time_triggers if t in content]
        matched_action_triggers = [t for t in self.action_triggers if t in content]
        
        # 提取时间信息
        time_info = self._extract_time_info(content)
        
        # 提取产品信息（简化版）
        product_info = self._extract_product_info(content)
        
        # 直接使用已解析的媒体信息（如果已经解析过）
        # 如果status包含media字段说明已经解析过，直接使用
        if 'media' in status:
            media_content = status['media']
        else:
            # 否则重新解析（针对原始数据）
            media_content = self._parse_media_content(status)
        
        return {
            'post_id': post_id,
            'user_name': user_name,
            'created_time': created_time,
            'content': content,
            'matched_time_triggers': matched_time_triggers,
            'matched_action_triggers': matched_action_triggers,
            'extracted_time': time_info,
            'product_info': product_info,
            'launch_type': self._classify_launch_type(matched_action_triggers),
            'media': media_content  # 使用媒体信息
        }
    
    def _extract_time_info(self, content: str) -> List[str]:
        """提取时间信息"""
        import re
        time_patterns = []
        
        # 提取具体时间点 (20:00, 19:30等)
        time_match = re.findall(r'\d{1,2}[:：]\d{2}', content)
        time_patterns.extend(time_match)
        
        # 提取日期 (27日, 25号等)
        date_match = re.findall(r'\d+[.\/]?\d*[日号]', content)
        time_patterns.extend(date_match)
        
        # 提取相对时间
        relative_times = [t for t in ['今晚', '明晚', '明天', '后天'] if t in content]
        time_patterns.extend(relative_times)
        
        return time_patterns
    
    def _extract_product_info(self, content: str) -> str:
        """简化的产品信息提取（取内容前100字符）"""
        return content[:100] + '...' if len(content) > 100 else content
    
    def _classify_launch_type(self, action_triggers: List[str]) -> str:
        """分类上新类型"""
        if any(t in action_triggers for t in ['预售', '开启预售']):
            return '预售'
        elif any(t in action_triggers for t in ['补货', '现货']):
            return '补货'
        elif any(t in action_triggers for t in ['上架', '发售', '释放']):
            return '正式发售'
        else:
            return '其他'
    
    def filter_launch_posts(self, parsed_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """过滤出所有上新/发售预告帖子"""
        launch_posts = []
        
        for post in parsed_data:
            if self.is_product_launch_post(post):
                launch_info = self.extract_launch_info(post)
                launch_posts.append(launch_info)
        
        return launch_posts
        
    def parse_weibo_response(self, json_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        解析微博API响应数据
        
        Args:
            json_data: 微博API返回的JSON数据
            
        Returns:
            解析后的动态列表
        """
        if 'statuses' not in json_data:
            return []
        
        statuses = json_data['statuses']
        
        parsed_statuses = []
        
        for index, status in enumerate(statuses, 1):
            # 检查是否为广告/推广内容
            if self._is_promotion(status):
                continue
            
            parsed_status = self._parse_single_status(status)
            if parsed_status:
                parsed_statuses.append(parsed_status)
        
        return parsed_statuses
    
    def _is_promotion(self, status: Dict[str, Any]) -> bool:
        """判断是否为广告/推广内容"""
        # 检查promotion字段
        if 'promotion' in status:
            return True
        
        # 检查content_auth_info
        content_auth_info = status.get('content_auth_info', {})
        if content_auth_info.get('content_auth_title') == '荐读':
            return True
        
        # 检查readtimetype
        if status.get('readtimetype') == 'adMblog':
            return True
        
        return False
    
    def _parse_single_status(self, status: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """解析单条动态"""
        try:
            # 检查是否为转发动态
            is_retweet = 'retweeted_status' in status
            
            # 如果是转发，获取原始内容
            original_status = status['retweeted_status'] if is_retweet else status
            
            # 基本信息
            basic_info = self._parse_basic_info(status, is_retweet)
            
            # 用户信息
            user_info = self._parse_user_info(status.get('user', {}))
            
            # 互动数据
            interaction_data = self._parse_interaction_data(status)
            
            # 媒体内容
            media_content = self._parse_media_content(original_status)
            
            # 如果是转发，解析原作者信息
            original_user_info = None
            if is_retweet:
                original_user_info = self._parse_user_info(original_status.get('user', {}))
                basic_info['original_text_raw'] = original_status.get('text_raw', '')
            
            return {
                **basic_info,
                'user': user_info,
                'interaction': interaction_data,
                'media': media_content,
                'original_user': original_user_info
            }
            
        except Exception as e:
            return None
    
    def _parse_basic_info(self, status: Dict[str, Any], is_retweet: bool) -> Dict[str, Any]:
        """解析基本信息"""
        return {
            'id': status.get('idstr', status.get('id')),
            'text_raw': status.get('text_raw', ''),
            'created_at': self._parse_time(status.get('created_at')),
            'source': self._clean_source(status.get('source', '')),
            'is_retweet': is_retweet
        }
    
    def _parse_user_info(self, user: Dict[str, Any]) -> Dict[str, Any]:
        """解析用户信息"""
        return {
            'screen_name': user.get('screen_name', ''),
            'user_id': user.get('idstr', user.get('id', '')),
            'profile_image_url': user.get('profile_image_url', ''),
            'followers_count': user.get('followers_count', 0),
            'verified': user.get('verified', False),
            'description': user.get('description', '')
        }
    
    def _parse_interaction_data(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """解析互动数据"""
        return {
            'reposts_count': status.get('reposts_count', 0),
            'comments_count': status.get('comments_count', 0),
            'attitudes_count': status.get('attitudes_count', 0)
        }
    
    def _parse_media_content(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """解析媒体内容（图片/视频）"""
        media_content = {
            'images': [],
            'videos': []
        }
        
        # 获取图片信息
        pic_ids = status.get('pic_ids', [])
        pic_infos = status.get('pic_infos', {})
        
        for pic_id in pic_ids:
            if pic_id in pic_infos:
                pic_info = pic_infos[pic_id]
                
                # 获取图片URL（优先large，其次original）
                image_url = self._get_best_image_url(pic_info)
                
                if image_url:
                    image_data = {
                        'pic_id': pic_id,
                        'url': image_url,
                        'width': pic_info.get('large', {}).get('geo', {}).get('width', 0),
                        'height': pic_info.get('large', {}).get('geo', {}).get('height', 0)
                    }
                    media_content['images'].append(image_data)
                
                # 检查是否有视频（Live Photo等）
                if 'video' in pic_info:
                    video_url = pic_info['video']
                    video_data = {
                        'pic_id': pic_id,
                        'url': video_url,
                        'type': 'live_photo'
                    }
                    media_content['videos'].append(video_data)
        
        return media_content
    
    def _get_best_image_url(self, pic_info: Dict[str, Any]) -> Optional[str]:
        """获取最佳质量的图片URL"""
        # 优先级: original > large > bmiddle > small
        for size in ['original', 'large', 'bmiddle', 'small']:
            if size in pic_info and 'url' in pic_info[size]:
                return pic_info[size]['url']
        return None
    
    def _parse_time(self, time_str: Optional[str]) -> str:
        """解析时间格式"""
        try:
            if time_str:
                # 微博时间格式: "Wed Jan 09 12:34:56 +0800 2025"
                dt = datetime.strptime(time_str, "%a %b %d %H:%M:%S %z %Y")
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            pass
        return time_str or ""
    
    def _clean_source(self, source: str) -> str:
        """清理发布来源信息"""
        # 移除HTML标签
        clean_source = re.sub(r'<[^>]+>', '', source)
        return clean_source.strip()
    
    def get_summary_stats(self, parsed_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """获取数据统计摘要"""
        if not parsed_data:
            return {}
        
        total_posts = len(parsed_data)
        retweet_count = sum(1 for post in parsed_data if post['is_retweet'])
        original_count = total_posts - retweet_count
        
        total_images = sum(len(post['media']['images']) for post in parsed_data)
        total_videos = sum(len(post['media']['videos']) for post in parsed_data)
        
        total_interactions = sum(
            post['interaction']['reposts_count'] + 
            post['interaction']['comments_count'] + 
            post['interaction']['attitudes_count']
            for post in parsed_data
        )
        
        return {
            'total_posts': total_posts,
            'original_posts': original_count,
            'retweet_posts': retweet_count,
            'total_images': total_images,
            'total_videos': total_videos,
            'total_interactions': total_interactions,
            'avg_interactions_per_post': total_interactions / total_posts if total_posts > 0 else 0
        }
    
    def print_summary(self, parsed_data: List[Dict[str, Any]]):
        """打印数据摘要"""
        pass
    
    def download_and_convert_image(self, image_url: str) -> Optional[Dict[str, str]]:
        """下载图片并转换为base64格式"""
        if not Image:
            return None
            
        try:
            # 下载图片
            response = requests.get(image_url, timeout=10)
            if response.status_code != 200:
                return None
            
            image_data = response.content
            
            # 检查图片大小（不超过2M）
            if len(image_data) > 2 * 1024 * 1024:
                # 压缩图片
                image = Image.open(BytesIO(image_data))
                # 调整图片尺寸
                image.thumbnail((800, 600), Image.Resampling.LANCZOS)
                
                # 重新编码
                buffer = BytesIO()
                image_format = image.format if image.format else 'JPEG'
                image.save(buffer, format=image_format, quality=85)
                image_data = buffer.getvalue()
            
            # 计算MD5
            md5_hash = hashlib.md5(image_data).hexdigest()
            
            # 转换为base64
            base64_data = base64.b64encode(image_data).decode('utf-8')
            
            return {
                'base64': base64_data,
                'md5': md5_hash
            }
        
        except Exception as e:
            return None
    
    def send_wechat_markdown(self, markdown_content: str) -> bool:
        """发送markdown格式消息到企业微信"""
        if not self.webhook_url:
            return False
        
        try:
            data = {
                "msgtype": "markdown",
                "markdown": {
                    "content": markdown_content
                }
            }
            
            response = requests.post(self.webhook_url, json=data, timeout=10)
            return response.status_code == 200
        
        except Exception as e:
            return False
    
    def send_wechat_text(self, text: str) -> bool:
        """发送纯文本消息到企业微信"""
        if not self.webhook_url:
            return False
        
        try:
            data = {
                "msgtype": "text",
                "text": {
                    "content": text
                }
            }
            
            response = requests.post(self.webhook_url, json=data, timeout=10)
            return response.status_code == 200
        
        except Exception as e:
            return False
    
    def send_wechat_image(self, image_info: Dict[str, str]) -> bool:
        """发送图片消息到企业微信"""
        if not self.webhook_url:
            return False
        
        try:
            data = {
                "msgtype": "image",
                "image": {
                    "base64": image_info['base64'],
                    "md5": image_info['md5']
                }
            }
            
            response = requests.post(self.webhook_url, json=data, timeout=10)
            return response.status_code == 200
        
        except Exception as e:
            return False
    
    def format_post_content_markdown(self, status: Dict[str, Any]) -> str:
        """格式化动态内容为markdown格式"""
        user_name = status['user']['screen_name']
        content = status['text_raw']
        created_time = status['created_at']
        post_id = status['id']
        
        # 根据内容长度决定是否截断
        max_length = 500
        if len(content) > max_length:
            content = content[:max_length] + "..."
        
        # 使用颜色和格式化
        formatted_text = f"## <font color=\"info\">📝 微博动态</font>\n\n"
        formatted_text += f"**<font color=\"comment\">👤 用户：</font>** {user_name}\n"
        formatted_text += f"**<font color=\"comment\">🕐 时间：</font>** {created_time}\n"
        formatted_text += f"**<font color=\"comment\">🆔 ID：</font>** {post_id}\n\n"
        
        # 添加内容
        formatted_text += f"### <font color=\"warning\">💬 内容：</font>\n"
        formatted_text += f"> {content}\n\n"
        
        # 添加媒体信息
        if status['media']['images']:
            image_count = len(status['media']['images'])
            formatted_text += f"**<font color=\"info\">🖼️ 图片：</font>** {image_count}张\n"
        
        if status['media']['videos']:
            video_count = len(status['media']['videos'])
            formatted_text += f"**<font color=\"info\">🎥 视频：</font>** {video_count}个\n"
        
        formatted_text += "\n---\n"
        
        return formatted_text
    
    def format_post_content(self, status: Dict[str, Any]) -> str:
        """格式化动态内容为纯文本"""
        user_name = status['user']['screen_name']
        content = status['text_raw']
        created_time = status['created_at']
        
        # 根据内容长度决定是否截断
        max_length = 300
        if len(content) > max_length:
            content = content[:max_length] + "..."
        
        formatted_text = f"📝 微博动态\n\n"
        formatted_text += f"👤 用户：{user_name}\n"
        formatted_text += f"🕐 时间：{created_time}\n\n"
        formatted_text += f"💬 内容：\n{content}"
        
        return formatted_text
    
    def send_post_notifications(self, parsed_data: List[Dict[str, Any]], max_posts: int = 20) -> int:
        """发送动态通知到企业微信（只使用纯文本格式）"""
        if not self.webhook_url:
            return 0
        
        sent_count = 0
        success_count = 0
        
        # 增加发送数量
        posts_to_send = parsed_data[:max_posts]
        
        print(f"\n📨 开始发送 {len(posts_to_send)} 条动态到企业微信...")
        
        for index, post in enumerate(posts_to_send, 1):
            try:
                print(f"\n🔄 正在发送第 {index} 条动态...")
                
                # 直接使用纯文本格式
                text_content = self.format_post_content(post)
                success = False
                
                # 重试机制（最多3次）
                for retry in range(3):
                    if self.send_wechat_text(text_content):
                        print(f"   ✅ 纯文本格式发送成功")
                        success = True
                        break
                    elif retry < 2:  # 不是最后一次重试
                        print(f"   ⚠️ 第{retry + 1}次重试...")
                        import time
                        time.sleep(1)
                
                if not success:
                    print(f"   ❌ 文本发送失败")
                else:
                    success_count += 1
                    sent_count += 1
                
                # 发送图片（如果有）
                if post['media']['images']:
                    print(f"   🖼️ 检测到 {len(post['media']['images'])} 张图片，发送第一张...")
                    first_image = post['media']['images'][0]
                    image_info = self.download_and_convert_image(first_image['url'])
                    
                    if image_info:
                        if self.send_wechat_image(image_info):
                            print(f"   ✅ 图片发送成功")
                        else:
                            print(f"   ❌ 图片发送失败")
                    else:
                        print(f"   ⚠️ 图片处理失败")
                
                # 快速发送，不等待
                # 注释：移除3秒延迟以提高发送速度
                
            except Exception as e:
                print(f"   ❌ 发送过程出错: {e}")
                continue
        
        print(f"\n🎉 发送完成！成功: {success_count}/{len(posts_to_send)} 条")
        return sent_count
    
    def print_detailed_data(self, parsed_data: List[Dict[str, Any]]):
        """打印详细数据"""
        print(f"\n📋 详细数据展示")
        print("-"*80)
        
        for i, status in enumerate(parsed_data, 1):
            print(f"\n🔹 动态 {i}")
            print(f"   ID: {status['id']}")
            print(f"   👤 用户: {status['user']['screen_name']}")
            # print(f"   📱 来源: {status['source']}")
            print(f"   🕐 时间: {status['created_at']}")
            # print(f"   🔄 转发: {'是' if status['is_retweet'] else '否'}")
            
            # 显示内容预览
            content = status['text_raw'][:100]
            if len(status['text_raw']) > 100:
                content += "..."
            print(f"   📄 内容: {content}")
            
            # 转发信息
            if status['is_retweet'] and status['original_user']:
                print(f"   👤 原作者: {status['original_user']['screen_name']}")
                original_content = status.get('original_text_raw', '')[:50]
                if len(status.get('original_text_raw', '')) > 50:
                    original_content += "..."
                print(f"   📄 原内容: {original_content}")
            
            # 互动数据
            # interaction = status['interaction']
            # print(f"   💬 互动: 👍{interaction['attitudes_count']} "
            #       f"💬{interaction['comments_count']} 🔄{interaction['reposts_count']}")
            
            # 媒体信息
            # if status['media']['images']:
            #     print(f"   🖼️ 图片: {len(status['media']['images'])}张")
            # if status['media']['videos']:
            #     print(f"   🎥 视频: {len(status['media']['videos'])}个")


def _get_post_datetime(created_at_str: Optional[str]) -> Optional[datetime]:
    """获取动态的发布时间（带时区信息）"""
    if not created_at_str:
        return None
    
    try:
        # 微博时间格式: "Sun Aug 24 12:34:56 +0800 2025" 或 "Wed Jan 09 12:34:56 +0800 2025"
        dt = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
        return dt
    except ValueError:
        # 如果第一种格式失败，尝试其他常见格式
        try:
            # 尝试不同的时间格式
            dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
            return dt
        except ValueError:
            print(f"⚠️ 无法解析时间格式: {created_at_str}")
            return None
    except Exception as e:
        print(f"⚠️ 时间解析错误: {e} - {created_at_str}")
        return None


def _get_post_date(created_at_str: Optional[str]) -> Optional[Any]:
    """获取动态的发布日期"""
    if not created_at_str:
        return None
    
    try:
        # 微博时间格式: "Sun Aug 24 12:34:56 +0800 2025" 或 "Wed Jan 09 12:34:56 +0800 2025"
        dt = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
        return dt.date()
    except ValueError:
        # 如果第一种格式失败，尝试其他常见格式
        try:
            # 尝试不同的时间格式
            dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
            return dt.date()
        except ValueError:
            print(f"⚠️ 无法解析时间格式: {created_at_str}")
            return None
    except Exception as e:
        print(f"⚠️ 时间解析错误: {e} - {created_at_str}")
        return None


def _is_promotion_simple(status: Dict[str, Any]) -> bool:
    """简化的广告检测函数，用于时间过滤阶段"""
    # 检查promotion字段
    if 'promotion' in status:
        return True
    
    # 检查content_auth_info
    content_auth_info = status.get('content_auth_info', {})
    if content_auth_info.get('content_auth_title') == '荐读':
        return True
    
    # 检查readtimetype
    if status.get('readtimetype') == 'adMblog':
        return True
    
    # 检查用户名称（特殊广告账号）
    user_name = status.get('user', {}).get('screen_name', '')
    if '新浪航天' in user_name:
        return True
    
    return False


def check_cookie_status(weibo_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    智能检查Cookie状态
    如果当前时间窗口没有数据，尝试检测上一个时间段是否有数据
    只有在确认无法获取任何历史数据时才判定Cookie过期
    """
    if weibo_data.get('statuses'):
        # 当前时间窗口有数据，Cookie正常
        return {
            'is_expired': False,
            'reason': '当前时间窗口内有动态数据'
        }
    
    print(f"\n🔍 当前时间窗口无数据，检测上一时间段验证Cookie状态...")
    
    # 尝试获取上一个时间段的数据进行验证
    try:
        verification_data = fetch_previous_period_data()
        
        if verification_data.get('statuses'):
            # 能获取到上一时间段的数据，说明Cookie正常，只是当前时间段确实没有动态
            return {
                'is_expired': False,
                'reason': f'当前时间窗口无动态，但上一时间段有 {len(verification_data["statuses"])} 条动态，Cookie状态正常'
            }
        else:
            # 连上一时间段的数据都获取不到，Cookie可能过期
            return {
                'is_expired': True,
                'reason': '当前和上一时间段都无法获取数据，Cookie可能已过期'
            }
    
    except Exception as e:
        print(f"⚠️ 验证检测失败: {e}")
        # 验证失败时保守处理，不发送Cookie过期提醒
        return {
            'is_expired': False,
            'reason': '验证检测失败，为避免误判暂不认定Cookie过期'
        }


def fetch_previous_period_data() -> Dict[str, Any]:
    """
    获取上一个时间段的数据用于验证Cookie状态
    只获取少量数据进行验证，不做完整解析
    """
    url = "https://weibo.com/ajax/feed/groupstimeline"
    
    base_params = {
        'list_id': '5159683220312291',
        'refresh': '4',
        'fast_refresh': '1',
        'count': '10'  # 只获取少量数据用于验证
    }
    
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        'client-version': 'v2.47.103',
        'priority': 'u=1, i',
        'referer': 'https://weibo.com/mygroups?gid=5159683220312291',
        'sec-ch-ua': '"Not;A=Brand";v="99", "Microsoft Edge";v="139", "Chromium";v="139"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'server-version': 'v2025.08.21.1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0',
        'x-requested-with': 'XMLHttpRequest',
    }
    
    # 从环境变量获取SUB Cookie
    sub_cookie = os.getenv('WEIBO_SUB_COOKIE')
    if not sub_cookie:
        return {"statuses": []}
    
    cookies = {
        'SUB': sub_cookie,
    }
    
    # 计算上一个时间段的时间窗口
    from datetime import datetime, timedelta
    import pytz
    
    beijing_tz = pytz.timezone('Asia/Shanghai')
    now_beijing = datetime.now(beijing_tz)
    current_hour = now_beijing.hour
    
    # 根据当前时间确定上一个时间段
    if current_hour >= 11 and current_hour < 15:  # 当前12:00运行
        # 检测昨天19:00-22:00时间段
        start_time = (now_beijing - timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
        end_time = (now_beijing - timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
        period_desc = "昨天19:00-22:00"
    elif current_hour >= 15 and current_hour < 17:  # 当前16:00运行
        # 检测昨天22:00到今天12:00时间段
        start_time = (now_beijing - timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
        end_time = now_beijing.replace(hour=12, minute=0, second=0, microsecond=0)
        period_desc = "昨天22:00-今天12:00"
    elif current_hour >= 17 and current_hour < 18:  # 当前18:00运行
        # 检测今天12:00-16:00时间段
        start_time = now_beijing.replace(hour=12, minute=0, second=0, microsecond=0)
        end_time = now_beijing.replace(hour=16, minute=0, second=0, microsecond=0)
        period_desc = "今天12:00-16:00"
    elif current_hour >= 18 and current_hour < 21:  # 当前19:00运行
        # 检测今天16:00-18:00时间段
        start_time = now_beijing.replace(hour=16, minute=0, second=0, microsecond=0)
        end_time = now_beijing.replace(hour=18, minute=0, second=0, microsecond=0)
        period_desc = "今天16:00-18:00"
    elif current_hour >= 21 or current_hour < 2:  # 当前22:00运行
        # 检测今天18:00-19:00时间段
        if current_hour >= 21:
            start_time = now_beijing.replace(hour=18, minute=0, second=0, microsecond=0)
            end_time = now_beijing.replace(hour=19, minute=0, second=0, microsecond=0)
        else:  # 跨日运行的情况
            start_time = (now_beijing - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
            end_time = (now_beijing - timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
        period_desc = "今天18:00-19:00"
    else:
        # 默认检测前6小时
        start_time = now_beijing - timedelta(hours=12)
        end_time = now_beijing - timedelta(hours=6)
        period_desc = "前6-12小时"
    
    print(f"   📋 验证时间段: {period_desc}")
    
    verification_statuses = []
    
    try:
        # 只请求一页进行验证
        response = requests.get(url, params=base_params, headers=headers, cookies=cookies, timeout=10)
        
        if response.status_code != 200:
            print(f"   ❌ 验证请求失败，状态码: {response.status_code}")
            return {"statuses": []}
        
        data = response.json()
        statuses = data.get('statuses', [])
        
        if not statuses:
            print(f"   📭 验证页面无动态")
            return {"statuses": []}
        
        print(f"   📊 验证获取到 {len(statuses)} 条原始动态")
        
        # 检查时间窗口内的动态
        for status in statuses:
            post_datetime = _get_post_datetime(status.get('created_at'))
            
            if post_datetime is None:
                continue
            
            # 跳过广告
            if _is_promotion_simple(status):
                continue
            
            # 转换为北京时间进行比较
            if post_datetime.tzinfo is None:
                post_datetime = beijing_tz.localize(post_datetime)
            else:
                post_datetime = post_datetime.astimezone(beijing_tz)
            
            if start_time <= post_datetime <= end_time:
                verification_statuses.append(status)
                if len(verification_statuses) >= 3:  # 找到3条就够了
                    break
        
        print(f"   ✅ 验证时间段内找到 {len(verification_statuses)} 条有效动态")
        
    except Exception as e:
        print(f"   ❌ 验证检测出错: {e}")
        return {"statuses": []}
    
    return {"statuses": verification_statuses}


def fetch_weibo_data() -> Dict[str, Any]:
    """智能分页抓取指定时间窗口的微博数据"""
    url = "https://weibo.com/ajax/feed/groupstimeline"
    
    base_params = {
        'list_id': '5159683220312291',
        'refresh': '4',
        'fast_refresh': '1',
        'count': '25'
    }
    
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        'client-version': 'v2.47.103',
        'priority': 'u=1, i',
        'referer': 'https://weibo.com/mygroups?gid=5159683220312291',
        'sec-ch-ua': '"Not;A=Brand";v="99", "Microsoft Edge";v="139", "Chromium";v="139"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'server-version': 'v2025.08.21.1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0',
        'x-requested-with': 'XMLHttpRequest',
    }
    
    # 从环境变量获取SUB Cookie
    sub_cookie = os.getenv('WEIBO_SUB_COOKIE')
    if not sub_cookie:
        print("❌ 错误：未设置 WEIBO_SUB_COOKIE 环境变量")
        print("🛠️ 请在 GitHub Actions Secrets 中设置 WEIBO_SUB_COOKIE")
        return {"statuses": []}
    
    cookies = {
        'SUB': sub_cookie,
    }
    
    # 计算时间窗口
    from datetime import datetime, timedelta
    import pytz
    
    # 获取北京时间
    beijing_tz = pytz.timezone('Asia/Shanghai')
    now_beijing = datetime.now(beijing_tz)
    current_hour = now_beijing.hour
    
    # 根据当前运行时间确定时间窗口
    if current_hour >= 11 and current_hour < 15:  # 12:00运行 (11:45-14:59)
        # 昨天22:00 到 今天12:00
        start_time = (now_beijing - timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
        end_time = now_beijing.replace(hour=12, minute=0, second=0, microsecond=0)
        window_desc = "昨天22:00 → 今天12:00"
    elif current_hour >= 15 and current_hour < 17:  # 16:00运行 (15:45-16:59)
        # 今天12:00 到 今天16:00
        start_time = now_beijing.replace(hour=12, minute=0, second=0, microsecond=0)
        end_time = now_beijing.replace(hour=16, minute=0, second=0, microsecond=0)
        window_desc = "今天12:00 → 今天16:00"
    elif current_hour >= 17 and current_hour < 18:  # 18:00运行 (17:45-17:59)
        # 今天16:00 到 今天18:00
        start_time = now_beijing.replace(hour=16, minute=0, second=0, microsecond=0)
        end_time = now_beijing.replace(hour=18, minute=0, second=0, microsecond=0)
        window_desc = "今天16:00 → 今天18:00"
    elif current_hour >= 18 and current_hour < 21:  # 19:00运行 (18:45-20:59)
        # 今天18:00 到 今天19:00
        start_time = now_beijing.replace(hour=18, minute=0, second=0, microsecond=0)
        end_time = now_beijing.replace(hour=19, minute=0, second=0, microsecond=0)
        window_desc = "今天18:00 → 今天19:00"
    elif current_hour >= 21 or current_hour < 2:  # 22:00运行 (21:45-01:59)
        # 今天19:00 到 今天22:00
        if current_hour >= 21:
            start_time = now_beijing.replace(hour=19, minute=0, second=0, microsecond=0)
            end_time = now_beijing.replace(hour=22, minute=0, second=0, microsecond=0)
        else:  # 跨日运行的情况
            start_time = (now_beijing - timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
            end_time = (now_beijing - timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
        window_desc = "今天19:00 → 今天22:00"
    else:
        # 默认情况：最近6小时
        start_time = now_beijing - timedelta(hours=6)
        end_time = now_beijing
        window_desc = "最近6小时"
    
    print(f"\n📅 时间窗口: {window_desc}")
    print(f"📅 开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    all_window_statuses = []  # 时间窗口内的动态
    all_raw_statuses = []  # 所有原始动态
    max_id = None
    page_count = 0
    
    try:
        while True:
            page_count += 1
            print(f"\n📄 正在抓取第 {page_count} 页...")
            
            # 构建请求参数
            params = base_params.copy()
            if max_id:
                params['max_id'] = max_id
                print(f"   🔗 使用 max_id: {max_id}")
            
            # 发送请求
            response = requests.get(url, params=params, headers=headers, cookies=cookies, timeout=10)
            
            if response.status_code != 200:
                print(f"   ❌ 请求失败，状态码: {response.status_code}")
                break
            
            data = response.json()
            statuses = data.get('statuses', [])
            
            if not statuses:
                print(f"   📭 本页没有动态")
                break
            
            print(f"   📊 本页获取到 {len(statuses)} 条原始动态")
            all_raw_statuses.extend(statuses)
            
            # 检查每条动态的发布时间
            found_old_post = False
            page_window_count = 0
            
            for i, status in enumerate(statuses):
                post_datetime = _get_post_datetime(status.get('created_at'))
                user_name = status.get('user', {}).get('screen_name', 'Unknown')
                created_at_str = status.get('created_at', '')
                
                print(f"   🔍 检查动态 {i+1}: {user_name} - {created_at_str}")
                
                if post_datetime is None:
                    print(f"      ⚠️ 无法解析时间，跳过")
                    continue
                
                print(f"      📅 解析时间: {post_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
                
                # 首先检查是否为广告
                if _is_promotion_simple(status):
                    print(f"      🚫 检测到广告内容，跳过（不影响时间判断）")
                    continue
                
                # 转换为北京时间进行比较
                if post_datetime.tzinfo is None:
                    # 如果没有时区信息，假设为北京时间
                    post_datetime = beijing_tz.localize(post_datetime)
                else:
                    # 转换到北京时间
                    post_datetime = post_datetime.astimezone(beijing_tz)
                    
                if start_time <= post_datetime <= end_time:
                    # 在时间窗口内的动态，添加到结果中
                    all_window_statuses.append(status)
                    page_window_count += 1
                    print(f"      ✅ 时间窗口内的动态，已保存")
                elif post_datetime < start_time:
                    # 发现早于时间窗口的非广告动态，停止抓取
                    print(f"      ⏹️ 发现早于时间窗口的非广告动态，停止抓取")
                    found_old_post = True
                    break
                else:
                    print(f"      🕰 未来的动态，跳过")
            
            print(f"   📈 本页时间窗口内动态: {page_window_count} 条")
            print(f"   📋 累计时间窗口内动态: {len(all_window_statuses)} 条")
            
            if found_old_post:
                print(f"   ⚙️ 遇到早期动态，结束分页")
                break
            
            # 获取下一页的max_id
            max_id = data.get('max_id_str')
            if not max_id:
                print(f"   📭 没有更多页面")
                break
    
    except requests.exceptions.RequestException as e:
        print(f"❌ 网络请求失败: {e}")
    except json.JSONDecodeError as e:
        print(f"❌ JSON解析失败: {e}")
    except Exception as e:
        print(f"❌ 抓取过程出错: {e}")
    
    print(f"\n🎉 抓取完成！")
    print(f"📄 总共抓取 {page_count} 页")
    print(f"📀 原始动态总数: {len(all_raw_statuses)} 条")
    print(f"🎯 时间窗口内动态: {len(all_window_statuses)} 条")
    
    return {"statuses": all_window_statuses}


def send_launch_notifications(parser: WeiboDataParser, launch_posts: List[Dict[str, Any]]) -> int:
    """发送上新预告通知到企业微信（包含文本+图片）"""
    if not parser.webhook_url or not launch_posts:
        return 0
    
    sent_count = 0
    
    for i, launch_info in enumerate(launch_posts, 1):
        try:
            print(f"\n🔄 正在发送第 {i} 条上新预告...")
            
            # 格式化上新预告内容
            formatted_content = format_launch_notification(launch_info)
            
            # 发送文本消息
            success = parser.send_wechat_text(formatted_content)
            
            if success:
                print(f"   ✅ 上新预告文本发送成功")
                sent_count += 1
                
                # 短暂延迟确保文本消息先到达
                import time
                time.sleep(1)
                
                # 发送图片（如果有）
                media_info = launch_info.get('media', {})
                if media_info and media_info.get('images'):
                    print(f"   🖼️ 检测到 {len(media_info['images'])} 张图片，发送第一张...")
                    first_image = media_info['images'][0]
                    image_info = parser.download_and_convert_image(first_image['url'])
                    
                    if image_info:
                        if parser.send_wechat_image(image_info):
                            print(f"   ✅ 上新预告图片发送成功")
                        else:
                            print(f"   ❌ 上新预告图片发送失败")
                    else:
                        print(f"   ⚠️ 上新预告图片处理失败")
                    
                    # 在下一条消息之前添加额外延迟
                    if i < len(launch_posts):  # 不是最后一条
                        time.sleep(2)
                        print(f"   🕰️ 等待 2 秒后发送下一条...")
                else:
                    print(f"   📝 该上新预告没有图片")
                    
                    # 没有图片时也需要延迟以保证顺序
                    if i < len(launch_posts):  # 不是最后一条
                        time.sleep(1)
                        print(f"   🕰️ 等待 1 秒后发送下一条...")
            else:
                print(f"   ❌ 上新预告文本发送失败")
                
        except Exception as e:
            print(f"   ❌ 发送过程出错: {e}")
            continue
    
    return sent_count


def format_launch_notification(launch_info: Dict[str, Any]) -> str:
    """格式化上新预告消息"""
    from datetime import datetime
    
    user_name = launch_info['user_name']
    launch_type = launch_info['launch_type']
    content = launch_info['content']
    extracted_time = launch_info['extracted_time']
    created_time = launch_info['created_time']  # 商家实际发帖时间
    
    # 根据内容长度决定是否截断
    max_length = 400
    if len(content) > max_length:
        content = content[:max_length] + "..."
    
    # 从商家发帖时间提取时分信息
    try:
        # 如果是已解析的格式（YYYY-MM-DD HH:MM:SS）
        if '-' in created_time and ':' in created_time:
            post_time = datetime.strptime(created_time, "%Y-%m-%d %H:%M:%S")
        else:
            # 如果是原始格式（Wed Jan 09 12:34:56 +0800 2025）
            post_time = datetime.strptime(created_time, "%a %b %d %H:%M:%S %z %Y")
        
        post_time_str = post_time.strftime('%H:%M')
    except Exception:
        # 如果解析失败，使用当前时间作为备选
        post_time_str = datetime.now().strftime('%H:%M')
    
    # 格式化消息
    formatted_text = f"🎆 上新预告提醒\n\n"
    formatted_text += f"👤 商家：{user_name}\n"
    formatted_text += f"📤 发帖时间：{post_time_str}\n"
    formatted_text += f"🏷️ 类型：{launch_type}\n"
    
    # 添加时间信息
    if extracted_time:
        formatted_text += f"⏰ 时间：{', '.join(extracted_time)}\n"
    
    formatted_text += f"\n📝 详细内容：\n{content}"
    
    return formatted_text


def main(webhook_url: Optional[str] = None, enable_push: bool = False):
    """主函数"""
    # 创建解析器实例
    parser = WeiboDataParser(webhook_url)
    
    # 直接从微博API获取真实数据
    weibo_data = fetch_weibo_data()
    
    # 检查Cookie是否过期（没有获取到任何动态数据）
    if not weibo_data.get('statuses'):
        print(f"\n⚠️ 未获取到动态数据，可能是Cookie过期")
        
        # 如果启用推送，发送Cookie更新提示
        if enable_push and webhook_url:
            cookie_update_message = (
                "⚠️ 微博数据抓取异常\n\n"
                "🔍 检测到问题：无法获取动态数据\n"
                "💡 可能原因：Cookie已过期\n\n"
                "🛠️ 解决方案：\n"
                "1. 登录微博网站\n"
                "2. 获取最新的Cookie信息\n"
                "3. 更新程序中的cookies配置\n\n"
                "📝 需要更新的字段：SUB(仅这一个)\n"
                "⏰ 请及时处理，以免影响数据抓取"
            )
            
            success = parser.send_wechat_text(cookie_update_message)
            if success:
                print(f"✅ 已发送Cookie更新提示到企业微信")
            else:
                print(f"❌ Cookie更新提示发送失败")
        
        return []
    
    # 解析数据
    parsed_statuses = parser.parse_weibo_response(weibo_data)
    
    # 识别上新/发售预告帖子
    launch_posts = parser.filter_launch_posts(parsed_statuses)
    
    # 显示详细数据
    parser.print_detailed_data(parsed_statuses)
    
    # 显示上新预告信息
    if launch_posts:
        print(f"\n\n YYS 识别到 {len(launch_posts)} 条上新/发售预告")
        print("="*80)
        
        for i, launch_info in enumerate(launch_posts, 1):
            print(f"\n🛍️ 上新预告 {i}")
            print(f"   ID: {launch_info['post_id']}")
            print(f"   👤 用户: {launch_info['user_name']}")
            print(f"   🕰️ 时间: {launch_info['created_time']}")
            print(f"   🏷️ 类型: {launch_info['launch_type']}")
            print(f"   ⏰ 时间关键词: {', '.join(launch_info['matched_time_triggers']) if launch_info['matched_time_triggers'] else '无'}")
            print(f"   🎨 动作关键词: {', '.join(launch_info['matched_action_triggers']) if launch_info['matched_action_triggers'] else '无'}")
            print(f"   📅 提取时间: {', '.join(launch_info['extracted_time']) if launch_info['extracted_time'] else '无'}")
            print(f"   📝 内容预览: {launch_info['product_info']}")
        
        # 生成JSON格式输出
        import json
        print(f"\n\n📊 JSON结构化输出:")
        print("="*80)
        print(json.dumps(launch_posts, ensure_ascii=False, indent=2))
        
        # 🎯 关键修改：只有识别到上新帖子时才推送到企业微信
        if enable_push and webhook_url:
            print(f"\n📨 开始推送 {len(launch_posts)} 条上新预告到企业微信...")
            sent_count = send_launch_notifications(parser, launch_posts)
            print(f"\n🎉 已发送 {sent_count} 条上新预告到企业微信")
    else:
        print(f"\n\n🚫 未识别到上新/发售预告帖子")
        if enable_push:
            print(f"💡 由于没有上新预告，跳过企业微信推送")
    
    # 返回解析后的数据供进一步处理
    return parsed_statuses


if __name__ == "__main__":
    import sys
    
    # 企业微信机器人 webhook URL
    webhook_url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=5a9b8214-4fae-4bbd-a147-9ecfe5eb6d90"
    enable_push = True
    
    # 检查命令行参数（允许覆盖默认URL）
    if len(sys.argv) > 1:
        if '--webhook' in sys.argv:
            webhook_index = sys.argv.index('--webhook')
            if webhook_index + 1 < len(sys.argv):
                webhook_url = sys.argv[webhook_index + 1]
                enable_push = True
        elif '--no-push' in sys.argv:
            enable_push = False
    
    # 也可以通过环境变量覆盖
    import os
    env_webhook = os.getenv('WECHAT_WEBHOOK_URL')
    if env_webhook:
        webhook_url = env_webhook
        enable_push = True
    
    try:
        result = main(webhook_url, enable_push)
    except Exception as e:
        pass
