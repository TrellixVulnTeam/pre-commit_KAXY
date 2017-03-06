from __future__ import print_function
from __future__ import unicode_literals

from collections import OrderedDict

from aspy.yaml import ordered_dump
from aspy.yaml import ordered_load

import pre_commit.constants as C
from pre_commit import output
from pre_commit.clientlib import CONFIG_SCHEMA
from pre_commit.clientlib import is_local_repo
from pre_commit.clientlib import load_config
from pre_commit.repository import Repository
from pre_commit.schema import remove_defaults
from pre_commit.util import CalledProcessError
from pre_commit.util import cmd_output
from pre_commit.util import cwd


class RepositoryCannotBeUpdatedError(RuntimeError):
    pass


def _update_repo(repo_config, runner, tags_only):
    """Updates a repository to the tip of `master`.  If the repository cannot
    be updated because a hook that is configured does not exist in `master`,
    this raises a RepositoryCannotBeUpdatedError

    Args:
        repo_config - A config for a repository
    """
    repo = Repository.create(repo_config, runner.store)

    with cwd(repo._repo_path):
        cmd_output('git', 'fetch')
        tag_cmd = ('git', 'describe', 'origin/master', '--tags')
        if tags_only:
            tag_cmd += ('--abbrev=0',)
        else:
            tag_cmd += ('--exact',)
        try:
            rev = cmd_output(*tag_cmd)[1].strip()
        except CalledProcessError:
            rev = cmd_output('git', 'rev-parse', 'origin/master')[1].strip()

    # Don't bother trying to update if our sha is the same
    if rev == repo_config['sha']:
        return repo_config

    # Construct a new config with the head sha
    new_config = OrderedDict(repo_config)
    new_config['sha'] = rev
    new_repo = Repository.create(new_config, runner.store)

    # See if any of our hooks were deleted with the new commits
    hooks = {hook['id'] for hook in repo.repo_config['hooks']}
    hooks_missing = hooks - (hooks & set(new_repo.manifest.hooks))
    if hooks_missing:
        raise RepositoryCannotBeUpdatedError(
            'Cannot update because the tip of master is missing these hooks:\n'
            '{}'.format(', '.join(sorted(hooks_missing)))
        )

    return new_config


def autoupdate(runner, tags_only):
    """Auto-update the pre-commit config to the latest versions of repos."""
    retv = 0
    output_configs = []
    changed = False

    input_configs = load_config(
        runner.config_file_path,
        load_strategy=ordered_load,
    )

    for repo_config in input_configs:
        if is_local_repo(repo_config):
            output_configs.append(repo_config)
            continue
        output.write('Updating {}...'.format(repo_config['repo']))
        try:
            new_repo_config = _update_repo(repo_config, runner, tags_only)
        except RepositoryCannotBeUpdatedError as error:
            output.write_line(error.args[0])
            output_configs.append(repo_config)
            retv = 1
            continue

        if new_repo_config['sha'] != repo_config['sha']:
            changed = True
            output.write_line('updating {} -> {}.'.format(
                repo_config['sha'], new_repo_config['sha'],
            ))
            output_configs.append(new_repo_config)
        else:
            output.write_line('already up to date.')
            output_configs.append(repo_config)

    if changed:
        with open(runner.config_file_path, 'w') as config_file:
            config_file.write(ordered_dump(
                remove_defaults(output_configs, CONFIG_SCHEMA),
                **C.YAML_DUMP_KWARGS
            ))

    return retv
