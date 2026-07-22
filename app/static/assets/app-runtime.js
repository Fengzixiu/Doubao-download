/**
 * =========================================================
 * 豆包图片去水印工具 - 前端逻辑
 * =========================================================
 * 
 * 核心功能:
 *   1. 监听用户输入
 *   2. 发送解析请求到后端API
 *   3. 展示解析结果（图片列表）
 *   4. 提供图片查看和下载功能
 * 
 * 文件结构:
 *   - App类: 主应用逻辑
 *   - UI相关方法: 处理界面交互
 *   - API相关方法: 与后端通信
 * 
 * 接口说明:
 *   - POST /api/parse: 解析分享链接
 *     请求体: {"text": "用户输入的文本"}
 *     响应: {"type": "image", "images": [{"url": "图片URL"}]}
 *   
 *   - GET /api/media?url=xxx: 媒体代理（解决CORS问题）
 *   - GET /api/download?url=xxx: 文件下载
 */

// =========================================================
// 常量定义
// =========================================================

/**
 * API基础URL
 * 当前页面部署在同域名下，使用相对路径
 */
const API_BASE = '';

/**
 * 支持的链接类型
 * - image: 图片链接
 * - unsupported: 不支持的链接类型
 */
const LINK_TYPES = {
    IMAGE: 'image',
    UNSUPPORTED: 'unsupported',
    EMPTY: 'empty'
};

// =========================================================
// App类 - 主应用逻辑
// =========================================================

class App {
    /**
     * 构造函数
     * 初始化DOM元素引用和事件监听
     */
    constructor() {
        // DOM元素引用
        this.inputText = document.getElementById('inputText');
        this.parseBtn = document.getElementById('parseBtn');
        this.resultArea = document.getElementById('resultArea');
        this.loading = document.getElementById('loading');
        this.errorMsg = document.getElementById('errorMsg');
        this.imageGrid = document.getElementById('imageGrid');
        this.emptyState = document.getElementById('emptyState');
        this.imageCount = document.getElementById('imageCount');

        // 初始化事件监听
        this.initListeners();
    }

