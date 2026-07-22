#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import json
import html
import urllib.request
import urllib.parse

from decode_worker import decode_main_url

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
        print(f"获取页面失败: {e}")
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
        print(f"提取图片失败: {e}")
    
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
                print(f"解析JSON失败: {e}")
        
        data_fn_args_pattern = r'data-fn-args="([^"]+)"'
        data_fn_args_matches = re.findall(data_fn_args_pattern, page_html)
        
        for args_str in data_fn_args_matches:
            try:
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


def decode_media_url(encoded_url, key_seed=""):
    try:
        return decode_main_url(encoded_url, key_seed)
    except Exception as e:
        print(f"解码URL失败: {e}")
        return encoded_url