#!/usr/bin/python3
# Copyright (C) 2017, 2018, 2020, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


from setuptools import setup

setup(
    name = "entanglement",
    license = "proprietary",
    maintainer = "Sam Hartman",
    maintainer_email = "sam.hartman@hadronindustries.com",
    url = "http://www.hadronindustries.com/",
    setup_requires = ['pytest-runner'],
    tests_require = ['pytest'],
    packages = ["entanglement", "entanglement.sql",
                'entanglement.protocol'],
    package_data = {
        'entanglement.sql': ['alembic', 'alembic/*', 'alembic/versions/*'],
        },
    install_requires = ['alembic', 'SQLAlchemy', 'pyOpenSSL', 'iso8601'],
    scripts = ['bin/entanglement-cli',
               'bin/entanglement-pki'],
    test_suite = "tests",
    version = "0.21",
)
