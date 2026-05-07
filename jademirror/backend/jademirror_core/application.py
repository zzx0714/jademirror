import base64
import json
import os
import secrets
import socket
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Blueprint, jsonify, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

PROFILE = 'web'

BASE_DIR = None
MODELS_DIR = None
AUTH_DB_PATH = None

bp = Blueprint('jademirror', __name__)


def configure_instance(instance_root: Path):
    global BASE_DIR, MODELS_DIR, AUTH_DB_PATH
    BASE_DIR = instance_root
    MODELS_DIR = instance_root / 'generated_models'
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    auth_db_env = (os.getenv('AUTH_DB_PATH') or '').strip()
    AUTH_DB_PATH = Path(auth_db_env) if auth_db_env else instance_root / 'jademirror_auth.db'


def refresh_settings(profile: str):
    global PROFILE
    global DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, QWEN_BASE_URL, QWEN_MODEL
    global REQUEST_TIMEOUT, REQUEST_MAX_RETRIES, RATE_LIMIT_PER_MINUTE
    global HUNYUAN3D_API_URL, HUNYUAN3D_ENABLE_TEXTURE, HUNYUAN3D_OCTREE_RESOLUTION, HUNYUAN3D_INFERENCE_STEPS
    global REPLICATE_API_TOKEN, REPLICATE_MODEL_VERSION
    global TC_3D_API_KEY, TC_3D_BASE_URL, ARK_API_KEY, ARK_BASE_URL, ARK_MODEL_ID
    global MESHY_API_KEY, MESHY_BASE_URL
    global DEEPSEEK_ALLOW_MOCK, AUTH_REQUIRED, AUTH_TOKEN_TTL_HOURS

    PROFILE = profile
    DEEPSEEK_BASE_URL = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com').rstrip('/')
    DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
    QWEN_BASE_URL = os.getenv('QWEN_BASE_URL', 'https://dashscope.aliyuncs.com').rstrip('/')
    QWEN_MODEL = os.getenv('QWEN_MODEL', 'qwen-image-2.0')
    REQUEST_TIMEOUT = float(os.getenv('REQUEST_TIMEOUT', '45'))
    REQUEST_MAX_RETRIES = int(os.getenv('REQUEST_MAX_RETRIES', '2'))
    RATE_LIMIT_PER_MINUTE = int(os.getenv('RATE_LIMIT_PER_MINUTE', '40'))
    HUNYUAN3D_API_URL = os.getenv('HUNYUAN3D_API_URL', '').rstrip('/')
    HUNYUAN3D_ENABLE_TEXTURE = os.getenv('HUNYUAN3D_ENABLE_TEXTURE', '0') == '1'
    HUNYUAN3D_OCTREE_RESOLUTION = int(os.getenv('HUNYUAN3D_OCTREE_RESOLUTION', '128'))
    HUNYUAN3D_INFERENCE_STEPS = int(os.getenv('HUNYUAN3D_INFERENCE_STEPS', '5'))
    REPLICATE_API_TOKEN = (os.getenv('REPLICATE_API_TOKEN') or '').strip()
    REPLICATE_MODEL_VERSION = os.getenv(
        'REPLICATE_MODEL_VERSION',
        'b1b9449a1277e10402781c5d41eb30c0a0683504fb23fab591ca9dfc2aabe1cb',
    )
    TC_3D_API_KEY = (os.getenv('TC_3D_API_KEY') or '').strip()
    TC_3D_BASE_URL = os.getenv('TC_3D_BASE_URL', 'https://api.ai3d.cloud.tencent.com')
    ARK_API_KEY = (os.getenv('ARK_API_KEY') or '').strip()
    ARK_BASE_URL = os.getenv('ARK_BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3')
    ARK_MODEL_ID = os.getenv('ARK_MODEL_ID', 'doubao-seed3d-2-0-260328')
    MESHY_API_KEY = (os.getenv('MESHY_API_KEY') or '').strip()
    MESHY_BASE_URL = os.getenv('MESHY_BASE_URL', 'https://api.meshy.ai')
    DEEPSEEK_ALLOW_MOCK = os.getenv('DEEPSEEK_ALLOW_MOCK', '0') == '1'
    AUTH_REQUIRED = os.getenv('AUTH_REQUIRED', '0') == '1'
    AUTH_TOKEN_TTL_HOURS = int(os.getenv('AUTH_TOKEN_TTL_HOURS', '168'))

request_hits = {}


def _server_listen_port():
    return int(os.getenv('PORT', '5000'))


