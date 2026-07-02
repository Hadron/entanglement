#!/usr/bin/python3
# Copyright (C) 2017, 2018, 2022, 2026, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import sh, os, os.path, tempfile
from os.path import exists

def gen_site_ca(pki_dir, ca_name = "Root CA"):

    ca_key = os.path.join(pki_dir, 'ca.key')
    ca_pem = os.path.join(pki_dir, 'ca.pem')
    if exists(ca_pem):
        return

    os.makedirs(pki_dir, exist_ok = True)
    if not exists(ca_key):
        sh.openssl('genrsa', '-out', ca_key, '2048')
    sh.openssl.req('-x509', '-key', ca_key,
                   '-subj',f'/CN={ca_name}',
                   '-days', '400',
                   '-extensions', 'v3_ca',
                   '-addext', 'keyUsage=keyCertSign,cRLSign',
                   '-out', ca_pem)

def host_cert_exts(host):
    return f'''\
basicConstraints=CA:FALSE
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
subjectAltName = DNS:{host}
'''

def host_cert(pki_dir, hostname, adl_subj, prefix = "", *,
              force = False):

    if prefix:
        prefix = prefix + '_'
    ca_key = os.path.join(pki_dir, 'ca.key')
    ca_pem = os.path.join(pki_dir, 'ca.pem')
    hostfile = os.path.join(pki_dir, prefix + hostname)

    if (not force) and exists(hostfile + '.key') and exists(hostfile + '.pem'):
        return

    gen_site_ca(pki_dir)
    
    sh.openssl.genrsa('-out', f'{hostfile}.key', '2048')
    with open(hostfile + '.ext', 'w+t') as extfile:
        extfile.write(host_cert_exts(hostname))

    sh.openssl.x509(
        '-CAkey', ca_key,
        '-CA', ca_pem,
        '-CAcreateserial',
        '-out', f'{hostfile}.pem',
        '-days', '400',
        '-extfile', hostfile + '.ext',
        '-req',
        _in=sh.openssl('req',
                       '-new', '-subj', f'{adl_subj}/CN={hostname}',
                       '-key', f'{hostfile}.key',
                       _truncate_exc=False,
        ),
        _truncate_exc=False)
