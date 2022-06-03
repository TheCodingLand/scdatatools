#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
from setuptools import setup, find_packages

with open("README.rst") as readme_file:
    readme = readme_file.read()

with open("HISTORY.rst") as history_file:
    history = history_file.read()

requirements = [
    "fnvhash~=0.1.0",
    "hexdump~=3.3",
    "humanize~=3.13.1",
    "numpy~=1.21.5",
    "packaging~=21.3",
    "pycryptodome~=3.12.0",
    "pyquaternion~=0.9.9",
    "pyrsi~=0.1.19",
    "python_nubia~=0.2b2",
    "rich~=12.4.4",
    "sentry-sdk==1.5.8",
    "tqdm~=4.62.3",
    "xxhash~=2.0.2",
    "zstandard~=0.12.0",
]

setup_requirements = []

test_requirements = []

if len(sys.argv) >= 2 and sys.argv[1] == "docs":
    import shutil
    from sphinx.ext import apidoc
    from subprocess import call

    print("Auto-generating API docs")

    proj_dir = os.path.dirname(os.path.realpath(__file__))
    api_dir = os.path.join(proj_dir, "docs", "api")
    shutil.rmtree(api_dir, ignore_errors=True)

    _orig_sysargv = sys.argv
    if len(sys.argv) > 2:
        args = sys.argv[2:]
    else:
        args = ["-e", "-M"]

    args += [
        "-o",
        os.path.join(proj_dir, "docs", "api"),
        os.path.join(proj_dir, "scdatatools"),
    ]
    print(args)
    apidoc.main(args)

    print("Generated API docs - run `python setup.py build_sphinx` to build docs")

    sys.exit(0)

setup(
    author="ventorvar",
    author_email="ventorvar@gmail.com",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
    ],
    description="Python tools for working with Star Citizen data files.",
    install_requires=requirements,
    license="MIT license",
    long_description=readme + "\n\n" + history,
    include_package_data=True,
    keywords="scdatatools",
    name="scdatatools",
    packages=find_packages(include=["scdatatools"]),
    entry_points={
        "console_scripts": ["scdt=scdatatools.cli:main"],
    },
    setup_requires=setup_requirements,
    test_suite="tests",
    tests_require=test_requirements,
    url="https://gitlab.com/scmodding/frameworks/scdatatools",
    version="1.0.3",
    zip_safe=True,
)
