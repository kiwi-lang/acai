install:
	pip install -e .[all]
	pip install -r requirements.txt
	pip install -r docs/requirements.txt
	pip install -r tests/requirements.txt

doc: build-doc

build-doc:
	sphinx-build -W --color -c docs/ -b html docs/ _build/html

serve-doc:
	sphinx-serve

update-doc: build-doc serve-doc


virtual-env:
	virtualenv .venv
	(. .venv/bin/activate && pip install -e .)

install:
	(. .venv/bin/activate && pip install -e .)

# back-dev:
# 	(. .venv/bin/activate && FLASK_STATIC=$(pwd) flask --debug --app assai.server.run:main run  --with-threads --port 5001)

back-dev:
	(. .venv/bin/activate && FLASK_STATIC=$(pwd) python -m assai.server.run)


front-dev:
	(cd assai/ui && npm i && npm run dev)
