/**
 * BiliSub - B站字幕提取工具
 * 前端JavaScript逻辑 - 支持批量处理
 */

// DOM元素
const apiKeyInput = document.getElementById('apiKey');
const toggleApiKeyBtn = document.getElementById('toggleApiKey');
const saveAndVerifyApiKeyBtn = document.getElementById('saveAndVerifyApiKey');
const apiKeyVerifyStatus = document.getElementById('apiKeyVerifyStatus');
const biliCookieInput = document.getElementById('biliCookie');
const fullCookieText = document.getElementById('fullCookieText');
const saveCookieBtn = document.getElementById('saveCookieBtn');
const verifyCookieBtn = document.getElementById('verifyCookieBtn');
const toggleCookieBtn = document.getElementById('toggleCookieVisibility');
const cookieStatusBar = document.getElementById('cookieStatusBar');
const cookieStatusIcon = document.getElementById('cookieStatusIcon');
const cookieStatusText = document.getElementById('cookieStatusText');
const videoUrlInput = document.getElementById('videoUrl');
const extractBtn = document.getElementById('extractBtn');
const transcriptContainer = document.getElementById('transcriptContainer');
const copyBtn = document.getElementById('copyBtn');
const videoListContainer = document.getElementById('videoListContainer');
const videoCountSpan = document.getElementById('videoCount');
const currentVideoTitle = document.getElementById('currentVideoTitle');
const downloadAllBtn = document.getElementById('downloadAllBtn'); // Moved to bottom
const toast = document.getElementById('toast');

// API基础URL
const API_BASE = '';

// 大模型配置默认值
const LLM_DEFAULTS = {
    apiUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-plus',
    prompt: '请将以下视频字幕总结为150字摘要。'
};

// 本地存储键
const STORAGE_KEY_API = 'bilisub_api_key';
const STORAGE_KEY_COOKIE = 'bilisub_bili_cookie';
const STORAGE_KEY_LLM_API_KEY = 'bilisub_llm_api_key';
const STORAGE_KEY_LLM_API_URL = 'bilisub_llm_api_url';
const STORAGE_KEY_LLM_MODEL = 'bilisub_llm_model';
const STORAGE_KEY_LLM_PROMPT = 'bilisub_llm_prompt';
const STORAGE_KEY_USE_SELF_HOSTED = 'bilisub_use_self_hosted';
const STORAGE_KEY_SELF_HOSTED_DOMAIN = 'bilisub_self_hosted_domain';

// 视频数据存储
let videoList = [];
let videoTranscripts = {};  // {index: transcript}
let videoAiResults = {};    // {index: AI处理结果}
let selectedVideoIndex = null;

// Guest 配额信息
let isGuestUser = false;
let guestQuotaRemaining = 5;
let guestQuotaTotal = 5;

// Cookie状态枚举
const COOKIE_STATUS = {
    NONE: 'none',           // 未配置
    CHECKING: 'checking',   // 验证中
    VALID: 'valid',         // 有效
    INVALID: 'invalid',     // 已失效
    INCOMPLETE: 'incomplete' // 缺少必要字段
};

// 当前Cookie状态
let currentCookieStatus = COOKIE_STATUS.NONE;
let currentUsername = '';

// LLM状态枚举
const LLM_STATUS = {
    NONE: 'none',           // 未配置
    CHECKING: 'checking',   // 测试中
    OK: 'ok',               // 测试成功（自定义配置）
    DEFAULT_OK: 'default_ok', // 测试成功（默认配置）
    ERROR: 'error'          // 测试失败
};

// 当前LLM状态
let currentLlmStatus = LLM_STATUS.NONE;

// 配置区域折叠状态
let configExpanded = false;

/**
 * 切换配置区域展开/折叠
 */
function toggleConfigSection() {
    const content = document.getElementById('configContent');
    const arrow = document.getElementById('configCollapseArrow');

    configExpanded = !configExpanded;

    if (configExpanded) {
        content.style.display = 'block';
        arrow.style.transform = 'rotate(180deg)';
    } else {
        content.style.display = 'none';
        arrow.style.transform = 'rotate(0deg)';
    }
}

/**
 * 更新配置状态摘要（折叠时显示）
 */
function updateConfigStatusSummary() {
    const apiKeyDot = document.getElementById('apiKeyStatusDot');
    const cookieDot = document.getElementById('cookieStatusDot');
    const llmDot = document.getElementById('llmStatusDot');

    // API Key状态 - 只有验证通过才显示绿色，否则保持灰色
    // API Key 的验证在 handleSaveAndVerifyApiKey 中处理，这里不自动设置颜色

    // Cookie状态
    if (cookieDot) {
        switch (currentCookieStatus) {
            case COOKIE_STATUS.VALID:
                cookieDot.className = 'status-dot status-ok';
                break;
            case COOKIE_STATUS.INVALID:
            case COOKIE_STATUS.INCOMPLETE:
                cookieDot.className = 'status-dot status-error';
                break;
            case COOKIE_STATUS.CHECKING:
                cookieDot.className = 'status-dot status-checking';
                break;
            default:
                // 未验证：灰色
                cookieDot.className = 'status-dot';
        }
    }

    // LLM状态
    if (llmDot) {
        // 根据LLM状态决定状态灯颜色
        switch (currentLlmStatus) {
            case LLM_STATUS.OK:
                llmDot.className = 'status-dot status-ok';
                break;
            case LLM_STATUS.DEFAULT_OK:
                llmDot.className = 'status-dot status-warning'; // 默认配置：黄色
                break;
            case LLM_STATUS.ERROR:
                llmDot.className = 'status-dot status-error';
                break;
            case LLM_STATUS.CHECKING:
                llmDot.className = 'status-dot status-checking';
                break;
            default:
                // 未验证：灰色
                llmDot.className = 'status-dot';
        }
    }

    // 更新提取方式提示
    updateExtractionModeHint();
}

/**
 * 更新提取方式提示
 * 根据 API Key 和 Cookie 的有效性状态显示不同的提示信息
 */
function updateExtractionModeHint() {
    const hintContainer = document.getElementById('extractionModeHint');
    const hintText = document.getElementById('extractionModeText');

    if (!hintContainer || !hintText) return;

    // 检测 API Key 和 Cookie 的有效性
    const apiKeyDot = document.getElementById('apiKeyStatusDot');
    const apiKeyValid = apiKeyDot?.classList.contains('status-ok');
    const cookieValid = currentCookieStatus === COOKIE_STATUS.VALID;

    // 检测本地直链状态
    const useSelfHostedToggle = document.getElementById('useSelfHostedStorage');
    const isLocalDirect = useSelfHostedToggle?.checked || false;

    // 移除所有状态类
    hintContainer.classList.remove('mode-ok', 'mode-warning', 'mode-error');

    // 构建提示信息
    let message = '';

    // 本地直链状态（始终放在最后）
    const directLinkStatus = isLocalDirect
        ? '本地直链可用（高速）'
        : '使用第三方直链（速度较慢）';

    if (apiKeyValid && cookieValid) {
        // 两者都有效
        if (isLocalDirect) {
            hintContainer.classList.add('mode-ok');
        } else {
            hintContainer.classList.add('mode-warning');
        }
        message = `优先提取自带字幕，无字幕视频使用语音转录 | ${directLinkStatus}`;
    } else if (apiKeyValid && !cookieValid) {
        // API 有效，Cookie 无效
        hintContainer.classList.add('mode-warning');
        message = `Cookie 无效，跳过字幕提取，全部视频使用语音转录 | ${directLinkStatus}`;
    } else if (!apiKeyValid && cookieValid) {
        // API 无效，Cookie 有效
        hintContainer.classList.add('mode-warning');
        message = `API Key 无效，仅能提取视频自带字幕，无字幕视频将提取失败 | ${directLinkStatus}`;
    } else {
        // 两者都无效
        hintContainer.classList.add('mode-error');
        message = `API Key 和 Cookie 均无效，无法提取字幕 | ${directLinkStatus}`;
    }

    hintText.textContent = message;
}


/**
 * 更新存储状态UI（本地直链/第三方直链）
 */
function updateStorageUI(useSelfHosted) {
    console.log('Updating storage UI:', useSelfHosted);

    const storageStatusDot = document.getElementById('storageStatusDot');
    const storageStatusText = document.getElementById('storageStatusText');

    // 更新状态灯
    if (storageStatusDot && storageStatusText) {
        if (useSelfHosted) {
            storageStatusDot.className = 'status-dot status-ok'; // 绿色
            storageStatusText.textContent = '本地直链';
        } else {
            storageStatusDot.className = 'status-dot status-warning'; // 黄色
            storageStatusText.textContent = '第三方直链';
        }
    } else {
        console.warn('[updateStorageUI] Status elements not found:', { storageStatusDot, storageStatusText });
    }

    // 更新隐藏开关状态
    const useSelfHostedToggle = document.getElementById('useSelfHostedStorage');
    if (useSelfHostedToggle) {
        useSelfHostedToggle.checked = useSelfHosted;
    }
}

/**
 * 初始化 UI 事件监听器（同步执行，无网络请求）
 * 这部分代码立即执行，确保页面可交互
 */
function initUIEventListeners() {
    if (toggleCookieBtn) {
        toggleCookieBtn.addEventListener('click', toggleCookieVisibility);
    }
    if (toggleApiKeyBtn) {
        toggleApiKeyBtn.addEventListener('click', toggleApiKeyVisibility);
    }
    if (extractBtn) {
        extractBtn.addEventListener('click', handleExtract);
    }
    if (copyBtn) {
        copyBtn.addEventListener('click', handleCopy);
    }
    if (downloadAllBtn) {
        downloadAllBtn.addEventListener('click', handleDownloadAll);
    }

    // 保存并验证 API Key 按钮
    if (saveAndVerifyApiKeyBtn) {
        saveAndVerifyApiKeyBtn.addEventListener('click', handleSaveAndVerifyApiKey);
    }

    // 保存Cookie按钮
    if (saveCookieBtn) {
        saveCookieBtn.addEventListener('click', saveAndVerifyCookie);
    }

    // 手动验证Cookie按钮
    if (verifyCookieBtn) {
        verifyCookieBtn.addEventListener('click', () => verifyCookie(biliCookieInput?.value));
    }

    // 清理缓存按钮
    const cleanupBtn = document.getElementById('cleanupBtn');
    if (cleanupBtn) {
        cleanupBtn.addEventListener('click', cleanupTempFiles);
    }

    // LLM配置保存按钮
    const saveLlmConfigBtn = document.getElementById('saveLlmConfigBtn');
    if (saveLlmConfigBtn) {
        saveLlmConfigBtn.addEventListener('click', saveLlmConfig);
    }

    // LLM API Key显示/隐藏
    const toggleLlmApiKey = document.getElementById('toggleLlmApiKey');
    const llmApiKeyInput = document.getElementById('llmApiKey');
    if (toggleLlmApiKey && llmApiKeyInput) {
        toggleLlmApiKey.addEventListener('click', () => {
            llmApiKeyInput.type = llmApiKeyInput.type === 'password' ? 'text' : 'password';
        });
    }

    if (videoUrlInput) {
        videoUrlInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                handleExtract();
            }
        });
    }

    console.log('[Init] UI event listeners bindingcompleted');
}

/**
 * 调度后台初始化任务（网络请求、配置验证等）
 * 使用 requestIdleCallback 让浏览器先完成渲染，再执行后台任务
 * 
 * 优化策略：
 * 1. 所有任务尽可能并行执行
 * 2. 历史功能在 Guest 状态确定后立即开始（不等配置加载）
 */
function scheduleBackgroundInit() {
    const doBackgroundInit = async () => {
        console.log('[Init] Starting background initialization...');

        try {
            // 并行执行所有任务，但历史功能需要在 Guest 状态确定后启动
            await Promise.all([
                // 任务1：加载配置并填充UI（验证在内部异步执行）
                loadSavedData().then(() => {
                    updateConfigStatusSummary();
                }).catch(error => {
                    console.error('Failed to load saved data:', error);
                    updateConfigStatusSummary();
                }),

                // 任务2：获取用户状态，完成后立即初始化历史功能
                fetchGuestQuota().then(() => {
                    // Guest 状态已确定，立即初始化历史功能
                    return initHistoryFeature();
                }).catch(e => console.error('Failed to fetch guest status or init history:', e)),

                // 任务3：加载用户信息显示
                loadCurrentUser().catch(e => console.error('Failed to load user:', e))
            ]);

            console.log('[Init] Background initialization completed');
        } catch (error) {
            console.error('[Init] Background initialization failed:', error);
        }
    };

    // 优先使用 requestIdleCallback（浏览器空闲时执行），降级用 setTimeout
    if ('requestIdleCallback' in window) {
        requestIdleCallback(() => doBackgroundInit(), { timeout: 2000 });
    } else {
        // 使用 setTimeout 0 让当前渲染任务完成后再执行
        setTimeout(() => doBackgroundInit(), 0);
    }
}

// 主初始化入口 - 唯一的 DOMContentLoaded 监听器
document.addEventListener('DOMContentLoaded', () => {
    // 阶段1：立即绑定 UI 事件（同步，无阻塞）
    initUIEventListeners();

    // 阶段2：调度后台初始化（异步，不阻塞页面渲染）
    scheduleBackgroundInit();
});

/**
 * 加载保存的数据（从服务器加载用户配置）
 * 优化版本：配置验证完全异步化，不阻塞页面加载
 */
async function loadSavedData() {
    try {
        // 公网访问检测异步执行，不阻塞配置加载
        checkPublicAccess().then(publicAccessResult => {
            updateStorageUI(publicAccessResult.is_public);
            console.log('[Auto-Storage] Public access check:', publicAccessResult.reason, '=>', publicAccessResult.is_public ? '本地直链' : '第三方直链');
        }).catch(e => {
            console.error('[Auto-Storage] 检测失败:', e);
            updateStorageUI(false);
        });

        // 直接获取配置（不等待公网检测）
        const configResponse = await fetch('/api/load-config');
        const data = await configResponse.json();

        if (data.success && data.config) {
            const config = data.config;

            // === 立即填充所有UI字段（无网络请求） ===
            if (config.api_key && apiKeyInput) {
                apiKeyInput.value = config.api_key;
            }

            if (config.bili_cookie && biliCookieInput) {
                biliCookieInput.value = config.bili_cookie;
                if (fullCookieText) {
                    fullCookieText.value = config.bili_cookie;
                }
            }

            const llmApiKey = document.getElementById('llmApiKey');
            const llmApiUrl = document.getElementById('llmApiUrl');
            const llmModel = document.getElementById('llmModelName');
            const llmPrompt = document.getElementById('llmPrompt');

            if (llmApiKey) llmApiKey.value = config.llm_api_key || '';
            if (llmApiUrl) llmApiUrl.value = config.llm_api_url || '';
            if (llmModel) llmModel.value = config.llm_model || '';
            if (llmPrompt) llmPrompt.value = config.llm_prompt || '';

            // 立即更新配置摘要（显示灰色状态灯）
            updateConfigStatusSummary();

            // === 配置验证完全异步化（不阻塞页面加载） ===
            // 使用 setTimeout 0 让 UI 先渲染，然后在后台执行验证
            setTimeout(() => {
                scheduleConfigVerification(config);
            }, 0);

            // loadSavedData 立即返回，不等待验证完成
        } else {
            updateCookieStatus(COOKIE_STATUS.NONE);
        }
    } catch (error) {
        console.error('加载配置失败:', error);
        updateCookieStatus(COOKIE_STATUS.NONE);
        updateStorageUI(false);
    }
}

/**
 * 后台执行配置验证（不阻塞页面加载）
 * 验证完成后更新状态灯，并显示检测进度提示
 */
