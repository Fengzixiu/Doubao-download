#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import urllib.request
import urllib.parse
import json
import re
import traceback
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, make_response
from flask_cors import CORS

PROJECT_ROOT = Path(__file__).parent
STATIC_DIR = PROJECT_ROOT / "app" / "static"

app = Flask(__name__, static_folder='app/static')
CORS(app)


def setup_logging():
    log_dir = PROJECT_ROOT / 'logs'
    log_dir.mkdir(exist_ok=True)
    
    log_file = log_dir / 'app.log'
    
    handler = RotatingFileHandler(
        str(log_file),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)
    
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.DEBUG)
    
    logger = logging.getLogger('doubao_parser')
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    
    return logger

logger = setup_logging()


DOUBAO_DOMAINS = [
    "doubao.com",
    "qianwen.com",
]

URL_PATTERN = re.compile(r'https?://[^\s"\'<>\(\)]+')
TRAILING_PUNCTUATION = re.compile(r'[.,;:!?)>\]}。，；：！、）】》]+$')


def extract_url(text):
    match = URL_PATTERN.search(text)
    if match:
        url = match.group(0)
        return TRAILING_PUNCTUATION.sub("", url)
    return ""


def detect_link_type(url):
    if not url:
        return {"type": "empty", "label": "等待输入"}
    
    if any(domain in url for domain in DOUBAO_DOMAINS):
        if "/thread/" in url or "/chat/" in url or "/share/" in url:
            return {"type": "image", "label": "等待解析"}
    
    return {"type": "unsupported", "label": "暂不支持"}


def fetch_doubao_page(url):
    try:
        import gzip
        import io
        import zlib
        
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
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            
            content_encoding = resp.headers.get("Content-Encoding", "")
            if "gzip" in content_encoding:
                buf = io.BytesIO(content)
                with gzip.GzipFile(fileobj=buf) as f:
                    content = f.read()
            elif "deflate" in content_encoding:
                content = zlib.decompress(content)
            
            return content.decode("utf-8", errors="ignore")
    except Exception as e:
        logger.error(f"获取页面失败: {e}")
        return ""


def extract_images_from_json(data):
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
        logger.error(f"提取图片失败: {e}")
    
    return images


def parse_doubao_page(page_html, url):
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
                logger.error(f"解析JSON失败: {e}")
        
        data_fn_args_pattern = r'data-fn-args="([^"]+)"'
        data_fn_args_matches = re.findall(data_fn_args_pattern, page_html)
        
        for args_str in data_fn_args_matches:
            try:
                decoded_args = __import__('html').unescape(args_str)
                args_data = json.loads(decoded_args)
                
                if isinstance(args_data, list):
                    if len(args_data) >= 3 and isinstance(args_data[2], dict):
                        inner_data = args_data[2]
                        images = extract_images_from_json(inner_data)
                        if images:
                            logger.info(f"从 data-fn-args 结构2提取到 {len(images)} 个图片")
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
                                                        logger.info(f"从 data-fn-args 结构1提取到 {len(images)} 个图片")
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
                                                        logger.info(f"从 data-fn-args 提取到 {len(images)} 个图片")
                                                        results["images"] = images
                                                        return results
            except Exception as e:
                logger.error(f"方法4错误: {e}")
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
                logger.error(f"解析 message_snapshot 失败: {e}")
    
    except Exception as e:
        logger.error(f"解析页面失败: {e}")
    
    return results


def decode_media_url(encoded_url, key_seed=""):
    try:
        try:
            from decode_worker import decode_main_url
            return decode_main_url(encoded_url, key_seed)
        except ImportError as e:
            logger.warning(f"解码模块导入失败，直接返回原始URL: {e}")
            return encoded_url
    except Exception as e:
        logger.error(f"解码URL失败: {e}")
        return encoded_url


