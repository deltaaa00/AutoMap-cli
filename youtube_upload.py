"""Server-side YouTube OAuth and background uploads for Video Studio."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from flask import jsonify, redirect, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

SCOPE = 'https://www.googleapis.com/auth/youtube.upload'


def metadata(data):
    """Validate YouTube limits before spending time rendering or uploading."""
    if not isinstance(data, dict):
        raise ValueError('YouTube settings must be an object.')
    title = data.get('title', '')
    description = data.get('description', '')
    tags = data.get('tags', [])
    if not isinstance(title, str) or not title.strip() or len(title) > 100 or any(c in title for c in '<>'):
        raise ValueError('YouTube title must contain 1–100 characters and no < or >.')
    if not isinstance(description, str) or len(description.encode('utf-8')) > 5000 or any(c in description for c in '<>'):
        raise ValueError('YouTube description must be at most 5000 UTF-8 bytes and contain no < or >.')
    if isinstance(tags, str):
        tags = [tag.strip() for tag in tags.split(',') if tag.strip()]
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise ValueError('YouTube tags must be strings or comma-separated text.')
    tags = list(dict.fromkeys(tag.strip() for tag in tags))
    if sum(len(tag) + (2 if ' ' in tag else 0) for tag in tags) + max(0, len(tags) - 1) > 500:
        raise ValueError('YouTube tags exceed the 500-character limit.')
    privacy = data.get('privacy', 'private')
    if privacy not in ('private', 'unlisted', 'public'):
        raise ValueError('Invalid YouTube visibility.')
    kids = data.get('made_for_kids', False)
    if not isinstance(kids, bool):
        raise ValueError('Made for kids must be true or false.')
    return {'snippet': {'title': title.strip(), 'description': description, 'tags': tags, 'categoryId': '22'},
            'status': {'privacyStatus': privacy, 'selfDeclaredMadeForKids': kids}}


def client_config(data):
    """Accept Google client files, never arbitrary OAuth/token endpoints."""
    if not isinstance(data, dict):
        raise ValueError('Upload a Google OAuth client secret JSON file.')
    kind = 'web' if 'web' in data else 'installed'
    config = data.get(kind)
    if not isinstance(config, dict) or not all(isinstance(config.get(k), str) and config[k] for k in ('client_id', 'client_secret')):
        raise ValueError('Use an OAuth client secret file (Web application or Desktop app), not an API key or service account.')
    if config.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth' or config.get('token_uri') != 'https://oauth2.googleapis.com/token':
        raise ValueError('The client secret file must use the standard Google OAuth endpoints.')
    return {kind: config}


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class YouTubeUploads:
    def __init__(self, app, worker_url, http):
        self.app, self.worker_url, self.http = app, worker_url, http
        self.jobs = {}
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(2)
        self.signer = URLSafeTimedSerializer(app.secret_key, salt='youtube-render')
        from cryptography.fernet import Fernet
        encryption_key = hmac.new(str(app.secret_key).encode(), b'automap-youtube-storage-v1', hashlib.sha256).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(encryption_key))
        self._routes()

    def owner(self):
        if 'youtube_owner' not in session:
            session['youtube_owner'] = secrets.token_hex(24)
            session.permanent = True
        # Include the login identity so changing login credentials invalidates access.
        return hashlib.sha256((session['youtube_owner'] + session.get('web_identity', '')).encode()).hexdigest()

    def token_path(self, owner):
        root = Path(self.app.config.get('YOUTUBE_DATA_DIR', Path(self.app.instance_path) / 'youtube'))
        return root / (owner + '.json')

    def save_secret(self, path, value):
        ciphertext = self.cipher.encrypt(json.dumps(value).encode()).decode()
        _write_json(path, {'encrypted': ciphertext})

    def load_secret(self, path):
        from cryptography.fernet import InvalidToken
        try:
            return json.loads(self.cipher.decrypt(json.loads(path.read_text())['encrypted']).decode())
        except (InvalidToken, KeyError, ValueError):
            raise ValueError('Saved YouTube credentials cannot be opened. Connect your account again.') from None

    def callback_uri(self):
        # Local launches must return to their own host/port, even if a hosted
        # callback was left in the environment from another deployment.
        if not self.app.config.get('WEB_AUTH_ENABLED', False):
            return url_for('youtube_callback', _external=True)
        return (self.app.config.get('YOUTUBE_REDIRECT_URI') or os.environ.get('YOUTUBE_REDIRECT_URI')
                or url_for('youtube_callback', _external=True))

    def credentials(self, owner):
        from google.oauth2.credentials import Credentials
        path = self.token_path(owner)
        if not path.is_file():
            raise ValueError('Connect your YouTube account first.')
        return Credentials.from_authorized_user_info(self.load_secret(path), scopes=[SCOPE])

    def csrf(self):
        if 'youtube_csrf' not in session:
            session['youtube_csrf'] = secrets.token_urlsafe(32)
        return session['youtube_csrf']

    def valid_csrf(self):
        token = request.headers.get('X-YouTube-CSRF', '')
        return bool(token) and hmac.compare_digest(token.encode(), session.get('youtube_csrf', '').encode())

    def render_token(self, path):
        return self.signer.dumps({'owner': self.owner(), 'path': path})

    def check_ready(self, settings):
        result = metadata(settings)
        self.credentials(self.owner())
        return result

    def start(self, token, body):
        try:
            rendered = self.signer.loads(token, max_age=86400)
        except (BadSignature, TypeError):
            raise ValueError('Render this video again before uploading to YouTube.') from None
        owner = self.owner()
        if rendered.get('owner') != owner:
            raise ValueError('This video belongs to another browser session.')
        credentials = self.credentials(owner)
        with self.lock:
            # One upload per render, including double clicks and repeated requests.
            for job_id, job in self.jobs.items():
                if job['owner'] == owner and job['token'] == token:
                    return job_id
            if not self.slots.acquire(blocking=False):
                raise ValueError('YouTube upload queue is busy. Try again after the current uploads finish.')
            self.jobs = {key: job for key, job in self.jobs.items() if time.time() - job['created'] < 86400 or job['status'] == 'uploading'}
            job_id = secrets.token_hex(16)
            self.jobs[job_id] = {'owner': owner, 'token': token, 'created': time.time(), 'status': 'uploading', 'progress': 0, 'phase': 'Downloading rendered video'}
        threading.Thread(target=self._upload, args=(job_id, rendered['path'], body, credentials), daemon=True).start()
        return job_id

    def _update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)

    def _upload(self, job_id, path, body, credentials):
        youtube = None
        try:
            from google.auth.transport.requests import Request
            import httplib2
            from google_auth_httplib2 import AuthorizedHttp
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            if not credentials.valid:
                credentials.refresh(Request())
                # A disconnect must not be undone by a running upload.
                token_path = self.token_path(self.jobs[job_id]['owner'])
                with self.lock:
                    if token_path.exists():
                        self.save_secret(token_path, json.loads(credentials.to_json()))
            with tempfile.TemporaryDirectory(prefix='automap-youtube-') as directory:
                video = Path(directory) / 'render.mp4'
                with self.http.get(f'{self.worker_url}/api/download_video', params={'path': path}, stream=True, timeout=(10, 120)) as response:
                    response.raise_for_status()
                    with video.open('wb') as stream:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                stream.write(chunk)
                self._update(job_id, phase='Uploading to YouTube')
                youtube = build('youtube', 'v3', http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=120)), cache_discovery=False)
                media = MediaFileUpload(str(video), mimetype='video/mp4', chunksize=8 * 1024 * 1024, resumable=True)
                try:
                    upload = youtube.videos().insert(part='snippet,status', body=body, media_body=media)
                    result = None
                    while result is None:
                        progress, result = upload.next_chunk(num_retries=5)
                        if progress:
                            self._update(job_id, progress=round(progress.progress() * 100))
                    video_id = result.get('id', '')
                    if not isinstance(video_id, str) or not video_id or not all(c.isalnum() or c in '_-' for c in video_id):
                        raise ValueError('YouTube did not return a video ID.')
                    self._update(job_id, status='complete', progress=100, phase='Uploaded', url=f'https://www.youtube.com/shorts/{video_id}')
                finally:
                    media.stream().close()
        except Exception as error:
            # OAuth and HTTP exceptions can contain tokens, secrets or signed URLs.
            message = 'Upload failed. Check your Google authorization, YouTube API access and quota. The rendered video is still available to download. Check YouTube Studio before rendering again to avoid duplicates.'
            if getattr(error, 'resp', None) is not None:
                try:
                    reason = json.loads(error.content)['error']['errors'][0]['reason']
                    if isinstance(reason, str) and reason.isalnum():
                        message = f'YouTube API: {reason}. ' + message
                except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                    pass
            self._update(job_id, status='error', error=message, phase='Upload failed')
        finally:
            try:
                if youtube is not None:
                    youtube.close()
            finally:
                self.slots.release()

    def _routes(self):
        app = self.app

        @app.before_request
        def youtube_csrf():
            if request.path.startswith('/api/youtube/') and request.method == 'POST':
                if not self.valid_csrf():
                    return jsonify(code='youtube_csrf_invalid', error='Invalid YouTube request token. Reload the page.'), 403

        @app.get('/api/youtube/status')
        def youtube_status():
            response = jsonify(connected=self.token_path(self.owner()).is_file(), csrf_token=self.csrf(),
                               redirect_uri=self.callback_uri())
            response.headers['Cache-Control'] = 'no-store'
            return response

        @app.post('/api/youtube/connect')
        def youtube_connect():
            try:
                from google_auth_oauthlib.flow import Flow
                upload = request.files.get('client_secret')
                if upload is None:
                    raise ValueError('Choose your client secret JSON file.')
                raw = upload.read(65537)
                if len(raw) > 65536:
                    raise ValueError('Client secret file exceeds 64 KB.')
                try:
                    parsed = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    raise ValueError('Invalid client secret JSON file.') from None
                config = client_config(parsed)
                callback = self.callback_uri()
                if 'installed' in config and urlsplit(callback).hostname not in ('localhost', '127.0.0.1', '::1'):
                    raise ValueError('Desktop app credentials require localhost. For a hosted AutoMap use a Web application client with the displayed redirect URI.')
                if urlsplit(callback).scheme != 'https' and urlsplit(callback).hostname not in ('localhost', '127.0.0.1', '::1'):
                    raise ValueError('Hosted Google authorization requires HTTPS.')
                flow = Flow.from_client_config(config, scopes=[SCOPE], redirect_uri=callback, autogenerate_code_verifier=True)
                auth_url, state = flow.authorization_url(access_type='offline', prompt='consent select_account')
                owner = self.owner()
                pending = {'config': config, 'state': state, 'verifier': flow.code_verifier, 'redirect': callback, 'created': time.time()}
                pending['return_to'] = '/api-manager' if request.form.get('return_to') == '/api-manager' else '/studio'
                self.save_secret(self.token_path(owner).with_suffix('.pending'), pending)
                return jsonify(authorization_url=auth_url)
            except ValueError as error:
                return jsonify(error=str(error)), 400
            except ImportError:
                return jsonify(error='Install requirements.txt to enable YouTube uploads.'), 503

        @app.get('/api/youtube/callback')
        def youtube_callback():
            pending_path = self.token_path(self.owner()).with_suffix('.pending')
            return_to = '/studio'
            try:
                pending = self.load_secret(pending_path)
                if pending.get('return_to') == '/api-manager':
                    return_to = '/api-manager'
                state = request.args.get('state', '')
                if not state or not hmac.compare_digest(state, pending['state']) or time.time() - pending['created'] > 600:
                    raise ValueError('Expired or invalid OAuth state.')
                pending_path.unlink(missing_ok=True)
                if request.args.get('error') or not request.args.get('code'):
                    return redirect(return_to + '?youtube=denied')
                from google_auth_oauthlib.flow import Flow
                flow = Flow.from_client_config(pending['config'], scopes=[SCOPE], state=state,
                                               redirect_uri=pending['redirect'], code_verifier=pending['verifier'])
                flow.fetch_token(code=request.args['code'])
                if not flow.credentials.refresh_token:
                    raise ValueError('Missing refresh token.')
                self.save_secret(self.token_path(self.owner()), json.loads(flow.credentials.to_json()))
                return redirect(return_to + '?youtube=connected')
            except Exception:
                # Never echo an OAuth exception into a page or log.
                return redirect(return_to + '?youtube=error')

        @app.post('/api/youtube/disconnect')
        def youtube_disconnect():
            path = self.token_path(self.owner())
            with self.lock:
                path.unlink(missing_ok=True)
                path.with_suffix('.pending').unlink(missing_ok=True)
            return jsonify(connected=False)

        @app.post('/api/youtube/upload')
        def youtube_upload():
            data = request.get_json(silent=True) or {}
            try:
                job_id = self.start(data.get('upload_token'), self.check_ready(data.get('youtube')))
                return jsonify(job_id=job_id), 202
            except ValueError as error:
                return jsonify(error=str(error)), 400
            except ImportError:
                return jsonify(error='Install requirements.txt to enable YouTube uploads.'), 503

        @app.get('/api/youtube/jobs/<job_id>')
        def youtube_job(job_id):
            with self.lock:
                job = self.jobs.get(job_id)
                if not job or job['owner'] != self.owner():
                    return jsonify(error='Upload job not found. If AutoMap restarted, check YouTube Studio.'), 404
                response = jsonify({key: value for key, value in job.items() if key not in ('owner', 'token', 'created')})
            response.headers['Cache-Control'] = 'no-store'
            return response
