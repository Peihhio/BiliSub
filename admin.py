"""
BiliSub 管理员后台路由
处理用户管理和邀请码管理
"""
import secrets
import os
import shutil
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
from functools import wraps
from models import db, User, HistoryItem, SystemConfig

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    """管理员权限装饰器"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            return jsonify({'success': False, 'error': '需要管理员权限'}), 403
        return f(*args, **kwargs)
    return decorated_function


@admin_bp.route('/admin')
@admin_required
def admin_page():
    """管理后台页面"""
    return render_template('admin.html')


# ============ 用户管理 ============

@admin_bp.route('/api/admin/users', methods=['GET'])
@admin_required
def get_users():
    """获取用户列表"""
    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify({
        'success': True,
        'users': [u.to_dict() for u in users]
    })


@admin_bp.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@admin_required
def delete_user(user_id):
    """删除用户"""
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'success': False, 'error': '用户不存在'}), 404
    
    if user.is_admin:
        return jsonify({'success': False, 'error': '不能删除管理员账户'}), 400
    
    username = user.username
    
    # 删除用户相关的临时文件（如果有）
    try:
        user_temp_dir = os.path.join(os.path.dirname(__file__), 'temp', f'user_{user_id}')
        if os.path.exists(user_temp_dir):
            shutil.rmtree(user_temp_dir)
    except Exception as e:
        print(f'[WARNING] 清理用户临时文件失败: {e}')
    
    # 删除用户（级联删除历史记录）
    db.session.delete(user)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'用户 {username} 已删除'
    })


@admin_bp.route('/api/admin/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def reset_user_password(user_id):
    """重置用户密码"""
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'success': False, 'error': '用户不存在'}), 404
    
    data = request.get_json()
    new_password = data.get('new_password', '') if data else ''
    
    if not new_password or len(new_password) < 4:
        return jsonify({'success': False, 'error': '新密码长度至少 4 个字符'}), 400
    
    user.set_password(new_password)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'用户 {user.username} 的密码已重置'
    })


# ============ 邀请码管理 ============

@admin_bp.route('/api/admin/invite-code', methods=['GET'])
@admin_required
def get_invite_code():
    """获取当前邀请码及使用统计"""
    invite_code = SystemConfig.get('invite_code', '')
    invite_code_limit = int(SystemConfig.get('invite_code_limit', '0') or '0')
    invite_code_used = int(SystemConfig.get('invite_code_used', '0') or '0')
    
    return jsonify({
        'success': True,
        'invite_code': invite_code,
        'limit': invite_code_limit,
        'used': invite_code_used
    })


@admin_bp.route('/api/admin/invite-code', methods=['POST'])
@admin_required
def set_invite_code():
    """设置邀请码"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': '请求体为空'}), 400
    
    new_code = data.get('invite_code', '').strip()
    
    if not new_code:
        return jsonify({'success': False, 'error': '邀请码不能为空'}), 400
    
    if len(new_code) < 4:
        return jsonify({'success': False, 'error': '邀请码长度至少 4 个字符'}), 400
    
    SystemConfig.set('invite_code', new_code)
    
    return jsonify({
        'success': True,
        'message': '邀请码已更新',
        'invite_code': new_code
    })


@admin_bp.route('/api/admin/invite-code/regenerate', methods=['POST'])
@admin_required
def regenerate_invite_code():
    """重新生成随机邀请码并重置使用次数"""
    new_code = secrets.token_urlsafe(12)[:16]
    SystemConfig.set('invite_code', new_code)
    # 重置已使用次数
    SystemConfig.set('invite_code_used', '0')
    
    invite_code_limit = int(SystemConfig.get('invite_code_limit', '0') or '0')
    
    return jsonify({
        'success': True,
        'message': '邀请码已重新生成，使用次数已重置',
        'invite_code': new_code,
        'limit': invite_code_limit,
        'used': 0
    })


@admin_bp.route('/api/admin/invite-code/limit', methods=['POST'])
@admin_required
def set_invite_code_limit():
    """设置邀请码使用次数限制"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': '请求体为空'}), 400
    
    try:
        limit = int(data.get('limit', 0))
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': '次数限制必须是数字'}), 400
    
    if limit < 0:
        return jsonify({'success': False, 'error': '次数限制不能为负数'}), 400
    
    SystemConfig.set('invite_code_limit', str(limit))
    
    return jsonify({
        'success': True,
        'message': f'使用次数限制已设置为 {limit}' if limit > 0 else '已取消使用次数限制',
        'limit': limit
    })


# ============ 系统统计 ============

@admin_bp.route('/api/admin/stats', methods=['GET'])
@admin_required
def get_stats():
    """获取系统统计信息"""
    total_users = User.query.count()
    total_history = HistoryItem.query.count()
    
    return jsonify({
        'success': True,
        'stats': {
            'total_users': total_users,
            'total_history': total_history
        }
    })
