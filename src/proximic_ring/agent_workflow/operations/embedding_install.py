"""Explicit, resumable BGE-M3 installation; no model imports or user data uploads."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from urllib.request import Request, urlopen

from .operation_embeddings import MODEL, REVISION, model_directory, compute_lock
from ..extraction.workflow_store import atomic_json


class InstallCancelled(Exception):
    pass


def manifest():
    return json.loads(Path(__file__).with_name('bge-m3.json').read_text(encoding='utf-8'))


def installed(directory=None):
    """Cheap readiness check; hashes are checked on install/explicit repair only."""
    directory=Path(directory or model_directory())
    try:
        marker=json.loads((directory/'proximic-model.json').read_text())
        if marker.get('model')!=MODEL or marker.get('revision')!=REVISION:return False
        for row in manifest()['files']:
            record=marker.get('files',{}).get(row['path'],{})
            stat=(directory/row['path']).stat()
            if (stat.st_size!=row['size'] or record.get('sha256')!=row['sha256']
                    or record.get('mtime_ns')!=stat.st_mtime_ns):return False
        return True
    except (OSError,ValueError,TypeError):return False


def check_cancel(cancelled):
    if cancelled():raise InstallCancelled()


def verified(path,row,cancelled):
    if not path.is_file() or path.stat().st_size!=row['size']:return False
    digest=hashlib.sha256()
    with path.open('rb') as handle:
        while data:=handle.read(4*1024*1024):
            check_cancel(cancelled);digest.update(data)
    return digest.hexdigest()==row['sha256']


def fetch(row,path,*,progress,cancelled,opener=urlopen):
    """Only pinned public files. A partial file is never considered installed."""
    partial=path.with_name(path.name+'.part');path.parent.mkdir(parents=True,exist_ok=True)
    size=partial.stat().st_size if partial.exists() else 0
    if size>row['size']:partial.unlink();size=0
    if size==row['size']:
        if verified(partial,row,cancelled):os.replace(partial,path);return
        partial.unlink();size=0
    headers={'User-Agent':'Mythlink-model-setup/1','Accept-Encoding':'identity'}
    if size:headers['Range']=f'bytes={size}-'
    url=f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{row["path"]}'
    check_cancel(cancelled)
    with opener(Request(url,headers=headers),timeout=20) as response:
        if size and response.status==206:
            match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
            if not match or int(match[1])!=size or int(match[3])!=row['size']:
                raise ValueError('下载续传范围不匹配，请重试')
        elif response.status==200:size=0
        else:raise ValueError('模型下载响应异常，请重试')
        with partial.open('ab' if size else 'wb') as handle:
            while True:
                check_cancel(cancelled);chunk=response.read(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>row['size']:raise ValueError('模型文件超过预期大小，未启用')
                handle.write(chunk);progress(size)
        check_cancel(cancelled)
    if size!=row['size']:raise ValueError('网络连接提前结束，已保留下载进度，请重试')
    if not verified(partial,row,cancelled):
        partial.unlink();raise ValueError('模型校验失败，未启用；请重试')
    os.replace(partial,path)


def install(*,directory=None,local_source=None,progress=lambda *_:None,cancelled=lambda:False,opener=urlopen):
    directory=Path(directory or model_directory());directory.mkdir(parents=True,exist_ok=True)
    rows=manifest()['files'];total=sum(row['size'] for row in rows)
    # Shares the inference lease: never replace files under a live encoder.
    with compute_lock():
        needed=[];done=0
        for row in rows:
            check_cancel(cancelled);progress('正在校验已有模型',done,total)
            if verified(directory/row['path'],row,cancelled):done+=row['size']
            else:needed.append(row)
        remaining=sum(row['size'] for row in needed)
        if not local_source:
            remaining-=sum(min((directory/(row['path']+'.part')).stat().st_size,row['size'])
                for row in needed if (directory/(row['path']+'.part')).exists())
        if shutil.disk_usage(directory).free<remaining+256*1024*1024:
            raise ValueError('磁盘空间不足，请为召回模型预留至少 2.6 GB 可用空间')
        for row in needed:
            check_cancel(cancelled);path=directory/row['path'];base=done
            if local_source:
                source=Path(local_source)/row['path']
                if not verified(source,row,cancelled):raise ValueError('所选文件夹不是完整的指定版本 BGE-M3 模型')
                partial=path.with_name(path.name+'.part');path.parent.mkdir(parents=True,exist_ok=True)
                with source.open('rb') as src,partial.open('wb') as dst:
                    copied=0
                    while chunk:=src.read(1024*1024):
                        check_cancel(cancelled);dst.write(chunk);copied+=len(chunk)
                        progress('正在导入本地模型',base+copied,total)
                if not verified(partial,row,cancelled):raise ValueError('本地模型复制校验失败，未启用')
                os.replace(partial,path)
            else:
                fetch(row,path,progress=lambda n:progress('正在下载召回模型',base+n,total),cancelled=cancelled,opener=opener)
            done+=row['size']
        check_cancel(cancelled)
        atomic_json(directory/'proximic-model.json',dict(model=MODEL,revision=REVISION,
            files={row['path']:dict(sha256=row['sha256'],mtime_ns=(directory/row['path']).stat().st_mtime_ns) for row in rows}))
        progress('召回模型已就绪',total,total)
    return directory
