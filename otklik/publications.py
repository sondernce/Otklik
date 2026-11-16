"""Approved public profile snapshots, independent of HTTP and booking."""
import json

from timestamps import now

PUBLIC_FIELDS = {'id', 'name', 'initials', 'gender', 'price', 'experience', 'topics', 'style', 'approach', 'bio', 'photo', 'education'}


def publish(db, profile):
    public = {key: value for key, value in profile.items() if key in PUBLIC_FIELDS}
    db.execute('INSERT INTO profile_publications VALUES(?,?,?) ON CONFLICT(profile_id) '
               'DO UPDATE SET data=excluded.data,published_at=excluded.published_at',
               (profile['id'], json.dumps(public, ensure_ascii=False), now()))


def publication(db, profile_id):
    row = db.execute('SELECT data FROM profile_publications WHERE profile_id=?', (profile_id,)).fetchone()
    return json.loads(row[0]) if row else None
