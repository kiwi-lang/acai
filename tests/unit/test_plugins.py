import assai.plugins
from assai.core import discover_plugins


def test_plugins():
    plugins = discover_plugins(assai.plugins)

    assert len(plugins) == 1
