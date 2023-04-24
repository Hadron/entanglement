"use strict";
/*
 * Copyright (C) 2017, 2020, Hadron Industries, INc.
 *  Entanglement is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU Lesser General Public License version 3
 *  as published by the Free Software Foundation. It is distributed
 *  WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
 *  LICENSE for details.
*/

/*
* For now, this implements the persistence protocols used on the
* Python side but does not actually provide persistence on the
* javascript side.  IndexDB could be used to provide that, but that
* has not been implemented yet.
*/

import { Synchronizable } from './index.js';

const classStorageMaps = new WeakMap();

export class PersistentSynchronizable extends Synchronizable {

    static storageKey(msg) {
        // Returns the storage key for a given object either from an instance or an entanglement message
        // Maps do accept complex objects as keys, but perform equality based on object identity, which is not helpful to us.
        if (!Array.isArray(this.syncPrimaryKeys))
            throw new TypeError("Primary keys must be a list for persistence");
        let keys = this.syncPrimaryKeys;
        if (keys.length == 1)
            return msg[keys[0]];
        let res = [];
        for (let k of keys) {
            res.push(msg[k]);
        }
        return JSON.stringify(res);
    }

static     get syncStorageMap() {
        if (this._overrideStorageMap !== undefined)
            return this._overrideStorageMap;
        let res = classStorageMaps.get(this);
    if (res)
        return res;
    res = new Map();
    classStorageMaps.set(this, res);
    return res;
}

    static set syncStorageMap(v) {
        Object.defineProperty(this, "_overrideStorageMap",
                              {
                               enumerable: false,
                               writable: true,
                               value: v});
        return v;
    }

    static syncConstruct(msg, options) {
        let key = this.storageKey(msg);
        let map = this.syncStorageMap;
        if (map.has(key))
            return map.get(key);
        let result = super.syncConstruct(msg, options);
        map.set(key, result);
        return result;
    }

    persistDelete(registry, is_disappear = false) {
        let cls = this.constructor;
        cls.syncStorageMap.delete(cls.storageKey(this));
        if (is_disappear)
            registry._dispatchEvent("disappear", this, {});
        if (this.constructor._dispatchEvent)
            this.constructor._dispatchEvent('disappear', this, {});
            }

    syncModified() {
        let attrs = new Set();
        if (this._orig === undefined) {
            for (let a of this.constructor._syncAttributes) {
                if (this[a] !== undefined)
                    attrs.add(a);
            }
            return attrs;
        }
        for (let a of this.constructor._syncAttributes) {
            if (this[a] !== this._orig[a]) {
                attrs.add(a);
            }
        }
        return attrs;
    }

    syncUpdate(manager) {
        let attributes = this.syncModified();
        for (let a of this.constructor.syncPrimaryKeys) 
            attributes.add(a);
        attributes.add('_sync_owner');
        return manager.synchronize(this, {
            operation: 'forward',
            response: true,
            attributes: attributes
        });
    }

    syncCreate(manager) {
        let attributes =this.syncModified();
        attributes.add('_sync_owner');
        // Note that this object will not be the persistent object that is created.
        return manager.synchronize(this, {
            attributes: attributes,
            operation: 'create',
            response: true
        });
    }

    syncDelete(manager) {
        return manager.synchronize(this, {
            attributes: this.constructor.syncPrimaryKeys,
            operation: 'delete',
            response: true});
    }
                
    
};

export class SyncOwner extends PersistentSynchronizable {

    async syncReceive(msg, options) {
        let orig = this._orig || {};
        super.syncReceive(msg, options);
        if (orig.epoch && (orig.epoch != this.epoch)) {
            this.incoming_serial = 0;
            await this.clearAllObjects(options.registry);}
        this.incoming_serial = this.incoming_serial || 0;
        let ihave = new IHave({
            _sync_owner : this.id,
            serial: this.incoming_serial});
        options.manager.synchronize(ihave, {operation: 'forward'});
        return this;
    }

    async clearAllObjects(registry) {
        console.log(`Deleting all objects for ${this.constructor.name} with id ${this.id}`);

        for (let [key, cls] of registry.registry) {
            if (cls.syncStorageMap !== undefined) {
                if (cls.syncStorageMap === SyncOwner.syncStorageMap) continue;
                for (let [ikey, inst] of cls.syncStorageMap) {
                    if (inst._sync_owner === this.id) {
                        // Stackoverflow seems to think that deleting from an ES6 map during iterations is okay
                        await inst.persistDelete(registry, true);
                    }
                }
            }
        }
    }

    async persistDelete(registry, is_disappear = false) {
        await this.clearAllObjects(registry);
        return await super.persistDelete(registry, is_disappear);
    }
    
    
};

SyncOwner.syncStorageMap = SyncOwner.syncStorageMap; //All classes extending SyncOwner get the same map

export class YouHave extends Synchronizable {

    async syncReceive(msg, options) {
        await super.syncReceive(msg, options);
        let owner_id = this._sync_owner;
        let owner = SyncOwner.syncStorageMap.get(owner_id);
        if (!owner) {
            console.error(`YouHave for unknown owner ${owner_id}`);
            return;
        }
        owner.incoming_serial = Number(this.serial);
    }

}

export class MyOwners extends Synchronizable {

    async syncReceive(msg, options) {
        let registry = options.registry;
        await super.syncReceive(msg, options);
        let map = SyncOwner.syncStorageMap;
        let owners = new Set(this.owners);
        for (let [id, owner] of map) {
            if (!owners.has(id))
                await owner.persistDelete(registry, true);
        }
    }

};

export class IHave extends Synchronizable {

    constructor(o) {
        super();
        Object.assign(this, o);
    }

};
 
export function setupPersistence(registry) {
    if (registry.bases.SyncOwner === undefined)
        throw new TypeError("Must call the sql_internal schema setup function first");
    registry.register(SyncOwner);
    registry.register(IHave);
    registry.register(YouHave);
    registry.register(MyOwners);
}

if (0) {
    var filter = require('./filter');
   
    module.exports = {
        SyncOwner,
        IHave,
        YouHave,
        MyOwners,
        PersistentSynchronizable,
        setupPersistence,
    relationship:filter.relationship,
    };
}
