import os
import pkgutil
import importlib
import traceback
# import importlib_resources


from flask import Flask, jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))

STATIC_FOLDER_DEFAULT = os.path.join(ROOT, 'static')
STATIC_FOLDER = os.path.abspath(os.getenv("FLASK_STATIC", STATIC_FOLDER_DEFAULT))
STATIC_UPLOAD_FOLDER = os.path.join(STATIC_FOLDER, 'uploads')

os.environ["XDG_CACHE_HOME"] = os.path.join(STATIC_FOLDER, "cache")
os.environ["HF_HOME"] = os.path.join(STATIC_FOLDER, "cache")

def discover_plugins(module):
    path = module.__path__
    name = module.__name__

    plugins = {}

    for _, name, _ in pkgutil.iter_modules(path, name + "."):
        try:
            plugins[name] = importlib.import_module(name)
            print(f" - Found plugin: {name}")
        except:
            traceback.print_exc()

    return plugins


# data_path = importlib_resources.files("assai.data")

# with open(data_path / "data.json", encoding="utf-8") as file:
#     print(json.dumps(json.load(file), indent=2))


class ASSAI:
    def __init__(self):
        print(STATIC_FOLDER)
        self.app = Flask(__name__, static_folder=STATIC_FOLDER)

        import assai.models

        models = discover_plugins(assai.models)

        for k, module in models.items():
            if hasattr(module, 'route'):
                module.route(self)


def main():
    server = ASSAI()
    return server.app
