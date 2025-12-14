"""
B站字幕提取网站后端服务
使用 Flask + yt-dlp + DashScope Paraformer SDK
"""

import os
import uuid
import tempfile
import logging
import json
import time
import secrets
from datetime import datetime
from http import HTTPStatus
from flask import Flask, request, jsonify, send_from_directory, Response, redirect, url_for, render_template
from flask_cors import CORS
from flask_login import LoginManager, login_required, current_user
from concurrent.futures import ThreadPoolExecutor
import threading


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='.', static_url_path='', template_folder='templates')

# SECRET_KEY 持久化：确保所有 worker 进程使用同一个密钥
def get_or_create_secret_key():
    """获取或创建 SECRET_KEY，确保持久化和跨 worker 共享"""
    # 优先使用环境变量
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    
    # 从文件读取或生成新密钥
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', '.secret_key')
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    
    if os.path.exists(key_file):
        with open(key_file, 'r') as f:
            return f.read().strip()
    else:
        # 生成新密钥并保存
        new_key = secrets.token_hex(32)
        with open(key_file, 'w') as f:
            f.write(new_key)
        logger.info('已生成新的 SECRET_KEY 并保存')
        return new_key

app.secret_key = get_or_create_secret_key()
CORS(app)

# 数据库配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, 'database')
os.makedirs(DATABASE_DIR, exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(DATABASE_DIR, "bilisub.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 初始化数据库
from models import db, User, init_database
db.init_app(app)

# 初始化 Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@login_manager.unauthorized_handler
def unauthorized():
    """处理未授权请求：API 请求返回 JSON，其他请求重定向到登录页"""
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': '请先登录'}), 401
    return redirect(url_for('login_page'))

# 注册蓝图
from auth import auth_bp
from admin import admin_bp
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)

# 初始化数据库（创建表和默认管理员）
with app.app_context():
    init_database(app)

# 临时文件目录
TEMP_DIR = tempfile.gettempdir()
AUDIO_DIR = os.path.join(TEMP_DIR, 'bilisub_audio')
os.makedirs(AUDIO_DIR, exist_ok=True)

# 全局任务池
# max_workers=8 经测试在 B站 rate limit 容忍范围内
# 有字幕的视频几乎无压力，语音识别瓶颈在网络传输
executor = ThreadPoolExecutor(max_workers=8)

# 第三方直链专用执行器：使用 5 个并发 worker 最大化处理速度
third_party_executor = ThreadPoolExecutor(max_workers=5)

# Guest 账户专用配置
guest_executor = ThreadPoolExecutor(max_workers=5)  # Guest 账户5并发执行

# Guest 并发控制器
class GuestConcurrencyController:
    """
    控制 guest 账户的全局并发数
    使用信号量实现，超过限制时自动排队等待
    """
    MAX_CONCURRENT = 5  # 最大并发数
    
    def __init__(self):
        self.lock = threading.Lock()
        self.semaphore = threading.Semaphore(self.MAX_CONCURRENT)
        self.current_count = 0
        self.queue_count = 0  # 排队中的任务数
    
    def acquire(self, batch_id: str, video_idx: int):
        """
        获取一个并发槽位，如果已满则阻塞排队
        返回: 是否成功获取（任务未被取消）
        """
        with self.lock:
            self.queue_count += 1
            logger.info(f"[Guest] 视频 {video_idx} 进入排队，当前排队数: {self.queue_count}")
        
        # 阻塞等待获取信号量
        self.semaphore.acquire()
        
        with self.lock:
            self.queue_count -= 1
            self.current_count += 1
            logger.info(f"[Guest] 视频 {video_idx} 获得槽位，当前并发: {self.current_count}/{self.MAX_CONCURRENT}")
        
        return True
    
    def release(self):
        """释放一个并发槽位"""
        with self.lock:
            self.current_count -= 1
            current = self.current_count
        self.semaphore.release()
        logger.info(f"[Guest] 释放槽位，当前并发: {current}/{self.MAX_CONCURRENT}")
    
    def get_status(self):
        """获取当前状态"""
        with self.lock:
            return {
                "current": self.current_count,
                "max": self.MAX_CONCURRENT,
                "queue": self.queue_count
            }

guest_concurrency = GuestConcurrencyController()

class TaskManager:
    def __init__(self):
        self.tasks = {} # batch_id -> task_info
        self.lock = threading.Lock()

    def create_batch(self, total_count):
        batch_id = str(uuid.uuid4())
        with self.lock:
            self.tasks[batch_id] = {
                "status": "processing",
                "total": total_count,
                "completed_count": 0,
                "created_at": datetime.now().isoformat(),
                "videos": [] # list of {id, title, status, progress, error, result}
            }
        return batch_id

    def init_video(self, batch_id, video_info):
        with self.lock:
            if batch_id in self.tasks:
                self.tasks[batch_id]["videos"].append({
                    "id": str(video_info.get('cid', uuid.uuid4())), 
                    "title": video_info.get('title', '未知视频'),
                    "original_index": video_info.get('index', len(self.tasks[batch_id]["videos"])),  # 保存原始索引
                    "status": "pending",
                    "progress": 0,
                    "error": None
                })
                # 返回 index
                return len(self.tasks[batch_id]["videos"]) - 1
        return -1

    def update_video_status(self, batch_id, video_index, status, progress=None, error=None, result=None):
        with self.lock:
            if batch_id in self.tasks and 0 <= video_index < len(self.tasks[batch_id]["videos"]):
                video = self.tasks[batch_id]["videos"][video_index]
                video["status"] = status
                if progress is not None:
                    video["progress"] = progress
                if error:
                    video["error"] = error
                if result:
                    video["result"] = result
                    
                # 检查是否全部完成（包括cancelled状态）
                failed_or_completed = sum(1 for v in self.tasks[batch_id]["videos"] 
                                        if v["status"] in ['completed', 'error', 'cancelled'])
                self.tasks[batch_id]["completed_count"] = failed_or_completed
                
                if failed_or_completed == self.tasks[batch_id]["total"]:
                    self.tasks[batch_id]["status"] = "completed"

    def get_status(self, batch_id):
        with self.lock:
            return self.tasks.get(batch_id)
    
    def get_video_status(self, batch_id, video_index):
        """获取单个视频的状态"""
        with self.lock:
            if batch_id in self.tasks and 0 <= video_index < len(self.tasks[batch_id]["videos"]):
                return self.tasks[batch_id]["videos"][video_index].get("status")
            return None
    
    def cancel_batch(self, batch_id):
        """取消批量任务，标记未处理和正在处理的视频为cancelled"""
        with self.lock:
            if batch_id not in self.tasks:
                return None
            
            task = self.tasks[batch_id]
            cancelled_indices = []
            processing_indices = []
            
            for idx, video in enumerate(task["videos"]):
                original_index = video.get("original_index", idx)
                # 取消pending状态的视频
                if video["status"] == "pending":
                    video["status"] = "cancelled"
                    video["progress"] = 100  # 设置为100%显示橙色进度条
                    cancelled_indices.append(original_index)
                # 也取消processing状态的视频（它们实际上可能还在后台执行，但UI上标记为取消）
                elif video["status"] == "processing":
                    video["status"] = "cancelled"
                    video["progress"] = 100  # 设置为100%显示橙色进度条
                    processing_indices.append(original_index)
            
            # 更新完成计数（包括cancelled的）
            finished = sum(1 for v in task["videos"] 
                          if v["status"] in ['completed', 'error', 'cancelled'])
            task["completed_count"] = finished
            
            # 标记批次为cancelled
            task["status"] = "cancelled"
            
            return {
                "cancelled_indices": cancelled_indices + processing_indices, 
                "status": task["status"],
                "has_processing": False  # 不再需要等待，都已标记为取消
            }
    
    def is_batch_cancelled(self, batch_id):
        """检查批次是否已被取消"""
        with self.lock:
            if batch_id in self.tasks:
                return self.tasks[batch_id].get("status") == "cancelled"
            return False

task_manager = TaskManager()


class ExtensionTaskManager:
    """
    Chrome 插件专用任务管理器
    使用数据库持久化 + 内存缓存
    """
    
    # 任务状态常量
    STATUS_PENDING = "pending"
    STATUS_DOWNLOADING = "downloading"
    STATUS_UPLOADING = "uploading"
    STATUS_TRANSCRIBING = "transcribing"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    
    # 阶段描述
    STAGE_DESC = {
        STATUS_PENDING: "等待处理",
        STATUS_DOWNLOADING: "正在下载音频",
        STATUS_UPLOADING: "正在上传到云端",
        STATUS_TRANSCRIBING: "正在语音识别",
        STATUS_PROCESSING: "正在处理结果",
        STATUS_COMPLETED: "字幕提取完成",
        STATUS_FAILED: "处理失败",
        STATUS_CANCELLED: "已取消"
    }
    
    def __init__(self):
        self.tasks = {}  # 内存缓存: task_id -> task_info
        self.user_tasks = {}  # user_id -> {bvid -> task_id}
        self.lock = threading.Lock()
    
    def _sync_to_db(self, task_id: str, task: dict):
        """将任务状态同步到数据库"""
        try:
            from models import ExtensionTask, db
            with app.app_context():
                db_task = ExtensionTask.query.filter_by(task_id=task_id).first()
                if db_task:
                    db_task.status = task.get('status', 'pending')
                    db_task.progress = task.get('progress', 0)
                    db_task.stage_desc = task.get('stage_desc', '')
                    db_task.error = task.get('error')
                    db_task.transcript = task.get('transcript')
                else:
                    db_task = ExtensionTask(
                        task_id=task_id,
                        user_id=task['user_id'],
                        bvid=task['bvid'],
                        title=task.get('title', task['bvid']),
                        status=task.get('status', 'pending'),
                        progress=task.get('progress', 0),
                        stage_desc=task.get('stage_desc', '等待处理')
                    )
                    db.session.add(db_task)
                db.session.commit()
        except Exception as e:
            logger.error(f"[ExtensionTask] 数据库同步失败: {e}")
    
    def _load_from_db(self, task_id: str = None, user_id: int = None, bvid: str = None) -> dict:
        """从数据库加载任务"""
        try:
            from models import ExtensionTask
            with app.app_context():
                if task_id:
                    db_task = ExtensionTask.query.filter_by(task_id=task_id).first()
                elif user_id and bvid:
                    # 查找用户该视频最新的未完成任务
                    db_task = ExtensionTask.query.filter_by(
                        user_id=user_id, bvid=bvid
                    ).filter(
                        ExtensionTask.status.notin_([self.STATUS_COMPLETED, self.STATUS_FAILED, self.STATUS_CANCELLED])
                    ).order_by(ExtensionTask.created_at.desc()).first()
                else:
                    return {}
                
                if db_task:
                    return {
                        'task_id': db_task.task_id,
                        'user_id': db_task.user_id,
                        'bvid': db_task.bvid,
                        'title': db_task.title,
                        'status': db_task.status,
                        'progress': db_task.progress,
                        'stage_desc': db_task.stage_desc or self.STAGE_DESC.get(db_task.status, ''),
                        'transcript': db_task.transcript,
                        'error': db_task.error,
                        'created_at': db_task.created_at.isoformat() if db_task.created_at else None,
                        'updated_at': db_task.updated_at.isoformat() if db_task.updated_at else None
                    }
        except Exception as e:
            logger.error(f"[ExtensionTask] 数据库加载失败: {e}")
        return {}
    
    def create_task(self, user_id: int, bvid: str, title: str = None, use_asr: bool = False) -> str:
        """创建新任务，返回任务ID"""
        task_id = str(uuid.uuid4())
        now = datetime.utcnow()
        
        with self.lock:
            # 检查是否已有该视频的进行中任务（先查内存，再查数据库）
            if user_id in self.user_tasks and bvid in self.user_tasks[user_id]:
                old_task_id = self.user_tasks[user_id][bvid]
                old_task = self.tasks.get(old_task_id)
                if old_task and old_task["status"] not in [self.STATUS_COMPLETED, self.STATUS_FAILED, self.STATUS_CANCELLED]:
                    return old_task_id
            
            # 再查数据库
            db_task = self._load_from_db(user_id=user_id, bvid=bvid)
            if db_task and db_task.get('status') not in [self.STATUS_COMPLETED, self.STATUS_FAILED, self.STATUS_CANCELLED]:
                # 恢复到内存缓存
                self.tasks[db_task['task_id']] = db_task
                if user_id not in self.user_tasks:
                    self.user_tasks[user_id] = {}
                self.user_tasks[user_id][bvid] = db_task['task_id']
                return db_task['task_id']
            
            # 创建新任务
            task_info = {
                "task_id": task_id,
                "user_id": user_id,
                "bvid": bvid,
                "title": title or bvid,
                "use_asr": use_asr,
                "status": self.STATUS_PENDING,
                "progress": 0,
                "stage_desc": self.STAGE_DESC[self.STATUS_PENDING],
                "transcript": None,
                "transcript_with_timestamps": None,
                "error": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat()
            }
            
            self.tasks[task_id] = task_info
            
            if user_id not in self.user_tasks:
                self.user_tasks[user_id] = {}
            self.user_tasks[user_id][bvid] = task_id
        
        # 同步到数据库（异步，不阻塞）
        try:
            self._sync_to_db(task_id, task_info)
        except Exception as e:
            logger.error(f"[ExtensionTask] 创建任务时数据库同步失败: {e}")
        
        return task_id
    
    def update_task(self, task_id: str, status: str = None, progress: int = None, 
                    transcript: str = None, transcript_with_timestamps: list = None,
                    error: str = None, stage_desc: str = None):
        """更新任务状态并同步到数据库"""
        with self.lock:
            if task_id not in self.tasks:
                # 尝试从数据库加载
                db_task = self._load_from_db(task_id=task_id)
                if db_task:
                    self.tasks[task_id] = db_task
                else:
                    return False
            
            task = self.tasks[task_id]
            if status:
                task["status"] = status
                # 如果没有提供 stage_desc，则使用默认描述
                if not stage_desc:
                    task["stage_desc"] = self.STAGE_DESC.get(status, status)
            if stage_desc:
                task["stage_desc"] = stage_desc
            if progress is not None:
                task["progress"] = progress
            if transcript is not None:
                task["transcript"] = transcript
            if transcript_with_timestamps is not None:
                task["transcript_with_timestamps"] = transcript_with_timestamps
            if error:
                task["error"] = error
            task["updated_at"] = datetime.utcnow().isoformat()
        
        # 同步到数据库
        try:
            self._sync_to_db(task_id, task)
        except Exception as e:
            logger.error(f"[ExtensionTask] 更新任务时数据库同步失败: {e}")
        
        return True
    
    def get_task(self, task_id: str) -> dict:
        """获取任务信息（先查内存，再查数据库）"""
        with self.lock:
            if task_id in self.tasks:
                return self.tasks[task_id].copy()
        
        # 查数据库
        return self._load_from_db(task_id=task_id)
    
    def get_task_by_bvid(self, user_id: int, bvid: str) -> dict:
        """通过 bvid 获取用户的任务"""
        with self.lock:
            if user_id in self.user_tasks and bvid in self.user_tasks[user_id]:
                task_id = self.user_tasks[user_id][bvid]
                if task_id in self.tasks:
                    return self.tasks[task_id].copy()
        
        # 查数据库
        return self._load_from_db(user_id=user_id, bvid=bvid)
    
    def get_user_tasks(self, user_id: int) -> list:
        """获取用户所有进行中的任务（从数据库）"""
        try:
            from models import ExtensionTask
            with app.app_context():
                tasks = ExtensionTask.query.filter_by(user_id=user_id).filter(
                    ExtensionTask.status.notin_([self.STATUS_COMPLETED, self.STATUS_FAILED, self.STATUS_CANCELLED])
                ).order_by(ExtensionTask.created_at.desc()).all()
                return [t.to_dict() for t in tasks]
        except Exception as e:
            logger.error(f"[ExtensionTask] 获取用户任务失败: {e}")
        return []
    
    def get_user_all_tasks(self, user_id: int, limit: int = 20) -> list:
        """获取用户所有任务（包括已完成和失败的，用于调试）"""
        try:
            from models import ExtensionTask
            with app.app_context():
                tasks = ExtensionTask.query.filter_by(user_id=user_id).order_by(
                    ExtensionTask.created_at.desc()
                ).limit(limit).all()
                return [t.to_dict() for t in tasks]
        except Exception as e:
            logger.error(f"[ExtensionTask] 获取用户所有任务失败: {e}")
        return []
    
    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        with self.lock:
            if task_id not in self.tasks:
                # 尝试从数据库加载
                db_task = self._load_from_db(task_id=task_id)
                if db_task:
                    self.tasks[task_id] = db_task
                else:
                    return False
                    
            task = self.tasks.get(task_id)
            if not task:
                return False
            if task["status"] in [self.STATUS_COMPLETED, self.STATUS_FAILED]:
                return False
            task["status"] = self.STATUS_CANCELLED
            task["stage_desc"] = self.STAGE_DESC[self.STATUS_CANCELLED]
            task["updated_at"] = datetime.utcnow().isoformat()
        
        # 同步到数据库
        try:
            self._sync_to_db(task_id, task)
        except Exception as e:
            logger.error(f"[ExtensionTask] 取消任务时数据库同步失败: {e}")
        
        return True
    
    def is_cancelled(self, task_id: str) -> bool:
        """检查任务是否已取消"""
        task = self.get_task(task_id)
        return task and task.get("status") == self.STATUS_CANCELLED
    
    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """清理旧任务（从数据库）"""
        from datetime import timedelta
        try:
            from models import ExtensionTask, db
            cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
            with app.app_context():
                # 删除已完成/失败/取消的旧任务
                ExtensionTask.query.filter(
                    ExtensionTask.created_at < cutoff,
                    ExtensionTask.status.in_([self.STATUS_COMPLETED, self.STATUS_FAILED, self.STATUS_CANCELLED])
                ).delete(synchronize_session=False)
                db.session.commit()
        except Exception as e:
            logger.error(f"[ExtensionTask] 清理旧任务失败: {e}")

extension_task_manager = ExtensionTaskManager()


class LogCollector:
    """日志收集器，用于收集处理过程中的日志，支持进度回调"""
    
    # 进度阶段定义
    STAGE_INIT = "init"
    STAGE_DOWNLOAD = "download"
    STAGE_CONVERT = "convert"
    STAGE_TRANSCRIBE = "transcribe"
    STAGE_COMPLETE = "complete"
    STAGE_ERROR = "error"
    
    def __init__(self, progress_callback=None):
        self.logs = []
        self.progress_callback = progress_callback
        self.current_stage = self.STAGE_INIT
        self.progress = 0  # 0-100
        self.video_duration = 0
        self.bytes_sent = 0
        self.total_bytes = 0
    
    def set_stage(self, stage: str, progress: int = None):
        """设置当前阶段和进度"""
        self.current_stage = stage
        if progress is not None:
            self.progress = progress
        self._notify_progress()
    
    def set_progress(self, progress: int):
        """设置进度百分比"""
        self.progress = min(100, max(0, progress))
        self._notify_progress()
    
    def _notify_progress(self):
        """通知进度更新"""
        if self.progress_callback:
            self.progress_callback({
                "type": "progress",
                "stage": self.current_stage,
                "progress": self.progress
            })
    
    def _notify_log(self, log_entry):
        """通知日志更新"""
        if self.progress_callback:
            self.progress_callback({
                "type": "log",
                "log": log_entry
            })
    
    def log(self, level: str, message: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_entry = {
            "timestamp": timestamp,
            "level": level,
            "message": message
        }
        self.logs.append(log_entry)
        # 同时输出到控制台
        getattr(logger, level.lower(), logger.info)(message)
        # 通知前端
        self._notify_log(log_entry)
    
    def info(self, message: str):
        self.log("INFO", message)
    
    def error(self, message: str):
        self.log("ERROR", message)
    
    def warning(self, message: str):
        self.log("WARNING", message)
    
    def get_logs(self):
        return self.logs


# 缓存buvid值，避免频繁请求
_buvid_cache = {'buvid3': None, 'buvid4': None, 'timestamp': 0}

def get_buvid():
    """
    通过B站官方API获取buvid3和buvid4
    
    Returns:
        dict: {'buvid3': str, 'buvid4': str} 或 None
    """
    import requests
    import time
    
    global _buvid_cache
    
    # 检查缓存（1小时有效期）
    if _buvid_cache['buvid3'] and time.time() - _buvid_cache['timestamp'] < 3600:
        return _buvid_cache
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.bilibili.com/'
        }
        
        # 使用B站官方API获取buvid
        resp = requests.get(
            'https://api.bilibili.com/x/frontend/finger/spi',
            headers=headers,
            timeout=10
        )
        data = resp.json()
        
        if data.get('code') == 0:
            buvid3 = data.get('data', {}).get('b_3', '')
            buvid4 = data.get('data', {}).get('b_4', '')
            
            if buvid3:
                _buvid_cache = {
                    'buvid3': buvid3,
                    'buvid4': buvid4,
                    'timestamp': time.time()
                }
                logger.info(f"成功获取buvid3: {buvid3[:20]}...")
                return _buvid_cache
    except Exception as e:
        logger.warning(f"获取buvid失败: {e}")
    
    return None


# WBI签名相关
# 混淆密钥映射表
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52
]