def detect_lan_ipv4():
    """Return this machine's LAN IPv4 for phone access (not 127.0.0.1). Best-effort."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith('127.'):
            return ip
    except OSError:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM):
            addr = info[4][0]
            if addr and not addr.startswith('127.'):
                return addr
    except OSError:
        pass
    return ''


def suggested_api_base_url():
    """
    Base URL the mobile app should use (…/api). Set PUBLIC_API_BASE or
    JADEMIRROR_PUBLIC_API_BASE in .env for production or when auto-detect is wrong.
    """
    explicit = (os.getenv('PUBLIC_API_BASE') or os.getenv('JADEMIRROR_PUBLIC_API_BASE') or '').strip()
    if explicit:
        base = explicit.rstrip('/')
        if base.endswith('/api'):
            return base
        return f'{base}/api'
    ip = detect_lan_ipv4()
    port = _server_listen_port()
    if ip:
        return f'http://{ip}:{port}/api'
    return f'http://127.0.0.1:{port}/api'


def utc_now_ts():
    return int(datetime.now(timezone.utc).timestamp())


def utc_expire_ts(hours):
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp())


def db_connect():
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_db():
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                nickname TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assistant_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                weight REAL NOT NULL DEFAULT 0.5,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        columns = [row['name'] for row in conn.execute('PRAGMA table_info(assistant_memory)').fetchall()]
        if 'pinned' not in columns:
            conn.execute('ALTER TABLE assistant_memory ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0')
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assistant_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                stage TEXT NOT NULL,
                user_text TEXT NOT NULL,
                assistant_reply TEXT NOT NULL,
                next_action TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assistant_memory_digest (
                user_id INTEGER PRIMARY KEY,
                digest_text TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_assistant_memory_user ON assistant_memory(user_id, updated_at DESC)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_assistant_events_user ON assistant_events(user_id, created_at DESC)'
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at)')
        conn.commit()


def prune_expired_sessions(conn):
    conn.execute('DELETE FROM sessions WHERE expires_at < ?', (utc_now_ts(),))


def create_session(conn, user_id):
    token = secrets.token_urlsafe(36)
    created_at = utc_now_ts()
    expires_at = utc_expire_ts(AUTH_TOKEN_TTL_HOURS)
    conn.execute(
        'INSERT INTO sessions(token, user_id, expires_at, created_at) VALUES(?, ?, ?, ?)',
        (token, user_id, expires_at, created_at),
    )
    return token, expires_at


def sanitize_user(row):
    return {
        'id': row['id'],
        'username': row['username'],
        'nickname': row['nickname'],
    }


def extract_bearer_token():
    auth_header = request.headers.get('Authorization', '')
    if auth_header.lower().startswith('bearer '):
        return auth_header[7:].strip()
    return ''


def get_authenticated_user():
    token = extract_bearer_token()
    if not token:
        return None

    with db_connect() as conn:
        prune_expired_sessions(conn)
        row = conn.execute(
            """
            SELECT u.id, u.username, u.nickname, s.expires_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at >= ?
            LIMIT 1
            """,
            (token, utc_now_ts()),
        ).fetchone()
        conn.commit()

    if not row:
        return None

    return {
        'token': token,
        'expires_at': row['expires_at'],
        'user': sanitize_user(row),
    }


def require_auth():
    if not AUTH_REQUIRED:
        return {'user': {'id': 0, 'username': 'guest', 'nickname': 'Guest'}, 'token': '', 'expires_at': 0}, None

    auth_result = get_authenticated_user()
    if not auth_result:
        return None, json_error('请先登录后再继续。', 401)
    return auth_result, None


def json_error(message, status=400):
    return jsonify({'error': message}), status


def get_client_key(endpoint_name):
    remote_addr = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown')
    return f'{remote_addr}:{endpoint_name}'


def check_rate_limit(endpoint_name):
    key = get_client_key(endpoint_name)
    now = time.time()
    window = now - 60

    hits = request_hits.get(key, [])
    hits = [timestamp for timestamp in hits if timestamp > window]

    if len(hits) >= RATE_LIMIT_PER_MINUTE:
        request_hits[key] = hits
        return False

    hits.append(now)
    request_hits[key] = hits
    return True


def request_with_retry(method, url, *, headers=None, payload=None, params=None):
    last_error = None

    for attempt in range(REQUEST_MAX_RETRIES + 1):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=payload,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code >= 500 and attempt < REQUEST_MAX_RETRIES:
                time.sleep(0.4 * (attempt + 1))
                continue

            return response
        except requests.RequestException as error:
            last_error = error
            if attempt < REQUEST_MAX_RETRIES:
                time.sleep(0.4 * (attempt + 1))

    raise RuntimeError(str(last_error) if last_error else '请求失败')


def unique_items(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def deepseek_base_url_candidates(base_url):
    base = (base_url or 'https://api.deepseek.com').rstrip('/')
    candidates = [base]

    if base.endswith('/v1'):
        candidates.append(base[:-3])
    else:
        candidates.append(f'{base}/v1')

    return unique_items(candidates)


def deepseek_endpoint_candidates(base_url):
    return [f'{item}/chat/completions' for item in deepseek_base_url_candidates(base_url)]


def build_jade_guard_prompt(jade_context, match_reason=''):
    if not isinstance(jade_context, dict) or not jade_context:
        return ''

    traits = jade_context.get('traits') or {}
    if isinstance(traits, dict) and traits:
        trait_text = '，'.join([f'{key}:{value}' for key, value in traits.items()])
    else:
        trait_text = '暂无'

    return (
        '【身份锁定】\n'
        f'- 你是玉器：{jade_context.get("dynasty", "未知")}{jade_context.get("name", "无名玉器")}\n'
        f'- 玉器描述：{jade_context.get("description", "暂无")}\n'
        f'- 核心特征：{trait_text}\n'
        f'- 匹配理由：{match_reason or "用户与这件玉器的气质最契合"}\n\n'
        '你只能以这件玉器的身份回答，不可切换到其他玉器。'
    )


def call_deepseek_by_sdk(*, api_key, model, messages, max_tokens, temperature):
    if OpenAI is None:
        raise RuntimeError('openai SDK 未安装，无法使用 client.chat.completions.create 调用')

    last_error = None

    for base_url in deepseek_base_url_candidates(DEEPSEEK_BASE_URL):
        try:
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=REQUEST_TIMEOUT,
                max_retries=REQUEST_MAX_RETRIES,
            )
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            choices = response.choices or []
            if not choices:
                raise RuntimeError('DeepSeek SDK 返回 choices 为空')

            content = choices[0].message.content
            if isinstance(content, list):
                content = ''.join(
                    item.get('text', '') for item in content if isinstance(item, dict)
                )

            content = str(content or '').strip()
            if not content:
                raise RuntimeError('DeepSeek SDK 返回内容为空')

            return content
        except Exception as error:
            last_error = error

    raise RuntimeError(f'DeepSeek SDK 调用失败：{last_error}')


def call_deepseek_by_http(*, api_key, model, messages, max_tokens, temperature):
    payload = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
    }

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    last_error = None
    for target_url in deepseek_endpoint_candidates(DEEPSEEK_BASE_URL):
        try:
            response = request_with_retry('POST', target_url, headers=headers, payload=payload)

            if response.status_code >= 400:
                try:
                    detail = response.json()
                except ValueError:
                    detail = {'message': response.text}
                last_error = RuntimeError(f'DeepSeek 返回错误：{detail}')
                continue

            raw_data = response.json()
            content = parse_deepseek_content(raw_data)
            content = str(content or '').strip()
            if not content:
                last_error = RuntimeError('DeepSeek HTTP 返回内容为空')
                continue

            return content
        except RuntimeError as error:
            last_error = error

    raise RuntimeError(str(last_error) if last_error else 'DeepSeek HTTP 调用失败')


def build_mock_chat_reply(messages):
    last_user_message = ''
    for item in reversed(messages):
        if item.get('role') == 'user':
            last_user_message = item.get('content', '')
            break

    if not last_user_message:
        last_user_message = '今夜心绪'

    return f'我听见你提到“{last_user_message}”。若心有波澜，可先慢三息，再看眼前光影。'


def build_mock_image_data_url(prompt_text):
    safe_prompt = prompt_text.strip()[:28] or 'JadeMirror'
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0%" stop-color="#dbe9df"/><stop offset="100%" stop-color="#88a592"/></linearGradient></defs>'
        '<rect width="1024" height="1024" fill="#f5f1e6" rx="60"/>'
        '<circle cx="512" cy="512" r="300" fill="url(#g)"/>'
        '<circle cx="512" cy="512" r="138" fill="#f5f1e6"/>'
        f'<text x="512" y="862" text-anchor="middle" fill="#24463d" font-size="44">{safe_prompt}</text>'
        '</svg>'
    )
    return f'data:image/svg+xml;charset=utf-8,{quote(svg)}'


def parse_deepseek_content(data):
    choices = data.get('choices') or []
    if not choices:
        return ''

    message = choices[0].get('message') or {}
    content = message.get('content', '')

    if isinstance(content, list):
        return ''.join(item.get('text', '') for item in content if isinstance(item, dict))

    return str(content)


def fetch_memories(user_id, limit=8):
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT id, memory_type, content, pinned, weight, updated_at
            FROM assistant_memory
            WHERE user_id = ?
            ORDER BY pinned DESC, weight DESC, updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def save_memory(user_id, memory_type, content, weight=0.6):
    text = str(content or '').strip()
    if not text:
        return

    now_ts = utc_now_ts()
    with db_connect() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM assistant_memory
            WHERE user_id = ? AND memory_type = ? AND content = ?
            LIMIT 1
            """,
            (user_id, memory_type, text),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE assistant_memory
                SET weight = ?, updated_at = ?
                WHERE id = ?
                """,
                (float(weight), now_ts, existing['id']),
            )
        else:
            conn.execute(
                """
                INSERT INTO assistant_memory(user_id, memory_type, content, weight, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (user_id, memory_type, text, float(weight), now_ts, now_ts),
            )
        conn.commit()


def pin_memory(user_id, memory_id, pinned):
    with db_connect() as conn:
        cursor = conn.execute(
            """
            UPDATE assistant_memory
            SET pinned = ?, weight = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                1 if pinned else 0,
                1.0 if pinned else 0.62,
                utc_now_ts(),
                int(memory_id),
                int(user_id),
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_memory(user_id, memory_id):
    with db_connect() as conn:
        cursor = conn.execute(
            'DELETE FROM assistant_memory WHERE id = ? AND user_id = ?',
            (int(memory_id), int(user_id)),
        )
        conn.commit()
        return cursor.rowcount > 0


def clear_memories(user_id):
    with db_connect() as conn:
        conn.execute('DELETE FROM assistant_memory WHERE user_id = ?', (int(user_id),))
        conn.execute('DELETE FROM assistant_memory_digest WHERE user_id = ?', (int(user_id),))
        conn.commit()


def append_assistant_event(user_id, stage, user_text, assistant_reply, next_action):
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO assistant_events(user_id, stage, user_text, assistant_reply, next_action, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                str(stage or 'idle'),
                str(user_text or '')[:1000],
                str(assistant_reply or '')[:2000],
                str(next_action or 'free_chat'),
                utc_now_ts(),
            ),
        )
        conn.commit()


def fetch_recent_events(user_id, limit=6):
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT stage, user_text, assistant_reply, next_action, created_at
            FROM assistant_events
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    history = []
    for row in reversed(rows):
        history.append(
            {
                'stage': row['stage'],
                'user_text': row['user_text'],
                'assistant_reply': row['assistant_reply'],
                'next_action': row['next_action'],
            }
        )
    return history


def get_memory_digest(user_id):
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT digest_text, updated_at
            FROM assistant_memory_digest
            WHERE user_id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return ''
    return str(row['digest_text'] or '').strip()


def rebuild_memory_digest(user_id):
    memories = fetch_memories(user_id, limit=12)
    events = fetch_recent_events(user_id, limit=6)

    memory_lines = []
    for item in memories[:6]:
        memory_type = item.get('memory_type', 'preference')
        content = str(item.get('content') or '').strip()
        if not content:
            continue
        pin_mark = '★' if item.get('pinned') else ''
        memory_lines.append(f'{pin_mark}{memory_type}:{content[:40]}')

    event_lines = []
    for item in events[-3:]:
        action = str(item.get('next_action') or 'free_chat')
        user_text = str(item.get('user_text') or '').strip()
        if not user_text:
            continue
        event_lines.append(f'{action}:{user_text[:32]}')

    digest_parts = []
    if memory_lines:
        digest_parts.append('长期记忆=' + ' | '.join(memory_lines))
    if event_lines:
        digest_parts.append('近期轨迹=' + ' | '.join(event_lines))
    digest_text = '；'.join(digest_parts)[:900] or '暂无可用记忆摘要'

    now_ts = utc_now_ts()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO assistant_memory_digest(user_id, digest_text, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET digest_text=excluded.digest_text, updated_at=excluded.updated_at
            """,
            (user_id, digest_text, now_ts),
        )
        conn.commit()
    return digest_text


def extract_json_object(text):
    raw = str(text or '').strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = raw.find('{')
    end = raw.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return {}


def normalize_next_action(action):
    mapping = {
        'start_test': 'start_test',
        'continue_test': 'continue_test',
        'show_result': 'show_result',
        'go_chat': 'go_chat',
        'go_generate': 'go_generate',
        'go_gallery': 'go_gallery',
        'free_chat': 'free_chat',
        'save_work': 'save_work',
        'generate_jade': 'generate_jade',
        'delete_work': 'delete_work',
        'open_work': 'open_work',
        'start_gallery_tour': 'start_gallery_tour',
        'next_gallery_item': 'next_gallery_item',
        'prev_gallery_item': 'prev_gallery_item',
        'stop_gallery_tour': 'stop_gallery_tour',
    }
    key = str(action or '').strip().lower()
    return mapping.get(key, 'free_chat')


def route_for_action(action):
    route_map = {
        'start_test': '/test',
        'continue_test': '/test',
        'show_result': '/result',
        'go_chat': '/chat',
        'go_generate': '/generate',
        'go_gallery': '/gallery',
        'generate_jade': '/generate',
        'save_work': '/generate',
        'delete_work': '/gallery',
        'open_work': '/gallery',
        'start_gallery_tour': '/gallery',
        'next_gallery_item': '/gallery',
        'prev_gallery_item': '/gallery',
        'stop_gallery_tour': '/gallery',
    }
    return route_map.get(action, '')


