"""Tests for the agent's render and approval workflows without live APIs."""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

import requests

from agent.automap_client import AutoMapClient, AutoMapClientError
from agent.db import AgentDB
from agent.media_pool import MediaPool
from agent.optimizer import EFFECT_ARMS
from agent.runner import AgentRunner


class AgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_render_sends_maps_and_media_as_binary_parts(self):
        bg = self.root / 'bg.mp4'
        bg.write_bytes(b'background')
        audio = self.root / 'track.mp3'
        audio.write_bytes(b'music')
        client = AutoMapClient()
        response = Mock(ok=True)
        response.json.return_value = {'status': 'success'}

        with patch.object(client, '_request', return_value=response) as request:
            client.render_video(
                map_data_url='data:image/png;base64,bWFw',
                background_path=bg,
                audio_path=audio,
                video_title='Title',
                extra_params={'reveal_clean_map_file': 'data:image/png;base64,Y2xlYW4='},
            )

        self.assertEqual(request.call_args.args, ('POST', '/api/render_video'))
        sent = request.call_args.kwargs
        self.assertEqual(json.loads(sent['data']['payload'])['audio_title'], 'track')
        self.assertEqual({key: file[1] for key, file in sent['files'].items()}, {
            'map_file': b'map', 'background_video': b'background', 'audio_file': b'music',
            'reveal_clean_map_file': b'clean',
        })

    def test_interrupted_download_preserves_previous_file(self):
        dest = self.root / 'exp_1.mp4'
        dest.write_bytes(b'previous complete video')
        client = AutoMapClient()
        response = MagicMock()
        response.__enter__.return_value = response

        def interrupted():
            yield b'first chunk'
            raise requests.ConnectionError('download interrupted')

        response.iter_content.return_value = interrupted()
        with patch.object(client, '_request', return_value=response):
            with self.assertRaises(requests.ConnectionError):
                client.download_rendered_video('/api/download_video', dest)
        self.assertEqual(dest.read_bytes(), b'previous complete video')
        self.assertEqual(list(self.root.glob('*.part')), [])

    def test_failed_clean_map_keeps_zoom_without_reveal(self):
        runner = AgentRunner.__new__(AgentRunner)
        runner.db = AgentDB(self.root / 'agent.db')
        runner.media_pool = MediaPool(self.root / 'media')
        runner.media_pool.ensure_dirs()
        (runner.media_pool.backgrounds_dir / 'bg.mp4').write_bytes(b'background')
        runner.renders_dir = self.root
        runner.optimizer = Mock()
        runner.optimizer.choose_arm.return_value = (EFFECT_ARMS[0], 'test')
        runner.client = Mock()
        runner.client.generate_map.side_effect = [
            {'image_url': 'original-map', 'map_values': {'france': 10}},
            AutoMapClientError('clean map unavailable'),
        ]
        runner.client.get_zoom_countries.return_value = {'europe': [{'key': 'france', 'x': 0.5, 'y': 0.4}]}
        runner.client.render_video.return_value = {'download_url': '/video'}
        runner.client.download_rendered_video.side_effect = lambda _url, path: path.write_bytes(b'rendered video')
        runner.bot = Mock()
        runner.bot.send_approval_video.return_value = None

        with patch('agent.runner.random.random', return_value=0), self.assertLogs('automap-agent', level='WARNING'):
            exp = runner.generate_candidate_video(forced_topic='Test map')

        clean_request = runner.client.generate_map.call_args.args[0]
        self.assertEqual(clean_request['ai_answer_override'], {'france': 10})
        self.assertEqual(clean_request['skip_label_countries'], ['france'])
        options = runner.client.render_video.call_args.kwargs['extra_params']
        self.assertTrue(options['zoom_enabled'])
        self.assertFalse(options['reveal_enabled'])
        self.assertEqual(exp['status'], 'pending_approval')

    def test_repeated_approval_does_not_upload_again(self):
        db = AgentDB(self.root / 'agent.db')
        mp4 = self.root / 'exp_1.mp4'
        mp4.write_bytes(b'rendered video')
        exp = db.create_experiment(topic='Test', arm='baseline', params={}, mp4_path=str(mp4),
                                   scheduled_for='2020-01-01T00:00:00Z')
        runner = AgentRunner.__new__(AgentRunner)
        runner.db = db

        def upload(**kwargs):
            with self.assertRaisesRegex(ValueError, 'no longer awaiting approval'):
                runner.handle_experiment_action(exp['id'], 'publish')
            return 'youtube-id', 'https://example.com/shorts/youtube-id'

        runner.intel = Mock()
        runner.intel.upload_video.side_effect = upload
        runner.handle_experiment_action(exp['id'], 'schedule')
        with self.assertRaisesRegex(ValueError, 'no longer awaiting approval'):
            runner.handle_experiment_action(exp['id'], 'reject')

        runner.intel.upload_video.assert_called_once()
        publish_at = runner.intel.upload_video.call_args.kwargs['publish_at']
        self.assertGreater(datetime.fromisoformat(publish_at), datetime.now(timezone.utc))
        stored = db.get_experiment(exp['id'])
        self.assertEqual(stored['status'], 'scheduled')
        self.assertEqual(stored['youtube_video_id'], 'youtube-id')
        self.assertEqual(stored['scheduled_for'], publish_at)