# 缓存WBI密钥
_wbi_cache = {'img_key': None, 'sub_key': None, 'timestamp': 0}


def get_wbi_keys(headers: dict = None):
    """
    获取WBI签名所需的img_key和sub_key
    
    Args:
        headers: 请求头（包含Cookie）
    
    Returns:
        tuple: (img_key, sub_key) 或 (None, None)
    """
    import requests
    import time
    import re
    
    global _wbi_cache
    
    # 检查缓存（30分钟有效期）
    if _wbi_cache['img_key'] and time.time() - _wbi_cache['timestamp'] < 1800:
        return _wbi_cache['img_key'], _wbi_cache['sub_key']
    
    if headers is None:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.bilibili.com/'
        }
    
    try:
        resp = requests.get(
            'https://api.bilibili.com/x/web-interface/nav',
            headers=headers,
            timeout=10
        )
        data = resp.json()
        
        if data.get('code') == 0:
            wbi_img = data.get('data', {}).get('wbi_img', {})
            img_url = wbi_img.get('img_url', '')
            sub_url = wbi_img.get('sub_url', '')
            
            # 从URL中提取key（文件名去掉扩展名）
            img_match = re.search(r'/([a-zA-Z0-9]+)\.png', img_url)
            sub_match = re.search(r'/([a-zA-Z0-9]+)\.png', sub_url)
            
            if img_match and sub_match:
                img_key = img_match.group(1)
                sub_key = sub_match.group(1)
                
                _wbi_cache = {
                    'img_key': img_key,
                    'sub_key': sub_key,
                    'timestamp': time.time()
                }
                logger.info(f"成功获取WBI密钥")
                return img_key, sub_key
    except Exception as e:
        logger.warning(f"获取WBI密钥失败: {e}")
    
    return None, None


def get_mixin_key(img_key: str, sub_key: str) -> str:
    """
    根据img_key和sub_key生成mixin_key
    
    Args:
        img_key: 从nav接口获取的img_key
        sub_key: 从nav接口获取的sub_key
    
    Returns:
        str: 混合后的mixin_key（32位）
    """
    raw_key = img_key + sub_key
    mixin_key = ''.join([raw_key[i] for i in MIXIN_KEY_ENC_TAB])
    return mixin_key[:32]


def sign_wbi_params(params: dict, img_key: str, sub_key: str) -> dict:
    """
    对请求参数进行WBI签名
    
    Args:
        params: 原始请求参数
        img_key: WBI img_key
        sub_key: WBI sub_key
    
    Returns:
        dict: 添加了wts和w_rid的签名后参数
    """
    import hashlib
    import time
    import urllib.parse
    
    mixin_key = get_mixin_key(img_key, sub_key)
    
    # 添加时间戳
    params = params.copy()
    params['wts'] = int(time.time())
    
    # 按key排序
    sorted_params = sorted(params.items())
    
    # 过滤特殊字符并序列化
    query_string = urllib.parse.urlencode(sorted_params)
    # 过滤掉 !'()* 字符
    for char in "!'()*":
        query_string = query_string.replace(urllib.parse.quote(char), '')
    
    # 计算w_rid
    w_rid = hashlib.md5((query_string + mixin_key).encode()).hexdigest()
    params['w_rid'] = w_rid
    
    return params


def get_ffmpeg_path():
    """获取ffmpeg路径，优先使用static-ffmpeg"""
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        return 'ffmpeg'
    except ImportError:
        # 尝试使用系统ffmpeg
        import shutil
        if shutil.which('ffmpeg'):
            return 'ffmpeg'
        return None


def convert_to_mp3(input_path: str, output_path: str, log_collector: LogCollector) -> bool:
    """
    使用ffmpeg将音频转换为mp3格式（文件更小，上传更快）
    
    Args:
        input_path: 输入音频文件路径
        output_path: 输出mp3文件路径
        log_collector: 日志收集器
    
    Returns:
        bool: 转换是否成功
    """
    import subprocess
    
    ffmpeg_path = get_ffmpeg_path()
    if not ffmpeg_path:
        log_collector.error("未找到ffmpeg，无法转换音频格式")
        return False
    
    try:
        log_collector.info(f"正在将音频转换为mp3格式...")
        
        # 使用ffmpeg转换为16kHz采样率的单声道mp3，比特率64k（语音足够）
        cmd = [
            ffmpeg_path,
            '-i', input_path,
            '-ar', '16000',      # 采样率16kHz
            '-ac', '1',          # 单声道
            '-b:a', '64k',       # 比特率64kbps（语音足够清晰）
            '-f', 'mp3',         # 输出格式
            '-y',                # 覆盖输出文件
            output_path
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5分钟超时
        )
        
        if result.returncode == 0 and os.path.exists(output_path):
            output_size = os.path.getsize(output_path) / (1024 * 1024)
            log_collector.info(f"音频转换完成，大小: {output_size:.2f} MB")
            return True
        else:
            log_collector.error(f"ffmpeg转换失败: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        log_collector.error("音频转换超时")
        return False
    except Exception as e:
        log_collector.error(f"音频转换出错: {str(e)}")
        return False


def download_bilibili_audio(url: str, output_dir: str, log_collector: LogCollector) -> str:
    """
    使用yt-dlp下载B站视频的音频
    
    Args:
        url: B站视频URL
        output_dir: 输出目录
        log_collector: 日志收集器
    
    Returns:
        str: 音频文件路径（wav格式）
    """
    import yt_dlp
    
    # 确保 ffmpeg 可用（使用 static-ffmpeg 包提供的预编译版本）
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        log_collector.info("使用 static-ffmpeg 提供的 ffmpeg")
    except Exception as e:
        log_collector.info(f"static-ffmpeg 不可用，尝试使用系统 ffmpeg: {e}")
    
    log_collector.info(f"开始下载B站视频音频: {url}")
    
    # 生成唯一的输出文件名
    output_filename = f"audio_{uuid.uuid4().hex}"
    output_template = os.path.join(output_dir, output_filename)
    
    # 自定义进度钩子
    def progress_hook(d):
        if d['status'] == 'downloading':
            try:
                p = d.get('_percent_str', '0%').replace('%', '')
                current_percent = float(p)
                # 映射到总进度的 5% - 40% (下载阶段权重增加到35%)
                mapped_percent = 5 + (current_percent * 0.35)
                log_collector.set_progress(int(mapped_percent))
            except:
                pass

    # 下载原始音频格式（不需要 ffmpeg 转换）
    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',  # 优先下载 m4a 格式，避免需要转换
        'outtmpl': output_template + '.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook],
        'prefer_ffmpeg': False,  # 不优先使用 ffmpeg
        'ffmpeg_location': None,  # 不指定 ffmpeg 位置
        # 不使用 postprocessors，避免依赖 ffmpeg
    }
    
    duration = 0
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            log_collector.info("正在获取视频信息...")
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', '未知标题')
            duration = info.get('duration', 0)
            log_collector.info(f"视频标题: {video_title}")
            log_collector.info(f"视频时长: {duration}秒")
            
            log_collector.info("正在下载音频...")
            ydl.download([url])
        
        # 扫描下载的音频文件（可能是 m4a, mp3, webm, opus 等格式）
        audio_extensions = ['.m4a', '.mp3', '.webm', '.opus', '.aac', '.wav', '.ogg']
        downloaded_file = None
        
        for ext in audio_extensions:
            potential_file = output_template + ext
            if os.path.exists(potential_file):
                downloaded_file = potential_file
                break
        
        if not downloaded_file:
            # 尝试扫描目录找到匹配的文件
            for f in os.listdir(output_dir):
                if f.startswith(output_filename):
                    for ext in audio_extensions:
                        if f.endswith(ext):
                            downloaded_file = os.path.join(output_dir, f)
                            break
                    if downloaded_file:
                        break
        
        if not downloaded_file:
            raise FileNotFoundError(f"未找到下载的音频文件: {output_template}.*")
        
        file_size = os.path.getsize(downloaded_file) / (1024 * 1024)
        log_collector.info(f"音频下载完成: {os.path.basename(downloaded_file)}")
        log_collector.info(f"音频文件大小: {file_size:.2f} MB")
        log_collector.set_progress(40)  # 下载完成40%
        
        return downloaded_file, duration
    
    except Exception as e:
        log_collector.error(f"下载音频失败: {str(e)}")
        raise