def build_assistant_system_prompt(proactive_mode=False):
    mode_rules = (
        '5) 本轮是"空闲主动关怀模式"，先像朋友一样打个招呼聊两句，再自然地抛一个玉文化小知识或小问题，别让气氛冷下来。'
        if proactive_mode
        else '5) 优先接住用户的话题，像朋友聊天一样自然回应，然后顺带引导下一步。'
    )
    return (
        '【重要】你必须始终返回纯JSON格式，不要有任何JSON之外的文本！\n\n'
        '你是"玉灵童子"，一个从古玉里蹦出来的小精灵，是用户的AI管家和小伙伴。\n'
        '你不是一个正经八百的助手，你更像一个活泼、话多、偶尔犯二但很靠谱的朋友。\n\n'
        '核心人设：\n'
        '- 说话像朋友聊天，不要文绔绔的。用"嘿""哇""哈哈""诶"这种语气词，偶尔用点网络用语也行。\n'
        '- 不要用"吾""汝""阁下"这种古风称呼，直接说"你"就行。\n'
        '- 你对玉文化了如指掌——从良渚的玉琢到清代的翡翠，从和田玉的羊脂白到岖岩玉的青绿，从谷纹蒲纹到螭龙凤鸟，你都能聊。但聊知识的时候也要像朋友分享趣事一样，不要像老师上课。\n'
        '- 你会主动分享玉文化冷知识，比如"你知道汉代人为什么喜欢在玉上刻螭龙吗？因为他们觉得螭龙能通天！超酷的对吧"\n'
        '- 偶尔犯点小迷糊，比如"等等让我想想……哦对！你刚才说的那个……"\n'
        '- 用户难过的时候你会安慰，用户开心的时候你比他还嘿。\n\n'
        '你的目标：\n'
        '1) 像朋友一样引导用户完成：测试→匹配→对话→生成→展厅，但不要催，别像导游赶行程。\n'
        '2) 语气轻松、活泼、有温度，像微信聊天不像写文章。每次回复30-100字，简短有力。\n'
        '3) 用户跑题了？没关系，先聊开心了再顺回来。玉文化闲聊也是你的强项。\n'
        '4) 始终维持"玉灵童子"身份，不可自称模型或AI助手。\n'
        f'{mode_rules}\n\n'
        '【工具调用决策流程】\n'
        '第1步：理解用户意图\n'
        '- "开始测试" → 明确，但需要先询问版本\n'
        '- "测试" → 不明确，调用 ask_clarification 询问\n'
        '- "为什么古人喜欢玉" → 闲聊，不调用工具（tool_calls为空数组）\n\n'
        '第2步：检查前置条件\n'
        '- 生成玉 → 需要先完成测试，如果没完成就提示并询问\n'
        '- 保存到展厅 → 需要先生成图片，如果没生成就提示\n\n'
        '第3步：返回工具调用\n'
        '- 可以一次返回多个工具，如 [{"name": "navigate", "args": {...}}, {"name": "start_guided_test", "args": {}}]\n'
        '- 没有操作时返回空数组 []\n'
        '- 闲聊时必须返回空数组 []\n\n'
        '【可用工具详解】\n\n'
        '1. navigate - 页面跳转工具\n'
        '   作用：跳转到指定页面\n'
        '   参数：{route: "/test|/result|/chat|/generate|/gallery|/home"}\n'
        '   页面说明：\n'
        '     • /home - 首页，介绍应用功能\n'
        '     • /test - 照心测试页，用户在这里回答性格测试题\n'
        '     • /result - 结果页，展示匹配的古玉和性格分析\n'
        '     • /chat - 对话页，与匹配的古玉进行深度对话\n'
        '     • /generate - 生成页，生成用户的专属玉图像\n'
        '     • /gallery - 展厅页，展示用户收藏的所有专属玉作品\n'
        '   何时调用：\n'
        '     • 用户明确说"去测试""去展厅""回首页"等\n'
        '     • 用户说"与玉对话""聊聊""对话""跟玉聊天"等（跳转到/chat）\n'
        '     • 用户说"生成玉""生成图片"等（跳转到/generate）\n'
        '     • 需要配合其他操作时（如开始测试前先跳转到/test）\n'
        '   ⚠️ 重要：\n'
        '     • 跳转到/chat前，必须确保用户已完成测试并有匹配结果\n'
        '     • 如果用户说"对话"但未完成测试，先提示完成测试\n'
        '   示例：\n'
        '     • "去测试" → [{"name": "navigate", "args": {"route": "/test"}}]\n'
        '     • "去展厅看看" → [{"name": "navigate", "args": {"route": "/gallery"}}]\n'
        '     • "回首页" → [{"name": "navigate", "args": {"route": "/home"}}]\n'
        '     • "与玉对话" → [{"name": "navigate", "args": {"route": "/chat"}}]\n'
        '     • "聊聊" → [{"name": "navigate", "args": {"route": "/chat"}}]\n'
        '     • "跟玉聊天" → [{"name": "navigate", "args": {"route": "/chat"}}]\n\n'
        '2. start_guided_test - 开始引导测试\n'
        '   作用：启动AI语音引导的照心测试，你会逐题播报问题并听取用户答案\n'
        '   参数：{mode: "quick" 或 "deep"}  # quick=六问版, deep=完整版\n'
        '   前置条件：用户必须已经选择了测试版本（六问版或完整版）\n'
        '   何时调用：\n'
        '     • 用户说"开始测试"且已选择版本\n'
        '     • 如果当前不在测试页，需要先调用 navigate 跳转到 /test 页面\n'
        '   ⚠️ 重要：\n'
        '     • 如果用户只说"开始测试"没有指定版本，必须先用 ask_clarification 询问版本\n'
        '     • mode参数必须是 "quick"（六问版）或 "deep"（完整版）\n'
        '     • 调用此工具后，前端会自动播报第一题，你的reply中不要包含题目内容\n'
        '     • 你的reply应该只是确认开始，如"好嘞！六问版走起～那咱们就正式开始啦！"\n'
        '   示例：\n'
        '     • 用户："开始测试" → [{"name": "ask_clarification", "args": {"question": "你想选六问快速版还是完整深度版？"}}]\n'
        '     • 用户："六问版" → reply: "好嘞！六问版走起～那咱们就正式开始啦！", tool_calls: [{"name": "start_guided_test", "args": {"mode": "quick"}}]\n'
        '     • 用户："完整版" → reply: "好嘞！完整版走起～咱们慢慢聊！", tool_calls: [{"name": "start_guided_test", "args": {"mode": "deep"}}]\n\n'
        '3. finish_test - 完成测试\n'
        '   作用：结束测试，计算匹配结果，展示匹配的古玉\n'
        '   参数：无\n'
        '   何时调用：\n'
        '     • 所有测试题目已回答完毕\n'
        '     • 用户说"完成测试""看结果"\n'
        '   示例：\n'
        '     • "看结果" → [{"name": "finish_test", "args": {}}]\n\n'
        '4. record_answer - 记录测试答案\n'
        '   作用：记录用户对当前题目的答案，并自动进入下一题\n'
        '   参数：{answer: "A|B|C|D"}\n'
        '   何时调用：\n'
        '     • 用户在测试过程中回答了选项（A、B、C、D）\n'
        '     • 必须在引导测试激活时调用\n'
        '   ⚠️ 重要：\n'
        '     • answer参数必须是 "A"、"B"、"C" 或 "D"\n'
        '     • 调用后前端会自动记录答案并播报下一题\n'
        '     • 你的reply应该简短确认用户的选择，如"A选项！雕工巧夺天工...选得好，咱们继续～"\n'
        '     • 不要在reply中播报下一题，前端会自动处理\n'
        '   示例：\n'
        '     • 用户："A" → reply: "A选项！雕工巧夺天工，看来你是个细节控！选得好，咱们继续～", tool_calls: [{"name": "record_answer", "args": {"answer": "A"}}]\n'
        '     • 用户："选B" → reply: "B！造型奇特诡异，你喜欢独特的东西～继续下一题！", tool_calls: [{"name": "record_answer", "args": {"answer": "B"}}]\n\n'
        '5. generate_jade - 生成专属玉\n'
        '   作用：根据测试结果和用户情绪，使用AI生成用户的专属玉图像\n'
        '   参数：无\n'
        '   前置条件：必须已完成测试并有匹配结果\n'
        '   何时调用：\n'
        '     • 用户说"生成玉""生成我的玉""生成图片"\n'
        '     • 如果未完成测试，先提示用户完成测试\n'
        '   示例：\n'
        '     • "生成我的玉" → [{"name": "generate_jade", "args": {}}]\n'
        '     • 未完成测试时："生成玉" → [{"name": "ask_clarification", "args": {"question": "要生成专属玉，需要先完成照心测试哦。要开始测试吗？"}}]\n\n'
        '5. save_to_gallery - 保存到展厅\n'
        '   作用：将当前生成的专属玉图像保存到用户的个人展厅\n'
        '   参数：无\n'
        '   前置条件：必须已生成图片\n'
        '   何时调用：\n'
        '     • 用户说"保存""保存到展厅""收藏"\n'
        '     • 如果未生成图片，先提示用户生成\n'
        '   示例：\n'
        '     • "保存到展厅" → [{"name": "save_to_gallery", "args": {}}]\n\n'
        '6. start_gallery_tour - 开始展厅导览\n'
        '   作用：启动展厅语音导览，你会逐件介绍用户收藏的作品\n'
        '   参数：无\n'
        '   何时调用：\n'
        '     • 用户说"导览""介绍展厅""讲解作品"\n'
        '   示例：\n'
        '     • "导览展厅" → [{"name": "start_gallery_tour", "args": {}}]\n\n'
        '7. delete_gallery_work - 删除展厅作品\n'
        '   作用：删除展厅中的某件作品\n'
        '   参数：{index: 作品序号从1开始}\n'
        '   何时调用：\n'
        '     • 用户说"删除第X件""删除作品"\n'
        '   示例：\n'
        '     • "删除第2件" → [{"name": "delete_gallery_work", "args": {"index": 2}}]\n\n'
        '8. open_gallery_work - 查看展厅作品\n'
        '   作用：详细介绍展厅中的某件作品\n'
        '   参数：{index: 作品序号从1开始}\n'
        '   何时调用：\n'
        '     • 用户说"看第X件""介绍第X个"\n'
        '   示例：\n'
        '     • "看第1件" → [{"name": "open_gallery_work", "args": {"index": 1}}]\n\n'
        '9. ask_clarification - 询问澄清\n'
        '   作用：当用户意图不明确时，向用户询问更多信息\n'
        '   参数：{question: "要问用户的问题"}\n'
        '   何时调用：\n'
        '     • 用户的请求模糊不清，无法判断具体意图\n'
        '     • 需要用户提供额外信息才能执行操作\n'
        '   ⚠️ 注意：\n'
        '     • 简单的询问（如询问测试版本）不需要调用此工具，直接在reply中询问即可\n'
        '     • 只有当需要用户做重要决策或提供关键信息时才使用\n'
        '   示例：\n'
        '     • 用户："测试" → 不确定是想开始测试还是查看测试结果 → [{"name": "ask_clarification", "args": {"question": "你是想开始照心测试，还是查看之前的测试结果？"}}]\n'
        '     • 用户："生成" → 不确定是生成玉还是生成其他 → [{"name": "ask_clarification", "args": {"question": "你是想生成专属玉图像吗？"}}]\n\n'
        '10. 不调用工具（返回空数组）\n'
        '    何时使用：\n'
        '      • 用户在闲聊玉文化知识\n'
        '      • 用户问问题（如"为什么古人喜欢玉"）\n'
        '      • 纯粹的对话交流，不需要执行任何操作\n'
        '    示例：\n'
        '      • "为什么古人喜欢玉？" → tool_calls: []\n'
        '      • "你好" → tool_calls: []\n\n'
        '【重要规则】\n'
        '- 用户意图明确 → 直接调用对应工具\n'
        '- 用户意图不明确 → 使用 ask_clarification\n'
        '- 纯闲聊玉文化 → tool_calls 返回空数组 []\n'
        '- 回复中的数据必须来自 context，不能编造\n'
        '- 如果 context.test.questions 有题目，必须用那些题目\n'
        '- 不要自己发明题目、玉器名称等数据\n\n'
        '【输出格式】\n'
        '输出必须是纯JSON，不要有任何JSON之外的文本：\n'
        '{\n'
        '  "reply": "给用户说的话（30-100字，口语化、像朋友聊天）",\n'
        '  "tool_calls": [\n'
        '    {"name": "工具名", "args": {参数对象}}\n'
        '  ],\n'
        '  "memory": ["可写入长期记忆的短句，最多2条"]\n'
        '}\n\n'
        '【完整示例】\n\n'
        '示例1：用户说"开始测试"（需要询问版本）\n'
        '用户: "开始测试"\n'
        '分析：用户想测试，但没说版本 → 需要询问（纯对话，不需要工具）\n'
        '输出: {\n'
        '  "reply": "好嘞！咱们有两个版本：六问快速版（5分钟）和完整深度版（15分钟）。你想选哪个？",\n'
        '  "tool_calls": [],\n'
        '  "memory": []\n'
        '}\n\n'
        '示例2：用户选择"六问快速版"（开始测试）\n'
        '用户: "六问快速版"\n'
        '分析：用户已选择版本 → 开始六问版测试（前端会自动播报第一题）\n'
        '输出: {\n'
        '  "reply": "好嘞！六问版走起～那咱们就正式开始啦！",\n'
        '  "tool_calls": [\n'
        '    {"name": "start_guided_test", "args": {"mode": "quick"}}\n'
        '  ],\n'
        '  "memory": ["用户选择六问版测试"]\n'
        '}\n'
        '注意：用户可能说"六问快速版""六问版""快速版""六问的"等，都应该识别为选择quick模式\n\n'
        '示例3：用户说"为什么古人喜欢玉"（闲聊）\n'
        '用户: "为什么古人喜欢玉？"\n'
        '分析：纯粹的知识问答 → 不需要任何工具\n'
        '输出: {\n'
        '  "reply": "哈哈这个问题问得好！古人觉得玉有五德——仁、义、智、勇、洁。你看玉温润有光泽，就像君子的品德。而且玉很硬但不伤人，敲起来声音清脆悦耳，超有灵性的！",\n'
        '  "tool_calls": [],\n'
        '  "memory": ["用户对玉文化感兴趣"]\n'
        '}\n\n'
        '示例4：用户说"去展厅"（跳转页面）\n'
        '用户: "去展厅"\n'
        '分析：明确的跳转请求 → 跳转到展厅页\n'
        '输出: {\n'
        '  "reply": "好嘞！这就带你去展厅看看你的收藏～",\n'
        '  "tool_calls": [\n'
        '    {"name": "navigate", "args": {"route": "/gallery"}}\n'
        '  ],\n'
        '  "memory": []\n'
        '}\n\n'
        '示例5：用户说"生成我的玉"但未完成测试（需要提示）\n'
        '用户: "生成我的玉"\n'
        '分析：context显示未完成测试 → 提示并询问\n'
        '输出: {\n'
        '  "reply": "要生成专属玉，需要先完成照心测试哦。这样我才能了解你的性格，生成最适合你的玉。要开始测试吗？",\n'
        '  "tool_calls": [\n'
        '    {"name": "ask_clarification", "args": {"question": "要开始照心测试吗？"}}\n'
        '  ],\n'
        '  "memory": ["用户想生成专属玉"]\n'
        '}\n\n'
        '示例6：用户说"导览展厅"（开始导览）\n'
        '用户: "导览展厅"\n'
        '分析：明确的导览请求 → 开始导览\n'
        '输出: {\n'
        '  "reply": "好嘞！那我就带你逐件欣赏你的收藏～",\n'
        '  "tool_calls": [\n'
        '    {"name": "start_gallery_tour", "args": {}}\n'
        '  ],\n'
        '  "memory": []\n'
        '}\n\n'
        '示例7：用户在测试中回答"A"（记录答案）\n'
        '用户: "A"\n'
        '分析：用户在测试中选择了A选项 → 记录答案（前端会自动播报下一题）\n'
        '输出: {\n'
        '  "reply": "A选项！雕工巧夺天工，看来你是个细节控！选得好，咱们继续～",\n'
        '  "tool_calls": [\n'
        '    {"name": "record_answer", "args": {"answer": "A"}}\n'
        '  ],\n'
        '  "memory": []\n'
        '}\n\n'
        '【再次强调】\n'
        '1. 你的输出必须是纯JSON，不要有任何其他文本\n'
        '2. tool_calls 字段必须存在，即使是空数组 []\n'
        '3. 闲聊时 tool_calls 必须是空数组 []，不要省略这个字段\n'
        '4. 需要操作时 tool_calls 必须包含具体的工具调用\n'
        '5. 每个工具调用必须有 name 和 args 两个字段\n'
        '6. memory 字段必须存在，即使是空数组 []\n'
    )


