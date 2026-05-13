.PHONY: doc build-doc serve-doc update-doc virtual-env install back-dev front-dev \
       build-ui build-wheel build clean tests hello vllm

doc: build-doc

build-doc:
	sphinx-build -W --color -c docs/ -b html docs/ _build/html

serve-doc:
	sphinx-serve

update-doc: build-doc serve-doc

virtual-env:
	@[ -d .venv ] || uv venv --python=3.12 --seed

install: virtual-env
	(. .venv/bin/activate && pip install -r requirements.txt)
	(. .venv/bin/activate && pip install -e '.[models]')

back-dev:
	(. .venv/bin/activate && FLASK_STATIC=$(pwd) acai uber --debug 1 --extern_llm 1)

vllm:
	(. .venv/bin/activate && FLASK_STATIC=$(pwd) acai serve --model "Qwen/Qwen3-Coder-Next-FP8")

vllm-small:
	(. .venv/bin/activate && FLASK_STATIC=$(pwd) acai serve --model "google/gemma-4-31B-it")


front-dev:
	(cd acai/ui && npm i && npm run dev)

# -- Build targets -----------------------------------------------------------

build-ui:
	cd acai/ui && npm ci && VITE_API_URL= VITE_BASE_PATH=/ npx vite build --outDir dist

build-wheel: build-ui
	python -m build

build: build-wheel

clean:
	rm -rf acai/ui/dist dist build *.egg-info

# -- Tests -------------------------------------------------------------------

tests:
	$(eval _MOD := acai.$(subst /,.,$(basename $(FILE))))
	(. .venv/bin/activate && python -m pytest tests/$(dir $(FILE))test_$(notdir $(FILE)) --cov=$(_MOD) --cov-report=term-missing -v)

hello:
	@echo "hello world"