def upload_to_temp_storage(file_path: str, log_collector: LogCollector) -> str:
    """
    上传文件到临时存储服务
    并发探测所有服务可用性，然后并发上传，谁先成功用谁
    """
    import requests
    import os
    from urllib.parse import urlparse
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    filename = os.path.basename(file_path)
    
    # 临时存储服务列表
    services = [
        {
            'name': 'tmpfile.link',
            'check_url': 'https://tmpfile.link',
            'upload_url': 'https://tmpfile.link/api/upload',
            'field': 'file',
            'data': {},
            'response_type': 'json',
            'json_key': 'downloadLink'
        },
        {
            'name': 'litterbox.catbox.moe',
            'check_url': 'https://litterbox.catbox.moe',
            'upload_url': 'https://litterbox.catbox.moe/resources/internals/api.php',
            'field': 'fileToUpload',
            'data': {'reqtype': 'fileupload', 'time': '1h'},
            'response_type': 'text'
        },
        {
            'name': 'file.io',
            'check_url': 'https://file.io',
            'upload_url': 'https://file.io',
            'field': 'file',
            'data': {'expires': '1h'},
            'response_type': 'json',
            'json_key': 'link'
        },
        {
            'name': '0x0.st',
            'check_url': 'https://0x0.st',
            'upload_url': 'https://0x0.st',
            'field': 'file',
            'data': {},
            'response_type': 'text'
        },
        {
            'name': 'transfer.sh',
            'check_url': 'https://transfer.sh',
            'upload_url': f'https://transfer.sh/{filename}',
            'method': 'PUT',
            'response_type': 'text'
        }
    ]
    
    def check_service(service):
        """检查单个服务可用性"""
        try:
            check_resp = requests.head(service['check_url'], timeout=3, allow_redirects=True, proxies={})
            if check_resp.status_code < 500:
                return service
        except:
            pass
        return None
    
    def upload_to_service(service, file_path, filename):
        """上传到单个服务"""
        try:
            method = service.get('method', 'POST')
            
            if method == 'PUT':
                with open(file_path, 'rb') as f:
                    response = requests.put(
                        service['upload_url'], 
                        data=f, 
                        timeout=120,
                        headers={'Content-Type': 'application/octet-stream'},
                        proxies={}
                    )
            else:
                with open(file_path, 'rb') as f:
                    files = {service['field']: (filename, f)}
                    response = requests.post(
                        service['upload_url'], 
                        files=files, 
                        data=service.get('data', {}), 
                        timeout=120,
                        proxies={}
                    )
            
            if response.status_code in [200, 201]:
                # 解析响应获取 URL
                if service.get('response_type') == 'json':
                    json_data = response.json()
                    if 'success' in json_data and str(json_data.get('success')).lower() == 'false':
                        return None
                    
                    primary_key = service.get('json_key', 'link')
                    result_url = json_data.get(primary_key)
                    
                    if not result_url or not isinstance(result_url, str):
                        for fallback_key in ['downloadLink', 'url', 'link', 'file_url', 'data']:
                            if fallback_key in json_data:
                                val = json_data[fallback_key]
                                if isinstance(val, str) and val.startswith('http'):
                                    result_url = val
                                    break
                else:
                    result_url = response.text.strip()
                
                if result_url and result_url.startswith('http'):
                    # 验证链接可访问性
                    verify_resp = requests.head(result_url, timeout=10, allow_redirects=True, proxies={})
                    if verify_resp.status_code < 400:
                        return (service['name'], result_url)
        except Exception as e:
            pass
        return None
    
    # === 第一步：并发探测所有服务可用性 ===
    log_collector.info("并发探测第三方存储服务...")
    available_services = []
    
    with ThreadPoolExecutor(max_workers=5) as check_executor:
        futures = {check_executor.submit(check_service, s): s for s in services}
        for future in as_completed(futures, timeout=5):
            try:
                result = future.result()
                if result:
                    available_services.append(result)
                    log_collector.info(f"  ✓ {result['name']} 可用")
            except:
                pass
    
    if not available_services:
        raise Exception("所有第三方存储服务均不可用")
    
    log_collector.info(f"发现 {len(available_services)} 个可用服务，开始并发上传...")
    
    # === 第二步：并发上传到所有可用服务，谁先成功用谁 ===
    with ThreadPoolExecutor(max_workers=len(available_services)) as upload_executor:
        futures = {
            upload_executor.submit(upload_to_service, s, file_path, filename): s 
            for s in available_services
        }
        
        for future in as_completed(futures, timeout=120):
            try:
                result = future.result()
                if result:
                    service_name, result_url = result
                    log_collector.info(f"上传成功 ({service_name}): {result_url[:60]}...")
                    # 取消其他未完成的上传任务
                    for f in futures:
                        f.cancel()
                    return result_url
            except:
                pass
    
    raise Exception("所有上传服务均失败")


def check_self_hosted_domain(domain: str, log_collector: LogCollector):
    """检查自建域名是否可访问"""
    import requests
    try:
        domain = domain.rstrip('/')
        test_url = f"{domain}/" # 测试根路径或某个特定路径
        log_collector.info(f"正在检查自建服务可用性: {test_url}")
        requests.head(test_url, timeout=5, verify=False) # 忽略证书错误，因为自建服务可能有自签证书
    except Exception as e:
        error_msg = f"自建服务 {domain} 连通性检查失败: {str(e)}"
        log_collector.error(error_msg)
        raise Exception(error_msg)


def transcribe_audio(audio_path: str, api_key: str, log_collector: LogCollector, self_hosted_domain: str = None, duration: int = 0) -> str:
    """
    使用阿里云Paraformer-v2进行录音文件语音识别（异步文件识别，更便宜）
    
    Args:
        audio_path: 音频文件路径
        api_key: 阿里云API Key
        log_collector: 日志收集器
        self_hosted_domain: 自建服务域名（支持HTTP/HTTPS，需公网可访问）
        duration: 音频时长（秒），用于进度估算
    
    Returns:
        str: 识别的字幕文本
    """
    import dashscope
    from dashscope.audio.asr import Transcription
    import json
    import requests
    import os
    
    log_collector.info(f"开始语音识别: {os.path.basename(audio_path)}")
    
    # 设置API Key
    dashscope.api_key = api_key
    
    try:
        # 1. 存储策略 - 切换到格式转换/上传阶段
        log_collector.set_stage(LogCollector.STAGE_CONVERT, 42)
        
        file_url = ""
        if self_hosted_domain:
            # 严格检查自建服务
            check_self_hosted_domain(self_hosted_domain, log_collector)
            
            # 使用自建服务（HTTP/HTTPS均支持，关键是公网可访问）
            filename = os.path.basename(audio_path)
            file_url = f"{self_hosted_domain.rstrip('/')}/temp_audio/{filename}"
            log_collector.info(f"使用本地直链服务: {file_url[:50]}...")
            
            # 确认文件是否可通过URL访问 (对于 Lucky 模式，实际上此时只是生成URL，文件在本地目录，
            # 但既然是自建，我们假设本地目录已经通过 web server 暴露)
            # 注意：这里的逻辑是基于 docker volume 挂载，文件在本地，web server (Lucky/Nginx) 提供映射
        else:
            # 使用第三方存储（catbox.moe）
            log_collector.info("上传音频文件到临时存储(catbox)...")
            file_url = upload_to_temp_storage(audio_path, log_collector)
            log_collector.info(f"文件已上传: {file_url[:50]}...")
        log_collector.set_progress(45)
        
        log_collector.info("提交语音识别任务...")
        log_collector.set_progress(45)
        
        # 异步提交转录任务
        task_response = Transcription.async_call(
            model='paraformer-v2',
            file_urls=[file_url],
            language_hints=['zh', 'en']
        )
        
        if task_response.status_code != 200:
            error_msg = f"提交任务失败: {task_response.message}"
            log_collector.error(error_msg)
            raise Exception(error_msg)
        
        task_id = task_response.output.get('task_id')
        log_collector.info(f"任务已提交，Task ID: {task_id}")
        
        # 切换到语音识别阶段
        log_collector.set_stage(LogCollector.STAGE_TRANSCRIBE, 50)
        
        # 使用fetch轮询模式等待任务完成，实现细粒度进度显示
        log_collector.info("等待语音识别完成...")
        
        import time
        poll_count = 0
        max_polls = 600  # 最大轮询次数（5分钟超时）
        poll_interval = 0.5  # 轮询间隔（秒）
        
        # 进度估算参数
        # PENDING阶段: 50% -> 55%
        # RUNNING阶段: 55% -> 85%（转录中）
        # Paraformer 处理速度通常很快，约 10-20 倍速 (1分钟音频约需3-6秒)
        # 估算总耗时 = 音频时长 / 10，最小 3 秒
        estimated_time = max(3, duration / 10) if duration > 0 else 10
        log_collector.info(f"估算转录耗时: {estimated_time:.1f}秒 (视频时长 {duration}秒)")

        pending_start = 50
        pending_end = 55
        running_start = 55
        running_end = 85
        
        last_status = None
        running_start_time = None
        
        while poll_count < max_polls:
            # 添加SSL错误重试逻辑
            retry_count = 0
            max_retries = 3
            transcription_response = None
            
            while retry_count < max_retries:
                try:
                    transcription_response = Transcription.fetch(task=task_id)
                    break  # 成功则跳出重试循环
                except Exception as e:
                    retry_count += 1
                    error_str = str(e)
                    if 'SSL' in error_str or 'Max retries' in error_str:
                        log_collector.warning(f"SSL连接错误，重试 {retry_count}/{max_retries}...")
                        time.sleep(1)  # 等待1秒后重试
                        if retry_count >= max_retries:
                            log_collector.error(f"SSL重试{max_retries}次仍失败: {error_str}")
                            raise
                    else:
                        raise  # 非SSL错误直接抛出
            
            if transcription_response is None:
                raise Exception("获取任务状态失败")
            
            current_status = transcription_response.output.task_status
            
            # 状态变化时记录
            if current_status != last_status:
                log_collector.info(f"任务状态: {current_status}")
                last_status = current_status
                if current_status == 'RUNNING':
                    running_start_time = time.time()
            
            # 根据状态计算进度
            if current_status == 'PENDING':
                # PENDING阶段：50% -> 55%，根据轮询次数线性增长
                progress = pending_start + min((pending_end - pending_start), poll_count * 0.5)
                log_collector.set_progress(int(progress))
                
            elif current_status == 'RUNNING':
                # RUNNING阶段：55% -> 85%，根据运行时间估算
                # 假设平均转录时间为30秒，实际会更快
                if running_start_time:
                    elapsed = time.time() - running_start_time
                    # 使用对数曲线使进度更平滑（快速增长后趋于平稳）
                    import math
                    # 根据估算时间计算进度
                    progress_ratio = min(1.0, elapsed / estimated_time)
                    # 使用平方根曲线，前期稍快，后期平缓
                    progress_ratio = math.pow(progress_ratio, 0.8)
                    progress = running_start + (running_end - running_start) * progress_ratio
                else:
                    progress = running_start
                log_collector.set_progress(int(progress))
                
            elif current_status == 'SUCCEEDED':
                log_collector.info("语音识别任务完成！")
                log_collector.set_progress(85)
                break
                
            elif current_status == 'FAILED':
                error_msg = f"语音识别失败: {transcription_response.output.get('message', '未知错误')}"
                log_collector.error(error_msg)
                raise Exception(error_msg)
            
            time.sleep(poll_interval)
            poll_count += 1
        
        # 超时检查
        if poll_count >= max_polls:
            raise Exception("语音识别超时（5分钟）")
        
        log_collector.set_progress(85)
        
        if transcription_response.status_code != 200:
            error_msg = f"识别失败: {transcription_response.message}"
            log_collector.error(error_msg)
            raise Exception(error_msg)
        
        # 解析结果
        results = transcription_response.output.get('results', [])
        log_collector.info(f"[DEBUG] 结果数量: {len(results)}")
        if results:
            log_collector.info(f"[DEBUG] 第一个结果: {results[0]}")
        
        if not results:
            log_collector.warning("未识别到任何文本内容")
            return "（未识别到任何语音内容）"
        
        # 获取第一个文件的结果
        first_result = results[0]
        transcription_url = first_result.get('transcription_url')
        
        if not transcription_url:
            # 检查是否有其他字段包含结果
            log_collector.warning(f"未获取到转录结果URL，完整返回: {first_result}")
            return "（未获取到转录结果）"
        
        # 下载转录结果JSON
        log_collector.info("获取转录结果...")
        resp = requests.get(transcription_url, timeout=30)
        transcript_data = resp.json()
        
        # 提取文本
        transcripts = transcript_data.get('transcripts', [])
        if not transcripts:
            log_collector.warning("转录结果为空")
            return "（转录结果为空）"
        
        # 合并所有文本（包含说话人标识）
        full_text_parts = []
        last_speaker_id = None
        
        for transcript in transcripts:
            sentences = transcript.get('sentences', [])
            for sentence in sentences:
                text = sentence.get('text', '').strip()
                if text:
                    # 获取说话人ID（开启说话人分离后会有此字段）
                    speaker_id = sentence.get('speaker_id')
                    
                    if speaker_id is not None:
                        # 有说话人信息，添加标识
                        if speaker_id != last_speaker_id:
                            # 换了说话人，添加标识
                            speaker_label = f"[说话人{speaker_id + 1}]"
                            full_text_parts.append(f"\n{speaker_label}\n{text}")
                            last_speaker_id = speaker_id
                        else:
                            # 同一说话人继续说
                            full_text_parts.append(text)
                    else:
                        # 没有说话人信息（单人或未启用分离）
                        full_text_parts.append(text)
        
        if not full_text_parts:
            # 尝试从text字段获取（兜底逻辑）
            for transcript in transcripts:
                text = transcript.get('text', '').strip()
                if text:
                    full_text_parts.append(text)
        
        full_text = '\n'.join(full_text_parts)
        # 清理多余空行
        import re
        full_text = re.sub(r'\n{3,}', '\n\n', full_text).strip()
        
        log_collector.info(f"识别完成，共 {len(full_text_parts)} 个片段，总字符数: {len(full_text)}")
        log_collector.set_progress(95)
        
        return full_text if full_text else "（未识别到任何语音内容）"
    
    except Exception as e:
        log_collector.error(f"语音识别失败: {str(e)}")
        raise
    finally:
        # 清理临时文件
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
                log_collector.info("临时音频文件已清理")
        except Exception as e:
            log_collector.warning(f"清理临时文件失败: {str(e)}")


@app.route('/temp_audio/<path:filename>')
def serve_temp_audio(filename):
    """
    提供临时音频文件的 HTTPS 访问
    当使用自建存储模式时，Paraformer 通过此路由访问音频文件
    """
    return send_from_directory(AUDIO_DIR, filename)