def build_assistant_user_prompt(*, stage, user_text, context, memories, events, profile, memory_digest=''):
    payload = {
        'stage': stage,
        'user_text': user_text,
        'context': context,
        'profile': profile,
        'memory_digest': memory_digest,
        'long_term_memories': memories,
        'recent_events': events,
    }
    return json.dumps(payload, ensure_ascii=False)


def extract_qwen_image_url(data, headers):
    # Synchronous multimodal response format for qwen-image-2.0 series.
    output = data.get('output') or {}
    choices = output.get('choices') or []
    if choices and isinstance(choices, list):
        message = (choices[0] or {}).get('message') or {}
        content = message.get('content') or []
        if content and isinstance(content, list):
            first = content[0] or {}
            image = first.get('image') or first.get('url') or first.get('image_url')
            if image:
                return image

    output = data.get('output') or {}

    results = output.get('results') or []
    if results and isinstance(results, list):
        first = results[0] or {}
        url = first.get('url') or first.get('image_url')
        if url:
            return url

    task_id = output.get('task_id')
    if task_id:
        return poll_qwen_task(task_id, headers)

    data_results = (data.get('data') or {}).get('results') or []
    if data_results and isinstance(data_results, list):
        first = data_results[0] or {}
        url = first.get('url') or first.get('image_url')
        if url:
            return url

    return ''


def is_qwen_async_model(model_name):
    normalized = str(model_name or '').strip().lower()
    return normalized == 'qwen-image' or normalized.startswith('qwen-image-plus')