function scheduleConfigVerification(config) {
    console.log('[Config] 开始后台配置验证...');

    // 显示检测进度提示
    const checkingHint = document.getElementById('configCheckingHint');
    const checkingText = document.getElementById('configCheckingText');
    if (checkingHint) {
        checkingHint.style.display = 'inline-flex';
    }

    // 收集所有验证 Promise
    const verificationPromises = [];
    let completedCount = 0;
    const totalCount = (config.api_key ? 1 : 0) +
        (config.bili_cookie ? 1 : 0) +
        ((config.llm_api_key || config.api_key) ? 1 : 0);

    // 更新检测进度文字
    const updateCheckingProgress = () => {
        if (checkingText) {
            if (completedCount < totalCount) {
                checkingText.textContent = `正在检测配置 (${completedCount}/${totalCount})...`;
            }
        }
    };

    // 1. API Key 验证（后台执行）
    if (config.api_key) {
        const apiKeyPromise = verifyApiKey(config.api_key).then(valid => {
            console.log('[Config] API Key 验证完成:', valid ? '有效' : '无效');
            completedCount++;
            updateCheckingProgress();
            updateConfigStatusSummary();
            checkConfigExpand();
            return valid;
        }).catch(e => {
            console.error('[Config] API Key 验证失败:', e);
            completedCount++;
            updateCheckingProgress();
            return false;
        });
        verificationPromises.push(apiKeyPromise);
    }

    // 2. Cookie 验证（后台执行）
    if (config.bili_cookie) {
        const cookiePromise = verifyCookie(config.bili_cookie).then(valid => {
            console.log('[Config] Cookie 验证完成:', valid ? '有效' : '无效');
            completedCount++;
            updateCheckingProgress();
            updateConfigStatusSummary();
            checkConfigExpand();
            return valid;
        }).catch(e => {
            console.error('[Config] Cookie 验证失败:', e);
            completedCount++;
            updateCheckingProgress();
            return false;
        });
        verificationPromises.push(cookiePromise);
    } else {
        updateCookieStatus(COOKIE_STATUS.NONE);
    }

    // 3. LLM 验证（后台执行）
    const effectiveApiKey = config.llm_api_key || config.api_key || '';
    const effectiveApiUrl = config.llm_api_url || LLM_DEFAULTS.apiUrl;
    const effectiveModel = config.llm_model || LLM_DEFAULTS.model;
    const isAllDefault = !config.llm_api_key && !config.llm_api_url && !config.llm_model;

    if (effectiveApiKey) {
        const llmPromise = testLlmConfig(effectiveApiKey, effectiveApiUrl, effectiveModel, isAllDefault).then(valid => {
            console.log('[Config] LLM 验证完成:', valid ? '有效' : '无效');
            completedCount++;
            updateCheckingProgress();
            updateConfigStatusSummary();
            return valid;
        }).catch(e => {
            console.error('[Config] LLM 验证失败:', e);
            completedCount++;
            updateCheckingProgress();
            return false;
        });
        verificationPromises.push(llmPromise);
    } else {
        updateLlmStatus('error', '未配置');
    }

    // 所有验证完成后隐藏检测提示
    Promise.all(verificationPromises).then(() => {
        console.log('[Config] 所有配置验证已完成');
        if (checkingHint) {
            // 短暂显示"验证完成"后隐藏
            if (checkingText) {
                checkingText.textContent = '验证完成';
            }
            setTimeout(() => {
                checkingHint.style.display = 'none';
            }, 1000);
        }
    });
}

/**
 * 检测服务是否有公网可访问性
 * 用于决定使用本地直链还是第三方直链服务
 * 
 * 阿里云 Paraformer-v2 要求：
 * - 支持 HTTP 和 HTTPS 协议
 * - 文件 URL 必须是公网可访问的
 */
async function checkPublicAccess() {
    try {
        const response = await fetch('/api/check-public-access', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                origin: window.location.origin
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error('[Public Access Check] 检测失败:', error);
        return {
            is_public: false,
            public_url: null,
            reason: '检测失败，默认使用第三方直链'
        };
    }
}

/**
 * 验证 API Key（带重试机制）
 * @param {string} apiKey - API Key
 * @param {number} retryCount - 当前重试次数（内部使用）
 */
async function verifyApiKey(apiKey, retryCount = 0) {
    const MAX_RETRIES = 3;
    const RETRY_DELAY = 2000; // 2秒

    if (!apiKey) return false;

    try {
        const response = await fetch('/api/llm_process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                api_key: apiKey,
                api_url: LLM_DEFAULTS.apiUrl,
                model: LLM_DEFAULTS.model,
                prompt: '请回复"测试成功"四个字。',
                content: '这是一个测试请求。'
            })
        });

        const data = await response.json();
        const apiKeyStatusDot = document.getElementById('apiKeyStatusDot');

        if (data.success) {
            if (apiKeyStatusDot) {
                apiKeyStatusDot.className = 'status-dot status-ok';
            }
            return true;
        } else {
            if (apiKeyStatusDot) {
                apiKeyStatusDot.className = 'status-dot status-error';
            }
            return false;
        }
    } catch (error) {
        console.error(`API Key 验证失败 (尝试 ${retryCount + 1}/${MAX_RETRIES}):`, error);

        // 如果是网络错误且还有重试次数，则重试
        if (retryCount < MAX_RETRIES - 1) {
            console.log(`[API Key] ${RETRY_DELAY / 1000}秒后重试...`);
            await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
            return verifyApiKey(apiKey, retryCount + 1);
        }

        const apiKeyStatusDot = document.getElementById('apiKeyStatusDot');
        if (apiKeyStatusDot) {
            apiKeyStatusDot.className = 'status-dot status-error';
        }
        return false;
    }
}


/**
 * 检查是否需要展开配置区域
 * 仅当 API Key 为空 或 Cookie 无效 时展开
 */
function checkConfigExpand() {
    const hasApiKey = apiKeyInput && apiKeyInput.value.trim().length > 0;
    const isCookieValid = currentCookieStatus === COOKIE_STATUS.VALID;

    console.log('[Config-Expand] API Key:', hasApiKey, ', Cookie valid:', isCookieValid);

    // 如果 API Key 为空 或 Cookie 无效，则展开配置
    if (!hasApiKey || !isCookieValid) {
        const content = document.getElementById('configContent');
        if (content && content.style.display === 'none') {
            toggleConfigSection(); // 展开
        }
    }
    // 否则保持折叠状态（默认）
}

/**
 * 保存并验证 API Key，然后自动验证大模型配置
 */
async function handleSaveAndVerifyApiKey() {
    const apiKey = apiKeyInput?.value?.trim() || '';

    // 显示验证中状态
    if (apiKeyVerifyStatus) {
        apiKeyVerifyStatus.textContent = '验证中...';
        apiKeyVerifyStatus.className = 'verify-status checking';
    }
    if (saveAndVerifyApiKeyBtn) {
        saveAndVerifyApiKeyBtn.disabled = true;
    }

    try {
        // 1. 保存到服务器（即使为空也保存）
        await fetch('/api/save-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: apiKey })
        });

        // 2. 如果 API Key 为空，直接标记为无效状态
        if (!apiKey) {
            showToast('API Key 已清空', 'info');
            if (apiKeyVerifyStatus) {
                apiKeyVerifyStatus.textContent = '✗ 未配置';
                apiKeyVerifyStatus.className = 'verify-status error';
            }
            // 更新 API Key 状态灯为熄灭（错误状态）
            const apiKeyStatusDot = document.getElementById('apiKeyStatusDot');
            if (apiKeyStatusDot) {
                apiKeyStatusDot.className = 'status-dot status-error';
            }
            // 同步更新大模型状态（因为大模型默认使用 Paraformer API Key）
            await saveLlmConfig(true);
            // 更新配置摘要
            updateConfigStatusSummary();
            return;
        }

        // 3. 验证 API Key（通过测试大模型接口）
        const response = await fetch('/api/llm_process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                api_key: apiKey,
                api_url: LLM_DEFAULTS.apiUrl,
                model: LLM_DEFAULTS.model,
                prompt: '请回复"测试成功"四个字。',
                content: '这是一个测试请求。'
            })
        });

        const data = await response.json();

        if (data.success) {
            // API Key 验证成功
            showToast('API Key 验证成功', 'success');
            if (apiKeyVerifyStatus) {
                apiKeyVerifyStatus.textContent = '✓ 验证通过';
                apiKeyVerifyStatus.className = 'verify-status ok';
            }

            // 更新 API Key 状态灯为绿色
            const apiKeyStatusDot = document.getElementById('apiKeyStatusDot');
            if (apiKeyStatusDot) {
                apiKeyStatusDot.className = 'status-dot status-ok';
            }

            // 4. 自动验证大模型配置
            await saveLlmConfig(true);

            // 更新配置摘要
            updateConfigStatusSummary();

            // 尝试自动折叠配置（如果 Cookie 也有效）
            checkAutoCollapse();
        } else {
            showToast(`API Key 验证失败: ${data.error}`, 'error');
            if (apiKeyVerifyStatus) {
                apiKeyVerifyStatus.textContent = '✗ 验证失败';
                apiKeyVerifyStatus.className = 'verify-status error';
            }
            // 更新 API Key 状态灯为错误状态
            const apiKeyStatusDot = document.getElementById('apiKeyStatusDot');
            console.log('[API Key] 验证失败，更新状态灯:', apiKeyStatusDot);
            if (apiKeyStatusDot) {
                apiKeyStatusDot.className = 'status-dot status-error';
                console.log('[API Key] 状态灯已更新为 error');
            }
            // 更新配置摘要（在 saveLlmConfig 之前）
            updateConfigStatusSummary();
            // 同步更新大模型状态（因为大模型默认使用 Paraformer API Key）
            try {
                await saveLlmConfig(true);
            } catch (e) {
                console.error('[API Key] saveLlmConfig 出错:', e);
            }
        }
    } catch (error) {
        showToast(`验证失败: ${error.message}`, 'error');
        if (apiKeyVerifyStatus) {
            apiKeyVerifyStatus.textContent = '✗ 网络错误';
            apiKeyVerifyStatus.className = 'verify-status error';
        }
        // 更新 API Key 状态灯为错误状态
        const apiKeyStatusDot = document.getElementById('apiKeyStatusDot');
        if (apiKeyStatusDot) {
            apiKeyStatusDot.className = 'status-dot status-error';
        }
        // 同步更新大模型状态（因为大模型默认使用 Paraformer API Key）
        await saveLlmConfig(true);
        updateConfigStatusSummary();
    } finally {
        if (saveAndVerifyApiKeyBtn) {
            saveAndVerifyApiKeyBtn.disabled = false;
        }
    }
}
/**
 * 保存并测试LLM配置
 * @param {boolean} autoTriggered - 是否由 API Key 验证自动触发
 */
async function saveLlmConfig(autoTriggered = false) {
    const llmApiKey = document.getElementById('llmApiKey');
    const llmApiUrl = document.getElementById('llmApiUrl');
    const llmModel = document.getElementById('llmModelName');
    const llmPrompt = document.getElementById('llmPrompt');
    const saveBtn = document.getElementById('saveLlmConfigBtn');

    // 获取用户输入值
    const userApiKey = llmApiKey?.value?.trim() || '';
    const userApiUrl = llmApiUrl?.value?.trim() || '';
    const userModel = llmModel?.value?.trim() || '';
    const userPrompt = llmPrompt?.value?.trim() || '';

    // 判断是否全部使用默认值（用于决定状态灯颜色）
    const isAllDefault = !userApiKey && !userApiUrl && !userModel;

    // 应用默认值：LLM API Key 默认使用上方的 DashScope API Key
    const effectiveApiKey = userApiKey || (apiKeyInput?.value?.trim() || '');
    const effectiveApiUrl = userApiUrl || LLM_DEFAULTS.apiUrl;
    const effectiveModel = userModel || LLM_DEFAULTS.model;
    const effectivePrompt = userPrompt || LLM_DEFAULTS.prompt;

    // 保存到服务器（只保存用户实际输入的值，不保存默认值）
    try {
        await fetch('/api/save-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                llm_api_key: userApiKey,
                llm_api_url: userApiUrl,
                llm_model: userModel,
                llm_prompt: userPrompt
            })
        });
    } catch (error) {
        console.error('保存 LLM 配置失败:', error);
    }

    updateConfigStatusSummary();

    // 检查是否有可用的 API Key
    if (!effectiveApiKey) {
        if (!autoTriggered) {
            showToast('API Key 未配置，大模型功能不可用', 'warning');
        }
        // API Key 无效时，大模型状态显示为错误
        updateLlmStatus('error', '未配置');
        return;
    }

    // 开始测试
    if (saveBtn && !autoTriggered) {
        saveBtn.disabled = true;
        saveBtn.querySelector('span').textContent = '测试中...';
    }
    updateLlmStatus('checking', '正在测试...');

    // 测试并根据结果设置状态灯颜色
    const success = await testLlmConfig(effectiveApiKey, effectiveApiUrl, effectiveModel, isAllDefault);

    if (saveBtn && !autoTriggered) {
        saveBtn.disabled = false;
        saveBtn.querySelector('span').textContent = '保存配置并测试';
    }

    return success;
}

/**
 * 实际测试LLM配置的函数
 * @param {string} apiKey - API密钥
 * @param {string} apiUrl - API地址
 * @param {string} model - 模型名称
 * @param {boolean} isAllDefault - 是否全部使用默认值（用于决定状态灯颜色）
 */
async function testLlmConfig(apiKey, apiUrl, model, isAllDefault = false) {
    try {
        const response = await fetch('/api/llm_process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                api_key: apiKey,
                api_url: apiUrl,
                model: model,
                prompt: '请回复"测试成功"四个字。',
                content: '这是一个测试请求。'
            })
        });

        const data = await response.json();

        if (data.success) {
            showToast('LLM配置测试成功！', 'success');
            // 根据是否使用默认值决定状态灯颜色
            if (isAllDefault) {
                // 全部使用默认值：黄色
                updateLlmStatus('warning', '默认配置');
            } else {
                // 有自定义配置：绿色
                updateLlmStatus('ok', '已连接');
            }
            checkAutoCollapse(); // 尝试自动折叠
            return true;
        } else {
            showToast(`LLM测试失败: ${data.error}`, 'error');
            updateLlmStatus('error', '连接失败');
            return false;
        }
    } catch (error) {
        showToast(`LLM测试失败: ${error.message}`, 'error');
        updateLlmStatus('error', '连接失败');
        return false;
    }
}

/**
 * 更新LLM状态指示器
 */
function updateLlmStatus(status, text) {
    // 更新全局LLM状态
    switch (status) {
        case 'ok':
            currentLlmStatus = LLM_STATUS.OK;
            break;
        case 'default_ok':
            currentLlmStatus = LLM_STATUS.DEFAULT_OK;
            break;
        case 'error':
            currentLlmStatus = LLM_STATUS.ERROR;
            break;
        case 'checking':
            currentLlmStatus = LLM_STATUS.CHECKING;
            break;
        case 'warning':
            currentLlmStatus = LLM_STATUS.DEFAULT_OK; // 默认配置验证通过
            break;
        default:
            currentLlmStatus = LLM_STATUS.NONE;
    }

    // 更新内部状态指示器
    const statusIcon = document.getElementById('llmStatusIcon');
    const statusText = document.getElementById('llmStatusText');

    if (statusIcon && statusText) {
        const icons = {
            'ok': '🟢',
            'warning': '🟡',
            'error': '🔴',
            'checking': '🔵'
        };
        statusIcon.textContent = icons[status] || '⚪';
        statusText.textContent = text || '未配置';
    }

    // 更新配置摘要中的状态灯
    updateConfigStatusSummary();
}

/**
 * 清理临时文件
 */
async function cleanupTempFiles() {
    const cleanupBtn = document.getElementById('cleanupBtn');
    if (cleanupBtn) {
        cleanupBtn.disabled = true;
        cleanupBtn.querySelector('span').textContent = '清理中...';
    }

    try {
        const response = await fetch('/api/cleanup', { method: 'POST' });
        const data = await response.json();

        if (data.success) {
            showToast(data.message, 'success');
        } else {
            showToast(`清理失败: ${data.error}`, 'error');
        }
    } catch (error) {
        showToast(`清理失败: ${error.message}`, 'error');
    } finally {
        if (cleanupBtn) {
            cleanupBtn.disabled = false;
            cleanupBtn.querySelector('span').textContent = '清理缓存';
        }
    }
}

/**
 * 使用LLM处理字幕
 * @param {string} type - 'video' 或 'history'
 * @param {number|string} identifier - 视频索引或历史记录ID
 */
