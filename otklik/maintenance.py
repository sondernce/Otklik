"""Atomic local receipts for successful backup and restore checks, without database writes."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile

KEYS={'backup_at','verified_at','restore_verified_at'}


def status(database):
    path=Path(str(database)+'.maintenance.json')
    result={'automatic_backups_configured':False,'available':True,**dict.fromkeys(KEYS)}
    try:
        if path.is_symlink() or path.stat().st_size>4096:raise ValueError()
        data=json.loads(path.read_text())
        if not isinstance(data,dict):raise ValueError()
        for key in KEYS:
            value=data.get(key)
            if value is not None:
                parsed=datetime.fromisoformat(value)
                if parsed.tzinfo is None:raise ValueError()
                result[key]=parsed.astimezone(timezone.utc).isoformat()
    except FileNotFoundError:pass
    except (OSError,ValueError,TypeError,OverflowError):result['available']=False
    return result


def record(database, operation):
    if operation not in ('backup','verify','restore'):raise ValueError('Unknown maintenance operation')
    database=Path(database).resolve(strict=True)
    path=Path(str(database)+'.maintenance.json');lock=Path(str(database)+'.maintenance.lock')
    # O_NOFOLLOW prevents a local receipt path from redirecting writes to another file.
    descriptor=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        values={key:value for key,value in status(database).items() if key in KEYS}
        stamp=datetime.now(timezone.utc).isoformat()
        if operation=='backup':values.update(backup_at=stamp,verified_at=stamp)
        elif operation=='verify':values['verified_at']=stamp
        else:values['restore_verified_at']=stamp
        descriptor,name=tempfile.mkstemp(prefix='.otklik-maintenance-',dir=database.parent)
        try:
            with os.fdopen(descriptor,'w') as output:
                json.dump(values,output);output.flush();os.fsync(output.fileno())
            os.replace(name,path)
        finally:
            Path(name).unlink(missing_ok=True)