@bp.post('/api/auth/register')
def auth_register():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    nickname = str(data.get('nickname', '')).strip() or username
    password = str(data.get('password', ''))

    if len(username) < 3:
        return json_error('用户名至少 3 个字符。')

    if len(password) < 6:
        return json_error('密码至少 6 位。')

    now_ts = utc_now_ts()

    with db_connect() as conn:
        exists = conn.execute('SELECT id FROM users WHERE username = ? LIMIT 1', (username,)).fetchone()
        if exists:
            return json_error('用户名已存在，请更换用户名。', 409)

        password_hash = generate_password_hash(password)
        cursor = conn.execute(
            'INSERT INTO users(username, nickname, password_hash, created_at) VALUES(?, ?, ?, ?)',
            (username, nickname, password_hash, now_ts),
        )
        user_id = cursor.lastrowid
        token, expires_at = create_session(conn, user_id)

        user_row = conn.execute(
            'SELECT id, username, nickname FROM users WHERE id = ? LIMIT 1',
            (user_id,),
        ).fetchone()
        conn.commit()

    return jsonify({'token': token, 'expires_at': expires_at, 'user': sanitize_user(user_row)})


@bp.post('/api/auth/guest')
def auth_guest():
    """Create a guest account with random credentials and return a token."""
    now_ts = utc_now_ts()
    suffix = secrets.token_hex(8)
    username = f'guest_{suffix}'
    nickname = f'游客_{suffix[:6]}'
    password = secrets.token_urlsafe(16)

    with db_connect() as conn:
        password_hash = generate_password_hash(password)
        cursor = conn.execute(
            'INSERT INTO users(username, nickname, password_hash, created_at) VALUES(?, ?, ?, ?)',
            (username, nickname, password_hash, now_ts),
        )
        user_id = cursor.lastrowid
        token, expires_at = create_session(conn, user_id)

        user_row = conn.execute(
            'SELECT id, username, nickname FROM users WHERE id = ? LIMIT 1',
            (user_id,),
        ).fetchone()
        conn.commit()

    return jsonify({'token': token, 'expires_at': expires_at, 'user': sanitize_user(user_row)})


@bp.post('/api/auth/login')
def auth_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))

    if not username or not password:
        return json_error('请输入用户名和密码。')

    with db_connect() as conn:
        prune_expired_sessions(conn)
        user_row = conn.execute(
            'SELECT id, username, nickname, password_hash FROM users WHERE username = ? LIMIT 1',
            (username,),
        ).fetchone()

        if not user_row or not check_password_hash(user_row['password_hash'], password):
            return json_error('用户名或密码错误。', 401)

        token, expires_at = create_session(conn, user_row['id'])
        conn.commit()

    return jsonify({'token': token, 'expires_at': expires_at, 'user': sanitize_user(user_row)})


@bp.get('/api/auth/me')
def auth_me():
    auth_result, error = require_auth()
    if error:
        return error

    return jsonify({'user': auth_result['user'], 'expires_at': auth_result['expires_at']})


@bp.post('/api/auth/logout')
def auth_logout():
    token = extract_bearer_token()
    if not token:
        return json_error('未提供登录令牌。', 401)

    with db_connect() as conn:
        conn.execute('DELETE FROM sessions WHERE token = ?', (token,))
        conn.commit()

    return jsonify({'ok': True})


@bp.post('/api/auth/update-password')
def auth_update_password():
    token = extract_bearer_token()
    if not token:
        return json_error('请先登录后再继续。', 401)

    data = request.get_json(silent=True) or {}
    current_password = str(data.get('currentPassword', '')).strip()
    new_password = str(data.get('newPassword', '')).strip()

    if not current_password or not new_password:
        return json_error('请填写当前密码和新密码。')

    if len(new_password) < 6:
        return json_error('新密码至少 6 位。')

    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.password_hash
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at >= ?
            LIMIT 1
            """,
            (token, utc_now_ts()),
        ).fetchone()
        conn.commit()

    if not row:
        return json_error('登录已过期，请重新登录。', 401)

    user_id = row['id']
    stored_hash = row['password_hash']

    if not check_password_hash(stored_hash, current_password):
        return json_error('当前密码不正确。', 403)

    new_hash = generate_password_hash(new_password)
    with db_connect() as conn:
        conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (new_hash, user_id))
        conn.commit()

    return jsonify({'ok': True})


@bp.post('/api/auth/update-nickname')
def auth_update_nickname():
    token = extract_bearer_token()
    if not token:
        return json_error('请先登录后再继续。', 401)

    data = request.get_json(silent=True) or {}
    nickname = str(data.get('nickname', '')).strip()

    if not nickname:
        return json_error('昵称不能为空。')

    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT u.id
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at >= ?
            LIMIT 1
            """,
            (token, utc_now_ts()),
        ).fetchone()
        conn.commit()

    if not row:
        return json_error('登录已过期，请重新登录。', 401)

    user_id = row['id']
    with db_connect() as conn:
        conn.execute('UPDATE users SET nickname = ? WHERE id = ?', (nickname, user_id))
        conn.commit()

    return jsonify({'ok': True})


def poll_qwen_task(task_id, headers):
    task_url = f'{QWEN_BASE_URL}/api/v1/tasks/{task_id}'

    for _ in range(20):
        response = request_with_retry('GET', task_url, headers=headers)
        if response.status_code >= 400:
            return ''

        data = response.json()
        output = data.get('output') or {}
        status = output.get('task_status') or data.get('task_status')

        if status == 'SUCCEEDED':
            results = output.get('results') or []
            if results and isinstance(results, list):
                return results[0].get('url') or results[0].get('image_url') or ''
            return ''

        if status in {'FAILED', 'CANCELED'}:
            return ''

        time.sleep(1.2)

    return ''


@bp.get('/api')
def api_root():
    """避免客户端/浏览器只访问 /api 时 404；真实接口均在 /api/... 下。"""
    return jsonify(
        {
            'ok': True,
            'service': 'jademirror-flask-proxy',
            'profile': PROFILE,
            'health': '/api/health',
            'hint': '请使用完整路径，例如 POST /api/auth/register',
        }
    )


@bp.get('/api/health')
def health():
    deepseek_api_key = (os.getenv('DEEPSEEK_API_KEY') or '').strip()
    qwen_api_key = (os.getenv('QWEN_API_KEY') or '').strip()
    api_base = suggested_api_base_url()
    lan_ip = detect_lan_ipv4()
    payload = {
            'status': 'ok',
            'service': 'jademirror-flask-proxy',
            'profile': PROFILE,
            'server_role': 'web' if PROFILE == 'web' else 'mobile_app',
            'auth_required': AUTH_REQUIRED,
            'auth_db_path': str(AUTH_DB_PATH),
            'suggested_api_base': api_base,
            'lan_ipv4': lan_ip or None,
            'deepseek_configured': bool(deepseek_api_key),
            'deepseek_base_url': DEEPSEEK_BASE_URL,
            'deepseek_sdk_enabled': bool(OpenAI),
            'deepseek_mock_enabled': DEEPSEEK_ALLOW_MOCK,
            'qwen_configured': bool(qwen_api_key),
            'hunyuan3d_configured': bool(HUNYUAN3D_API_URL),
            'tencent_3d_configured': bool(TC_3D_API_KEY),
            'volcengine_3d_configured': bool(ARK_API_KEY),
            'meshy_3d_configured': bool(MESHY_API_KEY),
            'replicate_3d_configured': bool(REPLICATE_API_TOKEN),
    }
    if PROFILE == 'app':
        payload['mobile_hint'] = (
            '在 App「我 → 服务器地址」填写 suggested_api_base；不要用 10.0.2.2（仅模拟器）。'
        )
    else:
        payload['cors_hint'] = '浏览器前端：Vite 代理 /api 到本进程；ALLOWED_ORIGINS 见 jademirror/backend/.env'
    return jsonify(payload)