async function processWithLLM(type, identifier) {
    // 获取LLM配置（空值使用默认值）
    const userApiKey = document.getElementById('llmApiKey')?.value?.trim() || '';
    const userApiUrl = document.getElementById('llmApiUrl')?.value?.trim() || '';
    const userModelName = document.getElementById('llmModelName')?.value?.trim() || '';
    const userPrompt = document.getElementById('llmPrompt')?.value?.trim() || '';

    // 应用默认值
    const apiKey = userApiKey || (apiKeyInput?.value?.trim() || ''); // 使用 DashScope API Key
    const apiUrl = userApiUrl || LLM_DEFAULTS.apiUrl;
    const modelName = userModelName || LLM_DEFAULTS.model;
    const prompt = userPrompt || LLM_DEFAULTS.prompt;

    // 检查是否有可用的 API Key
    if (!apiKey) {
        showToast('请先配置 DashScope API Key', 'error');
        return;
    }

    // 获取字幕内容
    let transcript = '';
    let itemData = null;

    if (type === 'video') {
        const index = parseInt(identifier);
        transcript = videoTranscripts[index];
        itemData = videoList.find(v => v.index === index);
        if (!transcript) {
            showToast('该视频尚未提取字幕', 'error');
            return;
        }
    } else if (type === 'history') {
        const item = historyData.find(h => h.id === identifier);
        if (item) {
            transcript = item.transcript;
            itemData = item;
        }
        if (!transcript) {
            showToast('未找到字幕内容', 'error');
            return;
        }
    }

    // 显示处理中状态
    showToast('AI处理中，请稍候...', 'info');

    try {
        const response = await fetch('/api/llm_process', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                api_key: apiKey,
                api_url: apiUrl,
                model: modelName,
                prompt: prompt || '请分析以下视频字幕内容，提取主要观点并生成摘要：',
                content: transcript
            })
        });

        const data = await response.json();

        if (data.success) {
            const aiResult = data.content;

            if (type === 'video') {
                // 存储到videoAiResults (使用整数key，与读取时一致)
                const numericIndex = parseInt(identifier);
                videoAiResults[numericIndex] = aiResult;

                // 如果当前选中的就是这个视频，更新显示
                if (selectedVideoIndex === numericIndex) {
                    displayVideoWithAiResult(numericIndex);
                }

                // 同步更新历史记录
                if (itemData && itemData.url) {
                    let historyItem = historyData.find(h => h.url === itemData.url);

                    // 如果URL完全匹配失败，且itemData有ID（BV号），尝试通过URL包含BV号来匹配
                    if (!historyItem && itemData.id && typeof itemData.id === 'string') {
                        // 尝试从URL提取BV号（如果ID不是BV号）
                        let bvid = itemData.id.startsWith('BV') ? itemData.id : null;
                        if (!bvid && itemData.url) {
                            const match = itemData.url.match(/(BV\w+)/);
                            if (match) bvid = match[1];
                        }

                        if (bvid) {
                            historyItem = historyData.find(h => (h.url && h.url.includes(bvid)) || (h.title === itemData.title));
                            if (historyItem) {
                                console.log('通过BV号/标题模糊匹配找到历史记录:', bvid, historyItem.title);
                            }
                        }
                    }

                    if (historyItem) {
                        console.log('同步AI结果到历史记录:', historyItem.title);
                        historyItem.aiAbstract = aiResult;
                        saveHistoryData();
                        // 如果当前查看的就是这个历史记录，尝试刷新（如果在历史视图中）
                        // 但注意不要与当前视图冲突。
                    } else {
                        console.warn('未找到对应的历史记录用于同步:', itemData.title, itemData.url);
                    }
                } else {
                    console.warn('视频数据缺少URL，无法同步历史记录:', itemData);
                }
            } else if (type === 'history') {
                // 更新历史记录中的aiAbstract字段
                const itemIndex = historyData.findIndex(h => h.id === identifier);
                if (itemIndex !== -1) {
                    historyData[itemIndex].aiAbstract = aiResult;
                    saveHistoryData();

                    // 如果当前选中的就是这个历史记录，更新显示
                    if (selectedHistoryId === identifier) {
                        displayHistoryWithAiResult(identifier);
                    }
                }
            }

            showToast('AI处理完成！', 'success');
        } else {
            showToast(`AI处理失败: ${data.error}`, 'error');
        }
    } catch (error) {
        showToast(`AI处理失败: ${error.message}`, 'error');
    }
}

/**
 * 显示视频字幕和AI结果
 */
function displayVideoWithAiResult(index) {
    const transcript = videoTranscripts[index];
    const aiResult = videoAiResults[index];

    if (!transcript) {
        transcriptContainer.innerHTML = `
            <div class="empty-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                    <line x1="8" y1="9" x2="16" y2="9"/>
                    <line x1="8" y1="13" x2="14" y2="13"/>
                </svg>
                <p>该视频尚未提取字幕</p>
            </div>
        `;
        copyBtn.disabled = true;
        return;
    }

    // Render content
    let content = '';

    // AI result display
    if (aiResult) {
        content += `<div class="ai-result-section">
            <div class="ai-result-header">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10" />
                    <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
                    <line x1="12" y1="17" x2="12.01" y2="17" />
                </svg>
                AI 处理结果
            </div>
            <div class="ai-result-content">${escapeHtml(aiResult)}</div>
        </div>
        <div class="transcript-divider"></div>`;
    }

    // Transcript content (using Flex layout, consistent with history tasks)
    content += `<div class="transcript-edit-wrapper">
        <textarea class="transcript-editor" id="currentTranscriptEditor" readonly
            placeholder="字幕内容...">${escapeHtml(transcript)}</textarea>
    </div>`;

    transcriptContainer.innerHTML = content;
    if (copyBtn) copyBtn.disabled = false;
}

/**
 * 显示历史记录字幕和AI结果（支持编辑和自动保存）
 */
function displayHistoryWithAiResult(id) {
    const item = historyData.find(h => h.id === id);
    if (!item || !historyTranscriptContainer) return;

    const aiResult = item.aiAbstract;
    let content = '';

    if (aiResult) {
        content += `<div class="ai-result-section">
            <div class="ai-result-header">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"/>
                    <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
                    <line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
                <span>AI 处理结果</span>
            </div>
            <div class="ai-result-content">${escapeHtml(aiResult)}</div>
        </div>
        <div class="transcript-divider"></div>`;
    }

    // 使用textarea实现可编辑字幕
    content += `<div class="transcript-edit-wrapper">
        <span class="transcript-save-indicator" id="saveIndicator">已保存</span>
        <textarea class="transcript-editor" id="transcriptEditor"
            placeholder="字幕内容..."
            data-id="${id}">${escapeHtml(item.transcript)}</textarea>
    </div>`;

    historyTranscriptContainer.innerHTML = content;

    // 添加编辑事件监听
    const editor = document.getElementById('transcriptEditor');
    if (editor) {
        let saveTimeout = null;
        const saveIndicator = document.getElementById('saveIndicator');

        editor.addEventListener('input', () => {
            // 防抖：500ms后自动保存
            if (saveTimeout) clearTimeout(saveTimeout);

            saveTimeout = setTimeout(() => {
                const itemId = editor.dataset.id;
                const newContent = editor.value;
                const itemIndex = historyData.findIndex(h => h.id === itemId);

                if (itemIndex !== -1) {
                    historyData[itemIndex].transcript = newContent;
                    saveHistoryData();

                    // 显示保存指示
                    if (saveIndicator) {
                        saveIndicator.classList.add('visible');
                        setTimeout(() => saveIndicator.classList.remove('visible'), 1500);
                    }
                }
            }, 500);
        });
    }
}

/**
 * 检查Cookie是否包含必要字段
 */
function checkCookieFields(cookie) {
    if (!cookie) return { valid: false, missing: ['SESSDATA', 'buvid3', 'bili_jct'] };

    const required = ['SESSDATA', 'buvid3', 'bili_jct'];
    const missing = required.filter(field => !cookie.includes(field + '='));

    return {
        valid: missing.length === 0,
        missing: missing
    };
}

/**
 * 验证Cookie有效性（带重试机制）
 * @param {string} cookie - Cookie 字符串
 * @param {number} retryCount - 当前重试次数（内部使用）
 */
async function verifyCookie(cookie, retryCount = 0) {
    const MAX_RETRIES = 3;
    const RETRY_DELAY = 2000; // 2秒

    if (!cookie) {
        updateCookieStatus(COOKIE_STATUS.NONE);
        return false;
    }

    // 先检查必要字段
    const fieldCheck = checkCookieFields(cookie);
    if (!fieldCheck.valid) {
        updateCookieStatus(COOKIE_STATUS.INCOMPLETE, null, fieldCheck.missing);
        return false;
    }

    // 显示验证中状态
    updateCookieStatus(COOKIE_STATUS.CHECKING);

    try {
        const response = await fetch(`${API_BASE}/api/verify_cookie`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cookie: cookie })
        });

        const data = await response.json();

        if (data.valid) {
            currentUsername = data.username || '已登录';
            updateCookieStatus(COOKIE_STATUS.VALID, currentUsername);
            return true;
        } else {
            updateCookieStatus(COOKIE_STATUS.INVALID);
            return false;
        }
    } catch (error) {
        console.error(`验证Cookie失败 (尝试 ${retryCount + 1}/${MAX_RETRIES}):`, error);

        // 如果是网络错误且还有重试次数，则重试
        if (retryCount < MAX_RETRIES - 1) {
            console.log(`[Cookie] ${RETRY_DELAY / 1000}秒后重试...`);
            await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
            return verifyCookie(cookie, retryCount + 1);
        }

        updateCookieStatus(COOKIE_STATUS.INVALID);
        return false;
    }
}

/**
 * 更新Cookie状态显示
 */
function updateCookieStatus(status, username = null, missingFields = []) {
    currentCookieStatus = status;

    if (!cookieStatusBar || !cookieStatusIcon || !cookieStatusText) return;

    // 移除所有状态类
    cookieStatusBar.className = 'cookie-status-bar';
    if (verifyCookieBtn) {
        verifyCookieBtn.classList.remove('verifying', 'valid', 'invalid');
        verifyCookieBtn.innerHTML = `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
        `; // Reset icon
    }

    switch (status) {
        case COOKIE_STATUS.NONE:
            cookieStatusBar.classList.add('status-none');
            cookieStatusIcon.textContent = '⚪';
            cookieStatusText.textContent = '未配置Cookie';
            break;

        case COOKIE_STATUS.CHECKING:
            cookieStatusBar.classList.add('status-checking');
            cookieStatusIcon.textContent = '🔄';
            cookieStatusText.textContent = '验证中...';
            if (verifyCookieBtn) {
                verifyCookieBtn.classList.add('verifying');
            }
            break;

        case COOKIE_STATUS.VALID:
            cookieStatusBar.classList.add('status-valid');
            cookieStatusIcon.textContent = '✅';
            cookieStatusText.textContent = `Cookie有效 (${username || '已登录'})`;
            if (verifyCookieBtn) {
                verifyCookieBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="20 6 9 17 4 12"></polyline>
                    </svg>
                `;
                verifyCookieBtn.classList.add('valid');
            }
            checkAutoCollapse(); // 尝试自动折叠
            break;

        case COOKIE_STATUS.INVALID:
            cookieStatusBar.classList.add('status-invalid');
            cookieStatusIcon.textContent = '❌';
            cookieStatusText.textContent = 'Cookie已失效，请重新获取';
            if (verifyCookieBtn) {
                verifyCookieBtn.classList.add('invalid');
            }
            break;

        case COOKIE_STATUS.INCOMPLETE:
            cookieStatusBar.classList.add('status-incomplete');
            cookieStatusIcon.textContent = '⚠️';
            cookieStatusText.textContent = `缺少必要字段: ${missingFields.join(', ')}`;
            if (verifyCookieBtn) {
                verifyCookieBtn.classList.add('invalid');
            }
            break;
    }

    // 更新折叠状态指示器
    updateConfigStatusSummary();
}

/**
 * 保存并验证Cookie
 */
async function saveAndVerifyCookie() {
    if (!fullCookieText) return;

    const cookieValue = fullCookieText.value.trim();

    // 如果 Cookie 为空，保存空值并更新状态为无效
    if (!cookieValue) {
        // 清空隐藏 input
        if (biliCookieInput) {
            biliCookieInput.value = '';
        }

        // 保存到服务器（空值）
        try {
            await fetch('/api/save-config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bili_cookie: '' })
            });
        } catch (error) {
            console.error('保存 Cookie 失败:', error);
        }

        // 更新状态为无效
        updateCookieStatus(COOKIE_STATUS.NONE);
        showToast('Cookie 已清空', 'info');
        updateConfigStatusSummary();
        return;
    }

    // 检查必要字段
    const fieldCheck = checkCookieFields(cookieValue);
    if (!fieldCheck.valid) {
        showToast(`Cookie缺少必要字段: ${fieldCheck.missing.join(', ')}`, 'error');
        updateCookieStatus(COOKIE_STATUS.INCOMPLETE, null, fieldCheck.missing);
        return;
    }

    // 保存到隐藏 input
    if (biliCookieInput) {
        biliCookieInput.value = cookieValue;
    }

    // 保存到服务器
    try {
        await fetch('/api/save-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bili_cookie: cookieValue })
        });
    } catch (error) {
        console.error('保存 Cookie 失败:', error);
    }

    // 验证Cookie
    const isValid = await verifyCookie(cookieValue);

    if (isValid) {
        showToast('Cookie已保存并验证通过', 'success');
    } else {
        showToast('Cookie保存成功，但验证失败，可能已过期', 'error');
    }
}

/**
 * 检查Cookie是否可用（在关键操作前调用）
 */
async function ensureCookieValid() {
    const cookie = biliCookieInput?.value;

    if (!cookie) {
        showToast('请先配置B站Cookie', 'error');
        return false;
    }

    // 检查必要字段
    const fieldCheck = checkCookieFields(cookie);
    if (!fieldCheck.valid) {
        showToast(`Cookie缺少必要字段: ${fieldCheck.missing.join(', ')}`, 'error');
        updateCookieStatus(COOKIE_STATUS.INCOMPLETE, null, fieldCheck.missing);
        return false;
    }

    // 如果当前状态不是有效，重新验证
    if (currentCookieStatus !== COOKIE_STATUS.VALID) {
        const isValid = await verifyCookie(cookie);
        if (!isValid) {
            showToast('Cookie已失效，请重新获取', 'error');
            return false;
        }
    }

    return true;
}

// 以下函数已废弃，保留空实现以防止错误
function updateLoginStatus(valid, text) {
    // 已被updateCookieStatus替代
}

/**
 * 切换Cookie显示状态
 */
function toggleCookieVisibility() {
    const input = document.getElementById('fullCookieText');
    const btn = document.getElementById('toggleCookieVisibility');

    if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                <circle cx="12" cy="12" r="3"></circle>
            </svg>
        `;
    } else {
        input.type = 'password';
        btn.innerHTML = `
             <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
                <line x1="1" y1="1" x2="23" y2="23"></line>
            </svg>
        `;
    }
}

/**
 * 切换API Key可见性
 */
function toggleApiKeyVisibility() {
    // 动态获取元素，确保安全
    const input = document.getElementById('apiKey');
    const btn = document.getElementById('toggleApiKey');

    if (!input || !btn) return;

    if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                <circle cx="12" cy="12" r="3"></circle>
            </svg>
        `;
        btn.title = "隐藏";
    } else {
        input.type = 'password';
        btn.innerHTML = `
             <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
                <line x1="1" y1="1" x2="23" y2="23"></line>
            </svg>
        `;
        btn.title = "显示";
    }
}

/**
 * HTML转义
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 显示Toast通知
 */
function showToast(message, type = 'info') {
    toast.textContent = message;
    toast.className = `toast ${type}`;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3000);
}

/**
 * 设置按钮状态
 */
function setButtonLoading(loading) {
    const buttonText = extractBtn.querySelector('span');
    const buttonIcon = extractBtn.querySelector('.button-icon');
    const buttonLoader = extractBtn.querySelector('.button-loader');

    extractBtn.disabled = loading;

    if (loading) {
        buttonText.textContent = '处理中...';
        buttonIcon.style.display = 'none';
        buttonLoader.style.display = 'block';
    } else {
        buttonText.textContent = '提取字幕';
        buttonIcon.style.display = 'block';
        buttonLoader.style.display = 'none';
    }
}

/**
 * 显示/隐藏进度条
 */
function showProgress(show) {
    const progressSection = document.getElementById('progressSection');
    if (progressSection) {
        progressSection.style.display = show ? 'block' : 'none';
    }
}

/**
 * 更新进度条
 */
function updateProgress(stage, progress) {
    const progressBar = document.getElementById('progressBar');
    const progressPercent = document.getElementById('progressPercent');
    const progressStage = document.getElementById('progressStage');

    if (progressBar) progressBar.style.width = `${progress}%`;
    if (progressPercent) progressPercent.textContent = `${progress}%`;

    const stageNames = {
        'init': '准备中...',
        'download': '下载视频音频',
        'convert': '转换音频格式',
        'transcribe': '语音识别中...',
        'complete': '处理完成！',
        'error': '处理出错'
    };

    if (progressStage) {
        progressStage.textContent = stageNames[stage] || stage;
    }

    const steps = ['download', 'convert', 'transcribe', 'complete'];
    const currentIndex = steps.indexOf(stage);

    steps.forEach((step, index) => {
        const stepElement = document.getElementById(`step-${step}`);
        if (stepElement) {
            stepElement.classList.remove('active', 'completed');
            if (index < currentIndex) {
                stepElement.classList.add('completed');
            } else if (index === currentIndex) {
                stepElement.classList.add('active');
            }
        }
    });
}

/**
 * 显示视频列表为空状态
 */
function showVideoListEmpty() {
    videoListContainer.innerHTML = `
        <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <rect x="2" y="7" width="20" height="15" rx="2" ry="2"/>
                <polyline points="17 2 12 7 7 2"/>
            </svg>
            <p>输入播放列表或合集链接后，视频列表将显示在这里</p>
        </div>
    `;
    if (videoCountSpan) videoCountSpan.textContent = '';
}

/**
 * 视频进度状态存储 {index: {status, progress}}
 */
let videoProgress = {};

/**
 * 渲染视频列表（与历史任务样式一致，包含复选框和进度条）
 */
function renderVideoList() {
    if (videoList.length === 0) {
        showVideoListEmpty();
        return;
    }

    if (videoCountSpan) {
        videoCountSpan.textContent = `(${videoList.length} 个视频)`;
    }

    let html = '';
    videoList.forEach(video => {
        const hasTranscript = videoTranscripts[video.index];
        const statusClass = video.status || '';
        const statusText = video.statusText || '';
        const isActive = selectedVideoIndex === video.index;
        const isChecked = selectedVideoIndices.has(video.index);

        // 获取进度信息
        const progress = videoProgress[video.index] || { status: 'pending', progress: 0 };
        const progressPercent = progress.progress || 0;

        // 状态徽章：完成、失败或取消时显示
        let statusBadge = '';
        let progressBarClass = '';
        let showRetryBtn = false;
        if (progress.status === 'completed' || video.status === 'completed') {
            statusBadge = '<span class="video-result-badge success">已完成</span>';
            progressBarClass = 'completed';
        } else if (progress.status === 'error' || video.status === 'error') {
            statusBadge = '<span class="video-result-badge error">提取失败</span>';
            progressBarClass = 'error';
            showRetryBtn = true;
        } else if (progress.status === 'cancelled' || video.status === 'cancelled') {
            statusBadge = '<span class="video-result-badge cancelled">已取消</span>';
            progressBarClass = 'cancelled';
            showRetryBtn = true;
        } else if (progress.status === 'processing') {
            progressBarClass = 'processing';
        }

        // 封面图：如果有就显示，否则使用占位符
        const coverUrl = video.pic || 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 100"%3E%3Crect fill="%23333" width="160" height="100"/%3E%3Ctext x="50%25" y="50%25" fill="%23666" text-anchor="middle" dy=".3em"%3E' + video.index + '%3C/text%3E%3C/svg%3E';

        // 重试按钮HTML（仅失败或取消时显示）
        const retryBtnHtml = showRetryBtn ? `
            <button class="video-retry-btn" title="重试" 
                    onclick="event.stopPropagation(); retrySingleVideo(${video.index})">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="23 4 23 10 17 10"></polyline>
                    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
                </svg>
            </button>
        ` : '';

        html += `
            <div class="video-item history-item-card ${statusClass} ${isActive ? 'active' : ''}"
                 data-index="${video.index}"
                 id="video-card-${video.index}"
                 onclick="selectVideo(${video.index})">
                ${retryBtnHtml}
                <input type="checkbox" class="video-checkbox"
                       ${isChecked ? 'checked' : ''}
                       onclick="event.stopPropagation(); toggleVideoSelection(${video.index})">
                <div class="video-cover">
                    <img src="${coverUrl}" alt="封面" loading="lazy" referrerpolicy="no-referrer"
                         onerror="this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 160 100%22%3E%3Crect fill=%22%23333%22 width=%22160%22 height=%22100%22/%3E%3Ctext x=%2250%25%22 y=%2250%25%22 fill=%22%23666%22 text-anchor=%22middle%22 dy=%22.3em%22%3E${video.index}%3C/text%3E%3C/svg%3E'">
                </div>
                <div class="video-info-wrapper">
                    <div class="video-title-area">
                        <span class="video-title" title="${escapeHtml(video.title)}">${escapeHtml(video.title)}</span>
                    </div>
                    <div class="video-meta-area">
                        <span class="video-author">UP主: ${escapeHtml(video.owner || '未知')}</span>
                        <div class="video-actions">
                            <button class="video-action-btn" title="查看原视频"
                                    onclick="event.stopPropagation(); window.open('${video.url}', '_blank')">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
                                    <polyline points="15 3 21 3 21 9"/>
                                    <line x1="10" y1="14" x2="21" y2="3"/>
                                </svg>
                            </button>
                            <button class="video-action-btn" title="下载Markdown"
                                    onclick="event.stopPropagation(); downloadTranscript(${video.index})">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                    <polyline points="7 10 12 15 17 10"/>
                                    <line x1="12" y1="15" x2="12" y2="3"/>
                                </svg>
                            </button>
                            <button class="video-action-btn" title="复制字幕"
                                    onclick="event.stopPropagation(); copyVideoTranscript(${video.index})">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                                </svg>
                            </button>
                            <button class="video-action-btn ai-btn" title="AI处理"
                                    onclick="event.stopPropagation(); processWithLLM('video', ${video.index})">
                                AI
                            </button>
                            <button class="video-action-btn delete-btn" title="删除"
                                    onclick="event.stopPropagation(); deleteVideoItem(${video.index})">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <polyline points="3 6 5 6 21 6"/>
                                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                                    <line x1="10" y1="11" x2="10" y2="17"/>
                                    <line x1="14" y1="11" x2="14" y2="17"/>
                                </svg>
                            </button>
                        </div>
                        ${statusBadge}
                    </div>
                </div>
                <!-- 进度条：贯穿卡片底部 -->
                <div class="video-card-progress ${progressBarClass}" id="progress-bar-${video.index}">
                    <div class="video-card-progress-fill" style="width: ${progressPercent}%"></div>
                </div>
            </div>
        `;
    });

    videoListContainer.innerHTML = html;

    // Update batch operation button states
    updateCurrentBatchButtons();
}

/**
 * 重试单个失败的视频
 * 根据当前最新的配置状态重新进行字幕提取
 */
async function retrySingleVideo(index) {
    const video = videoList.find(v => v.index === index);
    if (!video) {
        showToast('找不到该视频', 'error');
        return;
    }

    // 获取当前最新的配置状态
    const apiKey = apiKeyInput ? apiKeyInput.value.trim() : '';
    const biliCookie = biliCookieInput ? biliCookieInput.value.trim() : '';
    const cookieValid = currentCookieStatus === COOKIE_STATUS.VALID;
    const apiKeyValid = apiKey && document.getElementById('apiKeyStatusDot')?.classList.contains('status-ok');

    // 检查是否可以进行提取
    if (!cookieValid && !apiKeyValid) {
        showToast('Cookie 和 API Key 均无效，无法提取字幕', 'error');
        return;
    }

    // 获取存储模式配置
    const useSelfHostedToggle = document.getElementById('useSelfHostedStorage');
    const useSelfHosted = useSelfHostedToggle ? useSelfHostedToggle.checked : false;
    const selfHostedDomain = useSelfHosted ? window.location.origin : '';

    // 更新视频状态为处理中
    video.status = 'processing';
    video.statusText = '重新处理中...';
    videoProgress[index] = { status: 'processing', progress: 0 };
    renderVideoList();
    showToast(`正在重新提取：${video.title}`, 'info');

    try {
        console.log(`[Retry] Starting retry for video ${index}: ${video.title}`);

        // 调用批量处理API（只处理单个视频）
        const response = await fetch(`${API_BASE}/api/transcribe_batch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                videos: [video],  // 只传入一个视频
                api_key: apiKey,
                bili_cookie: biliCookie,
                use_self_hosted: useSelfHosted,
                self_hosted_domain: selfHostedDomain,
                cookie_valid: cookieValid,
                api_valid: apiKeyValid
            })
        });

        const data = await response.json();

        if (!response.ok || !data.success) {
            throw new Error(data.error || '重试请求失败');
        }

        const batchId = data.batch_id;
        console.log(`[Retry] Batch started for single video: ${batchId}`);

        // 轮询获取结果
        await pollRetryResult(batchId, index);

    } catch (error) {
        console.error(`[Retry] Failed for video ${index}:`, error);
        video.status = 'error';
        video.statusText = `失败: ${error.message}`;
        videoProgress[index] = { status: 'error', progress: 100 };
        renderVideoList();
        showToast(`重试失败: ${error.message}`, 'error');
    }
}

