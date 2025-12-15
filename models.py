"""
BiliSub 数据模型
使用 Flask-SQLAlchemy 进行 ORM 映射
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """用户模型"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_guest = db.Column(db.Boolean, default=False, nullable=False)  # Guest 用户标识
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 用户独立配置
    api_key = db.Column(db.Text, nullable=True)  # 阿里云 API Key
    bili_cookie = db.Column(db.Text, nullable=True)  # B站 Cookie
    llm_api_key = db.Column(db.Text, nullable=True)  # LLM API Key
    llm_api_url = db.Column(db.String(500), nullable=True)  # LLM API URL
    llm_model = db.Column(db.String(100), nullable=True)  # LLM 模型名称
    llm_prompt = db.Column(db.Text, nullable=True)  # LLM 提示词
    use_self_hosted = db.Column(db.Boolean, default=False)  # 是否使用自建存储
    self_hosted_domain = db.Column(db.String(500), nullable=True)  # 自建域名
    
    # 云存储配置
    cloud_storage_type = db.Column(db.String(20), nullable=True, default='gdrive')  # 'gdrive' 或 'webdav'
    cloud_service_account = db.Column(db.Text, nullable=True)  # Google Service Account JSON
    cloud_folder_name = db.Column(db.String(200), nullable=True)  # 云端文件夹名称/ID
    
    # WebDAV 配置
    webdav_url = db.Column(db.String(500), nullable=True)  # WebDAV 服务器地址
    webdav_username = db.Column(db.String(200), nullable=True)  # WebDAV 用户名
    webdav_password = db.Column(db.String(500), nullable=True)  # WebDAV 密码
    
    # Chrome 插件绑定
    extension_token = db.Column(db.String(64), unique=True, nullable=True, index=True)  # 插件令牌
    extension_last_sync = db.Column(db.DateTime, nullable=True)  # 插件最后同步时间
    
    # 关联的历史记录
    history_items = db.relationship('HistoryItem', backref='user', lazy='dynamic',
                                    cascade='all, delete-orphan')
    
    def set_password(self, password):
        """设置密码（哈希存储）"""
        # 使用 pbkdf2:sha256 算法，兼容 macOS 内置 Python
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
    
    def check_password(self, password):
        """验证密码"""
        return check_password_hash(self.password_hash, password)
    
    def to_dict(self, include_config=False):
        """转换为字典"""
        data = {
            'id': self.id,
            'username': self.username,
            'is_admin': self.is_admin,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
        if include_config:
            data.update({
                'api_key': bool(self.api_key),  # 只返回是否配置，不返回明文
                'bili_cookie': bool(self.bili_cookie),
                'llm_api_key': bool(self.llm_api_key),
                'llm_api_url': self.llm_api_url,
                'llm_model': self.llm_model,
                'use_self_hosted': self.use_self_hosted,
                'self_hosted_domain': self.self_hosted_domain
            })
        return data


class HistoryItem(db.Model):
    """历史记录模型"""
    __tablename__ = 'history_items'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), 
                        nullable=False, index=True)
    
    # 视频信息
    url = db.Column(db.String(500), nullable=False)
    title = db.Column(db.String(500), nullable=True)
    owner = db.Column(db.String(200), nullable=True)
    cover = db.Column(db.String(500), nullable=True)
    bvid = db.Column(db.String(50), nullable=True, index=True)
    duration = db.Column(db.Integer, nullable=True)
    pubdate = db.Column(db.Integer, nullable=True)
    tags = db.Column(db.Text, nullable=True)  # JSON 字符串
    
    # 处理结果
    transcript = db.Column(db.Text, nullable=True)  # 字幕文本
    ai_result = db.Column(db.Text, nullable=True)  # AI 处理结果（兼容旧版本）
    ai_summary = db.Column(db.Text, nullable=True)  # AI 处理结果（基于提示词）
    ai_chat = db.Column(db.Text, nullable=True)  # AI 对话历史
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        """转换为字典"""
        import json
        return {
            'id': self.id,
            'url': self.url,
            'title': self.title,
            'owner': self.owner,
            'cover': self.cover,
            'bvid': self.bvid,
            'duration': self.duration,
            'pubdate': self.pubdate,
            'tags': json.loads(self.tags) if self.tags else [],
            'transcript': self.transcript,
            'ai_result': self.ai_result,
            'ai_summary': self.ai_summary or self.ai_result or '',  # 优先返回新字段，兼容旧数据
            'ai_chat': self.ai_chat or '',
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class ExtensionTask(db.Model):
    """Chrome 插件任务模型 - 持久化任务状态"""
    __tablename__ = 'extension_tasks'
    
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.String(40), unique=True, nullable=False, index=True)  # UUID
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), 
                        nullable=False, index=True)
    bvid = db.Column(db.String(50), nullable=False, index=True)
    title = db.Column(db.String(500), nullable=True)
    cover = db.Column(db.String(500), nullable=True)  # 封面图 URL
    owner = db.Column(db.String(200), nullable=True)  # UP主名称
    
    # 任务状态
    status = db.Column(db.String(20), nullable=False, default='pending')
    progress = db.Column(db.Integer, default=0)
    stage_desc = db.Column(db.String(100), nullable=True)
    error = db.Column(db.Text, nullable=True)
    
    # 结果
    transcript = db.Column(db.Text, nullable=True)
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 用户关联
    user = db.relationship('User', backref=db.backref('extension_tasks', lazy='dynamic'))
    
    def to_dict(self):
        """转换为字典"""
        return {
            'task_id': self.task_id,
            'bvid': self.bvid,
            'title': self.title,
            'cover': self.cover,
            'owner': self.owner,
            'status': self.status,
            'progress': self.progress,
            'stage_desc': self.stage_desc,
            'error': self.error,
            'transcript': self.transcript,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

class SystemConfig(db.Model):
    """系统配置模型（存储邀请码等）"""
    __tablename__ = 'system_config'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @staticmethod
    def get(key, default=None):
        """获取配置值"""
        config = SystemConfig.query.filter_by(key=key).first()
        return config.value if config else default
    
    @staticmethod
    def set(key, value):
        """设置配置值"""
        config = SystemConfig.query.filter_by(key=key).first()
        if config:
            config.value = value
        else:
            config = SystemConfig(key=key, value=value)
            db.session.add(config)
        db.session.commit()
        return config


def init_database(app):
    """初始化数据库"""
    import secrets
    
    with app.app_context():
        db.create_all()
        
        # 数据库迁移：检查并添加缺失的列
        _migrate_extension_tasks_table()
        _migrate_history_items_table()
        _migrate_users_table()
        
        # 检查是否存在管理员账户
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin = User(username='admin', is_admin=True)
            admin.set_password('admin')  # 默认密码
            db.session.add(admin)
            print('[INFO] 创建默认管理员账户: admin/admin')
        
        # 检查是否存在 guest 账户
        guest = User.query.filter_by(username='guest').first()
        if not guest:
            guest = User(username='guest', is_admin=False, is_guest=True)
            guest.set_password('guest')
            db.session.add(guest)
            print('[INFO] 创建默认 Guest 账户: guest/guest (配置和历史仅保存在会话中)')
        elif not guest.is_guest:
            # 如果已存在但未标记为 guest，则更新
            guest.is_guest = True
            print('[INFO] 更新 guest 账户标记')
        
        # 检查是否存在邀请码
        invite_code = SystemConfig.get('invite_code')
        if not invite_code:
            # 生成 16 位随机邀请码
            invite_code = secrets.token_urlsafe(12)[:16]
            SystemConfig.set('invite_code', invite_code)
            print(f'[INFO] 生成初始邀请码: {invite_code}')
        
        db.session.commit()


def _migrate_extension_tasks_table():
    """检查并添加 extension_tasks 表中缺失的列"""
    try:
        # 检查表是否存在
        result = db.session.execute(db.text("SELECT name FROM sqlite_master WHERE type='table' AND name='extension_tasks'"))
        if not result.fetchone():
            return  # 表不存在，create_all 会创建
        
        # 获取现有列
        result = db.session.execute(db.text("PRAGMA table_info(extension_tasks)"))
        existing_columns = {row[1] for row in result.fetchall()}
        
        # 需要添加的列
        migrations = [
            ('cover', 'VARCHAR(500)'),
            ('owner', 'VARCHAR(200)'),
        ]
        
        for column_name, column_type in migrations:
            if column_name not in existing_columns:
                db.session.execute(db.text(f"ALTER TABLE extension_tasks ADD COLUMN {column_name} {column_type}"))
                print(f'[INFO] 数据库迁移: 添加列 extension_tasks.{column_name}')
        
        db.session.commit()
    except Exception as e:
        print(f'[WARNING] 数据库迁移失败: {e}')


def _migrate_history_items_table():
    """检查并添加 history_items 表中缺失的列（AI 分区功能）"""
    try:
        # 检查表是否存在
        result = db.session.execute(db.text("SELECT name FROM sqlite_master WHERE type='table' AND name='history_items'"))
        if not result.fetchone():
            return  # 表不存在，create_all 会创建
        
        # 获取现有列
        result = db.session.execute(db.text("PRAGMA table_info(history_items)"))
        existing_columns = {row[1] for row in result.fetchall()}
        
        # 需要添加的列
        migrations = [
            ('ai_summary', 'TEXT'),
            ('ai_chat', 'TEXT'),
        ]
        
        for column_name, column_type in migrations:
            if column_name not in existing_columns:
                db.session.execute(db.text(f"ALTER TABLE history_items ADD COLUMN {column_name} {column_type}"))
                print(f'[INFO] 数据库迁移: 添加列 history_items.{column_name}')
        
        # 数据迁移：将现有 ai_result 复制到 ai_summary（如果 ai_summary 为空）
        if 'ai_summary' not in existing_columns:
            db.session.execute(db.text(
                "UPDATE history_items SET ai_summary = ai_result WHERE ai_result IS NOT NULL AND ai_result != ''"
            ))
            print('[INFO] 数据库迁移: 已将现有 ai_result 复制到 ai_summary')
        
        db.session.commit()
    except Exception as e:
        print(f'[WARNING] history_items 数据库迁移失败: {e}')


def _migrate_users_table():
    """检查并添加 users 表中缺失的列（云存储功能）"""
    try:
        # 检查表是否存在
        result = db.session.execute(db.text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'"))
        if not result.fetchone():
            return
        
        # 获取现有列
        result = db.session.execute(db.text("PRAGMA table_info(users)"))
        existing_columns = {row[1] for row in result.fetchall()}
        
        # 需要添加的列
        migrations = [
            ('cloud_storage_type', 'VARCHAR(20)'),
            ('cloud_service_account', 'TEXT'),
            ('cloud_folder_name', 'VARCHAR(200)'),
            ('webdav_url', 'VARCHAR(500)'),
            ('webdav_username', 'VARCHAR(200)'),
            ('webdav_password', 'VARCHAR(500)'),
        ]
        
        for column_name, column_type in migrations:
            if column_name not in existing_columns:
                db.session.execute(db.text(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}"))
                print(f'[INFO] 数据库迁移: 添加列 users.{column_name}')
        
        db.session.commit()
    except Exception as e:
        print(f'[WARNING] users 数据库迁移失败: {e}')
