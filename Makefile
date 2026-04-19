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

# back-dev:
# 	(. .venv/bin/activate && FLASK_STATIC=$(pwd) flask --debug --app assai.server.run:main run  --with-threads --port 5001)

# back-dev: install
# 	(. .venv/bin/activate && FLASK_STATIC=$(pwd) python -m assai.server.run)

back-dev: # install
	(. .venv/bin/activate && FLASK_STATIC=$(pwd) assai uber --debug 1 --extern_llm 1)

vllm:
	(. .venv/bin/activate && FLASK_STATIC=$(pwd) assai serve --model "Qwen/Qwen3-Coder-Next-FP8")



front-dev:
	(cd assai/ui && npm i && npm run dev)

.PHONY: tests
tests:
	$(eval _MOD := assai.$(subst /,.,$(basename $(FILE))))
	(. .venv/bin/activate && python -m pytest tests/$(dir $(FILE))test_$(notdir $(FILE)) --cov=$(_MOD) --cov-report=term-missing -v)

hello:
	@echo "hello world"