/**
 * 轮询重试结果（用于单视频重试）
 */
async function pollRetryResult(batchId, videoIndex) {
    const maxAttempts = 600; // 最多轮询10分钟（语音转录可能较慢）
    let attempts = 0;

    while (attempts < maxAttempts) {
        try {
            const response = await fetch(`${API_BASE}/api/batch_status/${batchId}`);
            const json = await response.json();

            if (!response.ok) {
                throw new Error('获取状态失败');
            }

            const data = json.data; // 后端返回 { success: true, data: { ... } }

            // 查找对应视频的状态 (后端返回的是 original_index)
            const videoStatus = data.videos?.find(v => v.original_index === videoIndex);

            if (videoStatus) {
                // 更新进度
                updateVideoCardProgress(videoIndex, videoStatus.status, videoStatus.progress || 0);

                if (videoStatus.status === 'completed') {
                    // 成功完成
                    const video = videoList.find(v => v.index === videoIndex);
                    if (videoStatus.result && videoStatus.result.transcript) {
                        videoTranscripts[videoIndex] = videoStatus.result.transcript;

                        // 更新历史记录
                        if (video) {
                            addToHistory(video, videoStatus.result.transcript, videoStatus.result.metadata || {});
                        }
                    }
                    showToast(`重试成功：${video?.title || '视频'}`, 'success');
                    renderVideoList();
                    return;
                } else if (videoStatus.status === 'error' || videoStatus.status === 'failed') {
                    // 失败
                    throw new Error(videoStatus.error || '提取失败');
                }
            }

            // 检查批次是否完成
            if (data.status === 'completed' || data.status === 'error') {
                break;
            }

            // 等待1秒后继续轮询
            await new Promise(resolve => setTimeout(resolve, 1000));
            attempts++;
        } catch (error) {
            console.error(`[Retry Poll] Error:`, error);
            throw error;
        }
    }

    // 超时
    throw new Error('处理时间较长，请稍后查看历史记录确认结果');
}

/**
 * 更新单个视频卡片的进度条（不重建整个列表，提高性能）
 */
