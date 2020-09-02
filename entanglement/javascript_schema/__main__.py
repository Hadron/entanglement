# Copyright (C) 2017, 2020, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from . import output_js_schemas
import argparse

def main():
    import importlib
    parser = argparse.ArgumentParser()
    parser.add_argument('module',
                          nargs = '*',
                          help='Modules to import SyncRegistries from')
    parser.add_argument('--out',
                        '-o',
                        metavar = 'directory',
                        help = "Output directory",
                        required = True)
    args = parser.parse_args()
    for m in args.module:
        importlib.import_module(m)
    output_js_schemas(args.out)

if __name__ == '__main__':
    main()
