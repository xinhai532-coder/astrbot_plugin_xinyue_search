#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
License 验证器 - Token 授权方式
使用 RSA 签名验证，绑定机器人 QQ 号
"""

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import json
import base64
from datetime import datetime
import os


class LicenseValidator:
    # 公钥内置在代码中（与PHP生成器配对）
    PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAyuSebUvt7VJmFXp/eMk7
suHFfd3jLYSodYeXgGxg8gDanDFO4qxQuv2OsK5BuxJ3osfEz58rcA070TDaa3yT
HD4RgewbveMj7mHD5UcCKDPZD1FLZeAzDFgzrXuQGYshb/pd/Ygu9ZlacoT2gFk3
J4Tnt3TJE2uDElzBQVaOla51DbXZrhNt+PlDgA5kRi4HN6b6c4/uGQLvSLdI272l
5PU+wJHYU+hh8z9vHqPFwp6UlGRNi+zBAMRv/VVB+hN3mGfEsE9MR5zAgr30KtCz
xWjUy7OWNIty0ga9+zM1B63v5BnM28LTDVokv0oiDqff0NmX7JGOu39j4io2mtjP
vQIDAQAB
-----END PUBLIC KEY-----"""
    
    # 试用模式配置
    TRIAL_DAYS = 1  # 试用天数
    TRIAL_FILE = '.trial_start'  # 试用开始时间记录文件
    
    def __init__(self, license_token):
        """
        初始化验证器
        
        参数:
            license_token: 授权 Token 字符串，None 表示试用模式
        """
        self.license_token = license_token
        self.license_data = None
        self.last_check_file = '.last_check'
        self.is_trial_mode = (license_token is None)
        
        # 如果不是试用模式，加载公钥
        if not self.is_trial_mode:
            if not isinstance(license_token, str):
                raise Exception("授权Token格式错误")
            
            try:
                self.public_key = serialization.load_pem_public_key(
                    self.PUBLIC_KEY_PEM,
                    backend=default_backend()
                )
            except Exception as e:
                raise Exception(f"公钥加载失败: {e}")
    
    def _check_time_manipulation(self):
        """检测时间篡改"""
        current_time = int(datetime.now().timestamp())
        
        # 读取上次验证时间
        if os.path.exists(self.last_check_file):
            try:
                with open(self.last_check_file, 'r') as f:
                    last_check = int(f.read().strip())
                
                # 如果当前时间比上次验证时间还早，说明时间被回调了
                if current_time < last_check - 300:  # 允许5分钟误差
                    return False, "检测到系统时间异常（时间倒退）"
            except:
                pass
        
        # 记录本次验证时间
        try:
            with open(self.last_check_file, 'w') as f:
                f.write(str(current_time))
        except:
            pass
        
        return True, "时间检查通过"
    
    def _load_license_package(self):
        """
        加载 License 数据包
        
        返回:
            dict: License 数据包 {'data': ..., 'signature': ...}
        """
        try:
            # Token 格式：base64(json({'data': ..., 'signature': ...}))
            license_json = base64.b64decode(self.license_token).decode()
            return json.loads(license_json)
        except Exception as e:
            raise Exception(f"Token 格式错误: {e}")
    
    def validate(self, bot_qq):
        """
        验证授权
        
        参数:
            bot_qq: 机器人QQ号
            
        返回:
            (bool, str): (是否有效, 消息)
        """
        try:
            # 1. 检查时间篡改
            time_ok, time_msg = self._check_time_manipulation()
            if not time_ok:
                return False, time_msg
            
            # 2. 加载 License 数据包
            try:
                license_package = self._load_license_package()
            except Exception as e:
                return False, f"授权Token无效: {str(e)}"
            
            # 3. 解码数据和签名
            try:
                license_json = base64.b64decode(license_package['data']).decode()
                signature = base64.b64decode(license_package['signature'])
            except Exception as e:
                return False, f"License 数据格式错误: {e}"
            
            # 4. 验证签名（防止篡改）
            try:
                self.public_key.verify(
                    signature,
                    license_json.encode(),
                    padding.PKCS1v15(),  # PHP openssl_sign 使用 PKCS1 padding
                    hashes.SHA256()
                )
            except Exception:
                return False, "授权签名验证失败（Token已被篡改或无效）"
            
            # 5. 解析数据
            self.license_data = json.loads(license_json)
            
            # 6. 验证 QQ 号
            if str(self.license_data['bot_qq']) != str(bot_qq):
                return False, f"授权不匹配（此Token绑定的QQ号: {self.license_data['bot_qq']}）"
            
            # 7. 检查签发时间不能在未来
            issued_timestamp = self.license_data.get('issued_timestamp', 0)
            current_timestamp = int(datetime.now().timestamp())
            
            if current_timestamp < issued_timestamp - 86400:  # 允许1天误差
                return False, "授权签发时间异常"
            
            # 8. 验证过期时间
            expire_timestamp = self.license_data['expire_timestamp']
            
            if current_timestamp > expire_timestamp:
                expire_time = self.license_data['expire_time']
                return False, f"授权已过期（到期时间: {expire_time}）"
            
            # 9. 验证成功
            days_left = (expire_timestamp - current_timestamp) // 86400
            
            if days_left > 3650:  # 永久授权
                return True, "授权验证成功（永久授权）"
            else:
                return True, f"授权验证成功，剩余 {days_left} 天"
            
        except json.JSONDecodeError:
            return False, "授权数据格式错误"
        except KeyError as e:
            return False, f"授权数据不完整: {e}"
        except Exception as e:
            return False, f"授权验证失败: {str(e)}"
    
    def get_license_info(self):
        """
        获取 License 信息
        
        返回:
            dict: License 信息字典，如果未验证则返回 None
        """
        if not self.license_data:
            return None
        
        return {
            'bot_qq': self.license_data['bot_qq'],
            'plan_type': self.license_data['plan_type'],
            'expire_time': self.license_data['expire_time'],
            'user_info': self.license_data.get('user_info', ''),
            'issued_at': self.license_data.get('issued_at', ''),
            'version': self.license_data.get('version', '1.0')
        }
    
    def _get_trial_file(self, bot_qq):
        """
        生成隐藏的试用期文件名
        使用哈希算法隐藏真实用途，增加破解难度
        """
        import hashlib
        # 密钥盐值（可以修改为你自己的密钥）
        salt = "xinyue_search_plugin_secret_salt_2024"
        # 多重哈希增加安全性
        data = f"{bot_qq}{salt}{self.__class__.__name__}"
        hash1 = hashlib.sha256(data.encode()).hexdigest()
        hash2 = hashlib.md5(hash1.encode()).hexdigest()
        # 文件名伪装成系统缓存文件
        return f'.sys_{hash2[:16]}.cache'
    
    def validate_trial(self, bot_qq):
        """
        验证试用模式
        
        参数:
            bot_qq: 机器人QQ号（用于记录试用）
        
        返回:
            (bool, str): (是否有效, 消息)
        """
        try:
            if not bot_qq:
                return False, "机器人QQ号不能为空"
            
            current_time = int(datetime.now().timestamp())
            
            # 使用隐藏的文件名
            trial_file = self._get_trial_file(bot_qq)
            
            # 检查试用开始时间文件是否存在
            if os.path.exists(trial_file):
                try:
                    with open(trial_file, 'r') as f:
                        trial_start = int(f.read().strip())
                    
                    # 计算已使用天数
                    days_used = (current_time - trial_start) // 86400
                    days_left = self.TRIAL_DAYS - days_used
                    
                    if days_left <= 0:
                        return False, f"试用期已结束（试用期: {self.TRIAL_DAYS}天）"
                    else:
                        hours_left = ((self.TRIAL_DAYS * 86400) - (current_time - trial_start)) // 3600
                        return True, f"试用模式（QQ: {bot_qq}，剩余: {hours_left}小时）"
                        
                except Exception as e:
                    # 文件损坏，重新创建
                    pass
            
            # 首次使用，创建试用开始时间文件
            try:
                with open(trial_file, 'w') as f:
                    f.write(str(current_time))
                
                return True, f"试用模式已激活（QQ: {bot_qq}，有效期: {self.TRIAL_DAYS}天）"
            except Exception as e:
                return False, f"试用模式初始化失败: {e}"
                
        except Exception as e:
            return False, f"试用验证失败: {e}"
