"""One-shot, non-executing task audit and exact Git-tree aggregation."""
import json
import re
import subprocess
from pathlib import Path


def git(*args):
    return subprocess.check_output(['git', *args])


def text(*args):
    return git(*args).decode('utf-8').strip()


def tree_files(sha, path):
    return [p.decode('utf-8') for p in git('ls-tree', '-r', '--name-only', '-z', sha + ':' + path).split(b'\0') if p]


spec = json.loads(Path('.github/finalpool-selection.json').read_text())
assert text('branch', '--show-current') == 'finalpool'
assert subprocess.call(['git', 'merge-base', '--is-ancestor', spec['base_sha'], 'HEAD']) == 0
assert not text('status', '--porcelain')
assert not Path('tasks/finalpool').exists()
example = 'tasks/examples/example-task/'
for relative in ['readme.txt', 'docs/task.md', 'docs/agent_system_prompt.md', 'docs/user_system_prompt.md', 'preprocess/main.py', 'evaluation/main.py', 'initial_workspace/readme.txt', 'groundtruth_workspace/readme.txt']:
    print('EXAMPLE_JSON:' + json.dumps({'path': example + relative, 'content': git('show', spec['base_sha'] + ':' + example + relative).decode('utf-8')}, ensure_ascii=True), flush=True)

cjk = re.compile('[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]')
audits = []
selected = []
reserved = {'docs', 'preprocess', 'evaluation', 'initial_workspace', 'groundtruth_workspace'}
for branch in spec['branches']:
    sha = branch['sha']
    assert text('rev-parse', sha + '^{commit}') == sha
    root = 'tasks/' + branch['folder']
    known = set(branch['implemented'] + branch['implementing'])
    directories = []
    for record in git('ls-tree', '-z', sha + ':' + root).split(b'\0'):
        if not record:
            continue
        metadata, name = record.split(b'\t', 1)
        if metadata.split()[1] == b'tree' and name.decode() not in reserved:
            directories.append(name.decode())
    assert known <= set(directories), (branch['branch'], known - set(directories))
    new_paths = [(name, root + '/' + name) for name in sorted(set(directories) - known)]
    root_files = tree_files(sha, root)
    if 'docs/task.md' in root_files or 'docs/agent_system_prompt.md' in root_files:
        new_paths.append((None, root))
    latest_message = text('show', '-s', '--format=%s', sha)
    print('BRANCH_JSON:' + json.dumps({'branch': branch['branch'], 'sha': sha, 'latest_message': latest_message, 'untracked_task_paths': [p for _, p in new_paths]}, ensure_ascii=True), flush=True)
    for name, path in new_paths:
        files = tree_files(sha, path)
        if name is None:
            files = [p for p in files if p.split('/')[0] in reserved or '/' not in p]
        issues = []
        docs = {}
        for doc in ['docs/task.md', 'docs/agent_system_prompt.md', 'docs/user_system_prompt.md']:
            if doc not in files:
                if doc != 'docs/user_system_prompt.md':
                    issues.append('Missing required ' + doc)
                continue
            try:
                value = git('show', sha + ':' + path + '/' + doc).decode('utf-8-sig')
            except UnicodeDecodeError:
                issues.append(doc + ' is not readable UTF-8 English text')
                continue
            docs[doc] = value
            if doc != 'docs/user_system_prompt.md' and not value.strip():
                issues.append(doc + ' is empty')
            if value.strip() and cjk.search(value):
                issues.append(doc + ' contains Chinese/non-English text')
        if 'task_config.json' not in files:
            issues.append('Missing required task_config.json (needed_mcp_servers; needed_local_tools including claim_done)')
        else:
            try:
                config = json.loads(git('show', sha + ':' + path + '/task_config.json').decode('utf-8-sig'))
                if not isinstance(config, dict):
                    issues.append('task_config.json is not a JSON object')
                else:
                    if not config.get('needed_mcp_servers'):
                        issues.append('task_config.json: needed_mcp_servers is empty or missing')
                    local_tools = config.get('needed_local_tools')
                    if not local_tools or 'claim_done' not in local_tools:
                        issues.append('task_config.json: needed_local_tools must be non-empty and contain claim_done')
            except (ValueError, TypeError, UnicodeDecodeError):
                issues.append('task_config.json is invalid')
        introduction = text('log', '-1', '--format=%H', '--diff-filter=A', sha, '--', path + '/docs/task.md')
        audit = {'branch': branch['branch'], 'source_sha': sha, 'name': name, 'path': path, 'task_introduction_commit': introduction or None, 'latest_commit_message': latest_message, 'status': 'implementing' if issues else 'implemented', 'issues': issues, 'files': files, 'docs': docs}
        if name is None:
            audit['note'] = 'Unnamed task: blank task heading and no dedicated task directory; tracked by its actual source path.'
        audits.append(audit)
        print('TASK_AUDIT_JSON:' + json.dumps(audit, ensure_ascii=True), flush=True)
        if not issues:
            assert name is not None, 'A passing unnamed task needs an explicit destination review'
            selected.append({'name': name, 'branch': branch['branch'], 'source_sha': sha, 'source_path': path, 'selection': 'new task satisfying example requirements'})
    for name in sorted(branch['implemented']):
        selected.append({'name': name, 'branch': branch['branch'], 'source_sha': sha, 'source_path': root + '/' + name, 'selection': 'previously implemented in Task Tracker'})

