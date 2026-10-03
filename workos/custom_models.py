"""User-added endpoint/model identities; secrets live only in process memory."""
from __future__ import annotations
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from .model_catalog import endpoint_url,_model_id


def _label(value,default,maximum=100):
    if value is None or value=='':return default
    if not isinstance(value,str) or len(value)>maximum or re.search(r'[\x00-\x1f\x7f]',value):
        raise ValueError('模型名称与供应商名称请使用100字以内的单行文字')
    return value.strip() or default


class CustomModels:
    def __init__(self,data_dir):
        self.path=Path(data_dir)/'custom-models.json';self.lock=threading.RLock();self.keys={};self.models={}
        try:raw=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):raw={}
        for item in raw.get('models',[]) if isinstance(raw,dict) and isinstance(raw.get('models'),list) else []:
            try:
                if not isinstance(item,dict):continue
                entry=self._entry(item)
                if item.get('mode')!=entry['mode']:continue
                self.models[entry['mode']]=entry
            except ValueError:continue

    def _entry(self,body):
        if not isinstance(body,dict):raise ValueError('模型连接信息请填写为对象')
        model=body.get('model_id') or ''
        if not isinstance(model,str):raise ValueError('请填写服务提供的模型编号，例如 deepseek-chat')
        model=_model_id(model.strip());base=endpoint_url(body.get('base_url') or 'http://127.0.0.1:8787/v1')
        key=hashlib.sha256((base+'\0'+model).encode()).hexdigest()[:16]
        return {'mode':'custom-'+key,'model_id':model,'base_url':base,
                'name':_label(body.get('name'),model),'provider_label':_label(body.get('provider_label'),'自定义模型')}

    def configs(self):
        with self.lock:return [dict(item) for item in self.models.values()]

    def keys_snapshot(self):
        with self.lock:return dict(self.keys)

    def public(self):
        with self.lock:return [{**item,'has_api_key':bool(self.keys.get(mode))} for mode,item in self.models.items()]

    def _save(self):
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'schema_version':1,'models':self.configs()},ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temporary,self.path)

    def add(self,body):
        entry=self._entry(body);key=body.get('api_key','')
        if not isinstance(key,str) or len(key)>2000 or '\r' in key or '\n' in key:raise ValueError('API key 格式无效')
        with self.lock:
            if entry['mode'] not in self.models and len(self.models)>=50:raise ValueError('最多保存50组模型连接，请移除不用的连接后再添加')
            self.models[entry['mode']]=entry
            if 'api_key' in body:self.keys[entry['mode']]=key
            self._save()
            return {**entry,'has_api_key':bool(self.keys.get(entry['mode']))}

    def remove(self,key):
        mode='custom-'+key
        if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{16}',key):raise ValueError('模型连接编号无效')
        with self.lock:
            if mode not in self.models:raise ValueError('该模型连接已经移除')
            del self.models[mode];self.keys.pop(mode,None);self._save()
