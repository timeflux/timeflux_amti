#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""The setup script."""

from setuptools import setup, find_packages

requirements = [
    'numpy',
    'timeflux @ git+https://github.com/timeflux/timeflux#egg=timeflux',
]

setup_requirements = ['pytest-runner', ]

test_requirements = ['pytest', ]

setup(
    author='David Ojeda',
    author_email='david.ojeda@gmail.com',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
    ],
    description="Timeflux nodes for the AMTI force platform.",
    install_requires=requirements,
    license="MIT license",
    include_package_data=True,
    name='timeflux-amti',
    packages=find_packages(exclude=['doc', 'test']),
    setup_requires=setup_requirements,
    test_suite='test',
    tests_require=test_requirements,
    url='https://github.com/timeflux/timeflux_amti',
    version='0.2.0',
    zip_safe=False,
)
