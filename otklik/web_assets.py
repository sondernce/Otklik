"""Explicit public routes: source files and local databases are never served."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGES = {'/': 'index.html', '/admin': 'admin.html', '/psychologist': 'psychologist.html', '/client': 'client.html'}
HTML = {'index.html', 'admin.html', 'psychologist.html', 'client.html', 'documents.html'}
JS = {'admin-monitor.js', 'http.js', 'app.js', 'admin.js', 'psychologist.js', 'client.js', 'client-home.js', 'booking-ui.js', 'communications-ui.js', 'form-errors.js'}
CSS = {'styles.css', 'admin.css', 'client.css', 'dashboard.css', 'psychologist.css'}
MIME = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.jpeg': 'image/jpeg', '.jpg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}


def resolve(path):
    file = None
    if path in PAGES:
        file = ROOT / 'templates' / PAGES[path]
    elif path in {'/' + name for name in HTML}:
        file = ROOT / 'templates' / path[1:]
    else:
        # Preserve existing bookmarks and asset URLs while pages use /static/.
        for folder, names in (('js', JS), ('css', CSS)):
            for name in names:
                if path in {'/' + name, '/static/' + folder + '/' + name}:
                    file = ROOT / 'static' / folder / name
        if path == '/psychologist-dashboard.css':
            file = ROOT / 'static/css/dashboard.css'
        if path.startswith('/assets/'):
            candidate = (ROOT / path.lstrip('/')).resolve()
            if candidate.is_relative_to(ROOT / 'assets') and candidate.suffix.lower() in {'.svg', '.png', '.jpeg', '.jpg', '.webp'}:
                file = candidate
    return file if file is not None and file.is_file() else None


def content_type(file):
    return MIME[file.suffix.lower()]