@bp.post('/api/deepseek/chat')
def deepseek_chat():
    if not check_rate_limit('deepseek-chat'):
        return json_error('请求过于频繁，请稍后再试。', 429)

    _, auth_error = require_auth()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}
    messages = data.get('messages') or []
    system_prompt = data.get('systemPrompt') or ''
    jade_context = data.get('jadeContext') or {}
    match_reason = data.get('matchReason') or ''

    if not isinstance(messages, list) or not messages:
        return json_error('messages 不能为空。')

    deepseek_api_key = (os.getenv('DEEPSEEK_API_KEY') or '').strip()
    if not deepseek_api_key:
        if DEEPSEEK_ALLOW_MOCK:
            mock_content = build_mock_chat_reply(messages)
            return jsonify({'content': mock_content, 'mock': True})
        return json_error('DeepSeek API Key 未配置或未读取到，请检查当前实例目录下的 .env 并重启后端。', 500)

    payload_messages = []
    jade_guard_prompt = build_jade_guard_prompt(jade_context, match_reason)

    full_system_prompt = ''
    if jade_guard_prompt and system_prompt:
        full_system_prompt = f'{jade_guard_prompt}\n\n{system_prompt}'
    elif jade_guard_prompt:
        full_system_prompt = jade_guard_prompt
    else:
        full_system_prompt = system_prompt

    if full_system_prompt:
        payload_messages.append({'role': 'system', 'content': full_system_prompt})

    payload_messages.extend(messages)

    model = data.get('model') or DEEPSEEK_MODEL
    max_tokens = int(data.get('max_tokens', 300))
    temperature = float(data.get('temperature', 0.8))

    sdk_error = None

    try:
        content = call_deepseek_by_sdk(
            api_key=deepseek_api_key,
            model=model,
            messages=payload_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except RuntimeError as error:
        sdk_error = error

    if sdk_error is not None:
        try:
            content = call_deepseek_by_http(
                api_key=deepseek_api_key,
                model=model,
                messages=payload_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as error:
            return json_error(f'DeepSeek 调用失败：SDK={sdk_error}; HTTP={error}', 502)

    return jsonify({'content': content, 'mock': False})


@bp.post('/api/assistant/turn')
def assistant_turn():
    if not check_rate_limit('assistant-turn'):
        return json_error('请求过于频繁，请稍后再试。', 429)

    auth_result, auth_error = require_auth()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}
    user_text = str(data.get('text', '')).strip()
    stage = str(data.get('stage', 'idle')).strip() or 'idle'
    context = data.get('context') if isinstance(data.get('context'), dict) else {}
    privacy_mode = bool(context.get('privacy_mode', False))
    memory_enabled = not privacy_mode

    if not user_text:
        return json_error('text 不能为空。')

    user = auth_result['user']
    user_id = user['id']
    deepseek_api_key = (os.getenv('DEEPSEEK_API_KEY') or '').strip()
    memories = fetch_memories(user_id, limit=8) if memory_enabled else []
    events = fetch_recent_events(user_id, limit=6) if memory_enabled else []
    memory_digest = get_memory_digest(user_id) if memory_enabled else ''
    profile = {
        'id': user.get('id'),
        'username': user.get('username'),
        'nickname': user.get('nickname'),
    }

    if not deepseek_api_key:
        reply = f'我听见你说“{user_text}”。先由我带你稳稳往前，我们先从照心测试开始，好吗？'
        next_action = 'start_test'
        if memory_enabled:
            append_assistant_event(user_id, stage, user_text, reply, next_action)
            save_memory(user_id, 'preference', user_text, weight=0.45)
            rebuild_memory_digest(user_id)
        return jsonify(
            {
                'reply': reply,
                'next_action': next_action,
                'action_payload': {},
                'suggested_route': route_for_action(next_action),
                'memory_saved': memory_enabled,
                'privacy_mode': privacy_mode,
            }
        )

    prompt_messages = [
        {'role': 'system', 'content': build_assistant_system_prompt()},
        {
            'role': 'user',
            'content': build_assistant_user_prompt(
                stage=stage,
                user_text=user_text,
                context=context,
                memories=memories,
                events=events,
                profile=profile,
                memory_digest=memory_digest,
            ),
        },
    ]

    try:
        output = call_deepseek_by_sdk(
            api_key=deepseek_api_key,
            model=DEEPSEEK_MODEL,
            messages=prompt_messages,
            max_tokens=420,
            temperature=0.3,  # 降低temperature提高JSON格式稳定性
        )
    except RuntimeError as sdk_error:
        try:
            output = call_deepseek_by_http(
                api_key=deepseek_api_key,
                model=DEEPSEEK_MODEL,
                messages=prompt_messages,
                max_tokens=420,
                temperature=0.3,  # 降低temperature提高JSON格式稳定性
            )
        except Exception as http_error:
            return json_error(f'玉灵童子暂时无法回应：SDK={sdk_error}; HTTP={http_error}', 502)

    # 🔍 DEBUG: 打印AI原始输出
    print('=' * 80)
    print('🤖 AI原始输出:')
    print(output)
    print('=' * 80)

    parsed = extract_json_object(output)
    
    # 🔍 DEBUG: 打印解析后的JSON
    print('📦 解析后的JSON:')
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    print('=' * 80)
    reply = str(parsed.get('reply') or '').strip()
    if not reply:
        reply = f'我听见你说“{user_text}”。我们继续一步步来，我会一直陪着你。'
    next_action = normalize_next_action(parsed.get('next_action'))
    action_payload = parsed.get('action_payload') if isinstance(parsed.get('action_payload'), dict) else {}

    memory_items = parsed.get('memory')
    memory_saved = False
    if memory_enabled:
        if isinstance(memory_items, list):
            for item in memory_items[:2]:
                text = str(item or '').strip()
                if text:
                    save_memory(user_id, 'preference', text, weight=0.7)
                    memory_saved = True
        else:
            save_memory(user_id, 'preference', user_text, weight=0.5)
            memory_saved = True

    emotion = str(parsed.get('emotion') or '').strip()
    if emotion and memory_enabled:
        save_memory(user_id, 'emotion', emotion, weight=0.65)

    digest = ''
    if memory_enabled:
        append_assistant_event(user_id, stage, user_text, reply, next_action)
        digest = rebuild_memory_digest(user_id)
    
    # 新格式：tool_calls 数组
    tool_calls = parsed.get('tool_calls')
    if tool_calls and isinstance(tool_calls, list):
        # 使用新格式
        print('✅ 使用新格式 tool_calls:', tool_calls)
        pass
    else:
        # 兼容旧格式：next_action
        print('⚠️ 未找到 tool_calls，使用旧格式兼容')
        tool_calls = []
        if next_action and next_action != 'free_chat':
            tool_calls.append({
                'name': next_action,
                'args': action_payload
            })
            print(f'🔄 转换旧格式: next_action={next_action}, action_payload={action_payload}')
    
    print('📤 最终返回的 tool_calls:', tool_calls)
    print('=' * 80)
    
    return jsonify(
        {
            'reply': reply,
            'tool_calls': tool_calls,
            'memory_saved': memory_saved,
            'emotion': emotion,
            'memory_digest': digest,
            'privacy_mode': privacy_mode,
        }
    )


@bp.get('/api/assistant/memories')
def assistant_memories():
    auth_result, auth_error = require_auth()
    if auth_error:
        return auth_error

    user_id = auth_result['user']['id']
    memories = fetch_memories(user_id, limit=20)
    events = fetch_recent_events(user_id, limit=20)
    digest = get_memory_digest(user_id) or rebuild_memory_digest(user_id)
    return jsonify({'memories': memories, 'events': events, 'digest': digest})


@bp.patch('/api/assistant/memories/<int:memory_id>/pin')
def assistant_pin_memory(memory_id):
    auth_result, auth_error = require_auth()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}
    pinned = bool(data.get('pinned', True))
    user_id = auth_result['user']['id']
    ok = pin_memory(user_id, memory_id, pinned)
    if not ok:
        return json_error('记忆不存在或无权限操作。', 404)

    digest = rebuild_memory_digest(user_id)
    memories = fetch_memories(user_id, limit=20)
    return jsonify({'ok': True, 'digest': digest, 'memories': memories})


@bp.delete('/api/assistant/memories/<int:memory_id>')
def assistant_delete_memory(memory_id):
    auth_result, auth_error = require_auth()
    if auth_error:
        return auth_error

    user_id = auth_result['user']['id']
    ok = delete_memory(user_id, memory_id)
    if not ok:
        return json_error('记忆不存在或无权限操作。', 404)

    digest = rebuild_memory_digest(user_id)
    memories = fetch_memories(user_id, limit=20)
    return jsonify({'ok': True, 'digest': digest, 'memories': memories})


@bp.get('/api/assistant/memories/export')
def assistant_export_memories():
    auth_result, auth_error = require_auth()
    if auth_error:
        return auth_error

    user = auth_result['user']
    user_id = user['id']
    memories = fetch_memories(user_id, limit=200)
    events = fetch_recent_events(user_id, limit=100)
    digest = get_memory_digest(user_id) or rebuild_memory_digest(user_id)
    payload = {
        'profile': {
            'id': user.get('id'),
            'username': user.get('username'),
            'nickname': user.get('nickname'),
        },
        'digest': digest,
        'memory_count': len(memories),
        'event_count': len(events),
        'memories': memories,
        'events': events,
        'exported_at': utc_now_ts(),
    }
    return jsonify(payload)


@bp.delete('/api/assistant/memories')
def assistant_clear_memories():
    auth_result, auth_error = require_auth()
    if auth_error:
        return auth_error

    user_id = auth_result['user']['id']
    clear_memories(user_id)
    return jsonify({'ok': True, 'digest': '', 'memories': []})