assert len({x['name'] for x in selected}) == len(selected), 'Destination task names must be unique'
for task in sorted(selected, key=lambda t: t['name']):
    source = task['source_sha'] + ':' + task['source_path']
    assert text('cat-file', '-t', source) == 'tree'
    task['destination_path'] = 'tasks/finalpool/' + task['name']
    task['source_tree_sha'] = text('rev-parse', source)
    task['file_count'] = len(tree_files(task['source_sha'], task['source_path']))
    subprocess.check_call(['git', 'read-tree', '--prefix=' + task['destination_path'] + '/', source])
subprocess.check_call(['git', 'checkout-index', '--all', '--force'])
manifest = {'source_base_sha': spec['base_sha'], 'selection_policy': 'Preserve all previously implemented Task Tracker tasks and add new tasks only if they satisfy tasks/examples. Do not reclassify historical tasks.', 'tasks': sorted(selected, key=lambda t: t['name'])}
output = Path('tasks/finalpool')
output.mkdir(parents=True, exist_ok=True)
(output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
passed = sum(t['status'] == 'implemented' for t in audits)
failed = len(audits) - passed
(output / 'README.md').write_text('# Final task pool\n\n' + str(len(selected)) + ' implemented tasks selected from Task Tracker.\n\n## Selection\n\nAll previously implemented tasks are retained with their existing status. The newest untracked tasks across all 14 developer branches were checked against `tasks/examples`: ' + str(passed) + ' implemented and ' + str(failed) + ' implementing. Historical tasks were not reclassified.\n\nOnly explicit content requirements and required-file existence were checked. Optional preprocessing/evaluation scripts and workspace folders are not blockers; task code was not executed.\n\n## Provenance\n\n`manifest.json` records each task\'s source branch, pinned commit, source path, file count and Git tree hash. Every copied task directory is byte-for-byte identical to its source Git tree, including file modes. Tasks still implementing are excluded.\n', encoding='utf-8')
subprocess.check_call(['git', 'add', '-f', '--', 'tasks/finalpool/manifest.json', 'tasks/finalpool/README.md'])
# The caller removes the temporary automation via the GitHub API after verification.
# This job only commits task content and does not require workflow-write permission.
subprocess.check_call(['git', 'config', 'user.name', 'github-actions[bot]'])
subprocess.check_call(['git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com'])
subprocess.check_call(['git', 'commit', '-m', 'Populate tasks/finalpool with implemented tasks from Task Tracker'])
for task in selected:
    actual_tree = text('rev-parse', 'HEAD:' + task['destination_path'])
    assert actual_tree == task['source_tree_sha'], task
    print('COPIED_TASK_JSON:' + json.dumps(task, ensure_ascii=True), flush=True)
assert not text('status', '--porcelain')
subprocess.check_call(['git', 'push', 'origin', 'HEAD:refs/heads/finalpool'])
print('FINALPOOL_RESULT_JSON:' + json.dumps({'commit': text('rev-parse', 'HEAD'), 'new_tasks_checked': len(audits), 'new_implemented': passed, 'new_implementing': failed, 'pooled_tasks': len(selected), 'copied_files': sum(t['file_count'] for t in selected), 'all_task_tree_hashes_verified': True}), flush=True)
