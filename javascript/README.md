# entanglement-core

**Entanglement** is a state synchronization protocol for Python and
JavaScript objects, loosely based on lessons learned by the [Mosh](https://mosh.org/)
developers. It is designed to synchronize user interface objects across the
world, even over wildly variable network conditions.

This package is the JavaScript/TypeScript implementation of the protocol's
wire and client-side primitives. For the full motivation behind the design
and how it differs from database synchronization, see the
[project documentation](https://entanglement.readthedocs.io/).

## Install

```bash
npm install entanglement-core
```

## Core concepts

- `SyncManager` — owns a single WebSocket connection to an Entanglement
  server. It (re)connects with backoff, dispatches incoming sync messages to
  registered receivers, and provides `synchronize()` to send state changes.
- `SyncRegistry` — maps sync type names to `Synchronizable` classes. It
  reconstructs received objects and dispatches event callbacks
  (`receive`, operations, deletes, etc.).
- `Synchronizable` — per-object behavior: `toSync()`, `syncReceive()`,
  `syncClone()`, and event handling for the object's own lifecycle.

## Usage

```js
import SyncManager, { SyncRegistry, Synchronizable } from 'entanglement-core';
import { setupPersistence } from 'entanglement-core/persistence';
import { filter } from 'entanglement-core/filter';
```

### Registering a class

A class becomes *synchronizable* by having `syncType`, `_syncAttributes`,
and `syncPrimaryKeys`. Register your class (or its base) with a
`SyncRegistry`:

```js
class Thing extends Synchronizable {
  syncType = 'Thing';
  static _syncAttributes = ['name', 'value'];
  static syncPrimaryKeys = ['id'];
}

const registry = new SyncRegistry();
registry.register(Thing);
```

If your model was generated from a Python schema, the registry can also drive
base-class derivation from the schema names (the `_schemaItem` mechanism).

### Connecting and synchronizing

```js
const manager = new SyncManager({
  url: 'ws://example.com/entanglement',
  registries: [registry],
});

// Attach lifecycle callbacks:
manager.onopen(() => console.log('connected'));
manager.onclose(() => console.log('disconnected'));

// Send a change for a given object/attribute set:
await manager.synchronize(myObject, {
  attributes: ['name', 'value'],
  operation: 'sync',
});
```

`synchronize()` accepts either an object with a `toSync(options)` method, or
a plain object plus an `attributes` list to copy.

### Reacting to received state

Register handlers on the registry (or a class) for events such as
`receive`, the specific operation name, `delete`, and `brokenTransition`:

```js
registry.addEventListener('receive', (obj, msg) => {
  console.log('got', obj.syncType, obj);
});
```

## Persistence and filters (optional extras)

The package also ships:

- `persistence.js` — `setupPersistence` and related helpers (storage maps,
  ownership) for persisting synchronized objects locally.
- `filter.js` — `filter`, `mapFilter`, `relationship` for policy-filtering
  what is shared across connections.

## License

GNU Lesser General Public License, version 3 (LGPL-3.0-only) — see
[LICENSE](../LICENSE).
