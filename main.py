#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
=========================================================
团子素材解析工具 - 精简版 (仅图片去水印)
=========================================================

项目简介:
    这是一个豆包图片去水印工具，核心功能是从豆包分享链接中提取图片并去除水印。
    
核心流程:
    1. 用户输入豆包分享链接
    2. 服务器获取页面内容
    3. 解析页面中的图片数据
    4. 提取图片URL并进行解码（ARM64模拟执行）
    5. 返回无水印图片链接给前端

技术栈:
    - Python 3.13 (嵌入式运行时)
    - HTTP 服务器 (内置 BaseHTTPRequestHandler)
    - 正则表达式解析页面
    - HTML/JSON数据提取
    - Unicorn Engine (ARM64模拟执行)

项目结构:
    main.py                    - 主程序入口，包含HTTP服务器和解析逻辑
    decode_worker.py           - 核心解码引擎（ARM64模拟执行）
    app/static/index.html      - 前端页面
    app/static/assets/app-runtime.js - 前端逻辑
    app/vendor/libvideodec.so  - ARM64解码算法共享库

运行方式:
    python main.py --port 8081
"""

import re
import json
import html
import sys
import os
import urllib.request
import urllib.parse
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# 导入核心解码引擎
from decode_worker import decode_main_url

# =========================================================================
# 全局配置
# =========================================================================

# 项目根目录
PROJECT_ROOT = Path(__file__).parent

# 静态文件目录
STATIC_DIR = PROJECT_ROOT / "app" / "static"

# 豆包域名列表
DOUBAO_DOMAINS = [
    "doubao.com",
    "qianwen.com",
]

# URL正则表达式 - 匹配完整的https链接
URL_PATTERN = re.compile(r'https?://[^\s"\'<>\(\)]+')

# 尾部标点符号 - 用于清理URL末尾的标点
TRAILING_PUNCTUATION = re.compile(r'[.,;:!?)>\]}。，；：！、）】》]+$')

# =========================================================================
# 步骤1: URL提取与类型检测
# =========================================================================

def extract_url(text):
    """
    从文本中提取URL
    
    参数:
        text (str): 用户输入的文本内容
        
    返回:
        str: 提取到的URL，如果未找到则返回空字符串
    """
    match = URL_PATTERN.search(text)
    if match:
        url = match.group(0)
        return TRAILING_PUNCTUATION.sub("", url)
    return ""


def detect_link_type(url):
    """
    检测链接类型
    
    参数:
        url (str): 待检测的URL
        
    返回:
        dict: 包含类型和标签的字典
            - type: "image" | "unsupported"
            - label: 显示给用户的标签文本
    """
    if not url:
        return {"type": "empty", "label": "等待输入"}
    
    if any(domain in url for domain in DOUBAO_DOMAINS):
        if "/thread/" in url or "/chat/" in url or "/share/" in url:
            return {"type": "image", "label": "等待解析"}
    
    return {"type": "unsupported", "label": "暂不支持"}

# =========================================================================
# 步骤2: 获取豆包页面内容
# =========================================================================

def fetch_doubao_page(url):
    """
    获取豆包页面的HTML内容
    
    参数:
        url (str): 豆包页面URL
        
    返回:
        str: 页面HTML内容，如果获取失败返回空字符串
        
    注意:
        timeout参数必须传递给urlopen()函数，而不是Request()对象
        Request.__init__()不支持timeout参数
    """
    try:
        import gzip
        import io
        import zlib
        
        # 创建请求对象（不包含timeout参数）
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Referer": "https://www.doubao.com/",
            }
        )
        
        # 发送请求（timeout参数传递给urlopen，而不是Request）
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            
            # 处理gzip/deflate压缩
            content_encoding = resp.headers.get("Content-Encoding", "")
            if "gzip" in content_encoding:
                buf = io.BytesIO(content)
                with gzip.GzipFile(fileobj=buf) as f:
                    content = f.read()
            elif "deflate" in content_encoding:
                content = zlib.decompress(content)
            
            return content.decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"获取页面失败: {e}")
        return ""

# =========================================================================
# 步骤3: 页面解析 - 从页面中提取图片数据
# =========================================================================

def extract_images_from_json(data):
    """
    从JSON数据中提取图片信息
    
    参数:
        data (dict): 解析后的JSON数据
        
    返回:
        list: 图片信息列表，每个元素包含{"url": "图片URL"}
    """
    images = []
    
    try:
        if not isinstance(data, dict):
            return images
            
        if "data" not in data or not isinstance(data["data"], dict):
            return images
            
        data_obj = data["data"]
            
        if "message_snapshot" not in data_obj or not isinstance(data_obj["message_snapshot"], dict):
            return images
            
        snapshot = data_obj["message_snapshot"]
            
        if "message_list" not in snapshot or not isinstance(snapshot["message_list"], list):
            return images
            
        for msg in snapshot["message_list"]:
            if not isinstance(msg, dict) or "content_block" not in msg:
                continue
                
            for block in msg["content_block"]:
                if not isinstance(block, dict):
                    continue
                    
                content = block.get("content")
                if not isinstance(content, dict) or "creation_block" not in content:
                    continue
                    
                creation_block = content["creation_block"]
                if creation_block is None or not isinstance(creation_block, dict):
                    continue
                    
                creations = creation_block.get("creations", [])
                if not isinstance(creations, list):
                    continue
                    
                for creation in creations:
                    if not isinstance(creation, dict) or "image" not in creation:
                        continue
                        
                    img_data = creation["image"]
                    if not isinstance(img_data, dict):
                        continue
                        
                    if "image_ori_raw" in img_data and isinstance(img_data["image_ori_raw"], dict):
                        img_url = img_data["image_ori_raw"].get("url", "")
                        if img_url and "favicon" not in img_url.lower() and "user-avatar" not in img_url.lower():
                            images.append({"url": img_url})
                    elif "image_ori" in img_data and isinstance(img_data["image_ori"], dict):
                        img_url = img_data["image_ori"].get("url", "")
                        if img_url and "favicon" not in img_url.lower() and "user-avatar" not in img_url.lower():
                            images.append({"url": img_url})
    
    except Exception as e:
        print(f"提取图片失败: {e}")
    
    return images


def parse_doubao_page(page_html, url):
    """
    解析豆包页面，提取图片信息
    
    参数:
        page_html (str): 页面HTML内容
        url (str): 原始链接
        
    返回:
        dict: 包含图片信息的字典
        
    注意:
        参数名使用page_html而不是html，避免与html模块冲突
    """
    results = {"type": "image", "images": [], "video": None, "videos": []}
    
    try:
        data_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', page_html)
        
        if not data_match:
            data_match = re.search(r'window\.initialData\s*=\s*({.*?});', page_html)
        
        if not data_match:
            data_match = re.search(r'<script[^>]*>var\s+data\s*=\s*({.*?});', page_html)
        
        if data_match:
            try:
                data = json.loads(data_match.group(1))
                images = extract_images_from_json(data)
                if images:
                    results["images"] = images
                    return results
            except Exception as e:
                print(f"解析JSON失败: {e}")
        
        data_fn_args_pattern = r'data-fn-args="([^"]+)"'
        data_fn_args_matches = re.findall(data_fn_args_pattern, page_html)
        
        for args_str in data_fn_args_matches:
            try:
                # 使用html模块的unescape方法，而不是变量html
                decoded_args = html.unescape(args_str)
                args_data = json.loads(decoded_args)
                
                if isinstance(args_data, list):
                    if len(args_data) >= 3 and isinstance(args_data[2], dict):
                        inner_data = args_data[2]
                        images = extract_images_from_json(inner_data)
                        if images:
                            print(f"从 data-fn-args 结构2提取到 {len(images)} 个图片")
                            results["images"] = images
                            return results
                    
                    if len(args_data) >= 2:
                        second_item = args_data[1]
                        if isinstance(second_item, list):
                            for item in second_item:
                                if isinstance(item, dict) and "routerDataFnArgs" in item:
                                    router_args = item["routerDataFnArgs"]
                                    if isinstance(router_args, list):
                                        for arg in router_args:
                                            if isinstance(arg, str):
                                                try:
                                                    inner_data = json.loads(arg)
                                                    images = extract_images_from_json(inner_data)
                                                    if images:
                                                        print(f"从 data-fn-args 结构1提取到 {len(images)} 个图片")
                                                        results["images"] = images
                                                        return results
                                                except json.JSONDecodeError:
                                                    pass
                                                    
                                                url_pattern = r'"image_ori_raw"\s*:\s*\{[^{}]*"url"\s*:\s*"([^"]+)"'
                                                img_urls = re.findall(url_pattern, arg)
                                                if img_urls:
                                                    images = []
                                                    seen_urls = set()
                                                    for img_url in img_urls:
                                                        img_url = img_url.encode("utf-8").decode("unicode_escape")
                                                        if img_url and "favicon" not in img_url.lower() and "user-avatar" not in img_url.lower():
                                                            if img_url not in seen_urls:
                                                                seen_urls.add(img_url)
                                                                images.append({"url": img_url})
                                                    if images:
                                                        print(f"从 data-fn-args 提取到 {len(images)} 个图片")
                                                        results["images"] = images
                                                        return results
            except Exception as e:
                print(f"方法4错误: {e}")
                pass
        
        snapshot_match = re.search(r'"message_snapshot"\s*:\s*({[^}]*"message_list"\s*:\s*\[.*?\]})', page_html, re.DOTALL)
        if snapshot_match:
            try:
                snapshot_data = json.loads(snapshot_match.group(1))
                images = extract_images_from_json({"data": snapshot_data})
                if images:
                    results["images"] = images
                    return results
            except Exception as e:
                print(f"解析 message_snapshot 失败: {e}")
    
    except Exception as e:
        print(f"解析页面失败: {e}")
    
    return results

# =========================================================================
# 步骤4: 图片URL解码（核心）
# =========================================================================

def decode_media_url(encoded_url, key_seed=""):
    """
    解码媒体URL（核心解码函数）
    
    参数:
        encoded_url (str): 加密的图片URL
        key_seed (str): 解密密钥（可选）
        
    返回:
        str: 解码后的真实图片URL
    """
    try:
        return decode_main_url(encoded_url, key_seed)
    except Exception as e:
        print(f"解码URL失败: {e}")
        return encoded_url

# =========================================================================
# 步骤5: HTTP服务器 - 处理前端请求
# =========================================================================

class MainHandler(BaseHTTPRequestHandler):
    """
    HTTP请求处理器
    """
    
    def log_message(self, format, *args):
        pass
    
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        if path == "/":
            self._serve_index()
        elif path == "/api/media":
            self._handle_media_proxy(parsed.query)
        elif path == "/api/download":
            self._handle_download(parsed.query)
        elif path.startswith("/static/"):
            self._serve_static(path)
        else:
            self._send_error(404, "Not Found")
    
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        if path == "/api/parse":
            self._handle_parse()
        else:
            self._send_error(404, "Not Found")
    
    def _send_response(self, status_code, content, content_type="text/plain; charset=utf-8"):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)
    
    def _send_json_response(self, data, status_code=200):
        json_str = json.dumps(data, ensure_ascii=False)
        self._send_response(status_code, json_str.encode("utf-8"), "application/json; charset=utf-8")
    
    def _send_error(self, status_code, message):
        self._send_json_response({"detail": message}, status_code)
    
    def _serve_index(self):
        try:
            with open(STATIC_DIR / "index.html", "rb") as f:
                content = f.read()
            self._send_response(200, content, "text/html; charset=utf-8")
        except Exception as e:
            self._send_error(500, str(e))
    
    def _serve_static(self, path):
        try:
            file_path = STATIC_DIR / path[8:]
            
            if not file_path.exists():
                self._send_error(404, "File not found")
                return
            
            with open(file_path, "rb") as f:
                content = f.read()
            
            ext = file_path.suffix.lower()
            content_types = {
                ".js": "application/javascript",
                ".css": "text/css",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }
            content_type = content_types.get(ext, "application/octet-stream")
            
            self._send_response(200, content, content_type)
        except Exception as e:
            self._send_error(500, str(e))
    
    def _handle_parse(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(body)
            text = payload.get("text", "")
            
            print(f"解析请求: {text[:100]}...")
            
            url = extract_url(text)
            if not url:
                self._send_json_response({"detail": "未找到有效的链接"}, 400)
                return
            
            link_type = detect_link_type(url)
            print(f"链接类型: {link_type}, URL: {url}")
            
            if link_type["type"] == "unsupported":
                self._send_json_response({"detail": "暂不支持该链接类型"}, 400)
                return
            
            # 使用page_html变量名，避免覆盖html模块
            page_html = fetch_doubao_page(url)
            
            if not page_html:
                self._send_json_response({
                    "type": "image",
                    "images": [{"url": url}]
                })
                return
            
            result = parse_doubao_page(page_html, url)
            
            if result["images"]:
                for img in result["images"]:
                    img["url"] = decode_media_url(img["url"])
                
                seen_urls = set()
                unique_images = []
                for img in result["images"]:
                    if img["url"] not in seen_urls:
                        seen_urls.add(img["url"])
                        unique_images.append(img)
                result["images"] = unique_images
            
            if not result["images"]:
                result = {"type": "image", "images": [{"url": url}]}
            
            self._send_json_response(result)
            
        except Exception as e:
            print(f"解析错误: {e}")
            traceback.print_exc()
            self._send_json_response({"detail": str(e)}, 500)
    
    def _fetch_with_retry(self, target_url, headers, max_retries=3):
        """
        带重试机制的HTTP请求
        针对CDN的403错误进行多次重试，使用不同的请求头配置
        
        参数:
            target_url: 目标URL
            headers: 请求头
            max_retries: 最大重试次数
            
        返回:
            HTTP响应对象
        """
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(target_url, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return resp
            except urllib.error.HTTPError as e:
                if e.code == 403 and attempt < max_retries - 1:
                    print(f"403错误，第 {attempt + 1} 次重试...")
                    import time
                    time.sleep(0.5)
                    continue
                raise
    
    def _clean_cdn_url(self, url):
        """
        清理CDN URL，移除签名参数和处理参数
        
        CDN签名参数(lk3s等)是基于客户端IP生成的，服务端代理时使用服务端IP导致签名无效
        移除这些参数可能能绕过签名验证
        
        参数:
            url: 原始CDN URL
            
        返回:
            清理后的URL列表（尝试多种格式）
        """
        import re
        
        urls_to_try = [url]
        
        parsed = urllib.parse.urlparse(url)
        query = parsed.query
        
        if query:
            new_query = []
            for param in query.split('&'):
                if not param.startswith('lk3s=') and not param.startswith('sign='):
                    new_query.append(param)
            
            if new_query:
                clean_url = parsed._replace(query='&'.join(new_query)).geturl()
                urls_to_try.append(clean_url)
            else:
                clean_url = parsed._replace(query='').geturl()
                urls_to_try.append(clean_url)
        
        path = parsed.path
        if '~tplv-' in path:
            clean_path = re.sub(r'~tplv-[^/]+', '', path)
            clean_url = parsed._replace(path=clean_path, query='').geturl()
            urls_to_try.append(clean_url)
        
        if '/image_raw.' in path:
            clean_path = path.replace('/image_raw.', '/')
            clean_url = parsed._replace(path=clean_path, query='').geturl()
            urls_to_try.append(clean_url)
        
        if 'sign.byteimg.com' in parsed.netloc:
            base_netloc = parsed.netloc.replace('-sign', '')
            clean_url = parsed._replace(netloc=base_netloc, query='').geturl()
            urls_to_try.append(clean_url)
        
        return list(set(urls_to_try))
    
    def _handle_media_proxy(self, query):
        """
        媒体代理服务（解决CORS问题）
        
        注意:
            CDN服务器会检查请求头，缺少关键请求头会返回403 Forbidden
            需要设置完整的请求头模拟真实浏览器访问
        """
        try:
            params = urllib.parse.parse_qs(query)
            if "url" not in params:
                self._send_error(400, "缺少url参数")
                return
            
            target_url = urllib.parse.unquote(params["url"][0])
            
            target_url = target_url.replace("&amp;", "&")
            
            print(f"媒体代理: {target_url[:150]}...")
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": "https://www.doubao.com/",
                "Origin": "https://www.doubao.com",
                "Connection": "keep-alive",
                "Sec-Ch-Ua": "\"Not_A Brand\";v=\"8\", \"Chromium\";v=\"120\", \"Google Chrome\";v=\"120\"",
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": "\"Windows\"",
                "Sec-Fetch-Dest": "image",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "cross-site",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
                "TE": "Trailers",
                "DNT": "1",
            }
            
            urls_to_try = self._clean_cdn_url(target_url)
            
            resp = None
            last_error = None
            
            for i, url in enumerate(urls_to_try):
                try:
                    if i > 0:
                        print(f"尝试第 {i + 1} 种URL格式: {url[:100]}...")
                    resp = self._fetch_with_retry(url, headers)
                    break
                except Exception as e:
                    last_error = e
                    print(f"尝试失败: {e}")
                    continue
            
            if resp is None:
                raise last_error
            
            content_type = resp.headers.get("Content-Type", "application/octet-stream")
            content = resp.read()
            
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
                
        except Exception as e:
            print(f"媒体代理错误: {e}")
            self._send_error(500, str(e))
    
    def _handle_download(self, query):
        try:
            params = urllib.parse.parse_qs(query)
            if "url" not in params:
                self._send_error(400, "缺少url参数")
                return
            
            target_url = urllib.parse.unquote(params["url"][0])
            filename = params.get("filename", ["download"])[0]
            
            # 修复HTML实体编码的URL
            target_url = target_url.replace("&amp;", "&")
            
            req = urllib.request.Request(
                target_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Referer": "https://www.doubao.com/",
                    "Origin": "https://www.doubao.com",
                }
            )
            
            with urllib.request.urlopen(req, timeout=60) as resp:
                content_type = resp.headers.get("Content-Type", "application/octet-stream")
                content = resp.read()
                
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content)
                
        except Exception as e:
            print(f"下载错误: {e}")
            self._send_error(500, str(e))

# =========================================================================
# 主程序入口
# =========================================================================

def main():
    port = 8081
    
    for arg in sys.argv[1:]:
        if arg.startswith("--port="):
            try:
                port = int(arg.split("=")[1])
            except ValueError:
                pass
    
    print("=" * 60)
    print("zyf图片去水印")
    print("=" * 60)
    print(f"服务地址: http://127.0.0.1:{port}")
    print(f"静态目录: {STATIC_DIR}")
    print(f"解码引擎: decode_worker.py")
    print(f"解码库: libvideodec.so")
    print()
    print("按 Ctrl+C 停止服务器")
    print("=" * 60)
    
    try:
        server = HTTPServer(("127.0.0.1", port), MainHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
    except Exception as e:
        print(f"服务器启动失败: {e}")

if __name__ == "__main__":
    main()
