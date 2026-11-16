"""Cache only immutable snapshots; authorization still runs on every request."""
import hashlib
import json
import os
import redis
from .clients import RemoteClient
from .resilience import log

CACHEABLE = {'analysis': {'get_profile','get_result','compare_runs'},
             'case': {'get_report','export_report'}}

class CachedClient(RemoteClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cache = redis.Redis.from_url(os.environ['CACHE_URL'], decode_responses=True,
                                         socket_connect_timeout=0.15, socket_timeout=0.15)

    def __getattr__(self, name):
        remote = super().__getattr__(name)
        if self.actor['role']=='ANALYST' or name not in CACHEABLE.get(self.owner,set()):
            return remote
        def call(*args, **kwargs):
            # Principal ID and role isolate row-level visibility. Snapshot IDs are immutable.
            fingerprint = json.dumps([self.actor['id'],self.actor['role'],name,kwargs],sort_keys=True,default=str)
            key='bad_trans:cache:'+self.owner+':'+hashlib.sha256(fingerprint.encode()).hexdigest()
            try:
                saved=self.cache.get(key)
                if saved is not None:
                    value = json.loads(saved)
                    if not isinstance(value, dict):
                        raise ValueError('Invalid cached snapshot')
                    log('cache.hit',target=self.owner,method=name,key=key)
                    return value
                log('cache.miss',target=self.owner,method=name,key=key)
            except redis.RedisError:
                log('cache.unavailable',target=self.owner,method=name)
            except (ValueError, UnicodeError):
                log('cache.invalid',target=self.owner,method=name)
            value=remote(*args,**kwargs)
            if value is not None:
                try:
                    self.cache.setex(key,int(os.getenv('CACHE_TTL','30')),json.dumps(value,default=str))
                except redis.RedisError:
                    log('cache.write_unavailable',target=self.owner,method=name)
            return value
        return call
