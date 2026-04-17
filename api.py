'''KlingAI API client helpers.'''

from __future__ import annotations

import base64
import json
import os
import re
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import jwt
from loguru import logger


DEFAULT_CONFIG_PATH = Path('config.json')
DEFAULT_LOG_FILE_NAME = 'run.log'
DEFAULT_OUTPUT_DIR = 'outputs'
PLACEHOLDER_PATTERN = re.compile(r'\{([a-zA-Z0-9_]+)\}')


def get_config_or_env(config: dict[str, Any], config_key: str, env_name: str, default: str = '') -> str:
    '''Get value from config first, fallback to environment variable.'''

    config_value = str(config.get(config_key, '')).strip()
    if config_value:
        return config_value
    return os.environ.get(env_name, default).strip()


def configure_logger(log_dir: str | Path) -> None:
    '''Configure loguru output destinations.'''

    resolved_log_dir = Path(log_dir)
    resolved_log_dir.mkdir(parents=True, exist_ok=True)
    log_file = resolved_log_dir / DEFAULT_LOG_FILE_NAME

    logger.remove()
    logger.add(
        log_file,
        encoding='utf-8',
        rotation='1 MB',
        retention='10 days',
        enqueue=False,
        format='{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}',
    )
    logger.add(lambda message: print(message, end=''), format='{time:HH:mm:ss} | {level} | {message}')
    logger.info('Logger initialized: {}', log_file)


def load_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    '''Read and parse JSON config file.'''

    resolved_path = Path(config_path)
    if not resolved_path.exists():
        raise FileNotFoundError(f'Config file not found: {resolved_path}')

    config = json.loads(resolved_path.read_text(encoding='utf-8'))
    logger.info('Loaded config: {}', resolved_path)
    return config


def get_nested_value(payload: Any, dotted_path: str, default: Any = None) -> Any:
    '''Read a nested value from dict/list via dot path.'''

    if not dotted_path:
        return default

    current_value = payload
    for part in dotted_path.split('.'):
        if isinstance(current_value, dict):
            if part not in current_value:
                return default
            current_value = current_value[part]
            continue

        if isinstance(current_value, list):
            if not part.isdigit():
                return default
            index = int(part)
            if index >= len(current_value):
                return default
            current_value = current_value[index]
            continue

        return default

    return current_value


def remove_empty_values(payload: Any) -> Any:
    '''Recursively remove empty members from payload.'''

    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            cleaned_value = remove_empty_values(value)
            if cleaned_value in (None, '', [], {}):
                continue
            cleaned[key] = cleaned_value
        return cleaned

    if isinstance(payload, list):
        items = [remove_empty_values(item) for item in payload]
        return [item for item in items if item not in (None, '', [], {})]

    return payload


def render_template(template: Any, context: dict[str, Any]) -> Any:
    '''Render placeholders in template with context values.'''

    if isinstance(template, dict):
        return {key: render_template(value, context) for key, value in template.items()}

    if isinstance(template, list):
        return [render_template(item, context) for item in template]

    if isinstance(template, str):
        full_match = PLACEHOLDER_PATTERN.fullmatch(template)
        if full_match:
            return context.get(full_match.group(1), '')

        def replace_placeholder(match: re.Match[str]) -> str:
            name = match.group(1)
            value = context.get(name, '')
            return '' if value is None else str(value)

        return PLACEHOLDER_PATTERN.sub(replace_placeholder, template)

    return template


def encode_image_to_base64(image_path: str | Path) -> str:
    '''Read local image and return plain base64 string (no data URL prefix).'''

    resolved_image_path = Path(image_path)
    if not resolved_image_path.exists():
        raise FileNotFoundError(f'Image not found: {resolved_image_path}')

    encoded = base64.b64encode(resolved_image_path.read_bytes()).decode('utf-8')
    logger.info('Encoded image to base64: {}', resolved_image_path)
    return encoded


def resolve_image_value(image_input: str) -> str:
    '''Resolve image input to API accepted value (URL or plain base64).'''

    normalized = image_input.strip()
    if not normalized:
        return ''
    if normalized.startswith('http://') or normalized.startswith('https://'):
        return normalized
    return encode_image_to_base64(normalized)