@app.route('/login')
def login_page():
    """登录页面"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/')
@login_required
def index():
    """返回前端页面（需要登录）"""
    return send_from_directory('.', 'index.html')


@app.route('/api/transcribe', methods=['POST'])
@login_required
def transcribe():
    """
    处理转录请求
    
    请求体:
        {
            "url": "B站视频URL",
            "api_key": "阿里云API Key"
        }
    
    响应:
        {
            "success": true/false,
            "transcript": "字幕文本",
            "logs": [日志列表],
            "error": "错误信息（如果有）"
        }
    """
    log_collector = LogCollector()
    
    try:
        # 获取请求参数
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "transcript": "",
                "logs": log_collector.get_logs(),
                "error": "请求体为空"
            }), 400
        
        url = data.get('url', '').strip()
        api_key = data.get('api_key', '').strip()
        
        # 存储模式配置
        use_self_hosted = data.get('use_self_hosted', False)
        self_hosted_domain = data.get('self_hosted_domain', '').strip()
        
        log_collector.info("收到转录请求")
        log_collector.info(f"视频URL: {url}")
        if use_self_hosted:
            log_collector.info(f"存储模式: 自建HTTPS服务 ({self_hosted_domain})")
        
        # 验证参数
        if not url:
            log_collector.error("未提供视频URL")
            return jsonify({
                "success": False,
                "transcript": "",
                "logs": log_collector.get_logs(),
                "error": "请提供B站视频URL"
            }), 400
        
        if not api_key:
            log_collector.error("未提供API Key")
            return jsonify({
                "success": False,
                "transcript": "",
                "logs": log_collector.get_logs(),
                "error": "请提供阿里云API Key"
            }), 400
        
        # 验证URL格式（支持B站各种链接格式）
        if not any(domain in url for domain in ['bilibili.com', 'b23.tv']):
            log_collector.error("无效的B站链接")
            return jsonify({
                "success": False,
                "transcript": "",
                "logs": log_collector.get_logs(),
                "error": "请提供有效的B站视频链接"
            }), 400
        
        # 下载音频
        log_collector.info("=" * 50)
        log_collector.info("阶段1: 下载视频音频")
        log_collector.info("=" * 50)
        audio_path, duration = download_bilibili_audio(url, AUDIO_DIR, log_collector)
        
        # 语音识别
        log_collector.info("=" * 50)
        log_collector.info("阶段2: 语音识别")
        log_collector.info("=" * 50)
        transcript = transcribe_audio(audio_path, api_key, log_collector, self_hosted_domain if use_self_hosted else None, duration)
        
        log_collector.info("=" * 50)
        log_collector.info("处理完成!")
        log_collector.info("=" * 50)
        
        return jsonify({
            "success": True,
            "transcript": transcript,
            "logs": log_collector.get_logs(),
            "error": ""
        })
    
    except Exception as e:
        error_msg = str(e)
        log_collector.error(f"处理失败: {error_msg}")
        
        return jsonify({
            "success": False,
            "transcript": "",
            "logs": log_collector.get_logs(),
            "error": error_msg
        }), 500


@app.route('/api/transcribe_stream', methods=['POST'])
@login_required
def transcribe_stream():
    """
    流式处理转录请求，使用SSE推送进度
    """
    import queue
    import threading
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "请求体为空"}), 400
    
    url = data.get('url', '').strip()
    api_key = data.get('api_key', '').strip()
    
    # 验证参数
    if not url:
        return jsonify({"error": "请提供B站视频URL"}), 400
    
    if not api_key:
        return jsonify({"error": "请提供阿里云API Key"}), 400
    
    if not any(domain in url for domain in ['bilibili.com', 'b23.tv']):
        return jsonify({"error": "请提供有效的B站视频链接"}), 400
    
    # 创建消息队列
    message_queue = queue.Queue()
    
    def progress_callback(data):
        """进度回调，将消息放入队列"""
        message_queue.put(data)
    
    def process_task():
        """在后台线程中处理任务"""
        log_collector = LogCollector(progress_callback=progress_callback)
        
        try:
            # 阶段1: 下载
            log_collector.set_stage(LogCollector.STAGE_DOWNLOAD, 5)
            log_collector.info("阶段1: 下载视频音频")
            audio_path, duration = download_bilibili_audio(url, AUDIO_DIR, log_collector)
            log_collector.set_stage(LogCollector.STAGE_DOWNLOAD, 30)
            
            # 阶段2: 语音识别
            log_collector.set_stage(LogCollector.STAGE_TRANSCRIBE, 35)
            log_collector.info("阶段2: 语音识别")
            transcript = transcribe_audio(audio_path, api_key, log_collector, self_hosted_domain if use_self_hosted else None, duration)
            
            # 完成
            log_collector.set_stage(LogCollector.STAGE_COMPLETE, 100)
            log_collector.info("处理完成!")
            
            # 发送最终结果
            message_queue.put({
                "type": "result",
                "success": True,
                "transcript": transcript
            })
            
        except Exception as e:
            log_collector.set_stage(LogCollector.STAGE_ERROR, 0)
            log_collector.error(f"处理失败: {str(e)}")
            message_queue.put({
                "type": "result",
                "success": False,
                "error": str(e)
            })
        
        # 发送结束标记
        message_queue.put(None)
    
    def generate():
        """SSE生成器"""
        # 启动后台处理线程
        thread = threading.Thread(target=process_task, daemon=True)
        thread.start()
        
        # 持续发送事件
        while True:
            try:
                msg = message_queue.get(timeout=120)  # 2分钟超时
                if msg is None:
                    # 结束标记
                    break
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            except queue.Empty:
                # 发送心跳保持连接
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
    
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )


def get_video_info_from_bilibili(bvid: str) -> dict:
    """
    通过B站API获取视频详细信息
    
    Args:
        bvid: B站视频BV号
    
    Returns:
        dict: 视频信息 {title, cid, duration, aid, owner, pubdate}
    """
    import requests
    
    try:
        # 获取视频信息
        api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.bilibili.com/'
        }
        
        resp = requests.get(api_url, headers=headers, timeout=10)
        data = resp.json()
        
        if data.get('code') == 0:
            video_data = data.get('data', {})
            return {
                'title': video_data.get('title', '未知标题'),
                'cid': video_data.get('cid', 0),
                'duration': video_data.get('duration', 0),
                'aid': video_data.get('aid', 0),
                'owner': video_data.get('owner', {}).get('name', '未知'),
                'pubdate': video_data.get('pubdate', 0),
                'pic': video_data.get('pic', '').replace('http://', 'https://'),  # 封面图URL(强制HTTPS)
            }
    except Exception as e:
        logger.warning(f"获取视频信息失败 {bvid}: {e}")
    
    return None


def get_video_tags(bvid: str, cookie: str = None) -> list:
    """
    获取视频标签列表
    
    Args:
        bvid: B站视频BV号
        cookie: B站Cookie (可选，但推荐提供以获取完整标签)
    
    Returns:
        list: 标签名称列表 ['标签1', '标签2', ...]
    """
    import requests
    
    try:
        api_url = f"https://api.bilibili.com/x/web-interface/view/detail/tag?bvid={bvid}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.bilibili.com/'
        }
        
        if cookie:
            headers['Cookie'] = cookie
        
        resp = requests.get(api_url, headers=headers, timeout=10)
        data = resp.json()
        
        if data.get('code') == 0:
            tags_data = data.get('data', [])
            # 提取标签名称，排除BGM类型
            tags = []
            for tag in tags_data:
                tag_type = tag.get('tag_type', '')
                tag_name = tag.get('tag_name', '')
                if tag_name and tag_type != 'bgm':
                    tags.append(tag_name)
            return tags
    except Exception as e:
        logger.warning(f"获取视频标签失败 {bvid}: {e}")
    
    return []



def get_playlist_videos(url: str) -> list:
    """
    获取B站播放列表/合集中的所有视频信息
    
    Args:
        url: 播放列表/合集URL
    
    Returns:
        list: 视频信息列表
    """
    import yt_dlp
    import re
    
    # 先用flat模式快速获取视频ID列表
    ydl_opts_flat = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',
        'ignoreerrors': True,
    }
    
    videos = []
    
    with yt_dlp.YoutubeDL(ydl_opts_flat) as ydl:
        info = ydl.extract_info(url, download=False)
        
        if not info:
            return []
        
        # 检查是否是播放列表/合集
        if 'entries' in info:
            entries = list(info['entries'])
            for i, entry in enumerate(entries):
                if entry:
                    video_id = entry.get('id', '')
                    
                    # 提取BV号
                    bvid = video_id if video_id.startswith('BV') else None
                    if not bvid:
                        # 尝试从URL提取
                        entry_url = entry.get('url', '')
                        match = re.search(r'(BV\w+)', entry_url)
                        if match:
                            bvid = match.group(1)
                    
                    # 尝试通过B站API获取详细信息
                    title = entry.get('title')
                    if (not title or title == 'None') and bvid:
                        video_info = get_video_info_from_bilibili(bvid)
                        if video_info:
                            title = video_info.get('title', f'视频 {i + 1}')
                    
                    if not title:
                        title = f'视频 {i + 1}'
                    
                    video_url = entry.get('url') or entry.get('webpage_url', '')
                    if not video_url and bvid:
                        video_url = f"https://www.bilibili.com/video/{bvid}"
                    
                    # 调试日志
                    logger.info(f"[DEBUG] 视频 {i+1}: URL={video_url}, BV={bvid}, title={title}")
                    
                    videos.append({
                        'index': i,
                        'id': bvid or video_id,
                        'title': title,
                        'url': video_url,
                        'duration': entry.get('duration', 0),
                        'owner': video_info.get('owner', '未知') if video_info else '未知',
                        'pubdate': video_info.get('pubdate', 0) if video_info else 0,
                        'pic': video_info.get('pic', '') if video_info else '',
                    })
        else:
            # 单个视频
            video_id = info.get('id', '')
            bvid = video_id if video_id.startswith('BV') else None
            title = info.get('title')
            
            if (not title or title == 'None') and bvid:
                video_info = get_video_info_from_bilibili(bvid)
                if video_info:
                    title = video_info.get('title', '未知标题')
            else:
                video_info = get_video_info_from_bilibili(bvid) if bvid else None
            
            videos.append({
                'index': 0,
                'id': bvid or video_id,
                'title': title or '未知标题',
                'url': url,
                'duration': info.get('duration', 0),
                'owner': video_info.get('owner', '未知') if video_info else '未知',
                'pubdate': video_info.get('pubdate', 0) if video_info else 0,
                'pic': video_info.get('pic', '') if video_info else '',
            })
    
    return videos


def quick_check_subtitle_available(bvid: str, page_num: int = 1, bili_cookie: str = None) -> bool:
    """
    快速检查视频是否有可用字幕（不下载字幕内容）
    用于批量任务的优先级排序
    
    Args:
        bvid: 视频BV号
        page_num: 分P编号
        bili_cookie: B站Cookie
    
    Returns:
        bool: True 表示有字幕，False 表示需要语音识别
    """
    import requests
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.bilibili.com/'
    }
    
    if bili_cookie:
        if 'SESSDATA=' in bili_cookie:
            headers['Cookie'] = bili_cookie
        else:
            headers['Cookie'] = f'SESSDATA={bili_cookie}'
    
    try:
        # 获取 cid
        view_api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        resp = requests.get(view_api, headers=headers, timeout=5)
        data = resp.json()
        
        if data.get('code') != 0:
            return False
        
        pages = data.get('data', {}).get('pages', [])
        if not pages or page_num > len(pages):
            return False
        
        cid = pages[page_num - 1].get('cid')
        if not cid:
            return False
        
        # 检查字幕信息
        player_api = f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}"
        resp = requests.get(player_api, headers=headers, timeout=5)
        player_data = resp.json()
        
        subtitle_info = player_data.get('data', {}).get('subtitle', {})
        subtitles = subtitle_info.get('subtitles', [])
        
        return len(subtitles) > 0
        
    except Exception:
        return False  # 出错时假设没有字幕，走语音识别


def get_bilibili_subtitles(url: str, log_collector: LogCollector, bili_cookie: str = None) -> str:
    """
    尝试获取B站视频自带的字幕（通过B站API）
    
    Args:
        url: 视频URL
        log_collector: 日志收集器
        bili_cookie: B站登录Cookie（SESSDATA），用于获取AI字幕
    
    Returns:
        str: 字幕文本，如果没有则返回None
    """
    import requests
    import re
    
    log_collector.info("检查视频是否有自带字幕...")
    log_collector.info(f"[DEBUG] 传入的视频URL: {url}")
    
    # 从URL提取BV号
    match = re.search(r'(BV\w+)', url)
    if not match:
        log_collector.info("无法从URL提取BV号")
        return None
    
    bvid = match.group(1)
    log_collector.info(f"视频BV号: {bvid}")
    
    # 从URL提取分P编号（默认为1）
    page_match = re.search(r'[?&]p=(\d+)', url)
    page_num = int(page_match.group(1)) if page_match else 1
    log_collector.info(f"[DEBUG] 识别的分P编号: {page_num}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.bilibili.com/'
    }
    
    # 如果提供了Cookie，添加到请求头
    if bili_cookie:
        # 处理Cookie格式：可能是 "SESSDATA=xxx" 或直接是值
        if 'SESSDATA=' in bili_cookie:
            headers['Cookie'] = bili_cookie
        else:
            headers['Cookie'] = f'SESSDATA={bili_cookie}'
        
        # 调试：检查Cookie中的关键字段
        cookie_str = headers['Cookie']
        has_sessdata = 'SESSDATA=' in cookie_str
        has_bili_jct = 'bili_jct=' in cookie_str
        has_buvid3 = 'buvid3=' in cookie_str
        log_collector.info(f"使用B站Cookie请求字幕...")
        log_collector.info(f"[DEBUG] Cookie字段: SESSDATA={has_sessdata}, bili_jct={has_bili_jct}, buvid3={has_buvid3}")
        
        # 如果缺少buvid3，尝试自动获取并添加
        if not has_buvid3:
            log_collector.info("Cookie中缺少buvid3，正在自动获取...")
            buvid_info = get_buvid()
            if buvid_info and buvid_info.get('buvid3'):
                headers['Cookie'] = f"{headers['Cookie']}; buvid3={buvid_info['buvid3']}"
                if buvid_info.get('buvid4'):
                    headers['Cookie'] = f"{headers['Cookie']}; buvid4={buvid_info['buvid4']}"
                log_collector.info(f"已添加buvid3到Cookie")
            else:
                log_collector.warning("[警告] 无法获取buvid3，可能导致AI字幕获取异常")
    
    try:
        # 1. 获取视频信息（包含cid）
        view_api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        resp = requests.get(view_api, headers=headers, timeout=10)
        data = resp.json()
        
        if data.get('code') != 0:
            log_collector.info("获取视频信息失败")
            return None
        
        video_data = data.get('data', {})
        
        # 获取正确的cid：对于多分P视频，从pages数组获取
        pages = video_data.get('pages', [])
        cid = None
        
        if pages and len(pages) >= page_num:
            # 使用指定分P的cid
            cid = pages[page_num - 1].get('cid', 0)
            if len(pages) > 1:
                log_collector.info(f"多分P视频，使用第{page_num}P的cid")
        else:
            # fallback到默认cid
            cid = video_data.get('cid', 0)
        
        aid = video_data.get('aid', 0)
        
        if not cid:
            log_collector.info("无法获取视频cid")
            return None
        
        # 记录视频信息帮助调试
        video_title = video_data.get('title', '未知')
        owner_name = video_data.get('owner', {}).get('name', '未知')
        log_collector.info(f"视频标题: {video_title}")
        log_collector.info(f"UP主: {owner_name}")
        log_collector.info(f"视频cid: {cid}, aid: {aid}")
        
        # 2. 获取字幕信息 - 使用WBI签名
        import time
        
        # 获取WBI密钥
        img_key, sub_key = get_wbi_keys(headers)
        
        # 构建请求参数
        player_params = {
            'aid': aid,
            'cid': cid,
            'bvid': bvid,
        }
        
        # 如果获取到WBI密钥，进行签名
        if img_key and sub_key:
            log_collector.info("使用WBI签名请求字幕API...")
            signed_params = sign_wbi_params(player_params, img_key, sub_key)
            query_string = '&'.join([f"{k}={v}" for k, v in signed_params.items()])
            # 使用带WBI鉴权的接口，这个接口返回的字幕更准确
            player_api = f"https://api.bilibili.com/x/player/wbi/v2?{query_string}"
        else:
            log_collector.warning("无法获取WBI密钥，使用旧接口...")
            ts = int(time.time())
            player_params['_'] = ts
            query_string = '&'.join([f"{k}={v}" for k, v in player_params.items()])
            player_api = f"https://api.bilibili.com/x/player/v2?{query_string}"
        
        log_collector.info(f"请求字幕API: {player_api[:100]}...")
        
        # 添加更多请求头模拟真实浏览器
        subtitle_headers = headers.copy()
        subtitle_headers['Accept'] = 'application/json, text/plain, */*'
        subtitle_headers['Accept-Language'] = 'zh-CN,zh;q=0.9,en;q=0.8'
        subtitle_headers['Origin'] = 'https://www.bilibili.com'
        subtitle_headers['Cache-Control'] = 'no-cache'
        subtitle_headers['Pragma'] = 'no-cache'
        
        resp = requests.get(player_api, headers=subtitle_headers, timeout=10)
        
        # 检查响应状态
        if resp.status_code != 200:
            log_collector.info(f"字幕API返回状态码: {resp.status_code}")
            return None
        
        try:
            data = resp.json()
        except Exception as e:
            log_collector.warning(f"解析字幕响应失败: {str(e)}, 响应内容: {resp.text[:200]}")
            return None
        
        if data.get('code') != 0:
            log_collector.info("获取播放器信息失败")
            return None
        
        subtitle_info = data.get('data', {}).get('subtitle', {})
        subtitles = subtitle_info.get('subtitles', [])
        
        if not subtitles:
            log_collector.info("视频没有可用的字幕")
            return None
        
        # 优先选择简体中文字幕（AI字幕优先，因为质量更高）
        # 注意：需要完整Cookie（包含buvid3, bili_jct等）才能稳定获取AI字幕
        selected_sub = None
        priority_langs = ['ai-zh', 'zh-Hans', 'zh-CN', 'zh']
        
        log_collector.info(f"可用字幕列表: {[s.get('lan') for s in subtitles]}")
        
        for priority_lang in priority_langs:
            for sub in subtitles:
                lang = sub.get('lan', '')
                if lang == priority_lang or lang.lower().startswith(priority_lang.lower()):
                    selected_sub = sub
                    log_collector.info(f"发现简体中文字幕: {sub.get('lan_doc', lang)}")
                    break
            if selected_sub:
                break
        
        # 如果没有找到简体中文，跳过（不使用其他语言）
        if not selected_sub:
            log_collector.info("没有找到简体中文字幕")
            return None
        
        # 打印字幕数据结构帮助调试
        log_collector.info(f"字幕数据: {selected_sub}")
        
        # 3. 下载字幕内容 - 尝试不同的字段名
        subtitle_url = selected_sub.get('subtitle_url', '') or selected_sub.get('url', '')
        if not subtitle_url:
            log_collector.warning("检测到有中文字幕，但无法获取下载链接。这通常是因为Cookie缺少buvid3或已失效。")
            # 抛出特定异常，以便上层区分"无字幕"和"鉴权失败"
            raise Exception("COOKIE_INVALID: 获取到字幕列表但无下载链接，请检查Cookie")
        
        # 处理URL（可能是相对路径）
        if subtitle_url.startswith('//'):
            subtitle_url = 'https:' + subtitle_url
        
        log_collector.info(f"字幕URL: {subtitle_url[:80]}...")
        
        # 记录字幕URL信息（用于调试）
        aid_str = str(aid)
        if '/ai_subtitle/prod/' in subtitle_url:
            import re
            url_match = re.search(r'/ai_subtitle/prod/(\d+)', subtitle_url)
            if url_match:
                url_id = url_match.group(1)
                if url_id.startswith(aid_str):
                    log_collector.info(f"[DEBUG] 字幕URL ID验证通过")
                else:
                    # 不再拒绝，仅记录，因为WBI签名后的响应应该是正确的
                    log_collector.info(f"[DEBUG] 字幕URL格式: prod/{url_id[:15]}...")
        
        log_collector.info("正在下载字幕...")
        resp = requests.get(subtitle_url, headers=headers, timeout=10)
        subtitle_data = resp.json()
        
        # 4. 解析字幕内容
        body = subtitle_data.get('body', [])
        if not body:
            log_collector.info("字幕内容为空")
            return None
        
        lines = []
        for item in body:
            content = item.get('content', '').strip()
            if content:
                lines.append(content)
        
        if lines:
            log_collector.info(f"成功获取字幕，共 {len(lines)} 行")
            # 打印字幕前3行和后3行，帮助确认内容是否正确
            preview = lines[:3] + ['...'] + lines[-3:] if len(lines) > 6 else lines
            log_collector.info(f"字幕预览: {preview}")
            
            # 验证字幕有效性
            # 1. 检查是否字幕太短（少于5行可能是无效字幕）
            if len(lines) < 5:
                log_collector.info(f"字幕行数过少({len(lines)}行)，可能无效，改用语音识别")
                return None
            
            # 2. 检查是否全是音乐标记或无效内容
            invalid_patterns = ['♪', '音乐', '片头', '片尾', '[音乐]', '【音乐】']
            valid_lines = 0
            for line in lines:
                is_valid = True
                for pattern in invalid_patterns:
                    if pattern in line and len(line) < 20:
                        is_valid = False
                        break
                if is_valid:
                    valid_lines += 1
            
            valid_ratio = valid_lines / len(lines)
            if valid_ratio < 0.3:  # 如果超过70%是无效内容
                log_collector.info(f"字幕有效内容比例过低({valid_ratio:.1%})，可能是错误字幕，改用语音识别")
                return None
            
            return '\n'.join(lines)
        
    except Exception as e:
        log_collector.warning(f"获取字幕失败: {str(e)}")
    
    return None


@app.route('/api/cleanup', methods=['POST'])
@login_required
def cleanup_temp_files():
    """
    清理临时文件（音频文件等）
    
    清理范围：
    1. AUDIO_DIR 目录下的所有文件
    2. 系统 TEMP_DIR 下所有 bilisub 相关文件
    3. 项目目录下的 temp 文件夹
    
    返回:
        清理结果和统计信息
    """
    import glob
    import shutil
    
    try:
        cleaned_files = 0
        cleaned_size = 0
        errors = []
        
        # 1. 清理 AUDIO_DIR 目录下的所有文件
        if os.path.exists(AUDIO_DIR):
            for filename in os.listdir(AUDIO_DIR):
                file_path = os.path.join(AUDIO_DIR, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        size = os.path.getsize(file_path)
                        os.unlink(file_path)
                        cleaned_files += 1
                        cleaned_size += size
                except Exception as e:
                    errors.append(f"无法删除 {filename}: {str(e)}")
        
        # 2. 清理系统 TEMP_DIR 下所有 bilisub 相关文件
        temp_patterns = [
            'audio_*.m4a', 'audio_*.mp3', 'audio_*.wav',
            'audio_*_converted.mp3', 'audio_*_converted.wav',
            '*.m4a.part', '*.mp3.part'
        ]
        for pattern in temp_patterns:
            for file_path in glob.glob(os.path.join(TEMP_DIR, pattern)):
                try:
                    if os.path.isfile(file_path):
                        size = os.path.getsize(file_path)
                        os.unlink(file_path)
                        cleaned_files += 1
                        cleaned_size += size
                except Exception as e:
                    errors.append(f"无法删除 {os.path.basename(file_path)}: {str(e)}")
        
        # 3. 清理项目目录下的 temp 文件夹（用户临时数据）
        project_temp_dir = os.path.join(os.path.dirname(__file__), 'temp')
        if os.path.exists(project_temp_dir):
            for item in os.listdir(project_temp_dir):
                item_path = os.path.join(project_temp_dir, item)
                try:
                    if os.path.isfile(item_path):
                        size = os.path.getsize(item_path)
                        os.unlink(item_path)
                        cleaned_files += 1
                        cleaned_size += size
                    elif os.path.isdir(item_path):
                        # 计算目录大小
                        dir_size = 0
                        for root, dirs, files in os.walk(item_path):
                            for f in files:
                                try:
                                    dir_size += os.path.getsize(os.path.join(root, f))
                                    cleaned_files += 1
                                except:
                                    pass
                        shutil.rmtree(item_path)
                        cleaned_size += dir_size
                except Exception as e:
                    errors.append(f"无法删除 {item}: {str(e)}")
        
        # 确保目录存在
        os.makedirs(AUDIO_DIR, exist_ok=True)
        
        # 格式化大小
        def format_size(size):
            if size < 1024:
                return f"{size} B"
            elif size < 1024 * 1024:
                return f"{size / 1024:.1f} KB"
            else:
                return f"{size / (1024 * 1024):.1f} MB"
        
        logger.info(f"缓存清理完成: {cleaned_files} 个文件, {format_size(cleaned_size)}")
        
        return jsonify({
            "success": True,
            "cleaned_files": cleaned_files,
            "cleaned_size": format_size(cleaned_size),
            "errors": errors if errors else None,
            "message": f"已清理 {cleaned_files} 个文件，释放 {format_size(cleaned_size)}"
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/llm_process', methods=['POST'])
@login_required
def llm_process():
    """
    调用大模型处理字幕内容（OpenAI兼容格式）
    
    请求体:
        {
            "api_key": "sk-xxx",
            "api_url": "https://api.openai.com/v1/chat/completions",
            "model": "gpt-4o-mini",
            "prompt": "请总结以下内容...",
            "content": "字幕内容..."
        }
    
    响应:
        {
            "success": true,
            "result": "处理后的内容..."
        }
    """
    import requests
    
    try:
        data = request.get_json()
        
        api_key = data.get('api_key', '').strip()
        api_url = data.get('api_url', '').strip()
        model = data.get('model', 'gpt-4o-mini').strip()
        prompt = data.get('prompt', '').strip()
        content = data.get('content', '').strip()
        
        if not api_key:
            return jsonify({"success": False, "error": "缺少API Key"}), 400
        if not api_url:
            api_url = "https://api.openai.com/v1/chat/completions"
        if not content:
            return jsonify({"success": False, "error": "缺少字幕内容"}), 400
        
        # 构建OpenAI兼容格式的请求
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",  # 明确要求JSON响应
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"  # 伪装User-Agent防拦截
        }
        
        messages = []
        if prompt:
            messages.append({"role": "system", "content": prompt})
        messages.append({"role": "user", "content": content})
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7
        }
        
        def make_request(url):
            logging.info(f"尝试调用大模型API: {url}, 模型: {model}")
            try:
                # 禁用代理，避免本地代理拦截返回 HTML
                resp = requests.post(url, headers=headers, json=payload, timeout=120, proxies={})
                
                # 记录响应简要信息
                content_type = resp.headers.get('Content-Type', '').lower()
                is_html = 'html' in content_type or resp.text.strip().lower().startswith('<!doctype html')
                
                return resp, is_html
            except Exception as e:
                logging.error(f"请求异常: {url} - {str(e)}")
                return None, False

        # 第一次尝试：用户输入的原始URL
        response, is_html_response = make_request(api_url)
        
        # 智能重试逻辑
        # 如果请求失败(None) 或 返回HTML 或 404，且URL看起来不完整，尝试自动修正URL
        should_retry = False
        new_url = api_url
        
        if response is None:
            should_retry = True # 网络错误，可能是地址不对
        elif is_html_response:
            should_retry = True # 返回了HTML，肯定是地址不对（指向了网页）
        elif response.status_code == 404:
            should_retry = True # 404，可能是路径不对
            
        # 只有当URL不包含 standard path 时才尝试修正
        if should_retry and 'chat/completions' not in api_url:
            # 去除末尾斜杠
            base_url = api_url.rstrip('/')
            
            # 常见修正模式
            if base_url.endswith('/v1'):
                new_url = f"{base_url}/chat/completions"
            else:
                new_url = f"{base_url}/v1/chat/completions"
                
            logging.info(f"API调用疑似失败（HTML/404/Err），尝试自动修正URL: {new_url}")
            retry_response, retry_is_html = make_request(new_url)
            
            # 如果重试成功（不是None，且不是HTML，且是200），通过
            if retry_response and not retry_is_html and retry_response.status_code == 200:
                response = retry_response
                logging.info("自动修正URL重试成功！")
            elif retry_response and retry_response.status_code != 404 and not retry_is_html:
                 # 如果重试虽然不是200但也不是404/HTML（比如401认证错误），说明找对了接口，只是key不对
                 response = retry_response
                 logging.info("自动修正URL找到了有效接口（虽然返回错误）")

        # 处理最终响应
        if not response:
             return jsonify({
                "success": False,
                "error": "无法连接到API服务器，请检查网络或API地址"
            }), 500

        # ... 后续解析逻辑保持不变 ...
        logging.info(f"API最终响应状态码: {response.status_code}")
        
        if response.status_code != 200:
            error_msg = response.text[:500]
            logging.error(f"大模型API错误: {response.status_code} - {error_msg}")
            return jsonify({
                "success": False,
                "error": f"API调用失败: {response.status_code} - {error_msg[:100]}"
            }), 500
        
        # 尝试解析JSON响应
        try:
            result = response.json()
            logging.info(f"解析后的JSON: {str(result)[:500]}")
        except Exception as json_err:
            logging.error(f"API响应不是有效的JSON: {response.text[:500]}")
            
            # 检查是否为HTML响应
            resp_text = response.text.strip().lower()
            if resp_text.startswith('<!doctype html') or '<html' in resp_text:
                return jsonify({
                    "success": False,
                    "error": f"API地址错误：返回了网页HTML而不是JSON数据。请检查API地址是否完整（通常需要以此结尾：/v1/chat/completions）"
                }), 500
                
            return jsonify({
                "success": False,
                "error": f"API返回格式错误，不是有效的JSON。响应前100字符: {response.text[:100]}"
            }), 500
        
        # 尝试多种方式提取回复内容
        ai_response = None
        
        # 方式1: 标准OpenAI格式 - choices[0].message.content
        if 'choices' in result and len(result['choices']) > 0:
            choice = result['choices'][0]
            if 'message' in choice and 'content' in choice['message']:
                ai_response = choice['message']['content']
            elif 'text' in choice:
                # 某些API使用text而不是message
                ai_response = choice['text']
            elif 'content' in choice:
                ai_response = choice['content']
        
        # 方式2: 直接content字段
        if not ai_response and 'content' in result:
            ai_response = result['content']
        
        # 方式3: response字段
        if not ai_response and 'response' in result:
            ai_response = result['response']
        
        # 方式4: data.content格式
        if not ai_response and 'data' in result:
            data = result['data']
            if isinstance(data, dict) and 'content' in data:
                ai_response = data['content']
            elif isinstance(data, str):
                ai_response = data
        
        # 方式5: result字段
        if not ai_response and 'result' in result:
            ai_response = result['result']
        
        # 方式6: message字段
        if not ai_response and 'message' in result:
            msg = result['message']
            if isinstance(msg, dict) and 'content' in msg:
                ai_response = msg['content']
            elif isinstance(msg, str):
                ai_response = msg
        
        # 方式7: output字段
        if not ai_response and 'output' in result:
            ai_response = result['output']
        
        if ai_response:
            logging.info(f"大模型处理完成，返回 {len(ai_response)} 字符")
            return jsonify({
                "success": True,
                "content": ai_response
            })
        else:
            logging.error(f"无法从响应中提取内容，完整响应: {str(result)}")
            return jsonify({
                "success": False,
                "error": f"无法解析API响应格式，响应: {str(result)[:200]}"
            }), 500
            
    except requests.Timeout:
        return jsonify({
            "success": False,
            "error": "API请求超时"
        }), 500
    except requests.RequestException as req_err:
        logging.error(f"网络请求失败: {str(req_err)}")
        return jsonify({
            "success": False,
            "error": f"网络请求失败: {str(req_err)}"
        }), 500
    except Exception as e:
        logging.error(f"大模型处理失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/verify_cookie', methods=['POST'])
@login_required
def verify_cookie():
    """
    验证B站Cookie是否有效
    
    请求体:
        {"cookie": "SESSDATA=xxx"}
    
    响应:
        {
            "valid": true/false,
            "username": "用户名（如有效）",
            "message": "状态描述"
        }
    """
    import requests
    
    data = request.get_json()
    if not data:
        return jsonify({"valid": False, "username": "", "message": "请求体为空"})
    
    cookie = data.get('cookie', '').strip()
    if not cookie:
        return jsonify({"valid": False, "username": "", "message": "未提供Cookie"})
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.bilibili.com/'
        }
        
        # 处理Cookie格式
        if 'SESSDATA=' in cookie:
            headers['Cookie'] = cookie
        else:
            headers['Cookie'] = f'SESSDATA={cookie}'
        
        # 调用B站用户信息API验证Cookie
        resp = requests.get(
            'https://api.bilibili.com/x/web-interface/nav',
            headers=headers,
            timeout=10
        )
        data = resp.json()
        
        if data.get('code') == 0 and data.get('data', {}).get('isLogin'):
            username = data['data'].get('uname', '未知用户')
            return jsonify({
                "valid": True,
                "username": username,
                "message": f"Cookie有效，用户：{username}"
            })
        else:
            return jsonify({
                "valid": False,
                "username": "",
                "message": "Cookie已失效，请重新扫码登录"
            })
    
    except Exception as e:
        logger.error(f"验证Cookie失败: {str(e)}")
        return jsonify({
            "valid": False,
            "username": "",
            "message": f"验证失败: {str(e)}"
        })


@app.route('/api/qrcode/generate', methods=['GET'])
@login_required
def generate_qrcode():
    """
    生成B站登录二维码
    
    响应:
        {
            "success": true/false,
            "qrcode_key": "用于轮询的key",
            "qrcode_url": "二维码内容URL",
            "error": "错误信息"
        }
    """
    import requests
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.bilibili.com/'
        }
        
        resp = requests.get(
            'https://passport.bilibili.com/x/passport-login/web/qrcode/generate',
            headers=headers,
            timeout=10
        )
        data = resp.json()
        
        if data.get('code') == 0:
            return jsonify({
                "success": True,
                "qrcode_key": data['data']['qrcode_key'],
                "qrcode_url": data['data']['url'],
                "error": ""
            })
        else:
            return jsonify({
                "success": False,
                "qrcode_key": "",
                "qrcode_url": "",
                "error": data.get('message', '生成二维码失败')
            }), 500
    
    except Exception as e:
        logger.error(f"生成二维码失败: {str(e)}")
        return jsonify({
            "success": False,
            "qrcode_key": "",
            "qrcode_url": "",
            "error": str(e)
        }), 500


@app.route('/api/qrcode/poll', methods=['POST'])
@login_required
def poll_qrcode():
    """
    轮询二维码登录状态
    
    请求体:
        {"qrcode_key": "二维码key"}
    
    响应:
        {
            "status": "waiting" | "scanned" | "success" | "expired" | "error",
            "sessdata": "登录成功时返回的SESSDATA",
            "message": "状态描述"
        }
    """
    import requests
    
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "sessdata": "", "message": "请求体为空"}), 400
    
    qrcode_key = data.get('qrcode_key', '').strip()
    if not qrcode_key:
        return jsonify({"status": "error", "sessdata": "", "message": "缺少qrcode_key"}), 400
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.bilibili.com/'
        }
        
        resp = requests.get(
            f'https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={qrcode_key}',
            headers=headers,
            timeout=10
        )
        data = resp.json()
        
        code = data.get('data', {}).get('code', -1)
        
        if code == 0:
            # 登录成功，从响应中提取完整Cookie
            cookies = resp.cookies
            
            # 收集所有Cookie
            cookie_parts = []
            
            # 尝试从响应URL参数中提取（B站登录成功后会在URL参数中返回Cookie值）
            url = data.get('data', {}).get('url', '')
            if url:
                import urllib.parse
                parsed = urllib.parse.urlparse(url)
                params = urllib.parse.parse_qs(parsed.query)
                
                # 提取所有重要的Cookie参数
                # 注意：buvid3 是获取AI字幕的必要参数
                important_cookies = ['SESSDATA', 'bili_jct', 'DedeUserID', 'DedeUserID__ckMd5', 'sid', 'buvid3', 'buvid4']
                for cookie_name in important_cookies:
                    if cookie_name in params:
                        cookie_parts.append(f"{cookie_name}={params[cookie_name][0]}")
            
            # 也从响应Cookies中获取额外的值
            for cookie in cookies:
                cookie_name = cookie.name
                cookie_value = cookie.value
                # 避免重复
                if not any(cookie_name + '=' in part for part in cookie_parts):
                    cookie_parts.append(f"{cookie_name}={cookie_value}")
            
            # 组合成完整Cookie字符串
            full_cookie = '; '.join(cookie_parts)
            
            # 也单独提取SESSDATA用于兼容
            sessdata = ''
            if 'SESSDATA' in (params if url else {}):
                sessdata = params['SESSDATA'][0]
            elif 'SESSDATA' in cookies:
                sessdata = cookies['SESSDATA']
            
            return jsonify({
                "status": "success",
                "sessdata": sessdata,  # 保持兼容
                "full_cookie": full_cookie,  # 完整Cookie
                "message": "登录成功"
            })
        
        elif code == 86038:
            return jsonify({
                "status": "expired",
                "sessdata": "",
                "message": "二维码已过期，请重新生成"
            })
        
        elif code == 86090:
            return jsonify({
                "status": "scanned",
                "sessdata": "",
                "message": "已扫码，请在手机上确认登录"
            })
        
        elif code == 86101:
            return jsonify({
                "status": "waiting",
                "sessdata": "",
                "message": "等待扫码"
            })
        
        else:
            return jsonify({
                "status": "error",
                "sessdata": "",
                "message": data.get('data', {}).get('message', '未知状态')
            })
    
    except Exception as e:
        logger.error(f"轮询二维码状态失败: {str(e)}")
        return jsonify({
            "status": "error",
            "sessdata": "",
            "message": str(e)
        }), 500


@app.route('/api/playlist_info', methods=['POST'])
@login_required
def get_playlist_info():
    """
    获取播放列表/合集信息
    
    请求体:
        {"url": "播放列表/合集URL"}
    
    响应:
        {
            "success": true/false,
            "videos": [视频列表],
            "error": "错误信息"
        }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "videos": [], "error": "请求体为空"}), 400
        
        url = data.get('url', '').strip()
        if not url:
            return jsonify({"success": False, "videos": [], "error": "请提供URL"}), 400
        
        if not any(domain in url for domain in ['bilibili.com', 'b23.tv']):
            return jsonify({"success": False, "videos": [], "error": "请提供有效的B站链接"}), 400
        
        logger.info(f"获取播放列表信息: {url}")
        videos = get_playlist_videos(url)
        
        if not videos:
            return jsonify({"success": False, "videos": [], "error": "未找到视频信息"}), 404
        
        logger.info(f"找到 {len(videos)} 个视频")
        return jsonify({
            "success": True,
            "videos": videos,
            "error": ""
        })
    
    except Exception as e:
        logger.error(f"获取播放列表信息失败: {str(e)}")
        return jsonify({
            "success": False,
            "videos": [],
            "error": str(e)
        }), 500


