"""
BiliSub 认证路由
处理登录、注册、登出和密码修改
"""
import logging
from flask import Blueprint, request, jsonify, redirect, url_for, render_template, session
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, SystemConfig, HistoryItem
import json

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/api/login', methods=['POST'])
def login():
    """用户登录"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': '请求体为空'}), 400
    
    username = data.get('username', '').strip()
    password = data.get('password', '')
    remember = data.get('remember', False)
    
    if not username or not password:
        return jsonify({'success': False, 'error': '请输入用户名和密码'}), 400
    
    user = User.query.filter_by(username=username).first()
    
    if user is None or not user.check_password(password):
        return jsonify({'success': False, 'error': '用户名或密码错误'}), 401
    
    login_user(user, remember=remember)
    
    return jsonify({
        'success': True,
        'user': user.to_dict()
    })


@auth_bp.route('/api/register', methods=['POST'])
def register():
    """用户注册（需要邀请码）"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': '请求体为空'}), 400
    
    username = data.get('username', '').strip()
    password = data.get('password', '')
    invite_code = data.get('invite_code', '').strip()
    
    if not username or not password:
        return jsonify({'success': False, 'error': '请输入用户名和密码'}), 400
    
    if len(username) < 3 or len(username) > 20:
        return jsonify({'success': False, 'error': '用户名长度需在 3-20 个字符之间'}), 400
    
    if len(password) < 4:
        return jsonify({'success': False, 'error': '密码长度至少 4 个字符'}), 400
    
    # 验证邀请码
    correct_invite_code = SystemConfig.get('invite_code')
    if not correct_invite_code or invite_code != correct_invite_code:
        return jsonify({'success': False, 'error': '邀请码无效'}), 400
    
    # 检查邀请码使用次数限制
    invite_code_limit = int(SystemConfig.get('invite_code_limit', '0') or '0')
    invite_code_used = int(SystemConfig.get('invite_code_used', '0') or '0')
    
    if invite_code_limit > 0 and invite_code_used >= invite_code_limit:
        return jsonify({'success': False, 'error': '邀请码已达使用次数上限'}), 400
    
    # 检查用户名是否已存在
    if User.query.filter_by(username=username).first():
        return jsonify({'success': False, 'error': '用户名已存在'}), 400
    
    # 创建用户
    user = User(username=username, is_admin=False)
    user.set_password(password)
    db.session.add(user)
    
    # 增加邀请码使用次数
    SystemConfig.set('invite_code_used', str(invite_code_used + 1))
    
    db.session.commit()
    
    # 自动登录
    login_user(user)
    
    return jsonify({
        'success': True,
        'message': '注册成功',
        'user': user.to_dict()
    })


@auth_bp.route('/api/logout', methods=['POST'])
@login_required
def logout():
    """用户登出"""
    logout_user()
    return jsonify({'success': True, 'message': '已登出'})


@auth_bp.route('/api/me', methods=['GET'])
def get_current_user():
    """获取当前登录用户信息"""
    if current_user.is_authenticated:
        return jsonify({
            'authenticated': True,
            'user': current_user.to_dict(include_config=True)
        })
    else:
        return jsonify({
            'authenticated': False,
            'user': None
        })