function updateVideoCardProgress(index, status, progress) {
    // 更新内存中的状态
    videoProgress[index] = { status, progress };

    // 同步更新 videoList 中的状态（确保 renderVideoList 时能正确显示徽章）
    const video = videoList.find(v => v.index === index);
    if (video && (status === 'completed' || status === 'error' || status === 'cancelled')) {
        video.status = status;
        if (status === 'completed') {
            video.statusText = '已完成';
        } else if (status === 'error') {
            video.statusText = '提取失败';
        } else {
            video.statusText = '已取消';
        }
    }

    const progressBar = document.getElementById(`progress-bar-${index}`);
    const card = document.getElementById(`video-card-${index}`);

    if (progressBar) {
        const fill = progressBar.querySelector('.video-card-progress-fill');
        if (fill) {
            fill.style.width = `${progress}%`;
        }

        // 更新状态类
        progressBar.classList.remove('pending', 'processing', 'completed', 'error', 'cancelled');
        progressBar.classList.add(status);
    }

    // 更新状态徽章和重试按钮
    if (card) {
        // 移除旧的徽章
        const oldBadge = card.querySelector('.video-result-badge');
        if (oldBadge) oldBadge.remove();

        // 移除旧的重试按钮（状态变化时清理）
        if (status !== 'error' && status !== 'cancelled') {
            const oldRetryBtn = card.querySelector('.video-retry-btn');
            if (oldRetryBtn) oldRetryBtn.remove();
        }

        // 添加新徽章（完成、失败或取消时）
        if (status === 'completed' || status === 'error' || status === 'cancelled') {
            const metaArea = card.querySelector('.video-meta-area');
            console.log(`[Badge] 视频${index}: status=${status}, metaArea存在=${!!metaArea}`);
            if (metaArea) {
                const badge = document.createElement('span');
                let badgeClass = 'success';
                let badgeText = '已完成';

                if (status === 'error') {
                    badgeClass = 'error';
                    badgeText = '提取失败';
                } else if (status === 'cancelled') {
                    badgeClass = 'cancelled';
                    badgeText = '已取消';
                }

                badge.className = `video-result-badge ${badgeClass}`;
                badge.textContent = badgeText;
                metaArea.appendChild(badge);
                console.log(`[Badge] 视频${index}: 徽章已添加 (${badgeText})`);
            }
        }

        // 动态添加重试按钮（失败或取消时）
        if (status === 'error' || status === 'cancelled') {
            // 检查是否已存在重试按钮
            if (!card.querySelector('.video-retry-btn')) {
                const retryBtn = document.createElement('button');
                retryBtn.className = 'video-retry-btn';
                retryBtn.title = '重试';
                retryBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="23 4 23 10 17 10"></polyline>
                        <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
                    </svg>
                `;
                retryBtn.onclick = (event) => {
                    event.stopPropagation();
                    retrySingleVideo(index);
                };
                card.appendChild(retryBtn);
                console.log(`[Retry] 视频${index}: 重试按钮已添加`);
            }
        }
    } else {
        console.warn(`[Badge] 视频${index}: 找不到卡片元素 video-card-${index}`);
    }
}

/**
 * 删除单个视频项
 */
function deleteVideoItem(index) {
    const video = videoList.find(v => v.index === index);
    if (!video) return;

    // 直接删除，无需确认
    // 同步删除历史记录
    if (video.url) {
        deleteHistoryItemByUrl(video.url);
    } else if (video.id && typeof video.id === 'string' && video.id.startsWith('BV')) {
        // 尝试通过BV号匹配删除
        const historyItem = historyData.find(h => h.url && h.url.includes(video.id));
        if (historyItem) {
            deleteHistoryItemByUrl(historyItem.url);
        }
    }

    // 从列表中移除
    const listIndex = videoList.findIndex(v => v.index === index);
    if (listIndex !== -1) {
        videoList.splice(listIndex, 1);
        delete videoTranscripts[index];
        delete videoAiResults[index]; // 也要清理AI结果
        delete videoProgress[index];  // 清理进度跟踪
    }

    // 更新选中状态
    selectedVideoIndices.delete(index);
    if (selectedVideoIndex === index) {
        selectedVideoIndex = null;
        // 清空字幕显示区域
        transcriptContainer.innerHTML = `
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                    </svg>
                    <p>点击左侧视频列表中的视频名称查看字幕</p>
                </div>
            `;
        if (currentVideoTitle) currentVideoTitle.textContent = '选择视频查看字幕';
    }

    renderVideoList();
    showToast('已删除', 'success');

    // 检查删除后是否所有剩余视频都已完成
    checkAndCompletePollingAfterDeletion();
}

/**
 * 检查删除视频后是否应该停止轮询（当所有剩余视频都已完成时）
 */
function checkAndCompletePollingAfterDeletion() {
    // 如果没有在轮询中，无需处理
    if (!pollInterval || !currentBatchId) return;

    // 如果列表为空，停止轮询
    if (videoList.length === 0) {
        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }
        currentBatchId = null;

        // 隐藏进度区域
        const batchSection = document.getElementById('batchProgressSection');
        if (batchSection) batchSection.style.display = 'none';

        // 重新启用提取按钮
        if (extractBtn) {
            extractBtn.disabled = false;
            extractBtn.querySelector('.button-loader').style.display = 'none';
            extractBtn.querySelector('.button-text').textContent = '提取字幕';
        }
        return;
    }

    // 检查所有剩余视频是否都已完成
    const allDone = videoList.every(v => {
        const progress = videoProgress[v.index];
        const status = progress?.status || v.status;
        return status === 'completed' || status === 'error' || status === 'cancelled';
    });

    if (allDone) {
        console.log('[Poll] 删除后所有剩余视频已完成，停止轮询');

        // 停止轮询
        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }

        // 更新总进度条为100%
        const batchTitle = document.getElementById('batchTitle');
        const batchTotalProgressBar = document.getElementById('batchTotalProgressBar');
        const batchTotalPercent = document.getElementById('batchTotalPercent');
        const batchStatusBadge = document.getElementById('batchStatusBadge');
        const cancelBtn = document.getElementById('cancelBatchBtn');

        if (batchTitle) batchTitle.textContent = `处理完成 ${videoList.length}/${videoList.length} 个视频`;
        if (batchTotalProgressBar) batchTotalProgressBar.style.width = '100%';
        if (batchTotalPercent) batchTotalPercent.textContent = '100%';
        if (batchStatusBadge) {
            batchStatusBadge.textContent = '已完成';
            batchStatusBadge.className = 'batch-status-badge completed';
        }
        if (cancelBtn) cancelBtn.style.display = 'none';

        // 重新启用提取按钮
        if (extractBtn) {
            extractBtn.disabled = false;
            extractBtn.querySelector('.button-loader').style.display = 'none';
            extractBtn.querySelector('.button-text').textContent = '提取字幕';
        }

        // 计算结果统计
        const successCount = videoList.filter(v => {
            const status = videoProgress[v.index]?.status || v.status;
            return status === 'completed';
        }).length;
        const errorCount = videoList.filter(v => {
            const status = videoProgress[v.index]?.status || v.status;
            return status === 'error';
        }).length;

        showToast(`处理完成！成功 ${successCount} 个${errorCount > 0 ? `，失败 ${errorCount} 个` : ''}`, 'success');
    }
}

/**
 * Toggle video selection state
 */
function toggleVideoSelection(index) {
    if (selectedVideoIndices.has(index)) {
        selectedVideoIndices.delete(index);
    } else {
        selectedVideoIndices.add(index);
    }
    renderVideoList();
    updateCurrentSelectAllState();
}

/**
 * Update current task select all button state
 */
function updateCurrentSelectAllState() {
    const currentSelectAll = document.getElementById('currentSelectAll');
    if (currentSelectAll) {
        currentSelectAll.checked = videoList.length > 0 && selectedVideoIndices.size === videoList.length;
        currentSelectAll.indeterminate = selectedVideoIndices.size > 0 && selectedVideoIndices.size < videoList.length;
    }
}

/**
 * Update current task batch operation buttons
 */
function updateCurrentBatchButtons() {
    const hasSelection = selectedVideoIndices.size > 0;
    const downloadBtn = document.getElementById('downloadAllBtn');
    const deleteBtn = document.getElementById('currentDeleteSelected');
    const aiBtn = document.getElementById('currentAiProcessSelected');

    if (downloadBtn) downloadBtn.disabled = !hasSelection;
    if (deleteBtn) deleteBtn.disabled = !hasSelection;
    if (aiBtn) aiBtn.disabled = !hasSelection;
}

/**
 * Handle current task select all
 */
function handleCurrentSelectAll(e) {
    if (e.target.checked) {
        selectedVideoIndices = new Set(videoList.map(v => v.index));
    } else {
        selectedVideoIndices.clear();
    }
    renderVideoList();
}

/**
 * Current task: Download selected
 */
async function handleCurrentDownloadSelected() {
    if (selectedVideoIndices.size === 0) return;

    const items = videoList
        .filter(v => selectedVideoIndices.has(v.index) && videoTranscripts[v.index])
        .map(v => ({
            title: v.title,
            url: v.url,
            owner: v.owner || '未知',
            transcript: videoTranscripts[v.index],
            pubdateFormatted: v.pubdateFormatted || '',
            tags: v.tags || []
        }));

    if (items.length === 0) {
        showToast('选中的视频没有字幕可下载', 'warning');
        return;
    }

    await downloadAsZip(items);
}

/**
 * Current task: Delete selected
 */
function handleCurrentDeleteSelected() {
    if (selectedVideoIndices.size === 0) return;

    // 直接删除，无需确认

    // Delete in reverse order to avoid index issues
    const indicesToDelete = Array.from(selectedVideoIndices).sort((a, b) => b - a);

    indicesToDelete.forEach(index => {
        const video = videoList.find(v => v.index === index);
        // 同步删除历史记录
        if (video && video.url) {
            deleteHistoryItemByUrl(video.url);
        }

        const listIndex = videoList.findIndex(v => v.index === index);
        if (listIndex !== -1) {
            videoList.splice(listIndex, 1);
            delete videoTranscripts[index];
        }
    });

    selectedVideoIndices.clear();
    selectedVideoIndex = -1;
    renderVideoList();

    // Clear transcript display
    const container = document.getElementById('transcriptContainer');
    if (container) {
        container.innerHTML = `
            <div class="empty-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
                <p>点击左侧视频列表中的视频名称查看字幕</p>
            </div>`;
    }
    if (currentVideoTitle) currentVideoTitle.textContent = '';

    showToast('已删除选中任务', 'success');
}

/**
 * Current task: Clear list
 */
function handleCurrentClearAll() {
    if (videoList.length === 0) return;

    // 直接清空，无需确认

    videoList.length = 0;
    for (const key in videoTranscripts) delete videoTranscripts[key];
    selectedVideoIndices.clear();
    selectedVideoIndex = -1;

    renderVideoList();

    // Clear transcript display
    const container = document.getElementById('transcriptContainer');
    if (container) {
        container.innerHTML = `
            <div class="empty-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
                <p>点击左侧视频列表中的视频名称查看字幕</p>
            </div>`;
    }
    if (currentVideoTitle) currentVideoTitle.textContent = '';

    showToast('已清空列表', 'success');
}

/**
 * Current task: Batch AI processing
 */
async function handleCurrentAiProcessSelected() {
    if (selectedVideoIndices.size === 0) return;

    const indices = Array.from(selectedVideoIndices);
    showToast(`开始批量处理 ${indices.length} 个视频的AI摘要...`, 'info');

    for (const index of indices) {
        const video = videoList.find(v => v.index === index);
        if (video && videoTranscripts[index]) {
            await processWithLLM('video', index); // Call AI processing one by one
        }
    }
}

/**
 * 选择视频
 */
function selectVideo(index) {
    selectedVideoIndex = index;
    renderVideoList();

    const video = videoList.find(v => v.index === index);

    if (currentVideoTitle) {
        currentVideoTitle.textContent = video ? `- ${video.title}` : '';
    }

    // 使用新的显示函数（支持AI结果）
    displayVideoWithAiResult(index);
}

/**
 * 下载单个视频字幕为Markdown
 */
function downloadTranscript(index) {
    const video = videoList.find(v => v.index === index);
    const transcript = videoTranscripts[index];

    if (!transcript || !video) {
        showToast('没有可下载的字幕', 'error');
        return;
    }

    // 使用统一的Markdown格式（包含视频链接）
    // 使用新方式传递完整对象（包含元数据）
    const videoItem = {
        title: video.title,
        url: video.url,
        transcript: transcript,
        owner: video.owner,
        pubdateFormatted: video.pubdateFormatted || '',
        tags: video.tags || []
    };
    const markdown = generateMarkdownContent(videoItem);
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');
    a.href = url;
    a.download = `${video.title.replace(/[/\\?%*:|"<>]/g, '-')}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    showToast('字幕已下载', 'success');
}

/**
 * 复制单个视频字幕
 */
async function copyVideoTranscript(index) {
    const transcript = videoTranscripts[index];

    if (!transcript) {
        showToast('没有可复制的内容', 'error');
        if (copyBtn) copyBtn.disabled = true; // Disable copy button if no transcript
        return;
    }

    try {
        await navigator.clipboard.writeText(transcript);
        showToast('字幕已复制到剪贴板', 'success');
    } catch (error) {
        const textArea = document.createElement('textarea');
        textArea.value = transcript;
        textArea.style.position = 'fixed';
        textArea.style.left = '-9999px';
        document.body.appendChild(textArea);
        textArea.select();
        try {
            document.execCommand('copy');
            showToast('字幕已复制到剪贴板', 'success');
        } catch (e) {
            showToast('复制失败', 'error');
        }
        document.body.removeChild(textArea);
    }
}

/**
 * 下载全部字幕
 */
function handleDownloadAll() {
    const transcriptIndices = Object.keys(videoTranscripts);
    if (transcriptIndices.length === 0) {
        showToast('没有可下载的字幕', 'error');
        return;
    }

    // 收集所有有字幕的视频
    const itemsToDownload = [];
    videoList.forEach(video => {
        const transcript = videoTranscripts[video.index];
        if (transcript) {
            itemsToDownload.push({
                title: video.title,
                url: video.url,
                transcript: transcript
            });
        }
    });

    if (itemsToDownload.length === 0) {
        showToast('没有可下载的字幕', 'error');
        return;
    }

    // 使用ZIP格式下载
    downloadAsZip(itemsToDownload);
}

/**
 * 复制当前选中视频的字幕
 */
async function handleCopy() {
    if (selectedVideoIndex) {
        await copyVideoTranscript(selectedVideoIndex);
    } else {
        showToast('请先选择一个视频', 'error');
    }
}

/**
 * 处理字幕提取
 */
async function handleExtract() {
    const apiKey = apiKeyInput.value.trim();
    const videoUrl = videoUrlInput.value.trim();

    if (!videoUrl) {
        showToast('请输入B站视频链接', 'error');
        videoUrlInput.focus();
        return;
    }

    if (!videoUrl.includes('bilibili.com') && !videoUrl.includes('b23.tv')) {
        showToast('请输入有效的B站视频链接', 'error');
        return;
    }

    // 检测 Cookie 和 API Key 的有效性状态
    const biliCookie = biliCookieInput ? biliCookieInput.value.trim() : '';
    const cookieValid = currentCookieStatus === COOKIE_STATUS.VALID;
    const apiKeyValid = apiKey && document.getElementById('apiKeyStatusDot')?.classList.contains('status-ok');

    // 根据有效性状态决定是否可以继续
    if (!cookieValid && !apiKeyValid) {
        showToast('Cookie 和 API Key 均无效，无法提取字幕', 'error');
        return;
    }

    if (!apiKeyValid && !apiKey) {
        // API Key 为空，但 Cookie 有效，可以继续（仅提取自带字幕）
        showToast('API Key 未配置，将仅提取自带字幕的视频', 'warning');
    } else if (!cookieValid) {
        // Cookie 无效，但 API Key 有效，可以继续（全部用转录）
        showToast('Cookie 无效，将全部使用语音转录', 'warning');
    }

    setButtonLoading(true);
    showProgress(true);
    updateProgress('init', 0);

    // 重置数据
    videoList = [];
    videoTranscripts = {};
    videoProgress = {};
    selectedVideoIndex = -1;
    selectedVideoIndices = new Set(); // New: for multi-selection in current tasks
    showVideoListEmpty();
    // 保持右侧为默认空状态，不显示“正在获取视频列表”
    transcriptContainer.innerHTML = `
        <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <p>点击左侧视频列表中的视频名称查看字幕</p>
        </div>
    `;

    try {
        // 第1步：获取播放列表信息
        const playlistRes = await fetch(`${API_BASE}/api/playlist_info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: videoUrl })
        });

        const playlistData = await playlistRes.json();

        if (!playlistData.success) {
            throw new Error(playlistData.error || '获取视频列表失败');
        }

        videoList = playlistData.videos.map(v => ({
            ...v,
            status: '',
            statusText: '等待处理'
        }));

        renderVideoList();
        showToast(`找到 ${videoList.length} 个视频`, 'success');

        // 第2步：批量处理
        const biliCookie = biliCookieInput ? biliCookieInput.value.trim() : '';

        // 获取存储模式配置 (直接从DOM读取最新状态)
        const useSelfHostedToggle = document.getElementById('useSelfHostedStorage');

        const useSelfHosted = useSelfHostedToggle ? useSelfHostedToggle.checked : false;
        // 自动使用当前页面域名作为自建服务地址
        const selfHostedDomain = useSelfHosted ? window.location.origin : '';

        console.log('[Frontend] Starting batch transcription with storage config:', { useSelfHosted, selfHostedDomain, cookieValid, apiKeyValid });

        const response = await fetch(`${API_BASE}/api/transcribe_batch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                videos: videoList,
                api_key: apiKey,
                bili_cookie: biliCookie,
                use_self_hosted: useSelfHosted,
                self_hosted_domain: selfHostedDomain,
                cookie_valid: cookieValid,
                api_valid: apiKeyValid
            })
        });

        const data = await response.json();

        // 处理 Guest 配额超限错误
        if (data.quota_exceeded) {
            guestQuotaRemaining = data.remaining;
            guestQuotaTotal = data.daily_limit;
            updateGuestQuotaDisplay();
            throw new Error(`配额不足：今日剩余 ${data.remaining}/${data.daily_limit} 个视频`);
        }

        if (!response.ok || !data.success) {
            throw new Error(data.error || '批量处理请求失败');
        }

        const batchId = data.batch_id;
        console.log('[Frontend] Batch started:', batchId, 'Mode:', data.processing_mode);

        // 更新 Guest 配额显示
        if (data.remaining_quota !== undefined) {
            guestQuotaRemaining = data.remaining_quota;
            guestQuotaTotal = data.daily_limit;
            updateGuestQuotaDisplay();
        }

        // 启动轮询
        startBatchPolling(batchId);

        // 注意：这里不立即恢复按钮状态，等待轮询完成

    } catch (error) {
        console.error('请求错误:', error);
        showToast(error.message || '请求失败', 'error');
        // 只有出错时才立即重置按钮
        setButtonLoading(false);
        // 隐藏进度区域
        const batchSection = document.getElementById('batchProgressSection');
        if (batchSection) batchSection.style.display = 'none';
        updateProgress('error', 0); // 兼容旧逻辑
    }
}

/**
 * 处理批量处理的SSE消息
 */
function handleBatchMessage(data) {
    switch (data.type) {
        case 'video_start':
            // 更新视频状态为处理中
            updateVideoStatus(data.index, 'processing', `处理中 (${data.current}/${data.total})`);
            updateProgress('download', 5);
            break;

        case 'progress':
            updateProgress(data.stage, data.progress);
            break;

        case 'video_complete':
            if (data.success) {
                videoTranscripts[data.index] = data.transcript;
                updateVideoStatus(data.index, 'completed', '已完成');

                // 自动选中第一个完成的视频
                if (!selectedVideoIndex) {
                    selectVideo(data.index);
                }

                // 自动保存到历史记录
                const completedVideo = videoList.find(v => v.index === data.index);
                if (completedVideo && data.transcript) {
                    addToHistory(completedVideo, data.transcript, data.metadata || {});
                }
            } else {
                updateVideoStatus(data.index, 'error', `失败: ${data.error}`);
            }
            renderVideoList();
            break;

        case 'batch_complete':
            showToast(`全部完成！共处理 ${data.total} 个视频`, 'success');
            updateProgress('complete', 100);
            setTimeout(() => showProgress(false), 2000);
            break;

        case 'log':
            // 可以在控制台显示日志
            if (data.log) {
                console.log(`[${data.log.level}] ${data.log.message}`);
            }
            break;

        case 'heartbeat':
            break;
    }
}

/**
 * 更新视频状态
 */
function updateVideoStatus(index, status, statusText) {
    const video = videoList.find(v => v.index === index);
    if (video) {
        video.status = status;
        video.statusText = statusText;
        renderVideoList();
    }
}

// ==================== 历史任务功能 ====================

const STORAGE_KEY_HISTORY = 'bilisub_history';
const MAX_HISTORY_COUNT = 500;

// 历史任务DOM元素
const historySection = document.getElementById('historySection');
const historyVideoList = document.getElementById('historyVideoList');
const historyCountSpan = document.getElementById('historyCount');    // 历史任务相关
const historySelectAll = document.getElementById('historySelectAll');
const historyDownloadSelected = document.getElementById('historyDownloadSelected');
const historyDeleteSelected = document.getElementById('historyDeleteSelected');
const historyClearAll = document.getElementById('historyClearAll');
const historyAiProcessSelected = document.getElementById('historyAiProcessSelected');

// 当前任务相关（新增）
const currentSelectAll = document.getElementById('currentSelectAll');
const currentDownloadSelected = document.getElementById('downloadAllBtn'); // ID仍为downloadAllBtn
const currentDeleteSelected = document.getElementById('currentDeleteSelected');
const currentClearAll = document.getElementById('currentClearAll');
const currentAiProcessSelected = document.getElementById('currentAiProcessSelected');

// 绑定当前任务事件
if (currentSelectAll) {
    currentSelectAll.addEventListener('change', handleCurrentSelectAll);
}
if (currentDownloadSelected) {
    currentDownloadSelected.addEventListener('click', handleCurrentDownloadSelected);
}
if (currentDeleteSelected) {
    currentDeleteSelected.addEventListener('click', handleCurrentDeleteSelected);
}
if (currentClearAll) {
    currentClearAll.addEventListener('click', handleCurrentClearAll);
}
if (currentAiProcessSelected) {
    currentAiProcessSelected.addEventListener('click', handleCurrentAiProcessSelected);
}
const historyTranscriptContainer = document.getElementById('historyTranscriptContainer');
const historyCurrentVideoTitle = document.getElementById('historyCurrentVideoTitle');

// 历史数据
let historyData = [];
let selectedVideoIndices = new Set(); // For current tasks
let selectedHistoryIds = new Set();
let selectedHistoryId = null;

// 初始化历史任务功能
async function initHistoryFeature() {
    // Guest 用户不加载历史数据（历史区域已隐藏）
    if (isGuestUser) {
        console.log('[History] Guest 用户，跳过历史功能初始化');
        return;
    }

    await loadHistoryData();
    renderHistoryList();

    // 绑定事件
    if (historySelectAll) {
        historySelectAll.addEventListener('change', handleHistorySelectAll);
    }
    if (historyDownloadSelected) {
        historyDownloadSelected.addEventListener('click', handleHistoryDownloadSelected);
    }
    if (historyDeleteSelected) {
        historyDeleteSelected.addEventListener('click', handleHistoryDeleteSelected);
    }
    if (historyClearAll) {
        historyClearAll.addEventListener('click', handleHistoryClearAll);
    }
    if (historyAiProcessSelected) {
        historyAiProcessSelected.addEventListener('click', handleHistoryAiProcessSelected);
    }
}

/**
 * 加载历史数据（从服务器）
 */
async function loadHistoryData() {
    try {
        const response = await fetch('/api/history');
        const data = await response.json();

        if (data.success && data.history) {
            // 转换服务器数据格式为前端格式
            historyData = data.history.map(item => ({
                id: item.id.toString(),
                title: item.title,
                url: item.url,
                owner: item.owner || '未知',
                pic: item.cover || '',
                pubdate: item.pubdate || 0,
                pubdateFormatted: item.pubdate ? new Date(item.pubdate * 1000).toLocaleDateString() : '',
                tags: item.tags || [],
                transcript: item.transcript || '',
                aiResult: item.ai_result || '',
                date: item.created_at,
                dateFormatted: item.created_at ? formatDate(new Date(item.created_at)) : '',
                dateKey: item.created_at ? formatDateKey(new Date(item.created_at)) : ''
            }));
        } else {
            historyData = [];
        }
    } catch (e) {
        console.error('加载历史数据失败:', e);
        historyData = [];
    }
}

/**
 * 保存单条历史记录到服务器
 */
async function saveHistoryItem(item) {
    try {
        await fetch('/api/history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: item.url,
                title: item.title,
                owner: item.owner,
                cover: item.pic,
                bvid: extractBvid(item.url),
                duration: item.duration,
                pubdate: item.pubdate,
                tags: item.tags,
                transcript: item.transcript,
                ai_result: item.aiResult || ''
            })
        });
    } catch (e) {
        console.error('保存历史记录失败:', e);
    }
}