@app.route('/api/batch_status/<batch_id>', methods=['GET'])
@login_required
def get_batch_status(batch_id):
    """获取批量任务状态"""
    status = task_manager.get_status(batch_id)
    if not status:
        return jsonify({"success": False, "error": "任务不存在"}), 404
    return jsonify({"success": True, "data": status})


def process_single_video_task(batch_id, video_index, video_info, api_key, bili_cookie, use_self_hosted, self_hosted_domain, cookie_valid=True, api_valid=True):
    """
    单个视频处理任务，由线程池调用
    
    Args:
        cookie_valid: Cookie 是否有效，无效时跳过字幕提取直接转录
        api_valid: API Key 是否有效，无效时字幕提取失败直接标记错误
    """
    logger.info(f"[Task {video_index}] 任务开始执行: batch={batch_id}, cookie_valid={cookie_valid}, api_valid={api_valid}")
    
    try:
        # 检查任务是否已被取消（在线程池队列等待期间可能被取消）
        current_status = task_manager.get_video_status(batch_id, video_index)
        if current_status == "cancelled":
            logger.info(f"[Task {video_index}] 任务已被取消，跳过处理")
            return
        
        # 1. 更新为处理中
        task_manager.update_video_status(batch_id, video_index, "processing", progress=0)
        logger.info(f"[Task {video_index}] 状态已更新为 processing")
        
        video_url = video_info.get('url', '')
        video_title = video_info.get('title', '未知标题')
        
        # 适配 TaskManager 的 LogCollector
        class TaskLogCollector(LogCollector):
            def info(self, msg):
                super().info(msg)
                logger.info(f"[Task {video_index}] {msg}")
                
            def set_progress(self, percent):
                super().set_progress(percent)
                task_manager.update_video_status(batch_id, video_index, "processing", progress=percent)
                logger.debug(f"[Task {video_index}] 进度更新: {percent}%")
                
        log_collector = TaskLogCollector()
        
        log_collector.info(f"开始处理: {video_title}")
        
        # 更新进度到5%，让前端看到活动
        task_manager.update_video_status(batch_id, video_index, "processing", progress=5)
        
        transcript = None
        
        # 2. 根据 cookie_valid 决定是否尝试获取自带字幕
        if cookie_valid:
            # Cookie 有效，尝试获取自带字幕
            logger.info(f"[Task {video_index}] Cookie有效，尝试获取自带字幕...")
            try:
                transcript = get_bilibili_subtitles(video_url, log_collector, bili_cookie)
            except Exception as e:
                logger.warning(f"[Task {video_index}] 获取字幕异常: {e}")
                if "COOKIE_INVALID" in str(e):
                    raise e
                log_collector.warning(f"获取B站字幕出错: {e}，尝试使用语音识别")
            
            if transcript:
                log_collector.info("成功获取自带字幕")
                logger.info(f"[Task {video_index}] 字幕获取成功，长度: {len(transcript)}")
                # 立即完成，无需延迟
                task_manager.update_video_status(batch_id, video_index, "completed", progress=100, result={"transcript": transcript})
                return
        else:
            # Cookie 无效，跳过字幕提取
            logger.info(f"[Task {video_index}] Cookie无效，跳过字幕提取，直接使用语音识别")
            log_collector.info("Cookie无效，跳过字幕提取")
        
        # 3. 检查是否可以进行语音识别
        if not api_valid:
            # API 无效，无法进行语音识别，标记为失败
            error_msg = "此视频无自带字幕，且 API Key 无效无法进行语音转录"
            logger.warning(f"[Task {video_index}] {error_msg}")
            task_manager.update_video_status(batch_id, video_index, "error", error=error_msg)
            return
        
        # 检查是否已取消（在耗时的语音识别开始前检查）
        if task_manager.is_batch_cancelled(batch_id):
            logger.info(f"[Task {video_index}] 批次已取消，跳过语音识别")
            task_manager.update_video_status(batch_id, video_index, "cancelled", progress=100)
            return
        
        # 4. 语音识别流程 - 需要延迟避免 B站 风控
        import time
        import random
        delay = random.uniform(0.5, 1.5)  # 较短延迟，仅在下载音频前
        logger.info(f"[Task {video_index}] 准备下载音频，等待 {delay:.1f} 秒防风控...")
        time.sleep(delay)
        
        # 再次检查是否已取消
        if task_manager.is_batch_cancelled(batch_id):
            logger.info(f"[Task {video_index}] 批次已取消，跳过音频下载")
            task_manager.update_video_status(batch_id, video_index, "cancelled", progress=100)
            return
            
        logger.info(f"[Task {video_index}] 开始语音识别流程...")
        log_collector.info("使用语音识别...")
        
        # 下载音频
        audio_path, duration = download_bilibili_audio(video_url, AUDIO_DIR, log_collector)
        logger.info(f"[Task {video_index}] 音频下载完成: {audio_path}")
        
        # 下载完成后再次检查是否已取消
        if task_manager.is_batch_cancelled(batch_id):
            logger.info(f"[Task {video_index}] 批次已取消，跳过转录")
            task_manager.update_video_status(batch_id, video_index, "cancelled", progress=100)
            return
        
        # 转录音频
        transcript = transcribe_audio(audio_path, api_key, log_collector, self_hosted_domain if use_self_hosted else None, duration)
        logger.info(f"[Task {video_index}] 转录完成，长度: {len(transcript) if transcript else 0}")
        
        # 完成
        task_manager.update_video_status(batch_id, video_index, "completed", progress=100, result={"transcript": transcript})
        logger.info(f"[Task {video_index}] 任务完成!")
            
    except Exception as e:
        logger.error(f"[Task {video_index}] 任务失败: {e}", exc_info=True)
        task_manager.update_video_status(batch_id, video_index, "error", error=str(e))


