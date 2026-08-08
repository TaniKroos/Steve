"""
Git/GitHub tool group (plan §6.4) -- deliberately two different
mechanisms in one tool group:

  - `git_create_pr` / `git_update_pr_description` run **on the execution
    plane**, via `gh` inside the sandbox (`shell_exec`'s same mechanism,
    just called directly instead of through the LLM). They reuse the
    credential context the earlier `git push` already authenticated with
    -- `gh` picks up `GITHUB_TOKEN` from the sandbox's own environment
    natively, no separate auth step. Agent Loop's own process is never a
    party to these GitHub calls at all.
  - `git_view_pr` / `git_pr_checks` / `git_list_repos` are **direct API
    calls** (`GithubApiClient` below), unrelated to the sandbox's working
    directory, so routing them through a shell would buy nothing.

`git_pr_checks` needs the GitHub App's "Checks: Read" permission, which
per the plan was deliberately left ungranted when the App was set up --
this tool will 403 until that's added in the App's settings; not
something this code can fix.
"""

import shlex
from datetime import datetime, timedelta, timezone

import httpx
from cloudagent_core.github_app import GithubApp

from app.tools.base import Tool, ToolContext, ToolResult

_GITHUB_API_BASE = "https://api.github.com"
# Real installation tokens run ~1hr; refreshing proactively well before
# that (rather than reacting to an actual 401) keeps a long-running
# session's direct-API calls from ever hitting an expired token, without
# needing the real expiry timestamp -- which backend's /start payload
# doesn't carry (see the plan's design note on why the contract didn't
# need to change for this).
_TOKEN_REFRESH_MARGIN = timedelta(minutes=45)


class GithubApiClient:
    """Holds the one installation token this session uses for direct API
    reads, refreshing it via the same shared `GithubApp.mint_installation_token`
    backend already uses (plan §6.4's last paragraph -- this is exactly
    why that logic lives in cloudagent_core instead of duplicated here)."""

    def __init__(
        self, github_app: GithubApp, installation_id: int, repo_github_id: int, initial_token: str
    ) -> None:
        self._github_app = github_app
        self._installation_id = installation_id
        self._repo_github_id = repo_github_id
        self._token = initial_token
        self._minted_at = datetime.now(timezone.utc)

    async def _current_token(self) -> str:
        if datetime.now(timezone.utc) - self._minted_at > _TOKEN_REFRESH_MARGIN:
            self._token = await self._github_app.mint_installation_token(
                self._installation_id, repository_ids=[self._repo_github_id]
            )
            self._minted_at = datetime.now(timezone.utc)
        return self._token

    async def get_pr(self, owner: str, repo: str, number: int) -> dict:
        token = await self._current_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()
            return response.json()

    async def list_check_runs(self, owner: str, repo: str, ref: str) -> dict:
        token = await self._current_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{ref}/check-runs",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()
            return response.json()

    async def list_installation_repos(self) -> dict:
        token = await self._current_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_GITHUB_API_BASE}/installation/repositories",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()
            return response.json()


