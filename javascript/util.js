"use strict";
/*
 * Copyright (C) 2017, 2020, Hadron Industries, Inc.
 *  Entanglement is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU Lesser General Public License version 3
 *  as published by the Free Software Foundation. It is distributed
 *  WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
 *  LICENSE for details.
*/

function downFirst(s) {
    return s[0].toLowerCase()+s.substring(1)
}
module.exports = {
    downFirst}
