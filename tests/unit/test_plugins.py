import acai.plugins
from acai.server.run import discover_plugins


def test_plugins():
    plugins = discover_plugins(acai.plugins)

    assert len(plugins) == 1