class GitCreatePrTool(Tool):
    name = "git_create_pr"
    description = "Push the current branch (if not already pushed) and open a pull request against the default branch."
    parameters = {
        "type": "object",
        "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
        "required": ["title", "body"],
    }

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        title, body = input["title"], input["body"]
        # `--body-file` avoids every shell-quoting/newline hazard a
        # `--body "..."` argument would have -- write the body to a real
        # file via the filesystem API (same precise-edit reasoning as the
        # Editor tool group, not a shell heredoc) rather than trying to
        # escape arbitrary model-generated markdown into one command-line
        # argument.
        body_path = f"{context.repo_dir}/.cloudagent_pr_body.md"
        await context.sandbox.write_file(body_path, body)

        cmd = (
            f"cd {shlex.quote(context.repo_dir)} && "
            f"gh pr create --title {shlex.quote(title)} --body-file {shlex.quote(body_path)} "
            f"--base {shlex.quote(context.default_branch)}"
        )
        outcome = await context.sandbox.run_command(cmd, timeout=60)
        if outcome.exit_code != 0:
            return ToolResult(
                tool_use_id=tool_use_id, content=f"gh pr create failed:\n{outcome.stderr}", is_error=True
            )

        pr_url = outcome.stdout.strip().splitlines()[-1] if outcome.stdout.strip() else ""
        pr_number = int(pr_url.rstrip("/").rsplit("/", 1)[-1]) if pr_url else None

        branch_outcome = await context.sandbox.run_command(
            f"git -C {shlex.quote(context.repo_dir)} rev-parse --abbrev-ref HEAD", timeout=10
        )
        branch_name = branch_outcome.stdout.strip()

        if pr_number is not None:
            # Agent Loop owns this write directly (plan §2.2) -- never
            # proxied through backend. This is also the precise moment
            # plan §2.3 steps 10-11 describe: persist the PR, then tell
            # the browser about it, right as the PR actually opens (not
            # deferred to whenever the loop happens to end naturally,
            # since the model may keep working after this).
            await context.record_pr_opened(branch_name, pr_number, pr_url)
            await context.events.publish(context.session_id, {"type": "done", "pr_url": pr_url, "pr_number": pr_number})

        return ToolResult(
            tool_use_id=tool_use_id,
            content=f"opened pull request: {pr_url}",
            output={"pr_url": pr_url, "pr_number": pr_number, "branch": branch_name},
        )


class GitUpdatePrDescriptionTool(Tool):
    name = "git_update_pr_description"
    description = "Update the current branch's pull request description."
    parameters = {"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"]}

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        body_path = f"{context.repo_dir}/.cloudagent_pr_body.md"
        await context.sandbox.write_file(body_path, input["body"])
        cmd = f"cd {shlex.quote(context.repo_dir)} && gh pr edit --body-file {shlex.quote(body_path)}"
        outcome = await context.sandbox.run_command(cmd, timeout=30)
        if outcome.exit_code != 0:
            return ToolResult(tool_use_id=tool_use_id, content=f"gh pr edit failed:\n{outcome.stderr}", is_error=True)
        return ToolResult(tool_use_id=tool_use_id, content="pull request description updated")


class GitViewPrTool(Tool):
    name = "git_view_pr"
    description = "View a pull request's current state (title, body, status, mergeable) by number."
    parameters = {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]}

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        owner, repo = context.repo_full_name.split("/", 1)
        try:
            pr = await context.github.get_pr(owner, repo, input["number"])
        except httpx.HTTPStatusError as exc:
            return ToolResult(tool_use_id=tool_use_id, content=f"could not fetch PR: {exc}", is_error=True)
        summary = f"#{pr['number']} {pr['title']} ({pr['state']}) -- {pr['html_url']}"
        return ToolResult(tool_use_id=tool_use_id, content=summary, output=pr)


class GitPrChecksTool(Tool):
    name = "git_pr_checks"
    description = "View CI check-run status for a pull request's head commit."
    parameters = {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]}

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        owner, repo = context.repo_full_name.split("/", 1)
        try:
            pr = await context.github.get_pr(owner, repo, input["number"])
            checks = await context.github.list_check_runs(owner, repo, pr["head"]["sha"])
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                tool_use_id=tool_use_id,
                content=(
                    f"could not fetch checks: {exc} -- if this is a 403/404, the GitHub App likely needs "
                    "'Checks: Read' permission added in its settings"
                ),
                is_error=True,
            )
        summary = ", ".join(f"{c['name']}: {c['conclusion'] or c['status']}" for c in checks["check_runs"]) or "no checks reported"
        return ToolResult(tool_use_id=tool_use_id, content=summary, output=checks)


class GitListReposTool(Tool):
    name = "git_list_repos"
    description = "List repositories accessible to this session's GitHub App installation."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        data = await context.github.list_installation_repos()
        names = [r["full_name"] for r in data.get("repositories", [])]
        return ToolResult(tool_use_id=tool_use_id, content=", ".join(names) or "no repositories", output=data)


def build_git_github_tools() -> list[Tool]:
    return [GitCreatePrTool(), GitUpdatePrDescriptionTool(), GitViewPrTool(), GitPrChecksTool(), GitListReposTool()]
