import json
import subprocess

import pytest

from tests.compose.conftest import REPO_ROOT, compose_environment


def _compose(*args, env, timeout=120):
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.compose
def test_compose_config_is_valid(compose_env):
    result = _compose("config", "--quiet", env=compose_env)
    assert result.returncode == 0, result.stderr


@pytest.mark.compose
def test_compose_demo_profile_config_is_valid(compose_env):
    result = _compose("--profile", "demo", "config", "--quiet", env=compose_env)
    assert result.returncode == 0, result.stderr


@pytest.mark.compose
def test_the_hyperset_image_tag_is_isolated_per_run(compose_env):
    """hy-8uw0: an image tag is GLOBAL to the Docker daemon, so a fixed `hyperset/hyperset:dev`
    lets a concurrent seat/checkout build+serve the same tag out from under this run -- silently
    testing code that is not the code under test. `compose_env` forces a per-run HYPERSET_IMAGE_TAG
    and the rendered config uses it for the hyperset services, NOT the shared `:dev`. Read on the
    FIXTURE's own env (not the helper alone), and tied to docker-compose.yml via `config`, so a
    future edit that hardcodes the tag reddens this."""
    tag = compose_env["HYPERSET_IMAGE_TAG"]
    assert tag and tag != "dev"
    assert tag == compose_env["COMPOSE_PROJECT_NAME"]  # one identity keys the project AND the tag

    result = _compose("config", "--format", "json", env=compose_env)
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    hyperset_images = {
        service["image"]
        for service in config["services"].values()
        if str(service.get("image", "")).startswith("hyperset/hyperset:")
    }
    assert hyperset_images, "no hyperset/hyperset service image was rendered"
    # Every hyperset code image carries THIS run's tag, and none is the daemon-global default.
    assert hyperset_images == {f"hyperset/hyperset:{tag}"}, hyperset_images
    assert "hyperset/hyperset:dev" not in hyperset_images


@pytest.mark.compose
def test_both_services_receive_the_embedding_api_key(compose_env):
    """hy-8vm34: OpenAI embeddings authenticate with HYPERSET_EMBEDDING_API_KEY, and discover
    ranks with embeddings on BOTH the api and the mcp-http server. Neither passed the key, so a
    provider=openai deployment ranked unauthenticated. Both services must now carry the var (and
    the provider/base/model it pairs with). MUTATION-RED: drop the key from either service and
    this reddens. Uses the demo profile because mcp-http is profile-gated."""
    result = _compose("--profile", "demo", "config", "--format", "json", env=compose_env)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    for name in ("api", "mcp-http"):
        environment = services[name]["environment"]
        for var in (
            "HYPERSET_EMBEDDING_PROVIDER",
            "HYPERSET_EMBEDDING_BASE_URL",
            "HYPERSET_EMBEDDING_MODEL",
            # The width (hy-zakwj): both services pin the same embedding space, so both must
            # forward it or their indexes disagree. MUTATION-RED: drop it from either and this
            # reddens.
            "HYPERSET_EMBEDDING_DIMENSIONS",
            "HYPERSET_EMBEDDING_API_KEY",
        ):
            assert var in environment, f"{name} is missing {var}"


@pytest.mark.compose
def test_mcp_services_receive_the_secret_key_for_writeback(compose_env):
    result = _compose("--profile", "mcp", "config", "--format", "json", env=compose_env)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    for name in ("mcp", "mcp-http"):
        assert "HYPERSET_SECRET_KEY" in services[name]["environment"], (
            f"{name} cannot decrypt write-back targets"
        )


@pytest.mark.compose
def test_two_runs_get_distinct_hyperset_image_tags(compose_env):
    """The isolation is PER RUN: a second independent environment yields a different tag, so two
    concurrent seats never share the code image (the property `COMPOSE_PROJECT_NAME` gives to
    containers, now extended to the tag). Uses the fixture's env as one of the two so it is not
    the vacuous helper-alone check the conftest warns about."""
    other = compose_environment()
    assert other["HYPERSET_IMAGE_TAG"] != compose_env["HYPERSET_IMAGE_TAG"]
    assert other["HYPERSET_IMAGE_TAG"] == other["COMPOSE_PROJECT_NAME"]


@pytest.mark.compose
def test_core_stack_starts_and_migrates(compose_env):
    up = _compose("up", "-d", "postgres", env=compose_env)
    assert up.returncode == 0, up.stderr

    wait = _compose("up", "-d", "--wait", "postgres", env=compose_env, timeout=60)
    assert wait.returncode == 0, wait.stderr

    migrate = _compose("run", "--rm", "hyperset-migrate", env=compose_env, timeout=90)
    assert migrate.returncode == 0, migrate.stderr
    assert "Database upgraded to head" in migrate.stdout


@pytest.mark.compose
def test_down_preserves_volumes(compose_env):
    # The `up` return code was not checked here until hy-xjch, and unchecked it
    # made the whole test vacuous rather than merely lenient: with nothing
    # brought up, no volume of this project's is created, `down` still succeeds,
    # and the assertion below used to pass anyway on somebody ELSE'S volume.
    # MEASURED by pointing this line at a service that does not exist -- the
    # pre-hy-xjch test passed in 1.06s having started nothing at all.
    up = _compose("up", "-d", "--wait", "postgres", env=compose_env, timeout=60)
    assert up.returncode == 0, up.stderr

    down = _compose("down", env=compose_env)
    assert down.returncode == 0, down.stderr

    result = subprocess.run(
        ["docker", "volume", "ls", "--format", "{{.Name}}"], capture_output=True, text=True
    )
    # Scoped to THIS run's project, which only became possible once the project
    # name stopped being shared (hy-xjch). A developer's own stack and every
    # other seat publish a volume whose name ends in the same suffix, so the
    # bare substring answered "does anyone on this box have postgres data",
    # which is not what this test is named for.
    survivor = f"{compose_env['COMPOSE_PROJECT_NAME']}_hyperset-postgres-data"
    assert survivor in result.stdout.splitlines(), result.stdout
