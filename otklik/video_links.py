"""Participant-only meeting links supplied by the psychologist."""
from urllib.parse import urlsplit

import booking


def update(db, psychologist_id, data):
    db.execute('BEGIN IMMEDIATE')
    item = booking.owned_appointment(db, 'psychologist', psychologist_id, data.get('id'))
    booking.check_revision(item, data)
    booking.check_future(item, confirmed=True)
    url = data.get('url')
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('Проверьте ссылку на видеовстречу.')
    url = url.strip()
    if url:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or any(
            c.isspace() or ord(c) < 32 or c == '\\' for c in url
        ):
            raise ValueError('Введите HTTPS-ссылку на видеовстречу.')
        db.execute('INSERT INTO appointment_video VALUES (?,?) ON CONFLICT(appointment_id) DO UPDATE SET url=excluded.url', (item['id'], url))
    else:
        db.execute('DELETE FROM appointment_video WHERE appointment_id=?', (item['id'],))
    db.execute('UPDATE appointments SET revision=revision+1 WHERE id=?', (item['id'],))
    return {'video_url': url or None}