@bp.post('/api/assistant/proactive')
def assistant_proactive():
    if not check_rate_limit('assistant-proactive'):
        return json_error('请求过于频繁，请稍后再试。', 429)

    auth_result, auth_error = require_auth()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}
    stage = str(data.get('stage', 'idle')).strip() or 'idle'
    context = data.get('context') if isinstance(data.get('context'), dict) else {}
    privacy_mode = bool(context.get('privacy_mode', False))
    memory_enabled = not privacy_mode

    user = auth_result['user']
    user_id = user['id']
    deepseek_api_key = (os.getenv('DEEPSEEK_API_KEY') or '').strip()
    memories = fetch_memories(user_id, limit=8) if memory_enabled else []
    events = fetch_recent_events(user_id, limit=6) if memory_enabled else []
    digest = (get_memory_digest(user_id) or rebuild_memory_digest(user_id)) if memory_enabled else ''
    profile = {
        'id': user.get('id'),
        'username': user.get('username'),
        'nickname': user.get('nickname'),
    }

    if not deepseek_api_key:
        reply = '我在这里陪着你。若你愿意，我们聊聊“君子比德于玉”，也可以继续下一步体验。'
        next_action = 'free_chat'
        if memory_enabled:
            append_assistant_event(user_id, stage, '[proactive]', reply, next_action)
            rebuild_memory_digest(user_id)
        return jsonify(
            {
                'reply': reply,
                'next_action': next_action,
                'action_payload': {},
                'suggested_route': route_for_action(next_action),
                'privacy_mode': privacy_mode,
            }
        )

    prompt_messages = [
        {'role': 'system', 'content': build_assistant_system_prompt(proactive_mode=True)},
        {
            'role': 'user',
            'content': build_assistant_user_prompt(
                stage=stage,
                user_text='[idle_nudge]',
                context=context,
                memories=memories,
                events=events,
                profile=profile,
                memory_digest=digest,
            ),
        },
    ]

    try:
        output = call_deepseek_by_sdk(
            api_key=deepseek_api_key,
            model=DEEPSEEK_MODEL,
            messages=prompt_messages,
            max_tokens=320,
            temperature=0.7,
        )
    except RuntimeError as sdk_error:
        try:
            output = call_deepseek_by_http(
                api_key=deepseek_api_key,
                model=DEEPSEEK_MODEL,
                messages=prompt_messages,
                max_tokens=320,
                temperature=0.7,
            )
        except Exception as http_error:
            return json_error(f'主动关怀生成失败：SDK={sdk_error}; HTTP={http_error}', 502)

    parsed = extract_json_object(output)
    reply = str(parsed.get('reply') or '').strip() or '我在你身边。想继续照心流程，还是先聊聊你今天的心绪？'
    emotion = str(parsed.get('emotion') or '').strip()
    next_action = normalize_next_action(parsed.get('next_action'))
    action_payload = parsed.get('action_payload') if isinstance(parsed.get('action_payload'), dict) else {}
    if memory_enabled:
        append_assistant_event(user_id, stage, '[proactive]', reply, next_action)
    if emotion and memory_enabled:
        save_memory(user_id, 'emotion', emotion, weight=0.6)
    digest = rebuild_memory_digest(user_id) if memory_enabled else ''

    return jsonify(
        {
            'reply': reply,
            'next_action': next_action,
            'action_payload': action_payload,
            'suggested_route': route_for_action(next_action),
            'emotion': emotion,
            'memory_digest': digest,
            'privacy_mode': privacy_mode,
        }
    )


@bp.post('/api/qwen/image')
def qwen_image():
    if not check_rate_limit('qwen-image'):
        return json_error('请求过于频繁，请稍后再试。', 429)

    _, auth_error = require_auth()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}
    prompt = data.get('prompt', '').strip()

    if not prompt:
        return json_error('prompt 不能为空。')

    qwen_api_key = (os.getenv('QWEN_API_KEY') or '').strip()
    if not qwen_api_key:
        return jsonify({'image_url': build_mock_image_data_url(prompt), 'mock': True})

    model = str(data.get('model') or QWEN_MODEL).strip()
    size = str(data.get('size', '1024*1024')).strip() or '1024*1024'
    negative_prompt = str(data.get('negative_prompt', '')).strip()

    headers = {
        'Authorization': f'Bearer {qwen_api_key}',
        'Content-Type': 'application/json',
    }

    if is_qwen_async_model(model):
        # Async image endpoint supports qwen-image/qwen-image-plus family.
        payload = {
            'model': model,
            'input': {
                'prompt': prompt,
            },
            'parameters': {
                'size': size,
                'n': int(data.get('n', 1) or 1),
            },
        }
        if negative_prompt:
            payload['input']['negative_prompt'] = negative_prompt

        headers['X-DashScope-Async'] = 'enable'
        target_url = f'{QWEN_BASE_URL}/api/v1/services/aigc/text2image/image-synthesis'
    else:
        # qwen-image-2.0 and newer high-end variants should use sync multimodal endpoint.
        payload = {
            'model': model,
            'input': {
                'messages': [
                    {
                        'role': 'user',
                        'content': [
                            {'text': prompt},
                        ],
                    }
                ]
            },
            'parameters': {
                'size': size,
            },
        }
        if negative_prompt:
            payload['parameters']['negative_prompt'] = negative_prompt
        target_url = f'{QWEN_BASE_URL}/api/v1/services/aigc/multimodal-generation/generation'

    try:
        response = request_with_retry('POST', target_url, headers=headers, payload=payload)
    except RuntimeError as error:
        return json_error(f'Qwen 请求失败：{error}', 502)

    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = {'message': response.text}

        message = f'Qwen 返回错误：{detail}'
        if isinstance(detail, dict):
            detail_message = str(detail.get('message', ''))
            if 'url error' in detail_message.lower():
                message = (
                    f'{message}；通常是模型与端点不匹配。'
                    f'当前使用 model={model}，已选择接口={target_url}'
                )

        return json_error(message, response.status_code)

    raw_data = response.json()
    image_url = extract_qwen_image_url(raw_data, headers)

    if not image_url:
        return json_error('Qwen 返回成功但未拿到图片地址。', 502)

    return jsonify({'image_url': image_url, 'mock': False})


def _image_to_base64(image_url, image_base64):
    if image_base64:
        if ',' in image_base64:
            return image_base64.split(',', 1)[1]
        return image_base64
    if image_url:
        if image_url.startswith('data:'):
            if ',' in image_url:
                return image_url.split(',', 1)[1]
            return image_url
        resp = requests.get(image_url, timeout=30)
        resp.raise_for_status()
        return base64.b64encode(resp.content).decode()
    return ''


