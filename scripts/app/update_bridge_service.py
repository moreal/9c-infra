from tempfile import TemporaryFile
from time import time
from typing import List, NamedTuple

import structlog
import requests
from ruamel.yaml import YAML

from app.client import GithubClient
from app.config import config
from app.dockerhub.image import check_image_exists

logger = structlog.get_logger(__name__)


GQL_ENDPOINT = "https://9c-internal-rpc-1.nine-chronicles.com/graphql"
GQL_QUERY = {"query": "{ nodeStatus { tip { index } } }"}


class BridgeServiceUpdater:
    def __init__(self) -> None:
        self.github_client = GithubClient(
            config.github_token, org="planetarium", repo="TEMP"
        )

    def update(
        self,
        dir_name: str,
        file_name: str
    ):
        new_branch = f"update-bridge-service-{int(time())}"
        file_path = f"{dir_name}/multiplanetary/network/{file_name}.yaml"

        remote_values_file_contents, contents_response = self._init_github_ref(
            branch=new_branch,
            file_path=file_path,
        )
        result_values_file = remote_values_file_contents

        response = requests.post(GQL_ENDPOINT, json=GQL_QUERY)
        response_json = response.json()

        tip_index = response_json['data']['nodeStatus']['tip']['index']

        result_values_file = update_upstream_index(result_values_file, tip_index)

        pr_body = f"Update bridge-service to {tip_index}"
        self._create_pr(
            target_github_repo="9c-infra",
            base_commit_hash=contents_response["sha"],
            file_path=file_path,
            branch=new_branch,
            result_values=result_values_file,
            commit_msg=f"Update {file_path}",
            pr_body=pr_body,
        )
        logger.info("PR Created")

    def _init_github_ref(self, *, branch: str, file_path: str):
        self.github_client.org = "planetarium"
        self.github_client.repo = "9c-infra"
        head = self.github_client.get_ref(f"heads/main")
        logger.debug("Prev main branch ref", head_sha=head["object"]["sha"])

        self.github_client.create_ref(f"refs/heads/{branch}", head["object"]["sha"])
        logger.debug("Branch created", branch=branch)

        main_branch_file_contents, response = self.github_client.get_content(
            file_path, "main"
        )

        if main_branch_file_contents is None:
            raise

        return main_branch_file_contents, response

    def _create_pr(
        self,
        *,
        target_github_repo: str,
        base_commit_hash: str,
        file_path: str,
        branch: str,
        result_values: str,
        commit_msg: str,
        pr_body: str,
    ):
        self.github_client.repo = target_github_repo
        self.github_client.update_content(
            commit=base_commit_hash,
            path=file_path,
            branch=branch,
            content=result_values,
            message=commit_msg,
        )
        self.github_client.create_pull(
            title=f"Update bridge service [{branch}]",
            head=branch,
            base="main",
            body=pr_body,
            draft=False,
        )

def update_upstream_index(contents: str, tip_index: str):
    def update_index_recursively(data):
        if isinstance(data, dict):
            for key, value in data.items():
                if key == "defaultStartBlockIndex":
                    if "upstream" in data[key]:
                        data[key]["upstream"] = str(tip_index)
                else:
                    update_index_recursively(value)
        elif isinstance(data, list):
            for item in data:
                update_index_recursively(item)

    yaml = YAML()
    yaml.preserve_quotes = True  # type:ignore
    doc = yaml.load(contents)
    update_index_recursively(doc)

    with TemporaryFile(mode="w+") as fp:
        yaml.dump(doc, fp)
        fp.seek(0)
        new_doc = fp.read()

    return new_doc
