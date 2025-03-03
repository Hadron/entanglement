#!/usr/bin/python3
# Copyright (C) 2017, 2018, 2022, Hadron Industries, Inc.
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
    os.makedirs(pki_dir, exist_ok = True)
    if not exists(ca_key):
        sh.openssl('genrsa', '-out', ca_key, '2048',
    )

    
    if not exists(ca_pem):
        sh.openssl.req('-x509', '-key', ca_key,
                   '-subj','/CN={}'.format(ca_name),
                   '-days', '400',
                   '-extensions', 'v3_ca',
                   '-out', ca_pem)

def host_cert_exts(host):
    return f'''
basicConstraints=CA:FALSE
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
subjectAltName = DNS:{host}
'''

def host_cert(pki_dir, hostname, adl_subj, prefix = "", *,
              force = False):
    if prefix != "":
        prefix = prefix+"_"
    ca_key = os.path.join(pki_dir, 'ca.key')
    ca_pem = os.path.join(pki_dir, 'ca.pem')
    gen_site_ca(pki_dir)
    hostfile = os.path.join(pki_dir, prefix+hostname)
    
    if force or (not (exists (hostfile + '.key') and exists(hostfile + '.pem'))):
        sh.openssl.genrsa('-out', '{}.key'.format(hostfile),
                          '2048')
        with tempfile.NamedTemporaryFile(dir=pki_dir, mode='w+t') as extfile:
            extfile.write(host_cert_exts(hostname))
            extfile.flush()
            sh.openssl.x509(
            '-CAkey', ca_key,
            '-CA', ca_pem,
            '-CAcreateserial',
            '-out', '{}.pem'.format(hostfile),
            '-days', '400',
                "-extfile", extfile.name,
                '-req',
                _in=sh.openssl( 'req',
                            '-new', '-subj', '{adl_subj}/CN={}'.format(hostname, adl_subj = adl_subj),
                            '-key', '{}.key'.format(hostfile),
                            _piped=True,
                            _truncate_exc=False,
            ),
                _truncate_exc=False)

