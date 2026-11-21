# SPDX-License-Identifier: GPL-3.0-or-later
"""The installation scripts parse, are executable and carry the licence header.

They cannot run here (they change the system), so this mostly guards against a
broken script reaching a clean machine; shellcheck runs on them by hand. The
first stage of ``install.sh`` (which code to fetch, and whether its installer
can take over) runs here on a local repository.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted([ROOT / "install.sh", ROOT / "uninstall.sh", *(ROOT / "deploy").glob("*.sh")])


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_parses_and_is_executable(script: Path) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    subprocess.run([bash, "-n", str(script)], check=True)  # noqa: S603  (our own script)
    assert os.access(script, os.X_OK)
    assert "SPDX-License-Identifier: GPL-3.0-or-later" in script.read_text().splitlines()[1]


def test_installer_never_starts_a_new_installation() -> None:
    """The hardware checklist comes first on a new computer (docs/install.md 5)."""
    text = (ROOT / "install.sh").read_text()
    assert not re.search(r"enable\b.*remoteg90ctl", text)
    assert 'install.sh" --start' not in text
    # Only an existing installation is restarted, after an update.
    update = text.index("if ! $FRESH")
    assert text.index("systemctl restart remoteg90ctl") > update


PROTOCOL = "# installer-protocol: 1"


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """Releases v0.4.0rc1 (an installer that cannot take over), v0.5.0rc1 and
    main (both can)."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    script = repo / "install.sh"
    for tag, marker in (("v0.4.0rc1", False), ("v0.5.0rc1", True)):
        script.write_text(f"#!/bin/bash\n# {tag}\n" + (PROTOCOL + "\n" if marker else ""))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", tag)
        _git(repo, "tag", tag)
    script.write_text(f"#!/bin/bash\n# main\n{PROTOCOL}\n")
    _git(repo, "commit", "-qam", "main")
    return repo


def _fetch(
    tmp_path: Path, upstream: Path, release: str = "", branch: str = ""
) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    program = (
        f'source "{ROOT / "install.sh"}"; set +e; '
        'as_owner() { "$@"; }; record() { :; }; set -e; '
        f'REPO="{upstream}"; RELEASE="{release}"; BRANCH="{branch}"; '
        f'SRC="{tmp_path / "checkout"}"; fetch_code'
    )
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}
    env.pop("REMOTEG90CTL_RELEASE", None)
    env.pop("REMOTEG90CTL_BRANCH", None)
    return subprocess.run(  # noqa: S603  (our own script on a local repository)
        [bash, "-c", program], env=env, capture_output=True, text=True, check=False
    )


def _installed(tmp_path: Path) -> str:
    return (tmp_path / "checkout" / "install.sh").read_text().splitlines()[1]


def test_installer_marks_itself_as_able_to_take_over() -> None:
    assert PROTOCOL in (ROOT / "install.sh").read_text().splitlines()


def test_the_newest_release_is_fetched(tmp_path: Path, upstream: Path) -> None:
    result = _fetch(tmp_path, upstream)
    assert result.returncode == 0, result.stderr
    assert _installed(tmp_path) == "# v0.5.0rc1"
    assert _git(tmp_path / "checkout", "describe", "--tags", "--exact-match") == "v0.5.0rc1"


def test_a_release_without_a_suffix_wins_over_a_newer_candidate(
    tmp_path: Path, upstream: Path
) -> None:
    _git(upstream, "tag", "v0.5.0", "v0.5.0rc1")
    _git(upstream, "tag", "v0.6.0rc1", "main")
    result = _fetch(tmp_path, upstream)
    assert result.returncode == 0, result.stderr
    assert _git(tmp_path / "checkout", "describe", "--tags", "--exact-match").startswith("v0.5.0")
    assert "candidate" not in result.stdout


def test_a_fresh_install_of_a_release_too_old_leaves_nothing(
    tmp_path: Path, upstream: Path
) -> None:
    result = _fetch(tmp_path, upstream, release="v0.4.0rc1")
    assert result.returncode != 0
    assert "too old for this installer" in result.stderr
    assert not (tmp_path / "checkout").exists()


def test_a_release_too_old_to_take_over_is_refused_before_anything_changes(
    tmp_path: Path, upstream: Path
) -> None:
    assert _fetch(tmp_path, upstream).returncode == 0
    result = _fetch(tmp_path, upstream, release="v0.4.0rc1")
    assert result.returncode != 0
    assert "too old for this installer" in result.stderr
    assert _installed(tmp_path) == "# v0.5.0rc1"  # the installed release is untouched


def test_an_unknown_release_is_refused(tmp_path: Path, upstream: Path) -> None:
    result = _fetch(tmp_path, upstream, release="v9.9.9")
    assert result.returncode != 0
    assert "no release v9.9.9" in result.stderr


