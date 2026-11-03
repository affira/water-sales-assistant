"""Keep .env.example in sync with the environment variables the code actually reads."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_READ = re.compile(r"""os\.(?:environ\.get|getenv)\(\s*["']([A-Z0-9_]+)["']|os\.environ\[\s*["']([A-Z0-9_]+)["']""")


def env_vars_read_by_code() -> set[str]:
    names = set()
    for path in (ROOT / "aquaassist").rglob("*.py"):
        for match in ENV_READ.finditer(path.read_text(encoding="utf-8")):
            names.add(match.group(1) or match.group(2))
    return names


def env_example() -> dict[str, str]:
    pairs = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            pairs[key.strip()] = value.strip()
    return pairs


def test_env_example_lists_every_variable_the_code_reads() -> None:
    documented = set(env_example())
    # The Anthropic SDK reads ANTHROPIC_API_KEY itself, so the code never names it.
    assert env_vars_read_by_code() | {"ANTHROPIC_API_KEY"} <= documented


def test_env_example_contains_no_real_secrets() -> None:
    for key, value in env_example().items():
        assert not value.startswith("sk-ant-"), f"{key} looks like a real API key"


def test_env_file_is_gitignored() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored
