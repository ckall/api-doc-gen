.PHONY: help install dev lint test clean build publish

help: ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## 安装（用户模式）
	pip install .

dev: ## 安装（开发模式，可编辑）
	pip install -e .

reload: clean ## 重新加载（改代码后执行这个）
	pip install -e .
	@echo "✅ 已重新加载，api-doc-gen 可用"
	@api-doc-gen -V

lint: ## 代码检查
	python -m py_compile cli.py
	python -m py_compile gen_manifest.py
	python -m py_compile gen_docs.py
	python -m py_compile gen_flows.py
	python -m py_compile pipeline/state.py
	python -m py_compile pipeline/nodes.py
	python -m py_compile pipeline/review.py
	python -m py_compile pipeline/graph.py
	@echo "✅ 语法检查通过"

test: ## 运行测试（TODO）
	@echo "暂无测试，后续补充"

clean: ## 清理构建产物
	rm -rf dist/ build/ *.egg-info/
	rm -rf __pycache__ pipeline/__pycache__ templates/__pycache__
	find . -name "*.pyc" -delete

build: clean ## 构建发布包
	python -m build

publish: build ## 发布到 PyPI
	twine upload dist/*

publish-test: build ## 发布到 TestPyPI
	twine upload --repository testpypi dist/*

tag: ## 打版本 tag（用法: make tag v=0.1.0）
	@if [ -z "$(v)" ]; then echo "用法: make tag v=0.1.0"; exit 1; fi
	@sed -i '' 's/^version = .*/version = "$(v)"/' pyproject.toml
	git add pyproject.toml
	git commit -m "release: v$(v)"
	git tag -a v$(v) -m "Release v$(v)"
	git push origin main --tags
	@echo "✅ 已发布 v$(v)"