    /**
     * 初始化事件监听
     */
    initListeners() {
        // 解析按钮点击事件
        this.parseBtn.addEventListener('click', () => this.handleParse());

        // 输入框回车键事件（Shift+Enter换行，Enter提交）
        this.inputText.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.handleParse();
            }
        });

        // 输入框输入事件（实时检测链接类型）
        this.inputText.addEventListener('input', () => this.validateInput());
    }

    /**
     * 验证输入内容
     * 检测链接类型并更新按钮状态
     */
    validateInput() {
        const text = this.inputText.value.trim();
        const linkType = this.detectLinkType(text);

        // 根据链接类型更新按钮状态
        if (linkType === LINK_TYPES.EMPTY) {
            this.parseBtn.disabled = true;
            this.parseBtn.textContent = '开始解析';
        } else if (linkType === LINK_TYPES.UNSUPPORTED) {
            this.parseBtn.disabled = true;
            this.parseBtn.textContent = '暂不支持该链接类型';
        } else {
            this.parseBtn.disabled = false;
            this.parseBtn.textContent = '开始解析';
        }
    }

    /**
     * 检测链接类型
     * 
     * @param {string} text - 用户输入的文本
     * @returns {string} - 链接类型 ('image' | 'unsupported' | 'empty')
     */
    detectLinkType(text) {
        // 如果输入为空，返回empty类型
        if (!text.trim()) {
            return LINK_TYPES.EMPTY;
        }

        // 使用正则表达式提取URL
        const urlPattern = /https?:\/\/[^\s"\'<>\(\)]+/;
        const match = text.match(urlPattern);

        if (!match) {
            return LINK_TYPES.UNSUPPORTED;
        }

        const url = match[0];

        // 检查是否为豆包图片链接
        const doubaoDomains = ['doubao.com', 'qianwen.com'];
        const hasDomain = doubaoDomains.some(domain => url.includes(domain));

        if (hasDomain && (url.includes('/thread/') || url.includes('/chat/') || url.includes('/share/'))) {
            return LINK_TYPES.IMAGE;
        }

        return LINK_TYPES.UNSUPPORTED;
    }

    /**
     * 处理解析请求
     * 这是核心业务逻辑，流程如下:
     *   1. 获取用户输入
     *   2. 显示加载状态
     *   3. 发送请求到后端
     *   4. 处理响应结果
     */
    async handleParse() {
        const text = this.inputText.value.trim();
        
        // 验证输入
        if (!text) {
            this.showError('请输入链接');
            return;
        }

        const linkType = this.detectLinkType(text);
        if (linkType !== LINK_TYPES.IMAGE) {
            this.showError('暂不支持该链接类型');
            return;
        }

        // 显示结果区域和加载状态
        this.showLoading();

        try {
            // 发送解析请求
            const result = await this.parseLink(text);

            // 处理解析结果
            this.handleParseResult(result);

        } catch (error) {
            // 处理错误
            this.showError(error.message || '解析失败，请重试');
        }
    }

    /**
     * 发送解析请求到后端API
     * 
     * @param {string} text - 用户输入的文本
     * @returns {Promise<Object>} - 解析结果
     */
    async parseLink(text) {
        const response = await fetch(`${API_BASE}/api/parse`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ text })
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `请求失败 (${response.status})`);
        }

        return response.json();
    }

    /**
     * 处理解析结果
     * 
     * @param {Object} result - 后端返回的解析结果
     *   - result.type: 媒体类型 ('image')
     *   - result.images: 图片列表 [{url: '图片URL'}]
     */
    handleParseResult(result) {
        // 隐藏加载状态
        this.hideLoading();

        // 清空之前的错误和图片
        this.hideError();
        this.clearImages();

        // 检查结果类型
        if (result.type !== LINK_TYPES.IMAGE) {
            this.showError('不支持的媒体类型');
            return;
        }

        // 获取图片列表
        const images = result.images || [];

        if (images.length === 0) {
            // 没有找到图片，显示空状态
            this.showEmptyState();
        } else {
            // 显示图片列表
            this.showImages(images);
        }
    }

    /**
     * 显示图片列表
     * 
     * @param {Array} images - 图片信息列表
     */
    showImages(images) {
        // 更新图片数量
        this.imageCount.textContent = `${images.length} 张图片`;
        
        // 隐藏空状态
        this.emptyState.style.display = 'none';
        
        images.forEach((image, index) => {
            const imageUrl = image.url;
            const proxyUrl = `${API_BASE}/api/media?url=${encodeURIComponent(imageUrl)}`;

            const imageItem = document.createElement('div');
            imageItem.className = 'image-item';

            const img = document.createElement('img');
            img.src = imageUrl;
            img.alt = `图片 ${index + 1}`;
            img.loading = 'lazy';
            img.onerror = () => {
                img.src = proxyUrl;
            };

            // 创建操作按钮容器
            const actions = document.createElement('div');
            actions.className = 'actions';

            // 创建查看按钮
            const viewBtn = document.createElement('button');
            viewBtn.className = 'action-btn';
            viewBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg> 查看';
            viewBtn.addEventListener('click', () => this.viewImage(imageUrl));

            // 创建下载按钮
            const downloadBtn = document.createElement('button');
            downloadBtn.className = 'action-btn';
            downloadBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg> 下载';
            downloadBtn.addEventListener('click', () => this.downloadImage(imageUrl));

            // 组装DOM结构
            actions.appendChild(viewBtn);
            actions.appendChild(downloadBtn);
            imageItem.appendChild(img);
            imageItem.appendChild(actions);

            // 添加到图片网格
            this.imageGrid.appendChild(imageItem);
        });
    }

    /**
     * 查看图片（在新窗口打开）
     * 
     * @param {string} imageUrl - 图片原始URL
     */
    viewImage(imageUrl) {
        window.open(imageUrl, '_blank');
    }

    downloadImage(imageUrl) {
        const link = document.createElement('a');
        link.href = imageUrl;
        const filename = `image_${Date.now()}.png`;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    /**
     * 清空图片列表
     */
    clearImages() {
        this.imageGrid.innerHTML = '';
    }

    /**
     * 显示加载状态
     */
    showLoading() {
        this.resultArea.classList.add('show');
        this.loading.style.display = 'block';
        this.errorMsg.style.display = 'none';
        this.imageGrid.style.display = 'none';
        this.emptyState.style.display = 'none';
    }

    /**
     * 隐藏加载状态
     */
    hideLoading() {
        this.loading.style.display = 'none';
        this.imageGrid.style.display = 'grid';
    }

    /**
     * 显示错误信息
     * 
     * @param {string} message - 错误消息
     */
    showError(message) {
        this.errorMsg.textContent = message;
        this.errorMsg.style.display = 'block';
        this.imageGrid.style.display = 'none';
        this.emptyState.style.display = 'none';
    }

    /**
     * 隐藏错误信息
     */
    hideError() {
        this.errorMsg.style.display = 'none';
    }

    /**
     * 显示空状态
     */
    showEmptyState() {
        this.imageGrid.style.display = 'none';
        this.emptyState.style.display = 'block';
        this.imageCount.textContent = '0 张图片';
    }
}

// =========================================================
// 页面加载完成后初始化应用
// =========================================================

document.addEventListener('DOMContentLoaded', () => {
    new App();
});
