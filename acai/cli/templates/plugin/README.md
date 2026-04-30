# acai-plugin-{{name}}

An Acai plugin that adds custom tools, agents, and workflows.

## Install

```bash
pip install -e .
```

Once installed, `acai` will auto-discover tools, agents, and workflows
from this plugin.

## Structure

```
acai/plugins/{{name_underscored}}/
├── __init__.py          # register() hook
├── tools.py             # custom tools
├── agents/
│   └── example/
│       ├── definition.json
│       └── system.j2
└── workflows/           # (optional) bundled workflows
```