def _generate_3d_hunyuan(image_b64):
    url = f'{HUNYUAN3D_API_URL}/generate'
    payload = {
        'image': image_b64,
        'texture': HUNYUAN3D_ENABLE_TEXTURE,
        'octree_resolution': HUNYUAN3D_OCTREE_RESOLUTION,
        'num_inference_steps': HUNYUAN3D_INFERENCE_STEPS,
        'guidance_scale': 5.0,
        'type': 'glb',
    }
    resp = requests.post(url, json=payload, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(f'Hunyuan3D 返回 HTTP {resp.status_code}: {resp.text[:200]}')
    model_id = secrets.token_hex(8)
    filename = f'{model_id}.glb'
    filepath = MODELS_DIR / filename
    with open(filepath, 'wb') as f:
        f.write(resp.content)
    return jsonify({
        'model_url': f'/api/3d/models/{filename}',
        'model_id': model_id,
        'service': 'hunyuan3d',
        'textured': HUNYUAN3D_ENABLE_TEXTURE,
    })


def _generate_3d_replicate(image_b64):
    data_uri = f'data:image/png;base64,{image_b64}'
    headers = {
        'Authorization': f'Bearer {REPLICATE_API_TOKEN}',
        'Content-Type': 'application/json',
    }
    payload = {
        'version': REPLICATE_MODEL_VERSION,
        'input': {
            'image': data_uri,
            'steps': 50,
            'guidance_scale': 5.5,
            'octree_resolution': 256,
            'remove_background': True,
        },
    }
    resp = requests.post(
        'https://api.replicate.com/v1/predictions',
        headers=headers,
        json=payload,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f'Replicate 提交失败：HTTP {resp.status_code} - {resp.text[:200]}')
    prediction = resp.json()
    prediction_id = prediction.get('id')
    poll_url = f'https://api.replicate.com/v1/predictions/{prediction_id}'
    for _ in range(120):
        time.sleep(2)
        poll_resp = requests.get(poll_url, headers=headers, timeout=30)
        if poll_resp.status_code >= 400:
            raise RuntimeError(f'Replicate 轮询失败：HTTP {poll_resp.status_code}')
        result = poll_resp.json()
        status = result.get('status')
        if status == 'succeeded':
            output = result.get('output')
            glb_url = ''
            if isinstance(output, str):
                glb_url = output
            elif isinstance(output, list) and output:
                glb_url = output[0] if isinstance(output[0], str) else str(output[0])
            if not glb_url:
                raise RuntimeError('Replicate 返回成功但未拿到模型 URL')
            glb_resp = requests.get(glb_url, timeout=60)
            glb_resp.raise_for_status()
            model_id = secrets.token_hex(8)
            filename = f'{model_id}.glb'
            filepath = MODELS_DIR / filename
            with open(filepath, 'wb') as f:
                f.write(glb_resp.content)
            return jsonify({
                'model_url': f'/api/3d/models/{filename}',
                'model_id': model_id,
                'service': 'replicate',
            })
        if status == 'failed':
            error = result.get('error', '未知错误')
            raise RuntimeError(f'Replicate 生成失败：{error}')
        if status == 'canceled':
            raise RuntimeError('Replicate 生成被取消')
    raise RuntimeError('Replicate 生成超时')


def _generate_3d_tencent(image_b64):
    headers = {
        'Authorization': TC_3D_API_KEY,
        'Content-Type': 'application/json',
    }
    submit_payload = {
        'Model': '3.0',
        'ImageBase64': image_b64,
        'GenerateType': 'Normal',
        'EnablePBR': True,
        'FaceCount': 100000,
    }
    submit_resp = requests.post(
        f'{TC_3D_BASE_URL}/v1/ai3d/submit',
        headers=headers,
        json=submit_payload,
        timeout=30,
    )
    if submit_resp.status_code >= 400:
        raise RuntimeError(f'腾讯混元生3D提交失败：HTTP {submit_resp.status_code} - {submit_resp.text[:300]}')
    submit_data = submit_resp.json()
    resp_wrapper = submit_data.get('Response', submit_data)
    job_id = resp_wrapper.get('JobId')
    if not job_id:
        err = resp_wrapper.get('Error', {})
        err_code = err.get('Code', '')
        err_msg = err.get('Message', submit_data.get('message', '未知错误'))
        raise RuntimeError(f'腾讯混元生3D提交失败：[{err_code}] {err_msg}')

    for _ in range(120):
        time.sleep(3)
        query_payload = {'JobId': job_id}
        query_resp = requests.post(
            f'{TC_3D_BASE_URL}/v1/ai3d/query',
            headers=headers,
            json=query_payload,
            timeout=30,
        )
        if query_resp.status_code >= 400:
            raise RuntimeError(f'腾讯混元生3D查询失败：HTTP {query_resp.status_code}')
        query_data = query_resp.json()
        qresp = query_data.get('Response', query_data)
        status = qresp.get('Status', '')
        if status == 'DONE':
            files = qresp.get('ResultFile3Ds', [])
            glb_url = ''
            for f in files:
                if f.get('Type', '').upper() == 'GLB':
                    glb_url = f.get('Url', '')
                    break
            if not glb_url and files:
                glb_url = files[0].get('Url', '')
            if not glb_url:
                raise RuntimeError('腾讯混元生3D完成但未返回模型URL')
            glb_resp = requests.get(glb_url, timeout=120)
            glb_resp.raise_for_status()
            model_id = secrets.token_hex(8)
            filename = f'{model_id}.glb'
            filepath = MODELS_DIR / filename
            with open(filepath, 'wb') as fw:
                fw.write(glb_resp.content)
            return jsonify({
                'model_url': f'/api/3d/models/{filename}',
                'model_id': model_id,
                'service': 'tencent_hunyuan3d',
            })
        if status == 'FAIL':
            err_code = qresp.get('ErrorCode', '')
            err_msg = qresp.get('ErrorMessage', '未知错误')
            raise RuntimeError(f'腾讯混元生3D失败：[{err_code}] {err_msg}')
    raise RuntimeError('腾讯混元生3D超时')


def _generate_3d_volcengine(image_b64):
    headers = {
        'Authorization': f'Bearer {ARK_API_KEY}',
        'Content-Type': 'application/json',
    }
    data_uri = f'data:image/png;base64,{image_b64}'
    submit_payload = {
        'model': ARK_MODEL_ID,
        'content': [
            {
                'type': 'text',
                'text': '--subdivisionlevel medium --fileformat glb',
            },
            {
                'type': 'image_url',
                'image_url': {
                    'url': data_uri,
                },
            },
        ],
    }
    submit_resp = requests.post(
        f'{ARK_BASE_URL}/contents/generations/tasks',
        headers=headers,
        json=submit_payload,
        timeout=30,
    )
    if submit_resp.status_code >= 400:
        raise RuntimeError(f'火山引擎Seed3D提交失败：HTTP {submit_resp.status_code} - {submit_resp.text[:300]}')
    submit_data = submit_resp.json()
    task_id = submit_data.get('id')
    if not task_id:
        err = submit_data.get('error', {})
        raise RuntimeError(f'火山引擎Seed3D提交失败：{err.get("message", "未知错误")}')

    for _ in range(120):
        time.sleep(5)
        query_resp = requests.get(
            f'{ARK_BASE_URL}/contents/generations/tasks/{task_id}',
            headers=headers,
            timeout=30,
        )
        if query_resp.status_code >= 400:
            raise RuntimeError(f'火山引擎Seed3D查询失败：HTTP {query_resp.status_code}')
        query_data = query_resp.json()
        status = query_data.get('status', '')
        if status == 'succeeded':
            content = query_data.get('content', {})
            glb_url = content.get('3d_model_url', '') or content.get('model_url', '')
            if not glb_url:
                results = content.get('results', [])
                for r in results:
                    url = r.get('url', '')
                    if url:
                        glb_url = url
                        break
            if not glb_url:
                glb_url = query_data.get('output', {}).get('url', '')
            if not glb_url:
                raise RuntimeError(f'火山引擎Seed3D完成但未返回模型URL，响应：{json.dumps(query_data)[:200]}')
            glb_resp = requests.get(glb_url, timeout=120)
            glb_resp.raise_for_status()
            model_id = secrets.token_hex(8)
            filename = f'{model_id}.glb'
            filepath = MODELS_DIR / filename
            with open(filepath, 'wb') as fw:
                fw.write(glb_resp.content)
            return jsonify({
                'model_url': f'/api/3d/models/{filename}',
                'model_id': model_id,
                'service': 'volcengine_seed3d',
            })
        if status == 'failed':
            err = query_data.get('error', {})
            err_msg = err.get('message', '未知错误')
            raise RuntimeError(f'火山引擎Seed3D失败：{err_msg}')
    raise RuntimeError('火山引擎Seed3D超时')


def _generate_3d_meshy(image_b64, image_url=''):
    headers = {
        'Authorization': f'Bearer {MESHY_API_KEY}',
        'Content-Type': 'application/json',
    }
    if image_url and not image_url.startswith('data:'):
        img_input = image_url
    else:
        img_input = f'data:image/png;base64,{image_b64}'
    submit_payload = {
        'image_url': img_input,
        'ai_model': 'meshy-5',
        'should_texture': True,
        'should_remesh': True,
        'topology': 'triangle',
        'target_polycount': 30000,
    }
    submit_resp = requests.post(
        f'{MESHY_BASE_URL}/openapi/v1/image-to-3d',
        headers=headers,
        json=submit_payload,
        timeout=30,
    )
    if submit_resp.status_code == 402:
        raise RuntimeError('Meshy 积分不足，请前往 meshy.ai 充值')
    if submit_resp.status_code >= 400:
        raise RuntimeError(f'Meshy 提交失败：HTTP {submit_resp.status_code} - {submit_resp.text[:300]}')
    submit_data = submit_resp.json()
    result = submit_data.get('result', submit_data)
    task_id = result.get('id') if isinstance(result, dict) else result
    if not task_id:
        raise RuntimeError(f'Meshy 提交失败：未返回任务ID，响应：{json.dumps(submit_data)[:200]}')

    for _ in range(180):
        time.sleep(2)
        poll_resp = requests.get(
            f'{MESHY_BASE_URL}/openapi/v1/image-to-3d/{task_id}',
            headers=headers,
            timeout=30,
        )
        if poll_resp.status_code >= 400:
            raise RuntimeError(f'Meshy 轮询失败：HTTP {poll_resp.status_code}')
        poll_data = poll_resp.json()
        status = poll_data.get('status', '')
        if status == 'SUCCEEDED':
            model_urls = poll_data.get('model_urls') or {}
            glb_url = model_urls.get('glb') or ''
            if not glb_url:
                model_url = poll_data.get('model_url') or ''
                if model_url:
                    glb_url = model_url
            if not glb_url:
                raise RuntimeError(f'Meshy 完成但未返回GLB URL，响应：{json.dumps(poll_data)[:300]}')
            glb_resp = requests.get(glb_url, timeout=120)
            glb_resp.raise_for_status()
            model_id = secrets.token_hex(8)
            filename = f'{model_id}.glb'
            filepath = MODELS_DIR / filename
            with open(filepath, 'wb') as fw:
                fw.write(glb_resp.content)
            return jsonify({
                'model_url': f'/api/3d/models/{filename}',
                'model_id': model_id,
                'service': 'meshy',
            })
        if status == 'FAILED':
            raise RuntimeError(f'Meshy 生成失败：{poll_data.get("error", "未知错误")}')
    raise RuntimeError('Meshy 生成超时')


@bp.post('/api/3d/generate')
def generate_3d():
    if not check_rate_limit('3d-generate'):
        return json_error('请求过于频繁，请稍后再试。', 429)
    _, auth_error = require_auth()
    if auth_error:
        return auth_error
    data = request.get_json(silent=True) or {}
    image_url = str(data.get('image_url', '')).strip()
    image_base64 = str(data.get('image_base64', '')).strip()
    if not image_url and not image_base64:
        return json_error('必须提供 image_url 或 image_base64。')
    try:
        raw_b64 = _image_to_base64(image_url, image_base64)
    except Exception as e:
        return json_error(f'图片获取失败：{e}', 400)
    if not raw_b64:
        return json_error('图片数据为空。')
    errors = []
    if MESHY_API_KEY:
        try:
            return _generate_3d_meshy(raw_b64, image_url)
        except Exception as e:
            errors.append(f'Meshy: {e}')
    if ARK_API_KEY:
        try:
            return _generate_3d_volcengine(raw_b64)
        except Exception as e:
            errors.append(f'火山引擎Seed3D: {e}')
    if TC_3D_API_KEY:
        try:
            return _generate_3d_tencent(raw_b64)
        except Exception as e:
            errors.append(f'腾讯混元生3D: {e}')
    if HUNYUAN3D_API_URL:
        try:
            return _generate_3d_hunyuan(raw_b64)
        except Exception as e:
            errors.append(f'本地Hunyuan3D: {e}')
    if REPLICATE_API_TOKEN:
        try:
            return _generate_3d_replicate(raw_b64)
        except Exception as e:
            errors.append(f'Replicate: {e}')
    detail = '；'.join(errors) if errors else '未配置任何3D生成服务'
    return json_error(f'3D生成失败：{detail}。请在 .env 中配置 MESHY_API_KEY 或 ARK_API_KEY 或 TC_3D_API_KEY 或 HUNYUAN3D_API_URL 或 REPLICATE_API_TOKEN。', 503)


@bp.get('/api/3d/models/<path:filename>')
def serve_3d_model(filename):
    safe_name = Path(filename).name
    filepath = MODELS_DIR / safe_name
    if not filepath.exists():
        return json_error('模型文件不存在。', 404)
    return send_from_directory(str(MODELS_DIR), safe_name, mimetype='model/gltf-binary')


