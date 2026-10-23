"""Bounded extraction retries; each attempt remains a separate auditable job."""
from datetime import datetime

RETRY_DELAYS = (10, 30, 60)
MAX_RETRIES = len(RETRY_DELAYS)


class RetryableModelError(ValueError):
    """Safe provider error text for transient transport/incomplete responses."""


def retry_message(job):
    number = job.get('retry_number', 0)
    if job['status'] == 'failed':
        prefix = f'自动重试 {MAX_RETRIES} 次仍失败；' if number >= MAX_RETRIES else ''
        return prefix + job['error']
    if number:
        prefix = f'自动重试 {number}/{MAX_RETRIES}'
        if job['status'] == 'queued':
            stamp = datetime.fromisoformat(job['available_at']).astimezone()
            return f'{prefix} 等待中，最早 {stamp:%H:%M:%S} 在 Agent 空闲时继续'
        if job['status'] == 'running':
            return prefix + ' 正在后台提取'
        return prefix + ' 成功'
    return {'queued': '排队中', 'running': '正在后台提取', 'completed': '草稿已生成'}.get(job['status'], job['status'])
