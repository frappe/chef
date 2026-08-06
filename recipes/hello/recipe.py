# recipe.py — pure pyinfra, no chef/atlas imports.
from pyinfra.api import deploy
from pyinfra.operations import server


@deploy("build")
def build():
    server.shell(
        name="say hello",
        commands=["echo baking hello on $(hostname)"],
        _retries=2,
        _retry_delay=5,
    )
