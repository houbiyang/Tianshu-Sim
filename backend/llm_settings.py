"""Local DSH model settings. Secrets are never included in API responses."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = Path(os.environ.get('GENTACTIX_PROJECT_ROOT', str(ROOT))).expanduser().resolve()
CONFIG_PATH = Path(os.environ.get('GENTACTIX_LLM_CONFIG', str(PROJECT_ROOT / '.gentactix-llm.enc')))
KEY_PATH = Path(os.environ.get('GENTACTIX_VAULT_KEY_FILE', str(
    Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'GenTactix-DSH' / 'vault.key')))
HEADER = b'GENTACTIX-LLM-VAULT-v1\n'
LOAD_ERROR = ''
ROLES = ('engine', 'strategist', 'chat', 'planner', 'causal', 'counterfactual', 'connection-test')
PRESETS = [
    {'id': 'vllm', 'label': 'vLLM / OpenAI兼容', 'base_url': 'http://127.0.0.1:8000/v1', 'model': ''},
    {'id': 'deepseek', 'label': 'DeepSeek Flash（兼容V4）', 'base_url': 'https://api.deepseek.com/anthropic', 'model': 'deepseek-flash'},
]


def migrate_endpoint(url):
    """Map legacy official Chat-Completions roots to the DSH Messages root."""
    parsed = urlsplit(url)
    if parsed.scheme == 'https' and parsed.hostname == 'api.deepseek.com' and parsed.port is None \
            and parsed.path.rstrip('/') in ('', '/v1'):
        return 'https://api.deepseek.com/anthropic'
    return url


def validate(value):
    value = dict(value)
    url = migrate_endpoint(str(value.get('base_url', '')).strip().rstrip('/'))
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('API地址须为HTTP/HTTPS地址，不得包含用户名、密码、查询参数或片段')
    if not str(value.get('model', '')).strip():
        raise ValueError('请填写服务实际提供的模型ID')
    for key, low, high in (('max_tokens', 128, 262144), ('context_window', 1024, 2000000), ('timeout_seconds', 10, 1800)):
        val = value.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or not low <= val <= high:
            raise ValueError(f'{key}须为{low}–{high}之间的整数')
    if value['max_tokens'] > value['context_window']:
        raise ValueError('输出token上限不能超过上下文容量')
    value.update(base_url=url, model=str(value['model']).strip())
    if any(c in value['model'] for c in '\r\n'):
        raise ValueError('模型ID不得包含换行')
    key = value.get('api_key')
    if key is not None and (not isinstance(key, str) or '\n' in key or '\r' in key):
        raise ValueError('API Key格式不合法')
    policies = value.get('role_policies')
    if policies is not None:
        if not isinstance(policies, dict) or set(policies) - set(ROLES):
            raise ValueError('角色配置包含未知角色')
        for role, policy in policies.items():
            if not isinstance(policy, dict) or set(policy) - {'max_tokens', 'reasoning_effort'}:
                raise ValueError(f'{role}角色配置不合法')
            budget = policy.get('max_tokens')
            if isinstance(budget, bool) or not isinstance(budget, int) or not 128 <= budget <= 262144:
                raise ValueError(f'{role}输出上限须为128–262144之间的整数')
            if policy.get('reasoning_effort') not in (None, 'low', 'medium', 'high', 'xhigh'):
                raise ValueError(f'{role}推理强度不合法')
    for name, default, low, high in (('max_steps', 8, 1, 64), ('max_tool_calls', 16, 0, 128)):
        item = value.setdefault(name, default)
        if isinstance(item, bool) or not isinstance(item, int) or not low <= item <= high:
            raise ValueError(f'{name}须为{low}–{high}之间的整数')
    if not isinstance(value.get('clear_api_key', False), bool):
        raise ValueError('清除密钥选项不合法')
    return value


def _cipher(create=False):
    if not KEY_PATH.exists() and create:
        KEY_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(Fernet.generate_key())
                stream.flush()
                os.fsync(stream.fileno())
    return Fernet(KEY_PATH.read_bytes().strip())


def load():
    global LOAD_ERROR
    LOAD_ERROR = ''
    if not CONFIG_PATH.is_file():
        return None
    try:
        data = CONFIG_PATH.read_bytes()
        if not data.startswith(HEADER):
            raise ValueError('vault format')
        return validate(json.loads(_cipher().decrypt(data[len(HEADER):])))
    except (OSError, ValueError, InvalidToken):
        # Keep the UI usable to replace credentials; never silently delete an unreadable vault.
        LOAD_ERROR = '加密配置无法读取：文件损坏或本机密钥缺失。请恢复密钥，或重新填写并保存配置。'
        return None


def save(value):
    global LOAD_ERROR
    encrypted = HEADER + _cipher(create=True).encrypt(json.dumps(validate(value), ensure_ascii=False).encode('utf-8'))
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(encrypted)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    if LOAD_ERROR and CONFIG_PATH.exists():
        os.replace(CONFIG_PATH, CONFIG_PATH.with_suffix('.enc.unreadable'))
    os.replace(temporary, CONFIG_PATH)
    LOAD_ERROR = ''


def current(h):
    return {'base_url': h.LLM_BASE_URL, 'model': h.LLM_MODEL, 'max_tokens': h.LLM_MAX_TOKENS,
            'context_window': h.CONTEXT_WINDOW, 'timeout_seconds': int(h.REQUEST_TIMEOUT_SECONDS),
            'api_key_configured': bool(h.LLM_API_KEY), 'presets': PRESETS,
            'provider': 'deepseek' if urlsplit(h.LLM_BASE_URL).hostname == 'api.deepseek.com' else 'vllm',
            'role_policies': {role: {k: h.role_policy(role)[k] for k in ('max_tokens', 'reasoning_effort')} for role in ROLES} if hasattr(h, 'role_policy') else {},
            'max_steps': getattr(h, 'RUNTIME_MAX_STEPS', 8), 'max_tool_calls': getattr(h, 'RUNTIME_MAX_TOOL_CALLS', 16),
            'storage': {'path': str(CONFIG_PATH), 'encrypted': True, 'format': 'Fernet authenticated encryption',
                        'key_location': '用户配置目录（与项目分离）', 'error': LOAD_ERROR}}


def apply(h, value):
    value = validate(value)
    # A blank key retains the existing secret only for the exact same endpoint.
    value['api_key'] = '' if value.pop('clear_api_key', False) else (value.get('api_key') or (h.LLM_API_KEY if value['base_url'] == h.LLM_BASE_URL else ''))
    if value.get('role_policies') is None:
        value['role_policies'] = getattr(h, 'ROLE_POLICIES', {})
    save(value)
    h.close_harness()
    h.LLM_BASE_URL, h.LLM_MODEL, h.LLM_API_KEY = value['base_url'], value['model'], value['api_key']
    h.LLM_MAX_TOKENS, h.CONTEXT_WINDOW = value['max_tokens'], value['context_window']
    h.REQUEST_TIMEOUT_SECONDS = value['timeout_seconds']
    h.ROLE_POLICIES = value['role_policies']
    h.RUNTIME_MAX_STEPS, h.RUNTIME_MAX_TOOL_CALLS = value['max_steps'], value['max_tool_calls']
    return current(h)