/**
 * 从URL提取BV号
 */
function extractBvid(url) {
    if (!url) return '';
    const match = url.match(/BV[a-zA-Z0-9]+/);
    return match ? match[0] : '';
}

/**
 * 保存历史数据 - 兼容旧代码调用（实际不再需要批量保存）
 */
function saveHistoryData() {
    // 服务器端存储模式下，每次addToHistory时已经单独保存，这里不需要操作
    console.log('[History] saveHistoryData called (no-op in server mode)');
}

/**
 * 添加到历史记录（视频处理完成时自动调用）
 * @param {Object} video - 视频信息 {title, url, owner, ...}
 * @param {string} transcript - 字幕文本
 * @param {Object} metadata - 元数据 {owner, pubdate, pubdate_formatted, tags}
 */
async function addToHistory(video, transcript, metadata = {}) {
    const now = new Date();
    const historyItem = {
        id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
        title: video.title,
        url: video.url,
        owner: video.owner || metadata.owner || '未知',
        pic: video.pic || metadata.pic || '',  // 封面图URL
        pubdate: metadata.pubdate || 0,
        pubdateFormatted: metadata.pubdate_formatted || '',
        tags: metadata.tags || [],
        transcript: transcript,
        date: now.toISOString(),
        dateFormatted: formatDate(now),
        dateKey: formatDateKey(now)  // 用于日期分组: "2025-12-10"
    };

    // 检查是否已存在相同URL的记录，如存在则更新
    const existingIndex = historyData.findIndex(h => h.url === video.url);
    if (existingIndex >= 0) {
        historyData[existingIndex] = historyItem;
    } else {
        historyData.unshift(historyItem);
    }

    // Guest 用户不保存到服务器（只在当前会话内存中保存）
    if (isGuestUser) {
        console.log('[History] Guest 用户，跳过服务器保存');
        // 不渲染历史列表（Guest 用户隐藏了历史区域）
        return;
    }

    // 非 Guest 用户：保存到服务器
    await saveHistoryItem(historyItem);
    renderHistoryList();
}

/**
 * 格式化日期用于分组键 (YYYY-MM-DD)
 */
function formatDateKey(date) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
}

/**
 * 格式化日期
 */
function formatDate(date) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    const h = String(date.getHours()).padStart(2, '0');
    const min = String(date.getMinutes()).padStart(2, '0');
    return `${y}-${m}-${d} ${h}:${min}`;
}

/**
 * 下载单个历史记录字幕
 */
function downloadHistoryTranscript(id) {
    const item = historyData.find(h => h.id === id);
    if (!item) return;

    const markdown = generateMarkdownContent(item);
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');
    a.href = url;
    a.download = `${item.title.replace(/[/\\?%*:|"<>]/g, '-')}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    showToast('字幕已下载', 'success');
}

/**
 * 复制单个历史记录字幕
 */
async function copyHistoryTranscript(id) {
    const item = historyData.find(h => h.id === id);
    if (!item) return;

    try {
        await navigator.clipboard.writeText(item.transcript);
        showToast('字幕已复制到剪贴板', 'success');
    } catch (error) {
        // Fallback
        const textArea = document.createElement('textarea');
        textArea.value = item.transcript;
        document.body.appendChild(textArea);
        textArea.select();
        try {
            document.execCommand('copy');
            showToast('字幕已复制到剪贴板', 'success');
        } catch (e) {
            showToast('复制失败', 'error');
        }
        document.body.removeChild(textArea);
    }
}

/**
 * 渲染历史列表（按日期分组）
 */
function renderHistoryList() {
    if (!historyVideoList) return;

    if (historyCountSpan) {
        historyCountSpan.textContent = historyData.length;
    }

    if (historyData.length === 0) {
        historyVideoList.innerHTML = `
            <div class="empty-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>
                </svg>
                <p>暂无历史记录</p>
            </div>
        `;
        return;
    }

    // 按日期分组
    const groups = {};
    historyData.forEach(item => {
        // 兼容旧数据（没有dateKey的记录）
        const dateKey = item.dateKey || (item.date ? item.date.split('T')[0] : '未知日期');
        if (!groups[dateKey]) {
            groups[dateKey] = [];
        }
        groups[dateKey].push(item);
    });

    // 按日期倒序排列组
    const sortedDateKeys = Object.keys(groups).sort((a, b) => b.localeCompare(a));

    let html = '';
    for (const dateKey of sortedDateKeys) {
        // 组内按时间倒序排列（最新的在最上面）
        const items = groups[dateKey].sort((a, b) => {
            const timeA = new Date(a.date || 0).getTime();
            const timeB = new Date(b.date || 0).getTime();
            return timeB - timeA; // 倒序
        });
        const allSelected = items.every(item => selectedHistoryIds.has(item.id));
        const someSelected = items.some(item => selectedHistoryIds.has(item.id)) && !allSelected;

        html += `
            <div class="history-date-group" data-date="${dateKey}">
                <div class="date-header">
                    <input type="checkbox" class="date-checkbox" 
                        ${allSelected ? 'checked' : ''} 
                        ${someSelected ? 'indeterminate' : ''}
                        onchange="toggleDateSelection('${dateKey}', this.checked)">
                    <span class="date-label">${dateKey}</span>
                    <span class="date-count">(${items.length})</span>
                </div>
                ${items.map(item => renderHistoryItem(item)).join('')}
            </div>
        `;
    }

    historyVideoList.innerHTML = html;

    // 设置indeterminate状态（需要在渲染后设置）
    for (const dateKey of sortedDateKeys) {
        const items = groups[dateKey];
        const allSelected = items.every(item => selectedHistoryIds.has(item.id));
        const someSelected = items.some(item => selectedHistoryIds.has(item.id)) && !allSelected;
        if (someSelected) {
            const checkbox = historyVideoList.querySelector(`.history-date-group[data-date="${dateKey}"] .date-checkbox`);
            if (checkbox) checkbox.indeterminate = true;
        }
    }

    updateHistoryButtons();
}

/**
 * 渲染单个历史记录项
 */
function renderHistoryItem(item) {
    const authorDisplay = item.owner ? `UP主: ${escapeHtml(item.owner)}` : '';
    // 封面图：如果有就显示，否则使用占位符
    const coverUrl = item.pic || 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 100"%3E%3Crect fill="%23333" width="160" height="100"/%3E%3Ctext x="50%25" y="50%25" fill="%23666" text-anchor="middle" dy=".3em"%3E无封面%3C/text%3E%3C/svg%3E';

    return `
        <div class="video-item history-item-card ${selectedHistoryId === item.id ? 'active' : ''}" data-id="${item.id}">
            <input type="checkbox" class="video-checkbox" 
                ${selectedHistoryIds.has(item.id) ? 'checked' : ''}
                onchange="handleHistoryCheckboxChange('${item.id}', this.checked)">
            <div class="video-cover" onclick="selectHistoryItem('${item.id}')">
                <img src="${coverUrl}" alt="封面" loading="lazy" referrerpolicy="no-referrer" onerror="this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 160 100%22%3E%3Crect fill=%22%23333%22 width=%22160%22 height=%22100%22/%3E%3Ctext x=%2250%25%22 y=%2250%25%22 fill=%22%23666%22 text-anchor=%22middle%22 dy=%22.3em%22%3E加载失败%3C/text%3E%3C/svg%3E'">
            </div>
            <div class="video-info-wrapper" onclick="selectHistoryItem('${item.id}')">
                <div class="video-title-area">
                    <span class="video-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</span>
                </div>
                <div class="video-meta-area">
                    <span class="video-author">${authorDisplay}</span>
                    <div class="video-actions">
                        <button class="video-action-btn" title="查看原视频" 
                                onclick="event.stopPropagation(); window.open('${item.url}', '_blank')">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
                                <polyline points="15 3 21 3 21 9"/>
                                <line x1="10" y1="14" x2="21" y2="3"/>
                            </svg>
                        </button>
                        <button class="video-action-btn" title="下载Markdown" 
                                onclick="event.stopPropagation(); downloadHistoryTranscript('${item.id}')">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                <polyline points="7 10 12 15 17 10"/>
                                <line x1="12" y1="15" x2="12" y2="3"/>
                            </svg>
                        </button>
                        <button class="video-action-btn" title="复制字幕" 
                                onclick="event.stopPropagation(); copyHistoryTranscript('${item.id}')">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                            </svg>
                        </button>
                        <button class="video-action-btn ai-btn" title="AI处理" 
                                onclick="event.stopPropagation(); processWithLLM('history', '${item.id}')">
                            AI
                        </button>
                        <button class="video-action-btn delete-btn" title="删除" 
                                onclick="event.stopPropagation(); deleteHistoryItem('${item.id}')">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <polyline points="3 6 5 6 21 6"/>
                                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                                <line x1="10" y1="11" x2="10" y2="17"/>
                                <line x1="14" y1="11" x2="14" y2="17"/>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;
}

/**
 * 切换日期组的全选状态
 */
function toggleDateSelection(dateKey, checked) {
    historyData.forEach(item => {
        const itemDateKey = item.dateKey || (item.date ? item.date.split('T')[0] : '');
        if (itemDateKey === dateKey) {
            if (checked) {
                selectedHistoryIds.add(item.id);
            } else {
                selectedHistoryIds.delete(item.id);
            }
        }
    });
    renderHistoryList();
}

/**
 * 根据URL删除历史记录（用于同步删除）
 */
async function deleteHistoryItemByUrl(url) {
    if (!url) return;

    const initialLength = historyData.length;
    historyData = historyData.filter(h => h.url !== url);

    if (historyData.length !== initialLength) {
        // 清理选中状态
        const currentIds = new Set(historyData.map(h => h.id));
        for (const id of selectedHistoryIds) {
            if (!currentIds.has(id)) {
                selectedHistoryIds.delete(id);
            }
        }

        // 从服务器删除
        try {
            await fetch('/api/history/by-url', {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: url })
            });
        } catch (e) {
            console.error('从服务器删除历史记录失败:', e);
        }

        renderHistoryList();
    }
}

/**
 * 删除单个历史记录项
 */
async function deleteHistoryItem(id) {
    const item = historyData.find(h => h.id === id);
    if (!item) return;

    // 直接删除，无需确认
    historyData = historyData.filter(h => h.id !== id);
    selectedHistoryIds.delete(id);
    if (selectedHistoryId === id) {
        selectedHistoryId = null;
    }

    // 从服务器删除
    try {
        await fetch(`/api/history/${id}`, {
            method: 'DELETE'
        });
    } catch (e) {
        console.error('从服务器删除历史记录失败:', e);
    }

    renderHistoryList();
    showToast('已删除', 'success');
}

/**
 * 处理复选框变化
 */
function handleHistoryCheckboxChange(id, checked) {
    if (checked) {
        selectedHistoryIds.add(id);
    } else {
        selectedHistoryIds.delete(id);
    }
    updateHistoryButtons();
}

/**
 * 更新按钮状态
 */
function updateHistoryButtons() {
    const hasSelection = selectedHistoryIds.size > 0;

    if (historyDownloadSelected) historyDownloadSelected.disabled = !hasSelection;
    if (historyDeleteSelected) historyDeleteSelected.disabled = !hasSelection;
    if (historyAiProcessSelected) historyAiProcessSelected.disabled = !hasSelection;

    if (historySelectAll) {
        historySelectAll.checked = historyData.length > 0 && selectedHistoryIds.size === historyData.length;
    }
}

/**
 * 全选/取消全选
 */
function handleHistorySelectAll() {
    if (!historySelectAll) return;

    if (historySelectAll.checked) {
        historyData.forEach(item => selectedHistoryIds.add(item.id));
    } else {
        selectedHistoryIds.clear();
    }
    renderHistoryList();
}

/**
 * 选择历史项查看字幕
 */
function selectHistoryItem(id) {
    selectedHistoryId = id;
    const item = historyData.find(h => h.id === id);

    if (item && historyCurrentVideoTitle) {
        historyCurrentVideoTitle.textContent = item.title;
        // 使用新的显示函数（支持AI结果）
        displayHistoryWithAiResult(id);
    }

    renderHistoryList();
}

/**
 * 批量AI处理选中的历史记录
 */
async function handleHistoryAiProcessSelected() {
    if (selectedHistoryIds.size === 0) return;

    // 获取LLM配置（空值使用默认值）
    const userApiKey = document.getElementById('llmApiKey')?.value?.trim() || '';
    const userApiUrl = document.getElementById('llmApiUrl')?.value?.trim() || '';
    const userModelName = document.getElementById('llmModelName')?.value?.trim() || '';
    const userPrompt = document.getElementById('llmPrompt')?.value?.trim() || '';

    // 应用默认值
    const apiKey = userApiKey || (apiKeyInput?.value?.trim() || '');
    const apiUrl = userApiUrl || LLM_DEFAULTS.apiUrl;
    const modelName = userModelName || LLM_DEFAULTS.model;
    const prompt = userPrompt || LLM_DEFAULTS.prompt;

    // 检查是否有可用的 API Key
    if (!apiKey) {
        showToast('请先配置 DashScope API Key', 'error');
        return;
    }

    const selectedItems = historyData.filter(h => selectedHistoryIds.has(h.id));
    const total = selectedItems.length;
    let processed = 0;
    let failed = 0;

    // 禁用按钮并显示进度
    if (historyAiProcessSelected) {
        historyAiProcessSelected.disabled = true;
    }

    showToast(`开始AI批量处理 ${total} 个视频...`, 'info');

    for (const item of selectedItems) {
        try {
            const response = await fetch('/api/llm_process', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    api_key: apiKey,
                    api_url: apiUrl,
                    model: modelName,
                    prompt: prompt || '请分析以下视频字幕内容，提取主要观点并生成摘要：',
                    content: item.transcript
                })
            });

            const data = await response.json();

            if (data.success) {
                // 更新历史记录中的aiAbstract字段
                const itemIndex = historyData.findIndex(h => h.id === item.id);
                if (itemIndex !== -1) {
                    historyData[itemIndex].aiAbstract = data.content;
                }
                processed++;
            } else {
                failed++;
                console.error(`AI处理失败 [${item.title}]: ${data.error}`);
            }
        } catch (error) {
            failed++;
            console.error(`AI处理失败 [${item.title}]: ${error.message}`);
        }

        // 更新进度提示（每处理3个显示一次）
        if ((processed + failed) % 3 === 0 || (processed + failed) === total) {
            showToast(`AI处理进度: ${processed + failed}/${total}`, 'info');
        }
    }

    // 保存更新后的历史数据
    saveHistoryData();

    // 如果当前选中的项被处理了，更新显示
    if (selectedHistoryId && selectedHistoryIds.has(selectedHistoryId)) {
        displayHistoryWithAiResult(selectedHistoryId);
    }

    // 恢复按钮状态
    updateHistoryButtons();

    // 显示完成提示
    if (failed === 0) {
        showToast(`AI批量处理完成！成功处理 ${processed} 个视频`, 'success');
    } else {
        showToast(`AI批量处理完成：成功 ${processed} 个，失败 ${failed} 个`, 'warning');
    }
}

/**
 * 下载选中的历史记录（ZIP格式）
 */
async function handleHistoryDownloadSelected() {
    if (selectedHistoryIds.size === 0) return;

    const selectedItems = historyData.filter(h => selectedHistoryIds.has(h.id));
    await downloadAsZip(selectedItems);
}

/**
 * 删除选中的历史记录
 */