def test_a_checkout_following_a_branch_keeps_following_it(tmp_path: Path, upstream: Path) -> None:
    assert _fetch(tmp_path, upstream, branch="main").returncode == 0
    assert _installed(tmp_path) == "# main"
    (upstream / "install.sh").write_text(f"#!/bin/bash\n# main 2\n{PROTOCOL}\n")
    _git(upstream, "commit", "-qam", "main 2")
    result = _fetch(tmp_path, upstream)  # no branch or release asked for
    assert result.returncode == 0, result.stderr
    assert _installed(tmp_path) == "# main 2"
    assert _git(tmp_path / "checkout", "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_every_recorded_change_is_undone_by_the_uninstaller() -> None:
    """uninstall.sh reads every kind of change install.sh records."""
    kinds = set(re.findall(r"\brecord (\w+)", (ROOT / "install.sh").read_text()))
    assert kinds >= {"owner", "package", "masked", "uv", "tailscale", "clone"}
    uninstaller = (ROOT / "uninstall.sh").read_text()
    for kind in kinds:
        assert f"recorded {kind}" in uninstaller, kind


def test_purge_removes_every_directory_of_the_services() -> None:
    """deploy/uninstall.sh --purge leaves none of the units' state, log or runtime directories."""
    uninstaller = (ROOT / "deploy" / "uninstall.sh").read_text()
    roots = {"StateDirectory": "/var/lib", "LogsDirectory": "/var/log", "RuntimeDirectory": "/run"}
    found = 0
    for unit in (ROOT / "deploy" / "systemd").glob("*.service"):
        for key, value in re.findall(r"^(\w+Directory)=(.+)$", unit.read_text(), re.M):
            for entry in value.split():
                path = f"{roots[key]}/{entry.split('/')[0]}"
                assert f"={path}\n" in uninstaller, (unit.name, path)
                found += 1
    assert found >= 4


def test_the_installer_records_a_change_once(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    program = (
        f'source "{ROOT / "install.sh"}"; RECORD_DIR="{tmp_path}"; '
        "record package chrony; record package chrony; record masked brltty.service enabled"
    )
    subprocess.run([bash, "-c", program], check=True)  # noqa: S603  (our own script)
    assert (tmp_path / "changes").read_text().splitlines() == [
        "package chrony",
        "masked brltty.service enabled",
    ]


def test_a_long_step_shows_progress_and_its_output_only_on_failure() -> None:
    bash = shutil.which("bash")
    assert bash is not None
    good = subprocess.run(  # noqa: S603  (our own script)
        [bash, "-c", f'source "{ROOT / "install.sh"}"; busy "step" sh -c "echo noise"'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert good.returncode == 0
    assert "step … done" in good.stdout
    assert "noise" not in good.stdout + good.stderr
    bad = subprocess.run(  # noqa: S603
        [bash, "-c", f'source "{ROOT / "install.sh"}"; busy "step" sh -c "echo why; exit 3"'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode != 0
    assert "why" in bad.stderr
    assert "step failed" in bad.stderr


def test_both_installers_show_progress_the_same_way() -> None:
    def busy(path: Path) -> str:
        text = path.read_text()
        start = text.index("busy() {")
        return text[start : text.index("\n}\n", start)]

    assert busy(ROOT / "install.sh") == busy(ROOT / "deploy" / "install.sh")


def test_a_long_step_keeps_its_whole_output_in_the_log(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    log = tmp_path / "install.log"
    result = subprocess.run(  # noqa: S603  (our own script)
        [bash, "-c", f'source "{ROOT / "install.sh"}"; busy "step" sh -c "echo noise"'],
        env={**os.environ, "REMOTEG90CTL_LOG": str(log)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "noise" not in result.stdout
    assert "noise" in log.read_text()


def test_the_repository_address_is_shown_without_a_token() -> None:
    bash = shutil.which("bash")
    assert bash is not None
    shown = subprocess.run(  # noqa: S603  (our own script)
        [bash, "-c", f'source "{ROOT / "install.sh"}"; echo "$REPO_SHOWN"'],
        env={
            **os.environ,
            "REMOTEG90CTL_REPO": "https://x-access-token:TOKEN@example.com/o/r.git",
        },
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert shown == "https://example.com/o/r.git"


def test_the_installed_python_is_one_that_ci_tests() -> None:
    """deploy/install.sh limits uv to the Python versions in the CI matrix."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    matrix = re.search(r"python: \[([^\]]+)\]", ci)
    assert matrix is not None
    versions = sorted(int(v.strip(' "').split(".")[1]) for v in matrix.group(1).split(","))
    wanted = f'PYTHON_VERSIONS=">=3.{versions[0]},<3.{versions[-1] + 1}"'
    assert wanted in (ROOT / "deploy" / "install.sh").read_text()


# deploy/restore.sh takes only regular files from the archive (security review
# 2026-11-14, Z2): a link or a device would let an archive reach outside.


def _restore(tmp_path: Path, command: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    script = f'source "{ROOT / "deploy/restore.sh"}"; {command}'
    return subprocess.run(  # noqa: S603  (our own script, functions only)
        [bash, "-c", script], cwd=tmp_path, capture_output=True, text=True, check=False
    )


def test_restore_refuses_an_archive_with_links(tmp_path: Path) -> None:
    good = tmp_path / "good"
    (good / "guard").mkdir(parents=True)
    (good / "config.toml").write_text("")
    (good / "guard" / "lockout.json").write_text("{}")
    subprocess.run(["tar", "-C", str(good), "-czf", str(tmp_path / "good.tgz"), "."], check=True)  # noqa: S603, S607  (tar on our test files)
    assert _restore(tmp_path, "check_archive good.tgz").returncode == 0
    (good / "guard" / "running.json").symlink_to("/etc/shadow")
    subprocess.run(["tar", "-C", str(good), "-czf", str(tmp_path / "bad.tgz"), "."], check=True)  # noqa: S603, S607  (tar on our test files)
    assert _restore(tmp_path, "check_archive bad.tgz").returncode != 0


def test_restore_takes_only_regular_files(tmp_path: Path) -> None:
    (tmp_path / "plain.json").write_text("{}")
    (tmp_path / "link.json").symlink_to(tmp_path / "plain.json")
    (tmp_path / "dir.json").mkdir()
    plain = _restore(tmp_path, "staged_file plain.json")
    assert (plain.returncode, plain.stdout.strip()) == (0, "plain.json")
    absent = _restore(tmp_path, "staged_file absent.json")
    assert (absent.returncode, absent.stdout) == (0, "")
    for name in ("link.json", "dir.json"):
        refused = _restore(tmp_path, f"staged_file {name}")
        assert refused.returncode != 0, name
        assert "not a regular file" in refused.stderr


def test_restore_writes_only_to_the_services_directories() -> None:
    text = (ROOT / "deploy/restore.sh").read_text()
    assert "GUARD_STATE=/var/lib/remoteg90ctl\n" in text
    assert "SERVER_STATE=/var/lib/remoteg90ctl-server\n" in text
    assert '[[ $GUARD_DIR == "$GUARD_STATE" && $SERVER_DIR == "$SERVER_STATE" ]]' in text


def test_restore_and_backup_read_one_path_per_line() -> None:
    """Audit 2026-11-15, A1: three paths on one line let a newline in one of
    them hide the rest from the checks."""
    for script in ("restore.sh", "backup.sh"):
        text = (ROOT / "deploy" / script).read_text()
        assert "read -r GUARD_DIR TX_LOG SERVER_DIR" not in text, script
        assert "mapfile -t PATHS" in text, script
        assert "(( ${#PATHS[@]} == 3 ))" in text, script


def test_restore_reads_three_paths_or_stops(tmp_path: Path) -> None:
    for lines, code in (("/a\n/b\n/c", 0), ("/a /b /c", 1), ("/a\n/b\n/c\n/d", 1)):
        command = f"paths() {{ printf '{lines}\\n'; }}; STAGE=. read_paths; echo $TX_LOG"
        result = _restore(tmp_path, command)
        assert result.returncode == code, (lines, result.stderr)
        if code == 0:
            assert result.stdout.strip() == "/b"


def test_restore_unpacks_only_the_files_a_backup_has(tmp_path: Path) -> None:
    """Dependency audit 2026-11-15 (GNU tar CVE-2026-5704): entries hidden from
    the listing must not be unpacked, so only the known names are."""
    src = tmp_path / "src"
    (src / "guard").mkdir(parents=True)
    (src / "server").mkdir()
    for name in ("manifest.txt", "config.toml", "guard/lockout.json", "server/accounts.json"):
        (src / name).write_text("x")
    (src / "evil.sh").write_text("x")
    (src / "guard" / "other.json").write_text("x")
    subprocess.run(["tar", "-C", str(src), "-czf", str(tmp_path / "b.tgz"), "."], check=True)  # noqa: S603, S607  (tar on our test files)
    (tmp_path / "out").mkdir()
    result = _restore(tmp_path, "extract_known b.tgz out")
    assert result.returncode == 0, result.stderr
    found = sorted(
        str(p.relative_to(tmp_path / "out")) for p in (tmp_path / "out").rglob("*") if p.is_file()
    )
    assert found == ["config.toml", "guard/lockout.json", "manifest.txt", "server/accounts.json"]


def test_the_installer_updates_an_old_uv() -> None:
    """Dependency audit 2026-11-15: uv < 0.13.4 trusts a compromised CA."""
    text = (ROOT / "install.sh").read_text()
    assert "UV_MIN=0.13.4" in text
    assert '"$UV" self update' in text