@app.route('/api/transcribe_batch', methods=['POST'])
@login_required
def transcribe_batch():
    """
    批量处理转录请求（异步模式）
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "请求体为空"}), 400
    
    videos = data.get('videos', [])
    api_key = data.get('api_key', '').strip()
    bili_cookie = data.get('bili_cookie', '').strip()
    use_self_hosted = data.get('use_self_hosted', False)
    self_hosted_domain = data.get('self_hosted_domain', '').strip()
    cookie_valid = data.get('cookie_valid', False)  # Cookie 是否有效
    api_valid = data.get('api_valid', False)  # API Key 是否有效
    
    if not videos:
        return jsonify({"error": "请提供视频列表"}), 400
    
    # 至少需要一个有效的配置才能继续
    if not cookie_valid and not api_valid:
        return jsonify({"error": "Cookie 和 API Key 均无效，无法提取字幕"}), 400
    
    # === Guest 账户标识 ===
    is_guest = current_user.username == 'guest'
    
    # Guest 用户不再有配额限制，只有并发限制
    
    # === 优先级排序：有字幕的视频排前面 ===
    # 仅在视频数量 > 1 且非 Guest 时进行预检测（Guest 也可以享受排序优化）
    if len(videos) > 1:
        import re
        from concurrent.futures import ThreadPoolExecutor as CheckExecutor, as_completed
        
        def check_video_subtitle(video):
            """检查单个视频是否有字幕"""
            url = video.get('url', '')
            match = re.search(r'(BV\w+)', url)
            if not match:
                return (video, False)
            bvid = match.group(1)
            page_match = re.search(r'[?&]p=(\d+)', url)
            page_num = int(page_match.group(1)) if page_match else 1
            has_subtitle = quick_check_subtitle_available(bvid, page_num, bili_cookie)
            return (video, has_subtitle)
        
        # 并行检测（最多 10 个线程，快速完成）
        videos_with_priority = []
        with CheckExecutor(max_workers=10) as check_executor:
            futures = {check_executor.submit(check_video_subtitle, v): v for v in videos}
            for future in as_completed(futures):
                try:
                    video, has_subtitle = future.result(timeout=10)
                    videos_with_priority.append((video, has_subtitle))
                except Exception:
                    # 超时或出错，假设没有字幕
                    videos_with_priority.append((futures[future], False))
        
        # 排序：有字幕的排前面
        videos_with_priority.sort(key=lambda x: (not x[1], x[0].get('index', 0)))
        videos = [v[0] for v in videos_with_priority]
        
        subtitle_count = sum(1 for _, has in videos_with_priority if has)
        logger.info(f"[Batch] 优先级排序完成: {subtitle_count}/{len(videos)} 个视频有字幕")
    
    # 1. 创建任务批次
    batch_id = task_manager.create_batch(len(videos))
    
    # === 选择执行器和执行方式 ===
    if is_guest:
        # Guest 用户：使用并发控制器，5并发处理，超过则排队
        def process_guest_video(video, v_idx, batch_id_local):
            """Guest 视频处理任务（带并发控制）"""
            # 检查批次是否已取消
            if task_manager.is_batch_cancelled(batch_id_local):
                task_manager.update_video_status(batch_id_local, v_idx, 'cancelled', progress=100)
                return
            
            # 更新状态为排队中
            task_manager.update_video_status(batch_id_local, v_idx, 'queued', progress=0)
            
            # 获取并发槽位（可能阻塞排队）
            guest_concurrency.acquire(batch_id_local, v_idx)
            
            try:
                # 再次检查是否已取消（可能在排队期间被取消）
                if task_manager.is_batch_cancelled(batch_id_local):
                    task_manager.update_video_status(batch_id_local, v_idx, 'cancelled', progress=100)
                    return
                
                # 更新状态为处理中
                task_manager.update_video_status(batch_id_local, v_idx, 'processing', progress=5)
                
                # 执行实际处理
                process_single_video_task(
                    batch_id_local, v_idx, video, api_key, bili_cookie,
                    use_self_hosted, self_hosted_domain, cookie_valid, api_valid
                )
            finally:
                # 释放并发槽位
                guest_concurrency.release()
        
        # 先初始化所有视频状态，然后提交到执行器
        for video in videos:
            v_idx = task_manager.init_video(batch_id, video)
            # 立即标记为排队状态
            task_manager.update_video_status(batch_id, v_idx, 'queued', progress=0)
            guest_executor.submit(process_guest_video, video, v_idx, batch_id)
        
        mode = "并发处理 (Guest, 5并发排队)"
    
    elif not use_self_hosted:
        # 第三方直链：使用 5 并发处理
        for video in videos:
            v_idx = task_manager.init_video(batch_id, video)
            third_party_executor.submit(
                process_single_video_task,
                batch_id,
                v_idx,
                video,
                api_key,
                bili_cookie,
                use_self_hosted,
                self_hosted_domain,
                cookie_valid,
                api_valid
            )
        mode = "并发处理 (第三方直链, 5并发)"
    
    else:
        # 自建服务的非 Guest 用户：8 并发处理
        for video in videos:
            v_idx = task_manager.init_video(batch_id, video)
            executor.submit(
                process_single_video_task,
                batch_id,
                v_idx,
                video,
                api_key,
                bili_cookie,
                use_self_hosted,
                self_hosted_domain,
                cookie_valid,
                api_valid
            )
        mode = "并发处理 (本地直链, 8并发)"
    
    logger.info(f"[Batch] 批次 {batch_id}: {len(videos)} 个视频, 模式: {mode}")
    
    response_data = {
        "success": True, 
        "batch_id": batch_id, 
        "message": f"已提交 {len(videos)} 个任务，正在后台处理",
        "processing_mode": mode
    }
    
    # Guest 用户返回并发状态
    if is_guest:
        status = guest_concurrency.get_status()
        response_data["concurrent_status"] = status
    
    return jsonify(response_data)



@app.route('/api/health')
def health():
    """健康检查接口"""
    return jsonify({"status": "ok"})


# 缓存公网可访问性检测结果
_public_access_cache = {
    'result': None,
    'timestamp': 0,
    'cache_duration': 600  # 缓存10分钟
}


@app.route('/api/check-public-access', methods=['POST'])
def check_public_access():
    """
    检测服务是否可从公网访问
    
    阿里云 Paraformer-v2 要求：
    - 支持 HTTP 和 HTTPS 协议
    - 文件 URL 必须是公网可访问的
    
    优化后的检测逻辑（支持 Docker/NAT）：
    - 不再检查本机网卡是否持有公网 IP（Docker 容器内通常没有）
    - 而是检查用户访问时使用的域名（Origin）是否解析为公网 IP
    - 如果用户能通过公网域名/IP 访问到这里，且该域名解析为公网 IP，
      那么我们生成的基于该域名的链接通常也是公网可达的。
    
    Returns:
        {
            "is_public": true/false,
            "public_url": "http://x.x.x.x:port" or null,
            "reason": "检测说明"
        }
    """
    import requests
    import socket
    import ipaddress
    from urllib.parse import urlparse
    
    data = request.get_json() or {}
    origin = data.get('origin', '').strip()
    force_refresh = data.get('force_refresh', False)
    
    # 检查缓存
    current_time = time.time()
    if not force_refresh and _public_access_cache['result'] is not None:
        if current_time - _public_access_cache['timestamp'] < _public_access_cache['cache_duration']:
            logger.info('[公网检测] 使用缓存结果')
            return jsonify(_public_access_cache['result'])
    
    result = {
        'is_public': False,
        'public_url': None,
        'reason': '未检测'
    }
    
    try:
        # 步骤1：解析 Origin
        if not origin:
            result['reason'] = '无法获取访问地址(Origin)'
            return jsonify(update_cache(result, current_time))
            
        parsed_origin = urlparse(origin)
        hostname = parsed_origin.hostname
        port = parsed_origin.port
        scheme = parsed_origin.scheme
        
        if not hostname:
            result['reason'] = 'Origin 格式错误'
            return jsonify(update_cache(result, current_time))
            
        # 步骤2：解析 Hostname 获取 IP
        try:
            origin_ip = socket.gethostbyname(hostname)
        except Exception as e:
            result['reason'] = f'域名解析失败: {hostname}'
            return jsonify(update_cache(result, current_time))
            
        # 步骤3：判断 IP 类型
        try:
            ip_obj = ipaddress.ip_address(origin_ip)
            
            # 使用公网 URL
            if port:
                public_url = f"{scheme}://{hostname}:{port}"
            else:
                public_url = f"{scheme}://{hostname}"
            result['public_url'] = public_url

            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved:
                # 内网或回环 IP -> 判定为不可用
                result['is_public'] = False
                result['reason'] = f'检测到内网环境 (访问来源 {hostname} 解析为 {origin_ip})，默认使用第三方直链'
                logger.info(f'[公网检测] 内网环境: {hostname} -> {origin_ip}')
            else:
                # 公网 IP -> 判定为可用
                # 假设：如果用户能通过公网 IP/域名访问 API，且该域名解析为公网 IP，
                # 则外部服务（阿里云）也能通过该域名访问静态文件。
                result['is_public'] = True
                result['reason'] = f'检测到公网访问 (访问来源 {hostname} 解析为 {origin_ip})，启用本地直链'
                logger.info(f'[公网检测] 公网环境: {hostname} -> {origin_ip}')
                
        except ValueError:
            result['reason'] = f'无效的 IP 地址: {origin_ip}'
            
    except Exception as e:
        logger.error(f'[公网检测] 检测异常: {e}')
        result['reason'] = f'检测异常: {str(e)}'
    
    return jsonify(update_cache(result, current_time))

def update_cache(result, timestamp):
    _public_access_cache['result'] = result
    _public_access_cache['timestamp'] = timestamp
    return result


@app.route('/api/guest_status')
@login_required
def get_guest_status():
    """获取 guest 账户状态（并发信息）"""
    is_guest = current_user.username == 'guest'
    
    if is_guest:
        status = guest_concurrency.get_status()
        return jsonify({
            "success": True,
            "is_guest": True,
            "concurrent": status["current"],
            "max_concurrent": status["max"],
            "queue": status["queue"]
        })
    else:
        return jsonify({
            "success": True,
            "is_guest": False
        })


@app.route('/api/batch_cancel/<batch_id>', methods=['POST'])
@login_required
def cancel_batch(batch_id):
    """取消批量任务"""
    result = task_manager.cancel_batch(batch_id)
    
    if result is None:
        return jsonify({"success": False, "error": "任务不存在"}), 404
    
    # 清理临时音频文件（如果有）
    try:
        import glob
        # 删除可能的残留音频文件
        for pattern in ['*.m4a', '*.mp3', '*.wav', '*.opus', '*.webm']:
            for f in glob.glob(os.path.join(AUDIO_DIR, pattern)):
                try:
                    os.remove(f)
                    logger.info(f"[Cancel] 已删除临时文件: {f}")
                except Exception as e:
                    logger.warning(f"[Cancel] 删除文件失败: {f}, 错误: {e}")
    except Exception as e:
        logger.error(f"[Cancel] 清理临时文件出错: {e}")
    
    return jsonify({
        "success": True, 
        "cancelled_indices": result["cancelled_indices"],
        "status": result["status"],
        "has_processing": result.get("has_processing", False)
    })


# ==================== Chrome 插件专用 API ====================

def get_user_by_extension_token(token):
    """通过插件令牌获取用户"""
    if not token:
        return None
    from models import User
    return User.query.filter_by(extension_token=token).first()


def extension_auth_required(f):
    """插件认证装饰器"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('X-Extension-Token', '').strip()
        if not token:
            return jsonify({'success': False, 'error': '缺少插件令牌'}), 401
        
        user = get_user_by_extension_token(token)
        if not user:
            return jsonify({'success': False, 'error': '无效的插件令牌'}), 401
        
        # 将用户信息存入 g 对象
        from flask import g
        g.extension_user = user
        return f(*args, **kwargs)
    return decorated_function


