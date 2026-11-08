"""No external network, personal credential or remote write is used by these tests."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import httpx
from scripts import publish_personal as publish


class PersonalPublishTests(unittest.TestCase):
    def test_identity_guard_rejects_another_account_before_repository_access(self):
        seen = []
        def respond(request):
            seen.append(str(request.url))
            return httpx.Response(200, json={'login': 'different-account'})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaisesRegex(ValueError, 'personal_account_mismatch'):
                publish.verify_identity(client)
        self.assertEqual(seen, ['https://api.github.com/user'])

    def test_repository_guard_requires_exact_owner_and_push_permission(self):
        for allowed, name in ((False, 'hhhhao139-ai/commerce-insight'),
                              (True, 'different-account/commerce-insight'),
                              (True, 'hhhhao139-ai/commerce-insight')):
            def respond(request):
                body = {'login': publish.OWNER} if request.url.path == '/user' else {
                    'full_name': name, 'owner': {'login': publish.OWNER}, 'permissions': {'push': allowed}}
                return httpx.Response(200, json=body)
            with httpx.Client(transport=httpx.MockTransport(respond)) as client:
                if allowed and name == publish.OWNER + '/' + publish.REPOSITORY:
                    publish.verify_identity(client)
                else:
                    with self.assertRaisesRegex(ValueError, 'repository_or_permission_mismatch'):
                        publish.verify_identity(client)

    def test_environment_drops_injected_auth_and_trace(self):
        with patch.dict(os.environ, {'GIT_TRACE': '1', 'GIT_CONFIG_COUNT': '1',
                                    'GITHUB_TOKEN': 'fake', 'HTTPS_PROXY': 'fake'}):
            environment = publish.git_environment()
        self.assertFalse(set(environment) & {'GIT_TRACE', 'GIT_CONFIG_COUNT', 'GITHUB_TOKEN', 'HTTPS_PROXY'})
        self.assertEqual(environment['GIT_CONFIG_GLOBAL'], '/dev/null')

    def test_real_git_askpass_is_scoped_without_network(self):
        # Exercises this machine's Git prompt format using only a synthetic value.
        environment = publish.git_environment()
        environment.update(GIT_ASKPASS=str(publish.ROOT / 'scripts/personal_git_askpass.py'),
                           COMMERCE_PERSONAL_GIT_TOKEN='synthetic-publish-test')
        process = subprocess.run(publish.git_command('credential', 'fill'), env=environment,
            input='protocol=https\nhost=github.com\npath=hhhhao139-ai/commerce-insight.git\n\n',
            text=True, capture_output=True)
        self.assertEqual(process.returncode, 0)
        self.assertIn('password=synthetic-publish-test\n', process.stdout)
        rejected = subprocess.run([str(publish.ROOT / 'scripts/personal_git_askpass.py'),
            "Password for 'https://hhhhao139-ai@github.com/another/project.git': "],
            env=environment, capture_output=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(rejected.stdout, b'')


if __name__ == '__main__':
    unittest.main()