function handleHistoryDeleteSelected() {
    if (selectedHistoryIds.size === 0) return;

    // 直接删除，无需确认

    historyData = historyData.filter(h => !selectedHistoryIds.has(h.id));
    selectedHistoryIds.clear();
    selectedHistoryId = null;

    saveHistoryData();
    renderHistoryList();

    if (historyTranscriptContainer) {
        historyTranscriptContainer.innerHTML = `
            <div class="empty-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                </svg>
                <p>点击左侧视频查看字幕</p>
            </div>
        `;
    }
    if (historyCurrentVideoTitle) {
        historyCurrentVideoTitle.textContent = '选择视频查看字幕';
    }

    showToast('已删除选中记录', 'success');
}

/**
 * 清除所有历史记录
 */
async function handleHistoryClearAll() {
    if (historyData.length === 0) {
        showToast('暂无历史记录', 'info');
        return;
    }

    if (!confirm('确定要删除全部历史记录吗？此操作不可恢复。')) return;

    // 从服务器清空
    try {
        await fetch('/api/history/clear', {
            method: 'DELETE'
        });
    } catch (e) {
        console.error('从服务器清空历史记录失败:', e);
    }

    historyData = [];
    selectedHistoryIds.clear();
    selectedHistoryId = null;

    renderHistoryList();

    if (historyTranscriptContainer) {
        historyTranscriptContainer.innerHTML = `
            <div class="empty-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                </svg>
                <p>点击左侧视频查看字幕</p>
            </div>
        `;
    }
    if (historyCurrentVideoTitle) {
        historyCurrentVideoTitle.textContent = '选择视频查看字幕';
    }

    showToast('已清除全部历史记录', 'success');
}

/**
 * 批量下载多个MD文件（逐个下载）
 */
async function downloadAsZip(items) {
    if (items.length === 0) {
        showToast('没有可下载的文件', 'error');
        return;
    }

    // 显示下载进度提示
    showToast(`正在下载 ${items.length} 个文件...`, 'info');

    // 逐个下载文件
    let downloadCount = 0;
    for (const item of items) {
        try {
            const mdContent = generateMarkdownContent(item);
            const safeTitle = item.title.replace(/[<>:"/\\|?*]/g, '_').substring(0, 100);

            const blob = new Blob([mdContent], { type: 'text/markdown;charset=utf-8' });
            const url = URL.createObjectURL(blob);

            const a = document.createElement('a');
            a.href = url;
            a.download = `${safeTitle}.md`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            downloadCount++;

            // 添加短延迟避免浏览器阻止多次下载
            if (items.length > 1) {
                await new Promise(resolve => setTimeout(resolve, 200));
            }
        } catch (e) {
            console.error(`下载 ${item.title} 失败:`, e);
        }
    }

    if (downloadCount === items.length) {
        showToast(`已下载 ${downloadCount} 个字幕文件`, 'success');
    } else {
        showToast(`已下载 ${downloadCount}/${items.length} 个文件`, 'warning');
    }
}

/**
 * 生成Markdown内容（Obsidian frontmatter格式）
 * @param {Object|string} itemOrTitle - 历史记录项对象或标题字符串（后向兼容）
 * @param {string} urlParam - URL（仅在itemOrTitle为字符串时使用）
 * @param {string} transcriptParam - 字幕（仅在itemOrTitle为字符串时使用）
 */
function generateMarkdownContent(itemOrTitle, urlParam, transcriptParam) {
    // 支持两种调用方式：
    // 1. generateMarkdownContent(item) - 新方式，item包含所有信息
    // 2. generateMarkdownContent(title, url, transcript) - 旧方式，向后兼容

    let title, url, transcript, owner, pubdateFormatted, tags, aiAbstract;

    if (typeof itemOrTitle === 'object') {
        // 新方式：传入完整对象
        title = itemOrTitle.title || '未知标题';
        url = itemOrTitle.url || '';
        transcript = itemOrTitle.transcript || '';
        owner = itemOrTitle.owner || '未知';
        pubdateFormatted = itemOrTitle.pubdateFormatted || '';
        tags = itemOrTitle.tags || [];
        aiAbstract = itemOrTitle.aiAbstract || '';  // AI处理结果
    } else {
        // 旧方式：向后兼容
        title = itemOrTitle || '未知标题';
        url = urlParam || '';
        transcript = transcriptParam || '';
        owner = '未知';
        pubdateFormatted = '';
        tags = [];
        aiAbstract = '';
    }

    // 处理标签格式
    const tagsStr = tags.length > 0 ? tags.join(', ') : '';

    // 获取提取日期（updated字段）- 只保留日期部分（YYYY-MM-DD）
    let updatedDate = '';
    if (typeof itemOrTitle === 'object' && itemOrTitle.dateKey) {
        // dateKey 格式已经是 YYYY-MM-DD
        updatedDate = itemOrTitle.dateKey;
    } else if (typeof itemOrTitle === 'object' && itemOrTitle.dateFormatted) {
        // 从 dateFormatted 中提取日期部分
        const match = itemOrTitle.dateFormatted.match(/\d{4}-\d{2}-\d{2}/);
        updatedDate = match ? match[0] : itemOrTitle.dateFormatted.split(' ')[0];
    } else {
        // 使用当前日期
        const now = new Date();
        updatedDate = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    }

    // 生成Obsidian frontmatter（包含abstract字段用于AI处理结果）
    let frontmatter = `---
title: "${title.replace(/"/g, '\\"')}"
type: 视频字幕
author: "${owner.replace(/"/g, '\\"')}"
created: "${pubdateFormatted}"
updated: "${updatedDate}"
url: "${url}"
tags: [${tagsStr}]`;

    // 如果有AI处理结果，添加abstract字段
    if (aiAbstract) {
        // 处理多行abstract
        const abstractLines = aiAbstract.replace(/"/g, '\\"').split('\n');
        if (abstractLines.length === 1) {
            frontmatter += `\nabstract: "${abstractLines[0]}"`;
        } else {
            frontmatter += `\nabstract: |\n${abstractLines.map(line => '  ' + line).join('\n')}`;
        }
    }

    frontmatter += `\n---\n\n`;

    // 如果有AI处理结果，在正文前显示
    let content = '';
    if (aiAbstract) {
        content += `## AI 处理结果\n\n${aiAbstract}\n\n---\n\n## 原始字幕\n\n`;
    }
    content += transcript;

    return frontmatter + content;
}

/**
 * 格式化日期用于文件名
 */
function formatDateForFilename(date) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    const h = String(date.getHours()).padStart(2, '0');
    const min = String(date.getMinutes()).padStart(2, '0');
    return `${y}${m}${d}_${h}${min}`;
}

// 智能配置折叠功能
function toggleConfigVisibility(forceCollapse) {
    const section = document.getElementById('configSection');
    console.log('[ToggleConfig] section:', section, 'forceCollapse:', forceCollapse);
    if (!section) {
        console.log('[ToggleConfig] configSection 元素不存在！');
        return;
    }

    if (forceCollapse !== undefined) {
        if (forceCollapse) {
            section.classList.add('collapsed');
            console.log('[ToggleConfig] 添加 collapsed 类，当前类:', section.className);
        } else {
            section.classList.remove('collapsed');
            console.log('[ToggleConfig] 移除 collapsed 类，当前类:', section.className);
        }
    } else {
        section.classList.toggle('collapsed');
        console.log('[ToggleConfig] 切换 collapsed 类，当前类:', section.className);
    }
}

function checkAutoCollapse() {
    // 检查 Cookie 状态灯是否为绿色（验证通过）
    const cookieDot = document.getElementById('cookieStatusDot');
    const isCookieValid = cookieDot?.classList.contains('status-ok');

    // 检查 API Key 状态灯是否为绿色（验证通过）
    const apiKeyDot = document.getElementById('apiKeyStatusDot');
    const isApiKeyValid = apiKeyDot?.classList.contains('status-ok');

    console.log('[AutoCollapse] 检查自动折叠:', {
        isCookieValid,
        isApiKeyValid,
        cookieDotClass: cookieDot?.className,
        apiKeyDotClass: apiKeyDot?.className
    });

    // 只有当 Cookie 和 API Key 都验证成功时才自动折叠配置
    if (isCookieValid && isApiKeyValid) {
        console.log('[AutoCollapse] 条件满足，执行折叠');
        // 使用正确的折叠机制：直接设置内容显示状态
        const content = document.getElementById('configContent');
        const arrow = document.getElementById('configCollapseArrow');
        if (content && configExpanded) {  // 只有当前是展开状态才折叠
            content.style.display = 'none';
            if (arrow) arrow.style.transform = 'rotate(0deg)';
            configExpanded = false;
            console.log('[AutoCollapse] 配置已折叠');
        }
    }
}

// 历史功能初始化已合并到 scheduleBackgroundInit() 中
// 不再需要单独的 DOMContentLoaded 监听器

// ============ 用户认证相关 ============

/**
 * 加载当前用户信息
 */
async function loadCurrentUser() {
    try {
        const response = await fetch('/api/me');
        const data = await response.json();

        if (data.authenticated && data.user) {
            const userNameEl = document.getElementById('userName');
            const adminLinkEl = document.getElementById('adminLink');
            const changePasswordLinkEl = document.getElementById('changePasswordLink');

            if (userNameEl) {
                userNameEl.textContent = data.user.username;
            }

            if (adminLinkEl && data.user.is_admin) {
                adminLinkEl.style.display = 'block';
            }

            // Guest 用户不能修改密码，隐藏修改密码链接
            if (changePasswordLinkEl) {
                if (data.user.username === 'guest') {
                    changePasswordLinkEl.style.display = 'none';
                } else {
                    changePasswordLinkEl.style.display = 'block';
                }
            }
        }
    } catch (error) {
        console.error('加载用户信息失败:', error);
    }
}

/**
 * 切换用户下拉菜单
 */
function toggleUserMenu() {
    const dropdown = document.getElementById('userDropdown');
    if (dropdown) {
        dropdown.classList.toggle('show');
    }
}

// 点击其他地方关闭用户菜单
document.addEventListener('click', (e) => {
    const userArea = document.getElementById('userArea');
    const dropdown = document.getElementById('userDropdown');

    if (userArea && dropdown && !userArea.contains(e.target)) {
        dropdown.classList.remove('show');
    }
});

/**
 * 登出
 */
async function logout() {
    try {
        await fetch('/api/logout', { method: 'POST' });
        window.location.href = '/login';
    } catch (error) {
        console.error('登出失败:', error);
        showToast('登出失败', 'error');
    }
}

/**
 * 显示修改密码弹窗
 */
function showChangePasswordModal() {
    const modal = document.getElementById('changePasswordModal');
    if (modal) {
        modal.classList.add('show');
        document.getElementById('oldPassword').value = '';
        document.getElementById('newPassword').value = '';
    }
    // 关闭用户菜单
    const dropdown = document.getElementById('userDropdown');
    if (dropdown) dropdown.classList.remove('show');
}

/**
 * 隐藏修改密码弹窗
 */
function hideChangePasswordModal() {
    const modal = document.getElementById('changePasswordModal');
    if (modal) {
        modal.classList.remove('show');
    }
}

/**
 * 修改密码
 */
async function changePassword() {
    const oldPassword = document.getElementById('oldPassword').value;
    const newPassword = document.getElementById('newPassword').value;

    if (!oldPassword || !newPassword) {
        showToast('请输入原密码和新密码', 'error');
        return;
    }

    if (newPassword.length < 4) {
        showToast('新密码长度至少 4 个字符', 'error');
        return;
    }

    try {
        const response = await fetch('/api/change-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                old_password: oldPassword,
                new_password: newPassword
            })
        });

        const data = await response.json();

        if (data.success) {
            showToast('密码修改成功', 'success');
            hideChangePasswordModal();
        } else {
            showToast(data.error || '密码修改失败', 'error');
        }
    } catch (error) {
        console.error('修改密码失败:', error);
        showToast('网络错误', 'error');
    }
}

// 点击弹窗外部关闭
document.addEventListener('click', (e) => {
    const modal = document.getElementById('changePasswordModal');
    if (modal && e.target === modal) {
        hideChangePasswordModal();
    }
});

// 用户信息加载已合并到 scheduleBackgroundInit() 中
// 不再需要单独的 DOMContentLoaded 监听器

// ==================== 批量进度轮询功能 ====================

let currentBatchId = null;
let pollInterval = null;
let processedVideoIndices = new Set(); // 记录已处理完成并保存的视频索引

/**
 * 启动批量任务状态轮询
 */
function startBatchPolling(batchId) {
    console.log('[Poll] startBatchPolling 启动, batchId:', batchId);
    currentBatchId = batchId;
    processedVideoIndices.clear();

    // 重置所有视频进度
    videoProgress = {};
    videoList.forEach(v => {
        videoProgress[v.index] = { status: 'pending', progress: 0 };
    });

    // 显示整体进度区域（用户要求保留）
    const batchSection = document.getElementById('batchProgressSection');
    if (batchSection) {
        batchSection.style.display = 'block';
        // 初始化整体进度
        const batchTitle = document.getElementById('batchTitle');
        const batchTotalProgressBar = document.getElementById('batchTotalProgressBar');
        const batchTotalPercent = document.getElementById('batchTotalPercent');
        const batchStatusBadge = document.getElementById('batchStatusBadge');
        const batchTotalProgressContainer = document.getElementById('batchTotalProgress');

        if (batchTitle) batchTitle.textContent = `正在处理 0/${videoList.length} 个视频`;
        if (batchTotalProgressBar) batchTotalProgressBar.style.width = '0%';
        if (batchTotalPercent) batchTotalPercent.textContent = '0%';
        if (batchStatusBadge) {
            batchStatusBadge.textContent = '处理中';
            batchStatusBadge.className = 'batch-status-badge processing';
        }
        // 重置进度条容器样式
        if (batchTotalProgressContainer) {
            batchTotalProgressContainer.classList.remove('cancelled', 'completed', 'error');
        }
    }

    // 重新显示并启用取消按钮
    const cancelBtn = document.getElementById('cancelBatchBtn');
    if (cancelBtn) {
        cancelBtn.style.display = 'inline-flex';
        cancelBtn.disabled = false;
    }

    // 隐藏 batchVideoList（不在这里显示单独的列表，因为每个视频卡片有自己的进度条）
    const batchVideoList = document.getElementById('batchVideoList');
    if (batchVideoList) batchVideoList.style.display = 'none';

    const oldSection = document.getElementById('progressSection');
    if (oldSection) oldSection.style.display = 'none';

    // 重新渲染视频列表以显示进度条
    renderVideoList();

    // 立即执行一次
    pollBatchStatus();

    // 启动轮询，每秒一次
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollBatchStatus, 1000);
}

/**
 * 轮询批量状态
 */
