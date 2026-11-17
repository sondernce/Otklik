"""Verified SQLite snapshots and restoration to a new file; no HTTP endpoint."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

import database
import maintenance


def readonly(path):
    path = Path(path).resolve(strict=True)
    return sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)


def inspect(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if not 1 <= version <= database.SCHEMA_VERSION:
        raise ValueError('Неподдерживаемая версия базы.')
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('Нарушена целостность базы.')
    if db.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('В базе нарушены связи между записями.')
    required = {'settings','profiles','psychologists','clients','client_sessions','slots','appointments'}
    if version >= 2:
        required |= {'profile_publications','appointment_changes','appointment_events'}
    if version >= 3:
        required |= {'appointment_messages','support_tickets','support_messages'}
    if version >= 4:
        required.add('notifications')
    if version >= 5:
        required |= {'client_details','appointment_video'}
    if version >= 6:
        required |= {'psychologist_sessions','admin_sessions','login_attempts'}
    if version >= 7:
        required |= {'account_registry','admin_events'}
    present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required <= present:
        raise ValueError('В файле отсутствуют таблицы Отклика.')
    return {'schema_version': version, 'appointments': db.execute('SELECT count(*) FROM appointments').fetchone()[0]}


def verify(path):
    with closing(readonly(path)) as db:
        result = inspect(db)
    hasher = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            hasher.update(chunk)
    return result | {'sha256': hasher.hexdigest()}


def snapshot(source, output, restore=False):
    source = Path(source).resolve(strict=True)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError('Файл назначения уже существует. Выберите новое имя.')
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, stage_name = tempfile.mkstemp(prefix='.otklik-snapshot-', suffix='.sqlite3', dir=output.parent)
    os.close(descriptor)
    stage = Path(stage_name)
    deadline = time.monotonic()+30
    def progress(*_):
        if time.monotonic() > deadline:
            raise TimeoutError('Не удалось создать копию за 30 секунд. Повторите позже.')
    try:
        with closing(readonly(source)) as origin, closing(sqlite3.connect(stage)) as target:
            # sqlite backup includes committed WAL pages without copying a live database file.
            origin.backup(target, pages=256, progress=progress, sleep=0.05)
            inspect(target)
        if restore:
            with database.connect(stage) as db:
                # A backup must not revive sessions revoked after it was created.
                for table in ('client_sessions','psychologist_sessions','admin_sessions'):
                    db.execute(f'DELETE FROM {table}')
        result = verify(stage)
        with open(stage, 'rb') as snapshot_file:
            os.fsync(snapshot_file.fileno())
        # Publish without replacing an existing file, even if another process created it meanwhile.
        os.link(stage, output)
        return result | {'path': str(output)}
    finally:
        stage.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Копирование и проверка базы Отклика. Восстановление только в новый файл.')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create', help='Создать проверенную копию, в том числе при работающем сервере')
    create.add_argument('--database', required=True)
    create.add_argument('--output', required=True)
    check = commands.add_parser('verify', help='Проверить целостность и версию копии')
    check.add_argument('path')
    restore = commands.add_parser('restore', help='Восстановить в новый файл и завершить прежние сессии')
    restore.add_argument('--backup', required=True)
    restore.add_argument('--output', required=True)
    restore.add_argument('--record-for', help='Рабочая база, в кабинете которой отметить проверку восстановления')
    check.add_argument('--record-for', help='Рабочая база, в кабинете которой отметить проверку копии')
    args = parser.parse_args(argv)
    try:
        if args.command == 'verify':
            result = verify(args.path)
        else:
            result = snapshot(args.database if args.command == 'create' else args.backup, args.output, restore=args.command=='restore')
    except (OSError, sqlite3.Error, ValueError, TimeoutError) as error:
        parser.exit(1, f'Ошибка: {error}\n')
    record_for=args.database if args.command=='create' else args.record_for
    if record_for:
        try:
            maintenance.record(record_for,{'create':'backup','verify':'verify','restore':'restore'}[args.command])
        except (OSError,ValueError):
            result['monitoring_warning']='Операция выполнена, но сведения для кабинета записать не удалось.'
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