@app.route('/')
def index():
    try:
        if (STATIC_DIR / 'index.html').exists():
            return send_from_directory(STATIC_DIR, 'index.html')
        else:
            logger.error("index.html 文件不存在")
            return jsonify({"detail": "首页文件不存在"}), 500
    except Exception as e:
        logger.error(f"加载首页失败: {e}", exc_info=True)
        return jsonify({"detail": "服务器内部错误"}), 500


@app.route('/static/<path:filename>')
def static_files(filename):
    try:
        if (STATIC_DIR / filename).exists():
            return send_from_directory(STATIC_DIR, filename)
        else:
            logger.error(f"静态文件不存在: {filename}")
            return jsonify({"detail": "文件不存在"}), 404
    except Exception as e:
        logger.error(f"加载静态文件失败: {e}", exc_info=True)
        return jsonify({"detail": "服务器内部错误"}), 500


@app.route('/api/parse', methods=['POST'])
def api_parse():
    try:
        payload = request.get_json()
        text = payload.get("text", "")
        
        logger.info(f"解析请求: {text[:100]}...")
        
        url = extract_url(text)
        if not url:
            return jsonify({"detail": "未找到有效的链接"}), 400
        
        link_type = detect_link_type(url)
        logger.info(f"链接类型: {link_type}, URL: {url}")
        
        if link_type["type"] == "unsupported":
            return jsonify({"detail": "暂不支持该链接类型"}), 400
        
        page_html = fetch_doubao_page(url)
        
        if not page_html:
            logger.warning(f"获取页面失败，使用原始URL: {url}")
            return jsonify({
                "type": "image",
                "images": [{"url": url}]
            })
        
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
        
        logger.info(f"解析成功，提取到 {len(result['images'])} 张图片")
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"解析错误: {e}", exc_info=True)
        return jsonify({"detail": str(e)}), 500


def _fetch_with_retry(target_url, headers, max_retries=3):
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(target_url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp
        except urllib.error.HTTPError as e:
            if e.code == 403 and attempt < max_retries - 1:
                logger.warning(f"403错误，第 {attempt + 1} 次重试...")
                import time
                time.sleep(0.5)
                continue
            raise


def _clean_cdn_url(url):
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


@app.route('/api/media')
def api_media():
    try:
        target_url = request.args.get('url')
        if not target_url:
            return jsonify({"detail": "缺少url参数"}), 400
        
        target_url = urllib.parse.unquote(target_url)
        target_url = target_url.replace("&amp;", "&")
        
        logger.info(f"媒体代理: {target_url[:150]}...")
        
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
        
        urls_to_try = _clean_cdn_url(target_url)
        
        resp = None
        last_error = None
        
        for i, url in enumerate(urls_to_try):
            try:
                if i > 0:
                    logger.info(f"尝试第 {i + 1} 种URL格式: {url[:100]}...")
                resp = _fetch_with_retry(url, headers)
                break
            except Exception as e:
                last_error = e
                logger.warning(f"尝试失败: {e}")
                continue
        
        if resp is None:
            raise last_error
        
        content_type = resp.headers.get("Content-Type", "application/octet-stream")
        content = resp.read()
        
        response = make_response(content)
        response.headers.set('Content-Type', content_type)
        return response
            
    except Exception as e:
        logger.error(f"媒体代理错误: {e}", exc_info=True)
        return jsonify({"detail": str(e)}), 500


@app.route('/api/download')
def api_download():
    try:
        target_url = request.args.get('url')
        if not target_url:
            return jsonify({"detail": "缺少url参数"}), 400
        
        filename = request.args.get('filename', 'download')
        
        target_url = urllib.parse.unquote(target_url)
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
            
            response = make_response(content)
            response.headers.set('Content-Type', content_type)
            response.headers.set('Content-Disposition', f'attachment; filename*=UTF-8''{urllib.parse.quote(filename)}')
            return response
                
    except Exception as e:
        logger.error(f"下载错误: {e}", exc_info=True)
        return jsonify({"detail": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8081))
    app.run(host='0.0.0.0', port=port, debug=False)