"use strict";
/*
*  Copyright (C) 2017, 2020 by Hadron Industries
* All rights Reserved; distributed under license
*/

/*
* For now, this implements the persistence protocols used on the
* Python side but does not actually provide persistence on the
* javascript side.  IndexDB could be used to provide that, but that
* has not been implemented yet.
*/

const entanglement = require('./index');
const util = require('./util')
const classStorageMaps = new WeakMap();

class PersistentSynchronizable extends entanglement.Synchronizable {

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

class SyncOwner extends PersistentSynchronizable {

    async syncReceive(msg, options) {
        let orig = this._orig || {};
        super.syncReceive(msg, options);
        if (orig.epoch && orig.epoch != this.epoch) {
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

class YouHave extends entanglement.Synchronizable {

    async SyncReceive(msg, options) {
        await super.SyncReceive(msg, options);
        let owner_id = this._sync_owner;
        let owner = SyncOwner.syncStorageMap.get(owner_id);
        if (!owner) {
            console.error(`YouHave for unknown owner ${owner_id}`);
            return;
        }
        owner.incoming_serial = Number(this.serial);
    }

}

class MyOwners extends entanglement.Synchronizable {

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

class IHave extends entanglement.Synchronizable {

    constructor(o) {
        super();
        Object.assign(this, o);
    }

};
 
function setupPersistence(registry) {
    if (registry.bases.SyncOwner === undefined)
        throw new TypeError("Must call the sql_internal schema setup function first");
    registry.register(SyncOwner);
    registry.register(IHave);
    registry.register(YouHave);
    registry.register(MyOwners);
}

/*40*
* Relationship machinery
*/

const relationship_delete_ops = Object.freeze(new Set([
     'delete',
    'disappear',
]));
const relationship_add_ops = Object.freeze(new Set([
    'sync'
]));

var relationship_observer = null;
/**
   * Indicate a relationship between two :class:`Synchronizables`.
   * The *local* class is the one that in a database would have a
   * foreign key constraint; the one with a higher sync_priority.
   * This supports one-to-one and one-to-many relationships.
   *
   * :param local: The local class
   * :param options: A set of options for the relationship:
   *
   * uselist
   *    Defaults to true; if true, then the target side of the relationship is a list (one-to-many).
   *
   * target
   *    Class that is the other side of the relationship
   *
   * keys
   *    An array of key names mapping to the local columns that hold the target's :meth:`yncPrimaryKeys`
   *
   * local_prop
   *    The The local name of a property to use; defaults to downcased target name.
   *
   * target_prop
   *    Same for target.  Defaults to downcased local name possibly with 's' added.
   */
function relationship(local, target, options) {
    if (!options.keys)
        throw new TypeError("keys option is required");
    if(!Array.isArray(options.keys))
        options.keys = [options.keys];
    const keys = Object.freeze(options.keys);
        if (options.uselist === undefined)
            options.uselist = true;
    const uselist = options.uselist;
    const order = options.order;
    const debug = options.debug || false;
    const local_prop = options.local_prop ||util.downFirst(target.name);
    let target_prop;
    if (options.target_prop === undefined) {
        target_prop = util.downFirst(local.name);
        if (uselist) target_prop = target_prop+'s';
    } else target_prop = options.target_prop;

    if (debug)
        console.log(`Relationship from ${local.name} to ${target.name} local_prop: ${local_prop} target_prop: ${target_prop}`);

    const local_map = new WeakMap();
    const target_map = new WeakMap();
    let add;
    let remove;
    if (uselist)  {
        add = function add(key, obj){
            let target_obj = target.syncStorageMap.get(key);
            if (target_obj === undefined) {
                console.warn(`${target.name} with key ${key} not found for ${local.name} relationship`);
                return;
            }
            let res = target_map.get(target_obj);
            if (res === undefined) {
                res = [];
                res.sort_required = true;
                if (relationship_observer) res = relationship_observer(res);
                target_map.set(target_obj, res);
            }
            if (res.indexOf(obj) == -1) {
                res.push(obj);
                res.sort_required = true;
            }
        }
        remove = function remove(key, obj) {
            let target_obj = target.syncStorageMap.get(key);
            if (target_obj === undefined) return;
            let res = target_map.get(target_obj);
            if (res === undefined)
                return;
            let idx = res.indexOf(obj);
            if (idx != -1)
                res.splice(idx, 1);
        }
    }else  {
        add = function add(key, obj) {
            let target_obj = target.syncStorageMap.get(key);
            if (target_obj === undefined) {
                console.warn(`${target.name} with key ${key} not found for ${local.name} relationship`);
                return;
            }
            target_map.set(target, obj);
        }
        remove = function remove(key, obj) {
            let target_obj = target.syncStorageMap.get(key);
            if (target_obj)
                target_map.delete(target_obj);
        }
    }
    function key(local_obj) {
        let ko = {};
        for (let i = 0; i < keys.length; i++) {
            if (local_obj[keys[i]] == undefined) // or null
                return undefined;
            ko[target.syncPrimaryKeys[i]] = local_obj[keys[i]];
        }
        return target.storageKey(ko);
    }

    function addOperationHandler(lobj) {
        // An add operation that may add an object to target's relation is fired
        let old_key = local_map.get(lobj);
        let new_key = key(lobj);
        if (debug)
            console.log(`Relation ${local.name}->${target.name} old_key: ${old_key} new_key: ${new_key}`);
        if (new_key !== old_key) {
            if (old_key)
                remove(old_key, lobj);
            if (new_key) {
                add(new_key, lobj);
                local_map.set(lobj, new_key);
            }            else local_map.delete(lobj);
        }
    }

    
    function deleteOperationHandler(lobj) {
        let old_key = local_map.get(lobj);
        if (old_key)
            remove(old_key, lobj);
        local_map.delete(lobj);
    }

    for (let o of relationship_add_ops)
        local.addEventListener(o, addOperationHandler);
    for (let o of relationship_delete_ops)
        local.addEventListener(o, deleteOperationHandler);

    Object.defineProperty(
        local.prototype, local_prop,
        {enumerable: true,
         configurable: true,
         get: function() {
             let local_key = key(this);
             if (debug)
                 console.log(`get ${local.name} key: ${local_key}`);
             return target.syncStorageMap.get(local_key);
         },
         set: function(v) {
             if (!(v instanceof target))
                 throw new TypeError(`${local_prop} must be a ${target.name}`);
             let new_key = target.storageKey(v);
             if (new_key === undefined)
                 return;
             let old_key = local_map.get(this);
             if (old_key)
                 remove(old_key, this);
             add(new_key, v);
             for (let i =0; i < keys.length; i++)
                 this[keys[i]] = v[target.syncPrimaryKeys[i]];
             return target.syncStorageMap.get(new_key);
         },
        });
    Object.defineProperty(
        target.prototype, target_prop,
        {configurable: true,
         enumerable: true,
         get: function() {
             let res = target_map.get(this);
             if (uselist && order &&res && res.sort_required ) {
                 res.sort(order);
                 res.sort_required = false;
             }
             return res;
         },
        });

}

function relationshipObserver(o) {
    relationship_observer = o;
}

                 
             
   
try {
    module.exports = {
        SyncOwner,
        IHave,
        YouHave,
        MyOwners,
        PersistentSynchronizable,
        setupPersistence,
        relationship,
        relationshipObserver,
        
    };
} catch (e) { }
