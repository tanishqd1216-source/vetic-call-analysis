"""Minimal import-safe Flask module for the project.

This file is intentionally conservative: it provides a working Flask
app with two endpoints and a `load_from_webapp` helper. It avoids
project-specific database or file-system mutations so it can be
imported safely during development.
"""

from flask import Flask, request, jsonify
import requests
from typing import Tuple, Dict

try:
    import pandas as pd
except Exception:
    pd = None

app = Flask(__name__)


def load_from_webapp(url: str) -> Tuple[Dict[str, 'pd.DataFrame'] | None, str]:
    """Fetch JSON from a Google Apps Script web app and convert to pandas DataFrames.

    Returns (sheets_dict, title) on success or (None, error_message) on failure.
    """
    if pd is None:
        return None, 'pandas required'
    try:
        # follow redirects and give a reasonable timeout
        r = requests.get(url, timeout=30, allow_redirects=True)
    except Exception as e:
        return None, f'Network error: {e}'

    # Surface non-2xx responses immediately with status and body preview
    if not (200 <= r.status_code < 300):
        preview = (r.text or '')[:1000]
        return None, f'HTTP {r.status_code} response from webapp; body preview: {preview}'

    # Try parsing JSON; if it's not JSON, return content-type and body preview to help debug
    content_type = r.headers.get('Content-Type', '')
    if 'application/json' not in content_type.lower():
        preview = (r.text or '')[:1000]
        return None, f'Expected JSON (Content-Type: {content_type}); body preview: {preview}'

    try:
        data = r.json()
        # list-of-lists (first row headers)
        if isinstance(data, list):
            if data and isinstance(data[0], list):
                headers = data[0]
                rows = data[1:]
                df = pd.DataFrame(rows, columns=headers)
                return {'Sheet1': df}, 'Web App Data'
            if data and isinstance(data[0], dict):
                df = pd.DataFrame(data)
                return {'Sheet1': df}, 'Web App Data'
        # dict with sheets/data
        if isinstance(data, dict):
            if 'sheets' in data and isinstance(data['sheets'], list):
                sheets = {}
                for s in data['sheets']:
                    name = s.get('name', 'Sheet')
                    rows = s.get('data', [])
                    if rows and isinstance(rows[0], list):
                        headers = rows[0]
                        df = pd.DataFrame(rows[1:], columns=headers)
                    else:
                        df = pd.DataFrame(rows)
                    sheets[name] = df
                return sheets, data.get('title', 'Web App Data')
            if 'data' in data:
                rows = data['data']
                if rows and isinstance(rows[0], list):
                    headers = rows[0]
                    df = pd.DataFrame(rows[1:], columns=headers)
                else:
                    df = pd.DataFrame(rows)
                return {'Sheet1': df}, data.get('title', 'Web App Data')
            df = pd.DataFrame(data)
            return {'Sheet1': df}, data.get('title', 'Web App Data')
        return None, 'Unsupported format'
    except Exception as e:
        return None, str(e)


@app.route('/api/connect-webapp', methods=['GET', 'POST'])
def connect_webapp():
    # Support both POST (JSON body) and GET (query param ?url=...) for convenience.
    if request.method == 'GET':
        url = (request.args.get('url') or '').strip()
    else:
        payload = request.get_json(force=True) or {}
        url = (payload.get('url') or '').strip()
    if not url:
        return jsonify({'error': 'url required'}), 400
    sheets, title = load_from_webapp(url)
    if sheets is None:
        return jsonify({'error': title}), 400
    return jsonify({'success': True, 'title': title, 'sheets': list(sheets.keys())})


@app.route('/api/refresh-webapp/<int:sheet_id>', methods=['POST'])
def refresh_webapp(sheet_id: int):
    # Minimal placeholder; the real app should implement DB/file updates.
    return jsonify({'error': 'not implemented in stub'}), 501


@app.route('/', methods=['GET'])
def index():
    """Simple health / index endpoint."""
    return jsonify({
        'status': 'ok',
        'message': 'Claude_setup minimal API',
        'endpoints': ['/api/connect-webapp (GET/POST)', '/api/refresh-webapp/<id> (POST)']
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000)
        
