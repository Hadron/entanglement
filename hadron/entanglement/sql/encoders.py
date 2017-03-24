# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import datetime, iso8601

def datetime_encoder(propname):
    def encode(obj):
        return getattr(obj, propname).isoformat()
    return encode


def datetime_decoder(propname):
    def decode(obj, value):
        return iso8601.parse_date(value)
    return decode
