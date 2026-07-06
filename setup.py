from setuptools import setup, find_packages

setup(
    name="api-doc-gen",
    version="0.1.0",
    description="从 Swagger + 源码生成 AI 知识库文档",
    py_modules=["cli", "gen_manifest", "gen_docs"],
    packages=["pipeline"],
    python_requires=">=3.10",
    install_requires=[
        "pyyaml>=6.0",
        "langgraph>=0.4",
        "langchain-openai>=0.3.18",
        "langchain-core>=0.3.61",
        "rich>=14.0",
    ],
    entry_points={
        "console_scripts": [
            "api-doc-gen=cli:main",
        ],
    },
)