async function pollBatchStatus() {
    console.log('[Poll] pollBatchStatus 开始执行, batchId:', currentBatchId);
    if (!currentBatchId) return;

    try {
        const response = await fetch(`${API_BASE}/api/batch_status/${currentBatchId}`);
        console.log('[Poll] Fetch完成, status:', response.status, 'ok:', response.ok);

        // 先获取文本，再尝试解析JSON
        const responseText = await response.text();
        console.log('[Poll] 响应文本长度:', responseText.length, '前100字符:', responseText.substring(0, 100));

        let responseData;
        try {
            responseData = JSON.parse(responseText);
        } catch (jsonError) {
            console.error('[Poll] JSON解析失败:', jsonError.message, '原始文本:', responseText.substring(0, 200));
            return;
        }

        console.log('[Poll] API响应:', responseData);

        // API 返回格式: {success: true, data: {...}} 或 {success: false, error: "..."}
        if (!responseData.success) {
            console.error('[Poll] 轮询出错:', responseData.error);
            showToast(`轮询出错: ${responseData.error}`, 'error');
            stopBatchPolling();
            setButtonLoading(false);
            return;
        }

        // 获取实际的任务状态数据
        const data = responseData.data;
        console.log('[Poll] data对象:', data ? '存在' : '不存在', 'videos数组:', data?.videos ? `${data.videos.length}个` : '不存在');

        if (!data) {
            console.error('[Poll] 轮询返回数据为空');
            return;
        }

        // 更新整体进度条
        const total = data.total || 1;
        const completedCount = data.completed_count || 0;
        const overallPercent = Math.round((completedCount / total) * 100);

        const batchTitle = document.getElementById('batchTitle');
        const batchTotalProgressBar = document.getElementById('batchTotalProgressBar');
        const batchTotalPercent = document.getElementById('batchTotalPercent');
        const batchStatusBadge = document.getElementById('batchStatusBadge');

        if (batchTitle) batchTitle.textContent = `正在处理 ${completedCount}/${total} 个视频`;
        if (batchTotalProgressBar) batchTotalProgressBar.style.width = `${overallPercent}%`;
        if (batchTotalPercent) batchTotalPercent.textContent = `${overallPercent}%`;

        // 更新每个视频卡片的进度条
        console.log('[Poll] 准备处理videos, 类型:', typeof data.videos, 'Array?:', Array.isArray(data.videos));

        if (data.videos && data.videos.length > 0) {
            try {
                console.log('[Poll] 进入videos循环');
                console.log('[Poll] 视频任务列表:', data.videos.map(v => ({ idx: v.original_index, status: v.status, progress: v.progress })));

                data.videos.forEach((videoTask, forEachIdx) => {
                    console.log(`[Poll] forEach第${forEachIdx}次, videoTask:`, videoTask);

                    // 使用后端保存的原始索引，而不是 forEach 的循环索引
                    const videoIndex = videoTask.original_index;
                    if (videoIndex === undefined || videoIndex === null) {
                        console.warn('[Poll] 视频任务缺少 original_index:', videoTask);
                        return;
                    }

                    console.log(`[Poll] 更新视频${videoIndex}: status=${videoTask.status}, progress=${videoTask.progress}`);

                    // 更新进度条
                    updateVideoCardProgress(videoIndex, videoTask.status, videoTask.progress || 0);

                    // 检查完成状态
                    if (videoTask.status === 'completed' && !processedVideoIndices.has(videoIndex)) {
                        // 保存结果到内存
                        if (videoTask.result && videoTask.result.transcript) {
                            videoTranscripts[videoIndex] = videoTask.result.transcript;
                        }
                        processedVideoIndices.add(videoIndex);

                        // 更新原始列表状态
                        const originalVideo = videoList.find(v => v.index === videoIndex);
                        if (originalVideo) {
                            originalVideo.status = 'completed';
                            originalVideo.statusText = '已完成';

                            // 保存到历史记录
                            if (videoTask.result && videoTask.result.transcript) {
                                addToHistory(originalVideo, videoTask.result.transcript);
                            }

                            // 如果是第一个完成的，自动选中
                            if (!selectedVideoIndex && selectedVideoIndex !== 0) {
                                selectVideo(videoIndex);
                            }
                        }
                    } else if (videoTask.status === 'error' && !processedVideoIndices.has(videoIndex)) {
                        processedVideoIndices.add(videoIndex);

                        const originalVideo = videoList.find(v => v.index === videoIndex);
                        if (originalVideo) {
                            originalVideo.status = 'error';
                            originalVideo.statusText = videoTask.error || '提取失败';
                        }
                    }
                });
            } catch (videosLoopError) {
                console.error('[Poll] 处理videos循环出错:', videosLoopError);
            }
        }

        // 判断是否全部完成（包括有cancelled的情况）
        if (data.status === 'completed' || data.status === 'cancelled') {
            stopBatchPolling();

            // 统计结果
            const completedCount = data.videos ? data.videos.filter(v => v.status === 'completed').length : 0;
            const cancelledCount = data.videos ? data.videos.filter(v => v.status === 'cancelled').length : 0;
            const errorCount = data.videos ? data.videos.filter(v => v.status === 'error').length : 0;

            let toastMessage = `处理完成！成功 ${completedCount} 个`;
            if (cancelledCount > 0) toastMessage += `，取消 ${cancelledCount} 个`;
            if (errorCount > 0) toastMessage += `，失败 ${errorCount} 个`;

            showToast(toastMessage, completedCount > 0 ? 'success' : 'info');

            // 更新整体状态
            if (batchStatusBadge) {
                if (cancelledCount > 0 && completedCount === 0) {
                    batchStatusBadge.textContent = '已取消';
                    batchStatusBadge.className = 'batch-status-badge cancelled';
                } else if (cancelledCount > 0) {
                    batchStatusBadge.textContent = '部分完成';
                    batchStatusBadge.className = 'batch-status-badge completed';
                } else {
                    batchStatusBadge.textContent = '已完成';
                    batchStatusBadge.className = 'batch-status-badge completed';
                }
            }

            // 隐藏取消按钮
            const cancelBtn = document.getElementById('cancelBatchBtn');
            if (cancelBtn) cancelBtn.style.display = 'none';

            setButtonLoading(false);
        }

    } catch (error) {
        console.error('轮询请求失败:', error);
    }
}

/**
 * 停止轮询
 */
function stopBatchPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
    currentBatchId = null;
}

/**
 * 取消批量处理
 * 注意：已提交给 paraformer 的视频继续等待完成，只取消 pending 状态的视频
 */
async function cancelBatchProcess() {
    if (!currentBatchId) {
        showToast('没有正在进行的任务', 'warning');
        return;
    }

    const cancelBtn = document.getElementById('cancelBatchBtn');
    if (cancelBtn) {
        cancelBtn.disabled = true;
        cancelBtn.textContent = '取消中...';
    }

    try {
        const response = await fetch(`${API_BASE}/api/batch_cancel/${currentBatchId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();

        if (!data.success) {
            showToast(`取消失败: ${data.error}`, 'error');
            if (cancelBtn) {
                cancelBtn.disabled = false;
                cancelBtn.textContent = '取消';
            }
            return;
        }

        // 更新被取消的视频卡片
        data.cancelled_indices.forEach(videoIndex => {
            // 更新进度条和内存状态
            updateVideoCardProgress(videoIndex, 'cancelled', 100);
        });

        // 隐藏取消按钮
        if (cancelBtn) {
            cancelBtn.style.display = 'none';
        }

        // 所有任务都已结束，停止轮询
        stopBatchPolling();

        // 更新整体状态为取消（橙色）
        const batchStatusBadge = document.getElementById('batchStatusBadge');
        if (batchStatusBadge) {
            batchStatusBadge.textContent = '已取消';
            batchStatusBadge.className = 'batch-status-badge cancelled';
        }

        // 整体进度条设为100%橙色
        const batchTotalProgress = document.querySelector('.batch-total-progress');
        const batchTotalProgressBar = document.getElementById('batchTotalProgressBar');
        const batchTotalPercent = document.getElementById('batchTotalPercent');
        if (batchTotalProgress) batchTotalProgress.classList.add('cancelled');
        if (batchTotalProgressBar) batchTotalProgressBar.style.width = '100%';
        if (batchTotalPercent) batchTotalPercent.textContent = '100%';

        setButtonLoading(false);
        showToast('任务已取消', 'info');

    } catch (error) {
        console.error('取消请求失败:', error);
        showToast('取消失败，请重试', 'error');
        if (cancelBtn) {
            cancelBtn.disabled = false;
            cancelBtn.textContent = '取消';
        }
    }
}

/**
 * 渲染批量进度 UI
 */
function renderBatchProgress(data) {
    // 更新总进度
    const total = data.total || 1;
    const completed = data.completed_count || 0;
    const percent = Math.round((completed / total) * 100);

    const title = document.getElementById('batchTitle');
    const badge = document.getElementById('batchStatusBadge');
    const totalBar = document.getElementById('batchTotalProgressBar');
    const totalPercent = document.getElementById('batchTotalPercent');
    const list = document.getElementById('batchVideoList');

    if (title) title.textContent = `正在处理 ${completed}/${total} 个视频`;
    if (badge && data.status === 'processing') {
        badge.textContent = '处理中';
        badge.className = 'batch-status-badge processing';
    }

    if (totalBar) totalBar.style.width = `${percent}%`;
    if (totalPercent) totalPercent.textContent = `${percent}%`;

    // 更新列表
    if (list && data.videos) {
        // 如果列表为空或者长度不匹配，重新初始化
        // FIXME: 简单判断子元素数量，严谨做法是一一匹配
        if (list.children.length !== data.videos.length) {
            list.innerHTML = '';
            data.videos.forEach((v, idx) => {
                const div = document.createElement('div');
                div.className = 'batch-video-item';
                div.id = `batch-video-${idx}`;
                div.innerHTML = `
                     <div class="video-info">
                         <span class="video-title" title="${escapeHtml(v.title)}">${escapeHtml(v.title || '未知标题')}</span>
                         <span class="video-status" id="batch-video-status-${idx}">等待中...</span>
                     </div>
                     <div class="mini-progress-bar">
                         <div class="bar" id="batch-video-bar-${idx}" style="width: 0%"></div>
                     </div>
                 `;
                list.appendChild(div);
            });
        }

        // 更新每一项状态
        data.videos.forEach((v, idx) => {
            const statusSpan = document.getElementById(`batch-video-status-${idx}`);
            const barDiv = document.getElementById(`batch-video-bar-${idx}`);
            const itemDiv = document.getElementById(`batch-video-${idx}`);

            if (statusSpan && barDiv && itemDiv) {
                let statusText = '';

                // 移除旧状态类
                itemDiv.classList.remove('status-completed', 'status-error');

                if (v.status === 'pending') statusText = '等待中';
                else if (v.status === 'queued') statusText = '排队中';
                else if (v.status === 'processing') statusText = `处理中 ${v.progress}%`;
                else if (v.status === 'completed') {
                    statusText = '已完成';
                    itemDiv.classList.add('status-completed');
                }
                else if (v.status === 'cancelled') {
                    statusText = '已取消';
                    itemDiv.classList.add('status-cancelled');
                }
                else if (v.status === 'error') {
                    statusText = `失败: ${v.error}`;
                    itemDiv.classList.add('status-error');
                }

                statusSpan.textContent = statusText;
                barDiv.style.width = `${v.progress}%`;
            }
        });
    }
}

/**
 * 获取 Guest 状态信息（并发状态）
 */
async function fetchGuestQuota() {
    try {
        const response = await fetch('/api/guest_status');
        if (!response.ok) return;

        const data = await response.json();
        if (data.success) {
            isGuestUser = data.is_guest;
            if (isGuestUser) {
                // Guest 用户：隐藏历史任务区域
                const historySection = document.getElementById('historySection');
                if (historySection) {
                    historySection.style.display = 'none';
                }

                // 隐藏配额显示（不再需要）
                const quotaDisplay = document.getElementById('guestQuotaDisplay');
                if (quotaDisplay) quotaDisplay.style.display = 'none';

                console.log('[Guest] 已隐藏历史区域，并发限制: ' + data.max_concurrent);
            } else {
                // 非 Guest 用户，隐藏配额显示（但显示历史区域）
                const quotaDisplay = document.getElementById('guestQuotaDisplay');
                if (quotaDisplay) quotaDisplay.style.display = 'none';
            }
        }
    } catch (error) {
        console.error('Failed to fetch guest status:', error);
    }
}

/**
 * Guest 用户不需要配额显示，此函数保留但不再使用
 */
function updateGuestQuotaDisplay() {
    // Guest 用户不再显示配额，只在终端日志显示并发状态
    const quotaDisplay = document.getElementById('guestQuotaDisplay');
    if (quotaDisplay) {
        quotaDisplay.style.display = 'none';
    }
}

// ================== 插件任务显示功能 ==================

let extensionTasksPollingTimer = null;

/**
 * 获取并显示插件任务
 */
async function fetchExtensionTasks() {
    if (isGuestUser) return; // Guest 用户不支持插件

    try {
        // 获取所有任务（包括失败的，用于显示错误）
        const response = await fetch('/api/extension/tasks/all?limit=10');
        const data = await response.json();

        if (data.success) {
            renderExtensionTasks(data.tasks);
        }
    } catch (error) {
        console.error('[Extension Tasks] 获取失败:', error);
    }
}

/**
 * 渲染插件任务卡片（样式与当前任务完全一致）
 */
function renderExtensionTasks(tasks) {
    const section = document.getElementById('extensionTasksSection');
    const grid = document.getElementById('extensionTasksGrid');
    const countEl = document.getElementById('extensionTaskCount');

    if (!section || !grid) return;

    // 过滤显示：进行中的任务 + 最近1小时内失败的任务
    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000);
    const visibleTasks = tasks.filter(task => {
        // 进行中的任务始终显示
        if (!['completed', 'failed', 'cancelled'].includes(task.status)) {
            return true;
        }
        // 失败的任务：1小时内显示
        if (task.status === 'failed' && task.created_at) {
            const createdAt = new Date(task.created_at);
            return createdAt > oneHourAgo;
        }
        return false;
    });

    // 如果没有任务，隐藏区域
    if (!visibleTasks || visibleTasks.length === 0) {
        section.style.display = 'none';
        return;
    }

    // 显示区域
    section.style.display = 'block';

    // 统计进行中和失败的任务
    const inProgress = visibleTasks.filter(t => !['completed', 'failed', 'cancelled'].includes(t.status)).length;
    const failed = visibleTasks.filter(t => t.status === 'failed').length;

    // 更新计数
    if (countEl) {
        let countText = '';
        if (inProgress > 0) countText += `${inProgress} 个进行中`;
        if (failed > 0) countText += (countText ? '，' : '') + `${failed} 个失败`;
        countEl.textContent = countText || '无任务';
    }

    // 渲染卡片（使用与当前任务完全一致的结构，不显示百分数）
    grid.innerHTML = visibleTasks.map(task => {
        const isFailed = task.status === 'failed';
        const progressPercent = isFailed ? 100 : (task.progress || 0);

        // 确定进度条样式类
        let progressBarClass = '';
        let statusBadge = '';
        if (isFailed) {
            progressBarClass = 'error';
            statusBadge = '<span class="video-result-badge error">提取失败</span>';
        } else if (task.status === 'completed') {
            progressBarClass = 'completed';
            statusBadge = '<span class="video-result-badge success">已完成</span>';
        } else if (task.status !== 'pending') {
            progressBarClass = 'processing';
        }

        // 获取阶段描述（不含百分数，仅用于显示当前阶段）
        const stageDesc = task.stage_desc ? task.stage_desc.replace(/\s*\d+%\s*/g, '').trim() : getStageText(task.status);

        // 封面图：优先使用任务中的封面，否则使用占位符
        const coverUrl = task.cover || `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 160 100'%3E%3Crect fill='%23333' width='160' height='100'/%3E%3Ctext x='50%25' y='50%25' fill='%23666' text-anchor='middle' dy='.3em' font-size='24'%3E🔌%3C/text%3E%3C/svg%3E`;

        // UP主信息
        const ownerText = task.owner ? `UP主: ${escapeHtml(task.owner)}` : stageDesc;

        // 使用与 renderVideoList 完全一致的卡片结构
        return `
            <div class="video-item history-item-card" data-bvid="${task.bvid}">
                <div class="video-cover">
                    <img src="${coverUrl}" 
                         alt="${escapeHtml(task.title || task.bvid)}" loading="lazy" referrerpolicy="no-referrer"
                         onerror="this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 160 100%22%3E%3Crect fill=%22%23333%22 width=%22160%22 height=%22100%22/%3E%3Ctext x=%2250%25%22 y=%2250%25%22 fill=%22%23666%22 text-anchor=%22middle%22 dy=%22.3em%22%3E🔌%3C/text%3E%3C/svg%3E'">
                </div>
                <div class="video-info-wrapper">
                    <div class="video-title-area">
                        <span class="video-title" title="${escapeHtml(task.title)}">${escapeHtml(task.title || task.bvid)}</span>
                        ${statusBadge}
                    </div>
                    <div class="video-meta-area">
                        <span class="video-author">${ownerText}</span>
                        <div class="video-actions">
                            <button class="video-action-btn" title="查看原视频"
                                    onclick="event.stopPropagation(); window.open('https://www.bilibili.com/video/${task.bvid}', '_blank')">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
                                    <polyline points="15 3 21 3 21 9"/>
                                    <line x1="10" y1="14" x2="21" y2="3"/>
                                </svg>
                            </button>
                        </div>
                    </div>
                </div>
                <div class="video-card-progress ${progressBarClass}">
                    <div class="video-card-progress-fill" style="width: ${progressPercent}%"></div>
                </div>
            </div>
        `;
    }).join('');
}

/**
 * 获取任务状态文本
 */
function getStageText(status) {
    const statusMap = {
        'pending': '等待处理',
        'downloading': '下载音频',
        'uploading': '上传文件',
        'transcribing': '语音识别',
        'processing': '处理结果',
        'completed': '已完成',
        'failed': '失败',
        'cancelled': '已取消'
    };
    return statusMap[status] || status;
}

/**
 * 开始插件任务轮询
 */
function startExtensionTasksPolling() {
    // 立即获取一次
    fetchExtensionTasks();

    // 每 3 秒轮询一次
    if (extensionTasksPollingTimer) {
        clearInterval(extensionTasksPollingTimer);
    }
    extensionTasksPollingTimer = setInterval(fetchExtensionTasks, 3000);
}

/**
 * 停止插件任务轮询
 */
function stopExtensionTasksPolling() {
    if (extensionTasksPollingTimer) {
        clearInterval(extensionTasksPollingTimer);
        extensionTasksPollingTimer = null;
    }
}

// 页面加载后启动插件任务轮询
document.addEventListener('DOMContentLoaded', () => {
    // 延迟启动，等待用户状态加载完成
    setTimeout(() => {
        if (!isGuestUser) {
            startExtensionTasksPolling();
        }
    }, 2000);
});

// 页面卸载前停止轮询
window.addEventListener('beforeunload', stopExtensionTasksPolling);