@app.route('/api/extension/ping', methods=['GET'])
@extension_auth_required
def extension_ping():
    """
    简单的连接测试 API（用于插件检测连接状态）
    
    请求头:
        X-Extension-Token: 插件令牌
    
    响应:
        {"success": true, "message": "已连接", "user": "用户名"}
    """
    from flask import g
    user = g.extension_user
    
    return jsonify({
        'success': True,
        'message': '已连接',
        'user': user.username,
        'last_sync': user.extension_last_sync.isoformat() if user.extension_last_sync else None
    })


@app.route('/api/extension/sync-cookie', methods=['POST'])
@extension_auth_required
def extension_sync_cookie():
    """
    接收插件同步的 B站 Cookie
    
    请求头:
        X-Extension-Token: 插件令牌
    
    请求体:
        {"cookie": "SESSDATA=xxx; buvid3=yyy; ..."}
    
    响应:
        {"success": true, "message": "Cookie已同步"}
    """
    from flask import g
    from datetime import datetime
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '请求体为空'}), 400
        
        cookie = data.get('cookie', '').strip()
        if not cookie:
            return jsonify({'success': False, 'error': '缺少 Cookie 数据'}), 400
        
        user = g.extension_user
        
        # 更新用户的 B站 Cookie
        user.bili_cookie = cookie
        user.extension_last_sync = datetime.utcnow()
        db.session.commit()
        
        logger.info(f"[extension] 用户 {user.username} 同步了 Cookie，长度: {len(cookie)}")
        
        return jsonify({
            'success': True,
            'message': 'Cookie 已同步',
            'sync_time': user.extension_last_sync.isoformat()
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"[extension] Cookie 同步失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/extension/subtitle', methods=['POST'])
@extension_auth_required
def extension_get_subtitle():
    """
    获取视频字幕（插件专用）
    
    请求头:
        X-Extension-Token: 插件令牌
    
    请求体:
        {"bvid": "BVxxxxx", "use_asr": false}
    
    响应:
        {"success": true, "transcript": "字幕内容...", "source": "bilibili/asr"}
    """
    from flask import g
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '请求体为空'}), 400
        
        bvid = data.get('bvid', '').strip()
        use_asr = data.get('use_asr', False)  # 是否使用语音识别（默认否）
        
        if not bvid:
            return jsonify({'success': False, 'error': '缺少 bvid 参数'}), 400
        
        user = g.extension_user
        bili_cookie = user.bili_cookie
        api_key = user.api_key
        
        # 构建视频 URL
        video_url = f"https://www.bilibili.com/video/{bvid}"
        
        log_collector = LogCollector()
        transcript = None
        source = None
        
        # 优先尝试获取 B站自带字幕
        if bili_cookie:
            try:
                transcript = get_bilibili_subtitles(video_url, log_collector, bili_cookie)
                if transcript:
                    source = 'bilibili'
                    logger.info(f"[extension] 从 B站获取字幕成功: {bvid}")
            except Exception as e:
                logger.warning(f"[extension] 获取 B站字幕失败: {e}")
        
        # 如果没有字幕且允许使用语音识别
        if not transcript and use_asr and api_key:
            try:
                # 下载音频并转录
                audio_path, duration = download_bilibili_audio(video_url, AUDIO_DIR, log_collector)
                
                # 使用 transcribe_audio 进行语音识别
                # 该函数内部会根据 self_hosted_domain 自动选择本地直链或第三方服务
                use_self_hosted = user.use_self_hosted
                self_hosted_domain = user.self_hosted_domain if use_self_hosted else None
                
                transcript = transcribe_audio(
                    audio_path, 
                    api_key, 
                    log_collector, 
                    self_hosted_domain=self_hosted_domain,
                    duration=duration
                )
                
                if transcript:
                    source = 'asr'
                    logger.info(f"[extension] 语音识别成功: {bvid}")
                
                # 清理音频文件
                if os.path.exists(audio_path):
                    os.remove(audio_path)
            except Exception as e:
                logger.error(f"[extension] 语音识别失败: {e}")
        
        if transcript:
            return jsonify({
                'success': True,
                'transcript': transcript,
                'source': source
            })
        else:
            return jsonify({
                'success': False,
                'error': '未能获取字幕',
                'transcript': None
            }), 404
            
    except Exception as e:
        logger.error(f"[extension] 获取字幕失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/extension/llm', methods=['POST'])
@extension_auth_required
def extension_llm_process():
    """
    调用大模型处理字幕（插件专用）
    
    请求头:
        X-Extension-Token: 插件令牌
    
    请求体:
        {
            "content": "字幕内容",
            "question": "用户问题（可选）",
            "use_user_config": true  // 是否使用用户保存的 LLM 配置
        }
    
    或使用自定义配置:
        {
            "content": "字幕内容",
            "question": "用户问题",
            "api_key": "sk-xxx",
            "api_url": "https://api.openai.com/v1/chat/completions",
            "model": "gpt-4o-mini"
        }
    """
    from flask import g
    import requests as req
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '请求体为空'}), 400
        
        content = data.get('content', '').strip()
        question = data.get('question', '').strip()
        use_user_config = data.get('use_user_config', True)
        
        if not content:
            return jsonify({'success': False, 'error': '缺少字幕内容'}), 400
        
        user = g.extension_user
        
        # 获取 LLM 配置
        if use_user_config:
            api_key = user.llm_api_key or user.api_key  # 优先使用 LLM API Key，否则使用 Paraformer API Key
            api_url = user.llm_api_url or 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
            model = user.llm_model or 'qwen-turbo'
            prompt = user.llm_prompt or '你是一个有帮助的AI助手。请根据视频字幕内容回答用户问题。'
        else:
            api_key = data.get('api_key', '').strip()
            api_url = data.get('api_url', 'https://api.openai.com/v1/chat/completions').strip()
            model = data.get('model', 'gpt-4o-mini').strip()
            prompt = data.get('prompt', '你是一个有帮助的AI助手。').strip()
        
        if not api_key:
            return jsonify({'success': False, 'error': '未配置 LLM API Key'}), 400
        
        # 构建请求
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        messages = [
            {"role": "system", "content": prompt}
        ]
        
        # 添加字幕内容
        user_content = f"视频字幕内容:\n{content}"
        if question:
            user_content += f"\n\n用户问题: {question}"
        
        messages.append({"role": "user", "content": user_content})
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7
        }
        
        # 确保 API URL 完整
        if 'chat/completions' not in api_url:
            api_url = api_url.rstrip('/') + '/v1/chat/completions' if '/v1' not in api_url else api_url.rstrip('/') + '/chat/completions'
        
        response = req.post(api_url, headers=headers, json=payload, timeout=120, proxies={})
        
        if response.status_code != 200:
            return jsonify({
                'success': False,
                'error': f'API 调用失败: {response.status_code}'
            }), 500
        
        result = response.json()
        
        # 提取回复
        ai_response = None
        if 'choices' in result and len(result['choices']) > 0:
            choice = result['choices'][0]
            if 'message' in choice and 'content' in choice['message']:
                ai_response = choice['message']['content']
        
        if ai_response:
            return jsonify({
                'success': True,
                'response': ai_response
            })
        else:
            return jsonify({
                'success': False,
                'error': '无法解析 AI 响应'
            }), 500
            
    except Exception as e:
        logger.error(f"[extension] LLM 处理失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============ 插件异步任务 API ============

@app.route('/api/extension/task/create', methods=['POST'])
@extension_auth_required
def extension_create_task():
    """
    创建异步字幕提取任务
    
    请求头: X-Extension-Token
    请求体: {"bvid": "BVxxxxx", "title": "视频标题", "use_asr": false}
    响应: {"success": true, "task_id": "uuid", "status": "pending"}
    """
    from flask import g
    user = g.extension_user
    
    try:
        data = request.get_json() or {}
        bvid = data.get('bvid', '').strip()
        title = data.get('title', '')
        use_asr = data.get('use_asr', False)
        
        if not bvid:
            return jsonify({'success': False, 'error': '缺少 bvid 参数'}), 400
        
        # 检查是否已有历史记录
        from models import HistoryItem
        history = HistoryItem.query.filter_by(user_id=user.id, bvid=bvid).first()
        if history and history.transcript:
            # 已有历史记录，直接返回
            return jsonify({
                'success': True,
                'task_id': None,
                'status': 'completed',
                'from_history': True,
                'transcript': history.transcript,
                'ai_result': history.ai_result,
                'title': history.title
            })
        
        # 创建新任务
        task_id = extension_task_manager.create_task(
            user_id=user.id,
            bvid=bvid,
            title=title,
            use_asr=use_asr
        )
        
        # 获取任务状态（可能是复用的旧任务）
        task = extension_task_manager.get_task(task_id)
        
        if task['status'] == extension_task_manager.STATUS_PENDING:
            # 新任务，启动后台处理
            executor.submit(
                _extension_process_task,
                task_id,
                user.id,
                bvid,
                use_asr
            )
        
        return jsonify({
            'success': True,
            'task_id': task_id,
            'status': task['status'],
            'progress': task['progress'],
            'stage_desc': task['stage_desc']
        })
        
    except Exception as e:
        logger.error(f"[extension] 创建任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _extension_process_task(task_id: str, user_id: int, bvid: str, use_asr: bool):
    """后台处理字幕提取任务"""
    from models import User, HistoryItem
    
    logger.info(f"[extension] 开始处理任务: task_id={task_id}, bvid={bvid}, use_asr={use_asr}")
    
    with app.app_context():
        try:
            user = User.query.get(user_id)
            if not user:
                logger.error(f"[extension] 用户不存在: user_id={user_id}")
                extension_task_manager.update_task(task_id, 
                    status=ExtensionTaskManager.STATUS_FAILED,
                    error="用户不存在")
                return
            
            video_url = f"https://www.bilibili.com/video/{bvid}"
            bili_cookie = user.bili_cookie
            api_key = user.api_key
            
            # 自定义进度回调
            def update_progress(progress, stage_desc=None):
                extension_task_manager.update_task(task_id, progress=progress, 
                    stage_desc=stage_desc if stage_desc else None)
            
            log_collector = LogCollector()
            transcript = None
            source = None
            
            # 阶段 1：尝试获取 B站自带字幕 (0-15%)
            logger.info(f"[extension] [{bvid}] 阶段1: 尝试获取 B站自带字幕")
            extension_task_manager.update_task(task_id,
                status=ExtensionTaskManager.STATUS_DOWNLOADING,
                progress=5,
                stage_desc="检查B站字幕")
            
            if bili_cookie:
                try:
                    update_progress(8, "获取视频信息")
                    transcript = get_bilibili_subtitles(video_url, log_collector, bili_cookie)
                    if transcript:
                        source = 'bilibili'
                        logger.info(f"[extension] [{bvid}] 成功获取 B站自带字幕，长度: {len(transcript)}")
                except Exception as e:
                    logger.warning(f"[extension] [{bvid}] 获取 B站字幕失败: {e}")
            
            update_progress(15, "字幕检查完成")
            
            # 阶段 2：如果没有字幕且允许语音识别 (15-90%)
            if not transcript and use_asr and api_key:
                logger.info(f"[extension] [{bvid}] 阶段2: 开始语音识别")
                
                # 检查任务是否被取消
                if extension_task_manager.is_cancelled(task_id):
                    logger.info(f"[extension] [{bvid}] 任务已取消")
                    return
                
                try:
                    # 下载音频 (15-35%)
                    extension_task_manager.update_task(task_id,
                        status=ExtensionTaskManager.STATUS_DOWNLOADING,
                        progress=18,
                        stage_desc="下载音频")
                    
                    logger.info(f"[extension] [{bvid}] 开始下载音频")
                    audio_path, duration = download_bilibili_audio(video_url, AUDIO_DIR, log_collector)
                    logger.info(f"[extension] [{bvid}] 音频下载完成: {audio_path}, 时长: {duration}秒")
                    
                    if extension_task_manager.is_cancelled(task_id):
                        if os.path.exists(audio_path):
                            os.remove(audio_path)
                        return
                    
                    update_progress(35, "音频下载完成")
                    
                    # 上传/准备 (35-45%)
                    extension_task_manager.update_task(task_id,
                        status=ExtensionTaskManager.STATUS_UPLOADING,
                        progress=40,
                        stage_desc="准备上传")
                    
                    # 语音识别 (45-90%)
                    # 使用公网检测，与网站端一致（而非 user.use_self_hosted）
                    # 检查缓存的公网检测结果
                    use_local_url = False
                    self_hosted_domain = None
                    
                    if _public_access_cache.get('result') and _public_access_cache['result'].get('is_public'):
                        use_local_url = True
                        self_hosted_domain = _public_access_cache['result'].get('public_url')
                        logger.info(f"[extension] [{bvid}] 使用本地直链: {self_hosted_domain}")
                    else:
                        logger.info(f"[extension] [{bvid}] 本地直链不可用，使用第三方直链")
                    
                    extension_task_manager.update_task(task_id,
                        status=ExtensionTaskManager.STATUS_TRANSCRIBING,
                        progress=48,
                        stage_desc="语音识别中")
                    
                    logger.info(f"[extension] [{bvid}] 开始语音识别")
                    
                    # 创建带进度回调的日志收集器
                    # 注意：LogCollector 的 progress_callback 接收一个字典参数
                    def transcribe_progress_callback(data):
                        if data.get('type') == 'progress':
                            # 映射到 48-88% 区间
                            progress_pct = data.get('progress', 0)
                            mapped_progress = 48 + int(progress_pct * 0.4)
                            update_progress(mapped_progress, f"语音识别 {int(progress_pct)}%")
                    
                    log_collector.progress_callback = transcribe_progress_callback
                    
                    transcript = transcribe_audio(
                        audio_path, api_key, log_collector,
                        self_hosted_domain=self_hosted_domain,
                        duration=duration
                    )
                    
                    if transcript:
                        source = 'asr'
                        logger.info(f"[extension] [{bvid}] 语音识别完成，长度: {len(transcript)}")
                    else:
                        logger.warning(f"[extension] [{bvid}] 语音识别返回空结果")
                    
                    # 清理音频文件
                    if os.path.exists(audio_path):
                        os.remove(audio_path)
                        logger.info(f"[extension] [{bvid}] 临时音频已清理")
                        
                except Exception as e:
                    logger.error(f"[extension] [{bvid}] 语音识别失败: {e}")
                    import traceback
                    traceback.print_exc()
                    extension_task_manager.update_task(task_id,
                        status=ExtensionTaskManager.STATUS_FAILED,
                        progress=100,
                        error=f"语音识别失败: {str(e)}")
                    return
            
            # 阶段 3：处理结果 (90-100%)
            logger.info(f"[extension] [{bvid}] 阶段3: 处理结果")
            extension_task_manager.update_task(task_id,
                status=ExtensionTaskManager.STATUS_PROCESSING,
                progress=92,
                stage_desc="保存结果")
            
            if transcript:
                # 保存到历史记录
                try:
                    history = HistoryItem.query.filter_by(user_id=user_id, bvid=bvid).first()
                    if history:
                        history.transcript = transcript
                        history.updated_at = datetime.utcnow()
                        logger.info(f"[extension] [{bvid}] 更新历史记录")
                    else:
                        # 获取视频详细信息（包括封面）
                        update_progress(95, "获取视频信息")
                        video_info = get_video_info_from_bilibili(bvid) or {}
                        title = video_info.get('title') or extension_task_manager.get_task(task_id).get('title', bvid)
                        
                        history = HistoryItem(
                            user_id=user_id,
                            url=f"https://www.bilibili.com/video/{bvid}",
                            bvid=bvid,
                            title=title,
                            owner=video_info.get('owner', ''),
                            cover=video_info.get('pic', ''),
                            duration=video_info.get('duration', 0),
                            pubdate=video_info.get('pubdate', 0),
                            transcript=transcript
                        )
                        db.session.add(history)
                        logger.info(f"[extension] [{bvid}] 创建新历史记录")
                    db.session.commit()
                except Exception as e:
                    logger.error(f"[extension] [{bvid}] 保存历史记录失败: {e}")
                    db.session.rollback()
                
                extension_task_manager.update_task(task_id,
                    status=ExtensionTaskManager.STATUS_COMPLETED,
                    progress=100,
                    transcript=transcript,
                    stage_desc="完成")
                logger.info(f"[extension] [{bvid}] ✅ 任务完成!")
            else:
                error_msg = "未能获取字幕（视频无字幕且语音识别失败）"
                logger.warning(f"[extension] [{bvid}] {error_msg}")
                extension_task_manager.update_task(task_id,
                    status=ExtensionTaskManager.STATUS_FAILED,
                    progress=100,
                    error=error_msg)
                    
        except Exception as e:
            logger.error(f"[extension] [{bvid}] 任务处理失败: {e}")
            import traceback
            traceback.print_exc()
            extension_task_manager.update_task(task_id,
                status=ExtensionTaskManager.STATUS_FAILED,
                error=str(e))



@app.route('/api/extension/task/<task_id>', methods=['GET'])
@extension_auth_required
def extension_get_task(task_id):
    """
    查询任务状态
    
    响应: {"success": true, "task": {...}}
    """
    task = extension_task_manager.get_task(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    
    return jsonify({
        'success': True,
        'task': task
    })


@app.route('/api/extension/task/<task_id>/cancel', methods=['POST'])
@extension_auth_required
def extension_cancel_task(task_id):
    """取消任务"""
    success = extension_task_manager.cancel_task(task_id)
    return jsonify({'success': success})


@app.route('/api/extension/tasks', methods=['GET'])
@login_required
def get_extension_tasks():
    """
    获取当前用户的所有进行中插件任务（用于网站显示）
    
    响应: {"success": true, "tasks": [...]}
    """
    tasks = extension_task_manager.get_user_tasks(current_user.id)
    
    return jsonify({
        'success': True,
        'tasks': tasks
    })


@app.route('/api/extension/tasks/all', methods=['GET'])
@login_required
def get_extension_tasks_all():
    """
    获取当前用户的所有插件任务（包括已完成和失败的，用于调试）
    
    响应: {"success": true, "tasks": [...]}
    """
    limit = request.args.get('limit', 20, type=int)
    tasks = extension_task_manager.get_user_all_tasks(current_user.id, limit=limit)
    
    return jsonify({
        'success': True,
        'tasks': tasks
    })

@app.route('/api/extension/history/<bvid>', methods=['GET'])
@extension_auth_required
def extension_check_history(bvid):
    """
    检查该视频是否有历史记录
    
    响应: {"exists": true, "transcript": "...", "ai_result": "...", "title": "..."}
    """
    from flask import g
    from models import HistoryItem
    
    user = g.extension_user
    history = HistoryItem.query.filter_by(user_id=user.id, bvid=bvid).first()
    
    if history and history.transcript:
        return jsonify({
            'success': True,
            'exists': True,
            'transcript': history.transcript,
            'ai_result': history.ai_result,
            'title': history.title,
            'updated_at': history.updated_at.isoformat() if history.updated_at else None
        })
    else:
        # 检查是否有正在进行的任务
        task = extension_task_manager.get_task_by_bvid(user.id, bvid)
        if task and task.get('status') not in [ExtensionTaskManager.STATUS_COMPLETED, 
                                                 ExtensionTaskManager.STATUS_FAILED,
                                                 ExtensionTaskManager.STATUS_CANCELLED]:
            return jsonify({
                'success': True,
                'exists': False,
                'has_pending_task': True,
                'task_id': task.get('task_id'),
                'status': task.get('status'),
                'progress': task.get('progress'),
                'stage_desc': task.get('stage_desc')
            })
        
        return jsonify({
            'success': True,
            'exists': False,
            'has_pending_task': False
        })


@app.route('/api/extension/history/<bvid>/ai', methods=['POST'])
@extension_auth_required
def extension_save_ai_result(bvid):
    """
    保存 AI 对话结果到历史记录
    
    请求体: {"ai_result": "对话内容"}
    """
    from flask import g
    from models import HistoryItem
    
    user = g.extension_user
    data = request.get_json() or {}
    ai_result = data.get('ai_result', '')
    
    history = HistoryItem.query.filter_by(user_id=user.id, bvid=bvid).first()
    if not history:
        return jsonify({'success': False, 'error': '历史记录不存在'}), 404
    
    try:
        history.ai_result = ai_result
        history.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("B站字幕提取服务启动")
    logger.info("访问地址: http://localhost:5001")
    logger.info("=" * 60)
    
    # 根据环境变量决定是否启用 debug 模式
    is_debug = os.environ.get('FLASK_ENV', 'production') != 'production'
    app.run(host='0.0.0.0', port=5001, debug=is_debug, threaded=True)