class KlingAIClient:
    '''KlingAI client with config-driven request/response mapping.'''

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

        self.auth_mode = get_config_or_env(config, 'auth_mode', 'KLING_AUTH_MODE', 'jwt').lower()
        self.api_key = get_config_or_env(config, 'api_key', 'KLING_API_KEY')
        self.access_key = get_config_or_env(config, 'access_key', 'KLING_ACCESS_KEY')
        self.secret_key = get_config_or_env(config, 'secret_key', 'KLING_SECRET_KEY')

        self.base_url = str(config.get('base_url', '')).rstrip('/')
        self.create_endpoint = str(config.get('create_endpoint', ''))
        self.query_endpoint_template = str(config.get('query_endpoint_template', ''))
        self.request_timeout = int(config.get('request_timeout_seconds', 60))
        self.poll_interval_seconds = float(config.get('poll_interval_seconds', 3))
        self.poll_timeout_seconds = float(config.get('poll_timeout_seconds', 300))
        self.token_ttl_seconds = int(config.get('token_ttl_seconds', 1800))
        self.token_not_before_skew_seconds = int(config.get('token_not_before_skew_seconds', 5))
        self.token_clock_offset_seconds = int(config.get('token_clock_offset_seconds', 0))

        self.output_dir = Path(config.get('output_dir', DEFAULT_OUTPUT_DIR))
        self.headers_template = config.get('headers', {})
        self.request_template = config.get('request_template', {})
        self.task_id_path = str(config.get('task_id_path', 'data.task_id'))
        self.status_path = str(config.get('status_path', 'data.status'))
        self.result_url_path = str(config.get('result_url_path', 'data.images.0.url'))
        self.error_message_path = str(config.get('error_message_path', 'message'))
        self.success_status_values = {
            str(status).lower() for status in config.get('success_status_values', ['success', 'succeeded', 'completed'])
        }
        self.running_status_values = {
            str(status).lower()
            for status in config.get('running_status_values', ['submitted', 'pending', 'queued', 'running', 'processing'])
        }
        self.failed_status_values = {
            str(status).lower() for status in config.get('failed_status_values', ['failed', 'error', 'cancelled', 'canceled'])
        }

        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info('KlingAI client initialized, output dir: {}', self.output_dir)

    def build_jwt_token(self) -> str:
        '''Build JWT token with HS256 according to provider example.'''

        if not self.access_key or not self.secret_key:
            raise ValueError('JWT auth requires both access_key and secret_key')

        now = int(time.time()) + self.token_clock_offset_seconds
        payload = {
            'iss': self.access_key,
            'exp': now + self.token_ttl_seconds,
            'nbf': now - self.token_not_before_skew_seconds,
        }
        headers = {'alg': 'HS256', 'typ': 'JWT'}
        token = jwt.encode(payload, self.secret_key, algorithm='HS256', headers=headers)
        logger.info('Built JWT token for issuer: {}', self.access_key)
        return str(token)

    def build_authorization_value(self) -> str:
        '''Build Authorization header value from active auth mode.'''

        if self.auth_mode == 'jwt':
            return f'Bearer {self.build_jwt_token()}'

        if self.api_key:
            return f'Bearer {self.api_key}'

        if self.access_key and self.secret_key:
            return f'Bearer {self.build_jwt_token()}'

        raise ValueError('Missing auth info. Provide access_key/secret_key or api_key')

    def build_headers(self) -> dict[str, str]:
        '''Render request headers from template and runtime context.'''

        context = {
            'api_key': self.api_key,
            'access_key': self.access_key,
            'secret_key': self.secret_key,
            'authorization': self.build_authorization_value(),
        }
        rendered = render_template(self.headers_template, context)
        headers = remove_empty_values(rendered)
        logger.info('Rendered request headers')
        return headers

    def sync_clock_offset_from_http_date(self, http_date: str) -> bool:
        '''Sync token clock offset using Date header from server response.'''

        if not http_date:
            return False
        try:
            server_time = parsedate_to_datetime(http_date)
        except (TypeError, ValueError):
            logger.warning('Unable to parse server Date header: {}', http_date)
            return False

        offset = int(server_time.timestamp() - time.time())
        if offset == self.token_clock_offset_seconds:
            return False
        self.token_clock_offset_seconds = offset
        logger.warning('Auto-adjusted token clock offset to {} seconds', self.token_clock_offset_seconds)
        return True

    def build_url(self, endpoint: str) -> str:
        '''Build absolute URL from configured base URL and endpoint.'''

        if endpoint.startswith('http://') or endpoint.startswith('https://'):
            return endpoint
        if not self.base_url:
            raise ValueError('Missing base_url in config')
        return f'{self.base_url}{endpoint}'

    def send_json_request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        allow_auth_retry: bool = True,
    ) -> dict[str, Any]:
        '''Send JSON HTTP request and parse JSON response body.'''

        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

        req = request.Request(url=url, data=body, headers=self.build_headers(), method=method.upper())
        logger.info('Sending request: {} {}', method.upper(), url)

        try:
            with request.urlopen(req, timeout=self.request_timeout) as resp:
                text = resp.read().decode('utf-8')
                logger.info('Request succeeded: HTTP {}', resp.status)
        except error.HTTPError as http_error:
            error_text = http_error.read().decode('utf-8', errors='replace')
            error_code = None
            try:
                parsed_error = json.loads(error_text)
                error_code = parsed_error.get('code')
            except json.JSONDecodeError:
                parsed_error = None

            if (
                allow_auth_retry
                and self.auth_mode == 'jwt'
                and http_error.code == 401
                and error_code in {1002, 1003, 1004}
                and self.sync_clock_offset_from_http_date(http_error.headers.get('Date', ''))
            ):
                logger.warning('Retrying once after syncing clock offset from server Date')
                return self.send_json_request(method, url, payload, allow_auth_retry=False)

            logger.error('HTTP error {}: {}', http_error.code, error_text)
            raise RuntimeError(f'HTTP {http_error.code}: {error_text}') from http_error
        except error.URLError as url_error:
            logger.error('Network error: {}', url_error)
            raise RuntimeError(f'Network error: {url_error}') from url_error

        try:
            return json.loads(text)
        except json.JSONDecodeError as decode_error:
            logger.error('Response is not valid JSON: {}', text)
            raise RuntimeError(f'Response is not valid JSON: {text}') from decode_error

    def create_task(self, task_data: dict[str, str]) -> tuple[str, dict[str, Any]]:
        '''Create generation task and return task id with raw response.'''

        if self.auth_mode == 'jwt':
            if not self.access_key or not self.secret_key:
                raise ValueError('JWT mode requires access_key and secret_key')
        elif not self.api_key and not (self.access_key and self.secret_key):
            raise ValueError('Auth info is empty')

        image_path = str(task_data.get('image_path', '')).strip()
        prompt = str(task_data.get('prompt', '')).strip()
        negative_prompt = str(task_data.get('negative_prompt', '')).strip()
        output_name = str(task_data.get('output_name', '')).strip()

        render_context = {
            'prompt': prompt,
            'negative_prompt': negative_prompt,
            'image_path': image_path,
            'image_value': resolve_image_value(image_path) if image_path else '',
            'output_name': output_name,
            'image_name': Path(image_path).name if image_path else '',
            'model_name': self.config.get('model_name', 'kling-v2-1'),
            'image_count': int(self.config.get('image_count', 1)),
            'external_task_id': self.config.get('external_task_id', ''),
            'callback_url': self.config.get('callback_url', ''),
        }
        payload = remove_empty_values(render_template(self.request_template, render_context))

        response_payload = self.send_json_request('POST', self.build_url(self.create_endpoint), payload)
        task_id = str(get_nested_value(response_payload, self.task_id_path, '')).strip()
        if not task_id:
            raise RuntimeError(f'Unable to extract task id from response: {response_payload}')
        logger.info('Task created: {}', task_id)
        return task_id, response_payload

    def query_task(self, task_id: str) -> dict[str, Any]:
        '''Query task by task id.'''

        endpoint = self.query_endpoint_template.format(task_id=parse.quote(task_id))
        payload = self.send_json_request('GET', self.build_url(endpoint))
        logger.info('Task queried: {}', task_id)
        return payload

    def wait_for_result(self, task_id: str) -> dict[str, Any]:
        '''Poll task status until success, failure or timeout.'''

        deadline = time.time() + self.poll_timeout_seconds
        while time.time() < deadline:
            payload = self.query_task(task_id)
            status = str(get_nested_value(payload, self.status_path, '')).strip().lower()
            logger.info('Task status: {} -> {}', task_id, status or 'unknown')

            if status in self.success_status_values:
                return payload
            if status in self.failed_status_values:
                message = get_nested_value(payload, self.error_message_path, 'Task failed')
                raise RuntimeError(str(message))
            if status and status not in self.running_status_values:
                logger.warning('Unknown status encountered, continue polling: {}', status)

            time.sleep(self.poll_interval_seconds)

        raise TimeoutError(f'Task timeout: {task_id}')

    def download_result(self, result_url: str, output_name: str) -> Path:
        '''Download generated image to local output directory.'''

        if not result_url:
            raise ValueError('Result URL is empty')

        safe_name = output_name.strip() or f'kling_result_{int(time.time())}'
        suffix = Path(parse.urlparse(result_url).path).suffix or '.png'
        output_path = self.output_dir / f'{safe_name}{suffix}'
        logger.info('Downloading result: {}', result_url)

        try:
            with request.urlopen(result_url, timeout=self.request_timeout) as resp:
                output_path.write_bytes(resp.read())
        except error.URLError as url_error:
            raise RuntimeError(f'Download failed: {url_error}') from url_error

        logger.info('Downloaded result to: {}', output_path)
        return output_path

    def run_task(self, task_data: dict[str, str]) -> dict[str, Any]:
        '''Run full lifecycle: create task, wait result, download image.'''

        task_id, create_response = self.create_task(task_data)
        result_response = self.wait_for_result(task_id)
        result_url = str(get_nested_value(result_response, self.result_url_path, '')).strip()
        if not result_url:
            raise RuntimeError(f'Completed task without result URL: {result_response}')

        output_name = str(task_data.get('output_name', '')).strip() or task_id
        saved_path = self.download_result(result_url, output_name)
        logger.info('Task completed: {}', task_id)
        return {
            'task_id': task_id,
            'create_response': create_response,
            'result_response': result_response,
            'result_url': result_url,
            'saved_path': str(saved_path),
        }
