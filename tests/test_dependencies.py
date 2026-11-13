"""Keep Docker/requirements and package installations on the same dependencies."""
from importlib import metadata
from pathlib import Path
import tomllib

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_dependency_declarations_agree():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    package_dependencies = {Requirement(value) for value in project['project']['dependencies']}
    requirements_dependencies = {
        Requirement(line.strip())
        for line in (ROOT / 'requirements.txt').read_text().splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    }
    assert package_dependencies == requirements_dependencies


def test_pillow_version_satisfies_project_and_moviepy():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    dependencies = project['project']['dependencies'] + metadata.requires('moviepy')
    installed_pillow = metadata.version('pillow')
    for value in dependencies:
        requirement = Requirement(value)
        if requirement.name.lower() == 'pillow' and (not requirement.marker or requirement.marker.evaluate()):
            assert installed_pillow in requirement.specifier, str(requirement)
