"""Publish one reviewed commit using terminal-only personal authentication.

This owner-specific release utility is not part of the application runtime.
It never loads workstation Git credentials, asks for a model key, or stores a PAT.
"""
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
PERSONAL_ROOT = Path('/Users/haohao4/Downloads/multi-agent.zip/commerce-insight')
OWNER = 'hhh-hhh139'
REPOSITORY = 'commerce-insight'
REMOTE = f'https://github.com/{OWNER}/{REPOSITORY}.git'
REVIEW = ROOT / '.local/personal-release/approved.json'


def git_environment():
    # An allowlist, not a copy of shell variables. Ignore global/system Git config,
    # credential helpers, SSH, proxy settings, trace settings and injected Git options.
    return {'PATH': str(Path(sys.executable).parent) + ':/usr/bin:/bin',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_TERMINAL_PROMPT': '0', 'LANG': 'en_US.UTF-8'}


def git_command(*args):
    return ['/usr/bin/git', '-C', str(ROOT), '-c', 'credential.helper=',
            '-c', 'core.hooksPath=/dev/null', '-c', 'http.followRedirects=false',
            '-c', 'credential.useHttpPath=true', '-c', 'credential.username=' + OWNER,
            '-c', 'http.proactiveAuth=basic', '-c', 'http.emptyAuth=false', *args]


def reviewed_commit():
    if ROOT != PERSONAL_ROOT or (ROOT / '.git').is_symlink() or not (ROOT / '.git').is_dir():
        raise ValueError('personal_project_required')
    version = subprocess.check_output(git_command('--version'), env=git_environment(), text=True)
    if not version.startswith('git version 2.50.1 '):
        raise ValueError('revalidate_git_proactive_auth_before_using_another_version')
    # Our independent repository has only this locally generated core config.
    # Reject include/url rewrite/credential sections without evaluating them.
    from configparser import ConfigParser
    config = ConfigParser(); config.read(ROOT / '.git/config')
    if config.sections() != ['core'] or set(config['core']) - {
            'repositoryformatversion', 'filemode', 'bare', 'logallrefupdates',
            'ignorecase', 'precomposeunicode'}:
        raise ValueError('unexpected_local_git_configuration')
    review = json.loads(REVIEW.read_text())
    if review['owner'] != OWNER or review['remote'] != REMOTE:
        raise ValueError('release_destination_mismatch')
    commit = review['commit']
    if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
        raise ValueError('invalid_reviewed_commit')
    tree = subprocess.check_output(git_command('rev-parse', commit + '^{tree}'),
                                   env=git_environment(), text=True).strip()
    if tree != review['tree']:
        raise ValueError('reviewed_tree_mismatch')
    return commit


def verify_identity(client):
    user = client.get('https://api.github.com/user')
    if user.status_code != 200 or user.json().get('login') != OWNER:
        raise ValueError('personal_account_mismatch_or_authentication_failed')
    repo = client.get(f'https://api.github.com/repos/{OWNER}/{REPOSITORY}')
    if repo.status_code != 200:
        raise ValueError('personal_repository_unavailable')
    data = repo.json()
    if (data.get('full_name') != f'{OWNER}/{REPOSITORY}'
            or data.get('owner', {}).get('login') != OWNER
            or not data.get('permissions', {}).get('push')):
        raise ValueError('personal_repository_or_permission_mismatch')


def main():
    commit = reviewed_commit()
    print(f'只发布已审核提交 {commit} 到 {REMOTE} 的 main。')
    print('请输入仅授权该仓库的个人 GitHub fine-grained PAT；不会回显或落盘。')
    if not sys.stdin.isatty():
        raise ValueError('personal_interactive_terminal_required')
    token = getpass.getpass('Personal GitHub PAT: ').strip()
    if not token or any(c.isspace() for c in token):
        raise ValueError('invalid_token_input')
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=30,
                          headers={'Authorization': 'Bearer ' + token,
                                   'Accept': 'application/vnd.github+json'}) as client:
            verify_identity(client)
            environment = git_environment()
            environment.update(GIT_ASKPASS=str(ROOT / 'scripts/personal_git_askpass.py'),
                               COMMERCE_PERSONAL_GIT_TOKEN=token)
            try:
                process = subprocess.run(git_command('push', REMOTE, commit + ':refs/heads/main'),
                    env=environment, capture_output=True, text=True, timeout=180)
            finally:
                environment.pop('COMMERCE_PERSONAL_GIT_TOKEN', None)
            if process.returncode:
                # Do not persist or print arbitrary authenticated subprocess output.
                raise ValueError('push_failed_check_contents_and_workflows_permissions_or_remote_history')
            ref = client.get(f'https://api.github.com/repos/{OWNER}/{REPOSITORY}/git/ref/heads/main')
            if ref.status_code != 200 or ref.json().get('object', {}).get('sha') != commit:
                raise ValueError('remote_commit_not_confirmed')
        result = {'repository': f'{OWNER}/{REPOSITORY}', 'commit': commit,
                  'remote_verified': True, 'credential_saved': False}
        (REVIEW.parent / 'published.json').write_text(json.dumps(result, indent=2) + '\n')
        print('已核对远程提交。可以回到聊天回复“已发布”。')
    finally:
        token = ''


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        # Never include request/response bodies or arbitrary error text in output.
        safe_code = str(error) if type(error) is ValueError else type(error).__name__
        print('发布未确认：' + safe_code, file=sys.stderr)
        raise SystemExit(2)