@auth_bp.route('/api/change-password', methods=['POST'])
@login_required
def change_password():
    """修改密码"""
    # Guest 用户不允许自己修改密码
    if getattr(current_user, 'is_guest', False):
        return jsonify({'success': False, 'error': 'Guest 账户不允许修改密码'}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': '请求体为空'}), 400
    
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')
    
    if not old_password or not new_password:
        return jsonify({'success': False, 'error': '请输入原密码和新密码'}), 400
    
    if len(new_password) < 4:
        return jsonify({'success': False, 'error': '新密码长度至少 4 个字符'}), 400
    
    if not current_user.check_password(old_password):
        return jsonify({'success': False, 'error': '原密码错误'}), 400
    
    current_user.set_password(new_password)
    db.session.commit()
    
    return jsonify({'success': True, 'message': '密码修改成功'})


@auth_bp.route('/api/save-config', methods=['POST'])
@login_required
def save_user_config():
    """保存用户配置"""
    logger.info(f"[save-config] 用户 {current_user.username} (ID: {current_user.id}) 请求保存配置")
    
    data = request.get_json()
    if not data:
        logger.warning("[save-config] 请求体为空")
        return jsonify({'success': False, 'error': '请求体为空'}), 400
    
    logger.info(f"[save-config] 接收到数据: {list(data.keys())}")
    
    # Guest 用户：保存到 session
    if getattr(current_user, 'is_guest', False):
        guest_config = session.get('guest_config', {})
        
        if 'api_key' in data:
            guest_config['api_key'] = data['api_key']
        if 'bili_cookie' in data:
            guest_config['bili_cookie'] = data['bili_cookie']
        if 'llm_api_key' in data:
            guest_config['llm_api_key'] = data['llm_api_key']
        if 'llm_api_url' in data:
            guest_config['llm_api_url'] = data['llm_api_url']
        if 'llm_model' in data:
            guest_config['llm_model'] = data['llm_model']
        if 'llm_prompt' in data:
            guest_config['llm_prompt'] = data['llm_prompt']
        if 'use_self_hosted' in data:
            guest_config['use_self_hosted'] = data['use_self_hosted']
        if 'self_hosted_domain' in data:
            guest_config['self_hosted_domain'] = data['self_hosted_domain']
        
        session['guest_config'] = guest_config
        session.modified = True
        logger.info(f"[save-config] Guest 配置已保存到 session")
        return jsonify({'success': True, 'message': '配置已保存（会话级）'})
    
    # 普通用户：保存到数据库
    if 'api_key' in data:
        current_user.api_key = data['api_key']
        logger.info(f"[save-config] 更新 api_key, 长度: {len(data['api_key'])}")
    if 'bili_cookie' in data:
        current_user.bili_cookie = data['bili_cookie']
        logger.info(f"[save-config] 更新 bili_cookie, 长度: {len(data['bili_cookie'])}")
    if 'llm_api_key' in data:
        current_user.llm_api_key = data['llm_api_key']
    if 'llm_api_url' in data:
        current_user.llm_api_url = data['llm_api_url']
    if 'llm_model' in data:
        current_user.llm_model = data['llm_model']
    if 'llm_prompt' in data:
        current_user.llm_prompt = data['llm_prompt']
    if 'use_self_hosted' in data:
        current_user.use_self_hosted = data['use_self_hosted']
    if 'self_hosted_domain' in data:
        current_user.self_hosted_domain = data['self_hosted_domain']
    
    db.session.commit()
    logger.info(f"[save-config] 配置已保存到数据库")
    
    return jsonify({'success': True, 'message': '配置已保存'})


@auth_bp.route('/api/load-config', methods=['GET'])
@login_required
def load_user_config():
    """加载用户配置"""
    logger.info(f"[load-config] 用户 {current_user.username} (ID: {current_user.id}) 请求加载配置")
    
    # Guest 用户：从 session 加载
    if getattr(current_user, 'is_guest', False):
        guest_config = session.get('guest_config', {})
        config = {
            'api_key': guest_config.get('api_key', ''),
            'bili_cookie': guest_config.get('bili_cookie', ''),
            'llm_api_key': guest_config.get('llm_api_key', ''),
            'llm_api_url': guest_config.get('llm_api_url', ''),
            'llm_model': guest_config.get('llm_model', ''),
            'llm_prompt': guest_config.get('llm_prompt', ''),
            'use_self_hosted': guest_config.get('use_self_hosted', False),
            'self_hosted_domain': guest_config.get('self_hosted_domain', '')
        }
        logger.info(f"[load-config] Guest 从 session 加载配置")
        return jsonify({'success': True, 'config': config})
    
    # 普通用户：从数据库加载
    config = {
        'api_key': current_user.api_key or '',
        'bili_cookie': current_user.bili_cookie or '',
        'llm_api_key': current_user.llm_api_key or '',
        'llm_api_url': current_user.llm_api_url or '',
        'llm_model': current_user.llm_model or '',
        'llm_prompt': current_user.llm_prompt or '',
        'use_self_hosted': current_user.use_self_hosted or False,
        'self_hosted_domain': current_user.self_hosted_domain or ''
    }
    
    logger.info(f"[load-config] 返回配置: api_key长度={len(config['api_key'])}, cookie长度={len(config['bili_cookie'])}")
    
    return jsonify({
        'success': True,
        'config': config
    })


# ==================== 历史记录 API ====================

def _get_guest_history():
    """获取 guest 历史记录列表"""
    return session.get('guest_history', [])

def _save_guest_history(history_list):
    """保存 guest 历史记录列表"""
    session['guest_history'] = history_list
    session.modified = True

@auth_bp.route('/api/history', methods=['GET'])
@login_required
def get_history():
    """获取用户历史记录"""
    try:
        # Guest 用户：从 session 获取
        if getattr(current_user, 'is_guest', False):
            history = _get_guest_history()
            logger.info(f"[history] Guest 从 session 获取历史记录，共 {len(history)} 条")
            return jsonify({'success': True, 'history': history})
        
        # 普通用户：从数据库获取
        items = HistoryItem.query.filter_by(user_id=current_user.id)\
            .order_by(HistoryItem.created_at.desc())\
            .limit(500)\
            .all()
        
        return jsonify({
            'success': True,
            'history': [item.to_dict() for item in items]
        })
    except Exception as e:
        logger.error(f"[history] 获取历史记录失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@auth_bp.route('/api/history', methods=['POST'])
@login_required
def save_history():
    """保存/更新历史记录"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '请求体为空'}), 400
        
        url = data.get('url', '')
        if not url:
            return jsonify({'success': False, 'error': '缺少视频URL'}), 400
        
        # Guest 用户：保存到 session
        if getattr(current_user, 'is_guest', False):
            history = _get_guest_history()
            
            # 生成唯一 ID（使用时间戳）
            import time
            new_id = int(time.time() * 1000)
            
            # 查找是否已存在
            existing_index = next((i for i, h in enumerate(history) if h.get('url') == url), None)
            
            item_data = {
                'id': existing_index is not None and history[existing_index].get('id') or new_id,
                'url': url,
                'title': data.get('title', ''),
                'owner': data.get('owner', ''),
                'cover': data.get('cover', ''),
                'bvid': data.get('bvid', ''),
                'duration': data.get('duration'),
                'pubdate': data.get('pubdate'),
                'tags': data.get('tags', []),
                'transcript': data.get('transcript', ''),
                'ai_result': data.get('ai_result', ''),
                'created_at': data.get('created_at') or time.strftime('%Y-%m-%d %H:%M:%S')
            }
            
            if existing_index is not None:
                history[existing_index] = item_data
            else:
                history.insert(0, item_data)  # 新记录插入到开头
            
            # 限制最多 500 条
            if len(history) > 500:
                history = history[:500]
            
            _save_guest_history(history)
            logger.info(f"[history] Guest 历史记录已保存到 session")
            return jsonify({'success': True, 'item': item_data})
        
        # 普通用户：保存到数据库
        existing = HistoryItem.query.filter_by(user_id=current_user.id, url=url).first()
        
        if existing:
            existing.title = data.get('title', existing.title)
            existing.owner = data.get('owner', existing.owner)
            existing.cover = data.get('cover', existing.cover)
            existing.bvid = data.get('bvid', existing.bvid)
            existing.duration = data.get('duration', existing.duration)
            existing.pubdate = data.get('pubdate', existing.pubdate)
            existing.tags = json.dumps(data.get('tags', [])) if data.get('tags') else existing.tags
            existing.transcript = data.get('transcript', existing.transcript)
            existing.ai_result = data.get('ai_result', existing.ai_result)
            item = existing
        else:
            item = HistoryItem(
                user_id=current_user.id,
                url=url,
                title=data.get('title', ''),
                owner=data.get('owner', ''),
                cover=data.get('cover', ''),
                bvid=data.get('bvid', ''),
                duration=data.get('duration'),
                pubdate=data.get('pubdate'),
                tags=json.dumps(data.get('tags', [])) if data.get('tags') else None,
                transcript=data.get('transcript', ''),
                ai_result=data.get('ai_result', '')
            )
            db.session.add(item)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'item': item.to_dict()
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"[history] 保存历史记录失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@auth_bp.route('/api/history/<int:item_id>', methods=['DELETE'])
@login_required
def delete_history(item_id):
    """删除单条历史记录"""
    try:
        # Guest 用户：从 session 删除
        if getattr(current_user, 'is_guest', False):
            history = _get_guest_history()
            history = [h for h in history if h.get('id') != item_id]
            _save_guest_history(history)
            logger.info(f"[history] Guest 删除历史记录 ID: {item_id}")
            return jsonify({'success': True})
        
        # 普通用户：从数据库删除
        item = HistoryItem.query.filter_by(id=item_id, user_id=current_user.id).first()
        if not item:
            return jsonify({'success': False, 'error': '记录不存在'}), 404
        
        db.session.delete(item)
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"[history] 删除历史记录失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@auth_bp.route('/api/history/clear', methods=['DELETE'])
@login_required
def clear_history():
    """清空用户所有历史记录"""
    try:
        # Guest 用户：清空 session
        if getattr(current_user, 'is_guest', False):
            _save_guest_history([])
            logger.info(f"[history] Guest 历史记录已清空")
            return jsonify({'success': True, 'message': '历史记录已清空'})
        
        # 普通用户：清空数据库
        HistoryItem.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
        
        return jsonify({'success': True, 'message': '历史记录已清空'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"[history] 清空历史记录失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@auth_bp.route('/api/history/by-url', methods=['DELETE'])
@login_required
def delete_history_by_url():
    """根据URL删除历史记录"""
    try:
        data = request.get_json()
        url = data.get('url', '') if data else ''
        
        if not url:
            return jsonify({'success': False, 'error': '缺少URL参数'}), 400
        
        # Guest 用户：从 session 删除
        if getattr(current_user, 'is_guest', False):
            history = _get_guest_history()
            original_len = len(history)
            history = [h for h in history if h.get('url') != url]
            deleted = original_len - len(history)
            _save_guest_history(history)
            logger.info(f"[history] Guest 根据URL删除历史记录，删除 {deleted} 条")
            return jsonify({'success': True, 'deleted': deleted})
        
        # 普通用户：从数据库删除
        deleted = HistoryItem.query.filter_by(user_id=current_user.id, url=url).delete()
        db.session.commit()
        
        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        db.session.rollback()
        logger.error(f"[history] 根据URL删除历史记录失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Chrome 插件管理 API ====================

@auth_bp.route('/api/user/extension/generate-token', methods=['POST'])
@login_required
def generate_extension_token():
    """生成/重新生成插件令牌"""
    import secrets
    from datetime import datetime
    
    # Guest 用户不允许使用插件
    if getattr(current_user, 'is_guest', False):
        return jsonify({'success': False, 'error': 'Guest 账户不支持插件功能'}), 403
    
    try:
        # 生成 64 字符的安全令牌
        new_token = secrets.token_urlsafe(48)[:64]
        current_user.extension_token = new_token
        current_user.extension_last_sync = None  # 重置同步时间
        db.session.commit()
        
        logger.info(f"[extension] 用户 {current_user.username} 生成了新的插件令牌")
        
        return jsonify({
            'success': True,
            'token': new_token,
            'message': '插件令牌已生成'
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"[extension] 生成插件令牌失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@auth_bp.route('/api/user/extension/status', methods=['GET'])
@login_required
def get_extension_status():
    """获取插件绑定状态"""
    # Guest 用户
    if getattr(current_user, 'is_guest', False):
        return jsonify({
            'success': True,
            'has_token': False,
            'token': None,
            'last_sync': None,
            'message': 'Guest 账户不支持插件功能'
        })
    
    has_token = bool(current_user.extension_token)
    last_sync = current_user.extension_last_sync.isoformat() if current_user.extension_last_sync else None
    
    return jsonify({
        'success': True,
        'has_token': has_token,
        'token': current_user.extension_token if has_token else None,
        'last_sync': last_sync,
        'message': '已绑定' if has_token else '未绑定'
    })


@auth_bp.route('/api/user/extension/unbind', methods=['DELETE'])
@login_required
def unbind_extension():
    """解绑插件（清除令牌）"""
    if getattr(current_user, 'is_guest', False):
        return jsonify({'success': False, 'error': 'Guest 账户不支持插件功能'}), 403
    
    try:
        current_user.extension_token = None
        current_user.extension_last_sync = None
        db.session.commit()
        
        logger.info(f"[extension] 用户 {current_user.username} 解绑了插件")
        
        return jsonify({
            'success': True,
            'message': '插件已解绑'
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"[extension] 解绑插件失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

