import assai.plugins
from assai.server.run import discover_plugins


def test_plugins():
    plugins = discover_plugins(assai.plugins)

    assert len(plugins) == 1
